#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s <mock|baseline|a23-ee430> [test ...]\n' "$0" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
design="$1"
shift

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$PROJECT_ROOT/scripts/lib/mixed_phase_analysis.sh"
OUT_ROOT="${AER_CLEAN_OUT:-$PROJECT_ROOT/results/clean-benchmark}"
SIMULATOR="${AER_SIMULATOR:-}"
NUM_SOURCES="${AER_NUM_SOURCES:-4}"
ADDR_WIDTH="${AER_ADDR_WIDTH:-16}"
RETIRE_LANES="${AER_RETIRE_LANES:-2}"
STIM_CYCLES="${AER_STIM_CYCLES:-256}"
LOAD_PCT="${AER_LOAD_PCT:-3}"
SEED="${AER_SEED:-1}"
TRACE_JSONL="${AER_TRACE_JSONL:-}"
TRACE_MANIFEST="${AER_TRACE_MANIFEST:-}"
prepared_report_name=""

design_define=""
design_filelist=""
case "$design" in
  mock) ;;
  baseline)
    design_define="AER_DUT_BASELINE"
    design_filelist="$PROJECT_ROOT/tb/filelists/baseline.f"
    ;;
  a23-ee430)
    design_define="AER_DUT_A23_EE430"
    design_filelist="$PROJECT_ROOT/tb/filelists/a23_ee430.f"
    ;;
  *) usage ;;
esac

if [[ -n "$TRACE_JSONL" || -n "$TRACE_MANIFEST" ]]; then
  [[ -n "$TRACE_JSONL" && -n "$TRACE_MANIFEST" ]] || {
    printf 'AER_TRACE_JSONL and AER_TRACE_MANIFEST must be set together\n' >&2
    exit 2
  }
  [[ $# -eq 0 ]] || {
    printf 'explicit synthetic tests cannot be combined with trace mode\n' >&2
    exit 2
  }
  tests=(trace)
elif [[ $# -gt 0 ]]; then
  tests=("$@")
else
  tests=(
    basic_single basic_sparse basic_simultaneous basic_backpressure
    limit_load limit_elephant_mouse limit_global_fanin
    limit_local_cluster limit_distributed_burst limit_retrigger
    limit_timing_fidelity limit_backpressure_shock
  )
fi

if [[ -z "$SIMULATOR" ]]; then
  if command -v xrun >/dev/null 2>&1; then
    SIMULATOR=xrun
  elif command -v iverilog >/dev/null 2>&1; then
    SIMULATOR=iverilog
  else
    printf 'no supported simulator found; set AER_SIMULATOR=xrun or iverilog\n' >&2
    exit 1
  fi
fi

out_dir="$OUT_ROOT/$design-n${NUM_SOURCES}-seed${SEED}"
mkdir -p "$out_dir"

trace_args=()
if [[ -n "$TRACE_JSONL" ]]; then
  trace_stem="$(basename "$TRACE_JSONL")"
  trace_stem="${trace_stem%.events.jsonl}"
  prepared_trace="$out_dir/$trace_stem.svtrace"
  prepare_output="$(python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$TRACE_JSONL" --run-manifest "$TRACE_MANIFEST" \
    --output "$prepared_trace" --addr-width "$ADDR_WIDTH")"
  printf '%s\n' "$prepare_output"
  prepared_report_name="${prepare_output##*report_group=}"
  prepared_report_name="${prepared_report_name%% *}"
  trace_report_name="${AER_TRACE_NAME:-}"
  if [[ -z "$trace_report_name" ]]; then
    trace_report_name="$prepared_report_name"
  fi
  trace_args=("+TRACE_FILE=$prepared_trace" "+TRACE_NAME=$trace_report_name")
fi

common_params=(
  "aer_clean_tb.NUM_SOURCES=$NUM_SOURCES"
  "aer_clean_tb.ADDR_WIDTH=$ADDR_WIDTH"
  "aer_clean_tb.RETIRE_LANES=$RETIRE_LANES"
)

case "$SIMULATOR" in
  xrun)
    snapshot_design="${design//-/_}"
    snapshot="aer_clean_${snapshot_design}_n${NUM_SOURCES}"
    command=(xrun -64bit -sv -timescale 1ns/1ps -top aer_clean_tb
      -snapshot "$snapshot" -elaborate -xmlibdirname "$out_dir/xcelium.d")
    for parameter in "${common_params[@]}"; do
      command+=(-defparam "$parameter")
    done
    [[ -n "$design_define" ]] && command+=(-define "$design_define")
    [[ -n "$design_filelist" ]] && command+=(-f "$design_filelist")
    command+=(-f "$PROJECT_ROOT/tb/clean/files.f" -l "$out_dir/elaborate.log")
    (cd "$PROJECT_ROOT" && "${command[@]}")

    for test_name in "${tests[@]}"; do
      metrics_path="$out_dir/$test_name.csv"
      event_metrics_path="$out_dir/$test_name.events.csv"
      mixed_metrics_path="$out_dir/$test_name.mixed_metrics.json"
      mixed_phase_clear_outputs "$prepared_report_name" \
        "$metrics_path" "$event_metrics_path" "$mixed_metrics_path"
      run_command=(xrun -64bit -R -snapshot "$snapshot"
        -xmlibdirname "$out_dir/xcelium.d"
        "+CLEAN_TEST=$test_name" "+METRICS=$metrics_path"
        "+CANDIDATE=$design"
        "+EVENT_METRICS=$event_metrics_path"
        "+STIM_CYCLES=$STIM_CYCLES" "+LOAD_PCT=$LOAD_PCT" "+SEED=$SEED"
        -l "$out_dir/$test_name.log")
      run_command+=("${trace_args[@]}")
      if ! (cd "$PROJECT_ROOT" && "${run_command[@]}"); then
        # Retry only Xcelium's transient shared-server snapshot setup race.
        # Functional failures and all other setup errors remain fatal.
        if grep -q 'NOSTUP' "$out_dir/$test_name.log"; then
          sleep 1
          (cd "$PROJECT_ROOT" && "${run_command[@]}")
        else
          exit 1
        fi
      fi
      mixed_phase_require_qualified "$prepared_report_name" "$PROJECT_ROOT" \
        "$TRACE_MANIFEST" "$metrics_path" "$event_metrics_path" "$mixed_metrics_path"
    done
    ;;
  iverilog)
    command=(iverilog -g2012 -Wall -s aer_clean_tb)
    for parameter in "${common_params[@]}"; do
      command+=(-P "$parameter")
    done
    [[ -n "$design_define" ]] && command+=("-D$design_define")
    [[ -n "$design_filelist" ]] && command+=(-f "$design_filelist")
    command+=(-f "$PROJECT_ROOT/tb/clean/files.f" -o "$out_dir/aer_clean.vvp")
    (cd "$PROJECT_ROOT" && "${command[@]}")

    for test_name in "${tests[@]}"; do
      metrics_path="$out_dir/$test_name.csv"
      event_metrics_path="$out_dir/$test_name.events.csv"
      mixed_metrics_path="$out_dir/$test_name.mixed_metrics.json"
      mixed_phase_clear_outputs "$prepared_report_name" \
        "$metrics_path" "$event_metrics_path" "$mixed_metrics_path"
      vvp "$out_dir/aer_clean.vvp" "+CLEAN_TEST=$test_name" \
        "+CANDIDATE=$design" \
        "+METRICS=$metrics_path" "+STIM_CYCLES=$STIM_CYCLES" \
        "+EVENT_METRICS=$event_metrics_path" \
        "+LOAD_PCT=$LOAD_PCT" "+SEED=$SEED" "${trace_args[@]}" | \
        tee "$out_dir/$test_name.log"
      mixed_phase_require_qualified "$prepared_report_name" "$PROJECT_ROOT" \
        "$TRACE_MANIFEST" "$metrics_path" "$event_metrics_path" "$mixed_metrics_path"
    done
    ;;
  *)
    printf 'unsupported AER_SIMULATOR=%s\n' "$SIMULATOR" >&2
    exit 1
    ;;
esac

printf 'clean AER benchmark complete: %s\n' "$out_dir"
