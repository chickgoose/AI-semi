#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${AER_CLEAN_OUT:-$PROJECT_ROOT/results/a6-v2-lossless-codec}"
SIMULATOR="${AER_SIMULATOR:-}"
IVERILOG="${AER_IVERILOG:-iverilog}"
VVP="${AER_VVP:-vvp}"
IVERILOG_BASE="${AER_IVERILOG_BASE:-}"
VERILATOR="${AER_VERILATOR:-verilator}"
VERILATOR_ROOT_ARG="${AER_VERILATOR_ROOT:-}"
TRACE_JSONL="${AER_TRACE_JSONL:-}"
TRACE_MANIFEST="${AER_TRACE_MANIFEST:-}"
STIM_CYCLES="${AER_STIM_CYCLES:-256}"
LOAD_PCT="${AER_LOAD_PCT:-3}"
SEED="${AER_SEED:-1}"

if [[ -n "$TRACE_JSONL" || -n "$TRACE_MANIFEST" ]]; then
  [[ -n "$TRACE_JSONL" && -n "$TRACE_MANIFEST" ]] || {
    printf 'AER_TRACE_JSONL and AER_TRACE_MANIFEST must be set together\n' >&2
    exit 2
  }
  [[ $# -eq 0 ]] || {
    printf 'explicit tests cannot be combined with trace mode\n' >&2
    exit 2
  }
  tests=(trace)
else
  tests=("${@:-basic_single}")
fi

if [[ -z "$SIMULATOR" ]]; then
  if command -v xrun >/dev/null 2>&1; then
    SIMULATOR=xrun
  elif command -v "$IVERILOG" >/dev/null 2>&1; then
    SIMULATOR=iverilog
  elif command -v "$VERILATOR" >/dev/null 2>&1; then
    SIMULATOR=verilator
  else
    printf 'no supported simulator; set AER_SIMULATOR explicitly\n' >&2
    exit 1
  fi
fi

out_dir="$OUT_ROOT/n16-seed$SEED"
mkdir -p "$out_dir"
trace_args=()
if [[ -n "$TRACE_JSONL" ]]; then
  trace_stem="$(basename "$TRACE_JSONL" .events.jsonl)"
  prepared_trace="$out_dir/$trace_stem.svtrace"
  prepare_output="$(python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$TRACE_JSONL" --run-manifest "$TRACE_MANIFEST" \
    --output "$prepared_trace" --addr-width 6)"
  printf '%s\n' "$prepare_output"
  trace_report_name="${AER_TRACE_NAME:-}"
  if [[ -z "$trace_report_name" ]]; then
    trace_report_name="${prepare_output##*report_group=}"
    trace_report_name="${trace_report_name%% *}"
  fi
  trace_args=("+TRACE_FILE=$prepared_trace" "+TRACE_NAME=$trace_report_name")
fi

run_args() {
  local test_name="$1"
  RUN_ARGS=("+CLEAN_TEST=$test_name" "+CANDIDATE=a6-v2-lossless-codec"
    "+METRICS=$out_dir/$test_name.csv"
    "+EVENT_METRICS=$out_dir/$test_name.events.csv"
    "+A6_V2_LINK_METRICS=$out_dir/$test_name.link.txt"
    "+STIM_CYCLES=$STIM_CYCLES" "+LOAD_PCT=$LOAD_PCT" "+SEED=$SEED"
    "${trace_args[@]}")
}

case "$SIMULATOR" in
  xrun)
    snapshot=aer_clean_a6_v2_lossless_codec_n16
    (cd "$PROJECT_ROOT" && xrun -64bit -sv -timescale 1ns/1ps \
      -top aer_clean_tb -snapshot "$snapshot" -elaborate \
      -xmlibdirname "$out_dir/xcelium.d" \
      -defparam aer_clean_tb.NUM_SOURCES=16 \
      -defparam aer_clean_tb.ADDR_WIDTH=6 \
      -defparam aer_clean_tb.RETIRE_LANES=1 \
      -f rtl/candidates/a6_lossless_aer_codec/a6_v2_candidate_tb.f \
      -l "$out_dir/elaborate.log")
    for test_name in "${tests[@]}"; do
      run_args "$test_name"
      (cd "$PROJECT_ROOT" && xrun -64bit -R -snapshot "$snapshot" \
        -xmlibdirname "$out_dir/xcelium.d" "${RUN_ARGS[@]}" \
        -l "$out_dir/$test_name.log")
    done
    ;;
  iverilog)
    compiler=("$IVERILOG")
    runtime=("$VVP")
    if [[ -n "$IVERILOG_BASE" ]]; then
      compiler+=(-B "$IVERILOG_BASE")
      runtime+=(-M "$IVERILOG_BASE")
    fi
    (cd "$PROJECT_ROOT" && "${compiler[@]}" -g2012 -Wall -s aer_clean_tb \
      -P aer_clean_tb.NUM_SOURCES=16 -P aer_clean_tb.ADDR_WIDTH=6 \
      -P aer_clean_tb.RETIRE_LANES=1 \
      -f rtl/candidates/a6_lossless_aer_codec/a6_v2_candidate_tb.f \
      -o "$out_dir/aer_clean.vvp")
    for test_name in "${tests[@]}"; do
      run_args "$test_name"
      "${runtime[@]}" "$out_dir/aer_clean.vvp" "${RUN_ARGS[@]}" |
        tee "$out_dir/$test_name.log"
    done
    ;;
  verilator)
    verilator_env=()
    if [[ -n "$VERILATOR_ROOT_ARG" ]]; then
      verilator_env=(env "VERILATOR_ROOT=$VERILATOR_ROOT_ARG")
    fi
    obj_dir="$out_dir/verilator-obj"
    (cd "$PROJECT_ROOT" && "${verilator_env[@]}" "$VERILATOR" --binary \
      --timing -Wno-fatal --top-module aer_clean_tb -GNUM_SOURCES=16 \
      -GADDR_WIDTH=6 -GRETIRE_LANES=1 \
      -f rtl/candidates/a6_lossless_aer_codec/a6_v2_candidate_tb.f \
      --Mdir "$obj_dir" -o a6-v2-clean-sim)
    for test_name in "${tests[@]}"; do
      run_args "$test_name"
      "$obj_dir/a6-v2-clean-sim" "${RUN_ARGS[@]}" |
        tee "$out_dir/$test_name.log"
    done
    ;;
  *)
    printf 'unsupported AER_SIMULATOR=%s\n' "$SIMULATOR" >&2
    exit 1
    ;;
esac

printf 'A6 v2 lossless codec benchmark complete: %s\n' "$out_dir"
