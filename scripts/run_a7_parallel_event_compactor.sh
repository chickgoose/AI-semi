#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s <1|2|4|8> [synthetic-test ...]\n' "$0" >&2
  printf 'or set AER_TRACE_JSONL and AER_TRACE_MANIFEST for one frozen trace\n' >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
retire_lanes="$1"
shift
case "$retire_lanes" in 1|2|4|8) ;; *) usage ;; esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
out_root="${AER_CLEAN_OUT:-$project_root/results/a7-parallel-event-compactor}"
simulator="${AER_SIMULATOR:-}"
num_sources="${AER_NUM_SOURCES:-16}"
addr_width="${AER_ADDR_WIDTH:-16}"
stim_cycles="${AER_STIM_CYCLES:-256}"
load_pct="${AER_LOAD_PCT:-3}"
seed="${AER_SEED:-1}"
trace_jsonl="${AER_TRACE_JSONL:-}"
trace_manifest="${AER_TRACE_MANIFEST:-}"
implementation="${AER_A7_IMPL:-prefix}"
case "$implementation" in prefix|replicated) ;; *) usage ;; esac

if [[ -n "$trace_jsonl" || -n "$trace_manifest" ]]; then
  [[ -n "$trace_jsonl" && -n "$trace_manifest" && $# -eq 0 ]] || usage
  tests=(trace)
elif [[ $# -gt 0 ]]; then
  tests=("$@")
else
  tests=(basic_single basic_sparse basic_simultaneous limit_global_fanin)
fi

if [[ -z "$simulator" ]]; then
  if command -v xrun >/dev/null 2>&1; then simulator=xrun
  elif command -v verilator >/dev/null 2>&1; then simulator=verilator
  elif command -v iverilog >/dev/null 2>&1; then simulator=iverilog
  else printf 'no supported simulator; set AER_SIMULATOR=xrun, verilator, or iverilog\n' >&2; exit 1
  fi
fi

trace_stem="synthetic"
trace_args=()
if [[ -n "$trace_jsonl" ]]; then
  trace_stem="$(basename "$trace_jsonl" .events.jsonl)"
fi
out_dir="$out_root/$implementation/k$retire_lanes/$trace_stem"
build_dir="$out_root/$implementation/k$retire_lanes/build"
mkdir -p "$out_dir"
mkdir -p "$build_dir"

if [[ -n "$trace_jsonl" ]]; then
  prepared_trace="$out_dir/$trace_stem.svtrace"
  prepare_output="$(python3 "$project_root/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$trace_jsonl" --run-manifest "$trace_manifest" \
    --output "$prepared_trace" --addr-width "$addr_width")"
  printf '%s\n' "$prepare_output"
  trace_report_name="${AER_TRACE_NAME:-}"
  if [[ -z "$trace_report_name" ]]; then
    trace_report_name="${prepare_output##*report_group=}"
    trace_report_name="${trace_report_name%% *}"
  fi
  trace_args=("+TRACE_FILE=$prepared_trace" "+TRACE_NAME=$trace_report_name")
fi

candidate_files=(
  "$project_root/tb/clean/aer_bench_if.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_prefix_count.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_shared_rank_index_select.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_event_compactor.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv"
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv"
  "$project_root/tb/clean/native/a7_parallel_event_compactor_binding.sv"
  "$project_root/tb/clean/native/a7_replicated_selector_binding.sv"
  "$project_root/tests/a7_parallel_event_compactor/a7_normalized_candidate_cell.sv"
  "$project_root/tb/clean/aer_clean_assertions.sv"
  "$project_root/tb/clean/aer_clean_tb.sv"
)
candidate_name="a7_${implementation}_k$retire_lanes"
verilator_define=()
iverilog_define=()
xrun_define=()
if [[ "$implementation" == replicated ]]; then
  verilator_define=(-DAER_A7_REPLICATED_REFERENCE)
  iverilog_define=(-DAER_A7_REPLICATED_REFERENCE)
  xrun_define=(-define AER_A7_REPLICATED_REFERENCE)
fi

case "$simulator" in
  verilator)
    verilator --binary --timing -Wno-fatal --top-module aer_clean_tb \
      -GNUM_SOURCES="$num_sources" -GADDR_WIDTH="$addr_width" \
      -GRETIRE_LANES="$retire_lanes" --Mdir "$build_dir/obj" \
      -o a7_clean ${verilator_define[@]+"${verilator_define[@]}"} \
      "${candidate_files[@]}"
    for test_name in "${tests[@]}"; do
      "$build_dir/obj/a7_clean" "+CLEAN_TEST=$test_name" \
        "+CANDIDATE=$candidate_name" "+METRICS=$out_dir/$test_name.csv" \
        "+EVENT_METRICS=$out_dir/$test_name.events.csv" \
        "+STIM_CYCLES=$stim_cycles" "+LOAD_PCT=$load_pct" "+SEED=$seed" \
        ${trace_args[@]+"${trace_args[@]}"} | tee "$out_dir/$test_name.log"
    done
    ;;
  iverilog)
    iverilog -g2012 -Wall -s aer_clean_tb \
      ${iverilog_define[@]+"${iverilog_define[@]}"} \
      -P "aer_clean_tb.NUM_SOURCES=$num_sources" \
      -P "aer_clean_tb.ADDR_WIDTH=$addr_width" \
      -P "aer_clean_tb.RETIRE_LANES=$retire_lanes" \
      "${candidate_files[@]}" -o "$build_dir/a7_clean.vvp"
    for test_name in "${tests[@]}"; do
      vvp "$build_dir/a7_clean.vvp" "+CLEAN_TEST=$test_name" \
        "+CANDIDATE=$candidate_name" "+METRICS=$out_dir/$test_name.csv" \
        "+EVENT_METRICS=$out_dir/$test_name.events.csv" \
        "+STIM_CYCLES=$stim_cycles" "+LOAD_PCT=$load_pct" "+SEED=$seed" \
        ${trace_args[@]+"${trace_args[@]}"} | tee "$out_dir/$test_name.log"
    done
    ;;
  xrun)
    snapshot="a7_compactor_k${retire_lanes}_n${num_sources}"
    xrun -64bit -sv -timescale 1ns/1ps -top aer_clean_tb \
      -snapshot "$snapshot" -elaborate -xmlibdirname "$build_dir/xcelium.d" \
      -defparam "aer_clean_tb.NUM_SOURCES=$num_sources" \
      -defparam "aer_clean_tb.ADDR_WIDTH=$addr_width" \
      -defparam "aer_clean_tb.RETIRE_LANES=$retire_lanes" \
      ${xrun_define[@]+"${xrun_define[@]}"} \
      "${candidate_files[@]}" -l "$build_dir/elaborate.log"
    for test_name in "${tests[@]}"; do
      xrun -64bit -R -snapshot "$snapshot" -xmlibdirname "$build_dir/xcelium.d" \
        "+CLEAN_TEST=$test_name" "+CANDIDATE=$candidate_name" \
        "+METRICS=$out_dir/$test_name.csv" \
        "+EVENT_METRICS=$out_dir/$test_name.events.csv" \
        "+STIM_CYCLES=$stim_cycles" "+LOAD_PCT=$load_pct" "+SEED=$seed" \
        ${trace_args[@]+"${trace_args[@]}"} -l "$out_dir/$test_name.log"
    done
    ;;
  *) printf 'unsupported AER_SIMULATOR=%s\n' "$simulator" >&2; exit 1 ;;
esac

printf 'A7 benchmark complete: %s\n' "$out_dir"
