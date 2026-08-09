#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s [test ...]\n' "$0" >&2
  printf 'trace mode: set AER_TRACE_JSONL and AER_TRACE_MANIFEST together\n' >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${AER_CLEAN_OUT:-$PROJECT_ROOT/results/a2-adaptive-dual-path}"
SIMULATOR="${AER_SIMULATOR:-}"
NUM_SOURCES="${AER_NUM_SOURCES:-16}"
ADDR_WIDTH="${AER_ADDR_WIDTH:-16}"
RETIRE_LANES="${AER_RETIRE_LANES:-1}"
STIM_CYCLES="${AER_STIM_CYCLES:-256}"
LOAD_PCT="${AER_LOAD_PCT:-3}"
SEED="${AER_SEED:-1}"
A2_RESERVOIR_DEPTH="${A2_RESERVOIR_DEPTH:-8}"
A2_BANK_COUNT="${A2_BANK_COUNT:-2}"
A2_ENTER_LEVEL="${A2_ENTER_LEVEL:-4}"
A2_EXIT_LEVEL="${A2_EXIT_LEVEL:-1}"
A2_QUIET_CYCLES="${A2_QUIET_CYCLES:-3}"
TRACE_JSONL="${AER_TRACE_JSONL:-}"
TRACE_MANIFEST="${AER_TRACE_MANIFEST:-}"
CANDIDATE="a2-adaptive-dual-path"

if [[ "${1:-}" == "--help" ]]; then
  usage
fi

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
    basic_single basic_sparse basic_simultaneous basic_reset_drain
    limit_load limit_elephant_mouse limit_global_fanin
    limit_local_cluster limit_distributed_burst limit_retrigger
    limit_timing_fidelity
  )
fi

if [[ -z "$SIMULATOR" ]]; then
  if command -v xrun >/dev/null 2>&1; then
    SIMULATOR=xrun
  elif command -v verilator >/dev/null 2>&1; then
    SIMULATOR=verilator
  else
    printf 'no supported simulator found; set AER_SIMULATOR=xrun or verilator\n' >&2
    exit 1
  fi
fi

out_dir="$OUT_ROOT/n${NUM_SOURCES}-seed${SEED}"
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
  trace_report_name="${AER_TRACE_NAME:-}"
  if [[ -z "$trace_report_name" ]]; then
    trace_report_name="${prepare_output##*report_group=}"
    trace_report_name="${trace_report_name%% *}"
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
    snapshot="aer_clean_a2_n${NUM_SOURCES}_b${A2_BANK_COUNT}_d${A2_RESERVOIR_DEPTH}"
    command=(xrun -64bit -sv -timescale 1ns/1ps -top aer_clean_tb
      -snapshot "$snapshot" -elaborate -xmlibdirname "$out_dir/xcelium.d"
      "+define+A2_RESERVOIR_DEPTH=$A2_RESERVOIR_DEPTH"
      "+define+A2_BANK_COUNT=$A2_BANK_COUNT"
      "+define+A2_ENTER_LEVEL=$A2_ENTER_LEVEL"
      "+define+A2_EXIT_LEVEL=$A2_EXIT_LEVEL"
      "+define+A2_QUIET_CYCLES=$A2_QUIET_CYCLES")
    for parameter in "${common_params[@]}"; do
      command+=(-defparam "$parameter")
    done
    command+=(-f "$PROJECT_ROOT/rtl/candidates/a2_adaptive_dual_path/a2_benchmark.f"
      -l "$out_dir/elaborate.log")
    (cd "$PROJECT_ROOT" && "${command[@]}")

    for test_name in "${tests[@]}"; do
      run_command=(xrun -64bit -R -snapshot "$snapshot"
        -xmlibdirname "$out_dir/xcelium.d"
        "+CLEAN_TEST=$test_name" "+METRICS=$out_dir/$test_name.csv"
        "+CANDIDATE=$CANDIDATE"
        "+EVENT_METRICS=$out_dir/$test_name.events.csv"
        "+STIM_CYCLES=$STIM_CYCLES" "+LOAD_PCT=$LOAD_PCT" "+SEED=$SEED"
        -l "$out_dir/$test_name.log")
      run_command+=("${trace_args[@]}")
      if ! (cd "$PROJECT_ROOT" && "${run_command[@]}"); then
        if rg -q 'NOSTUP' "$out_dir/$test_name.log"; then
          sleep 1
          (cd "$PROJECT_ROOT" && "${run_command[@]}")
        else
          exit 1
        fi
      fi
    done
    ;;
  verilator)
    verilator_binary="${A2_VERILATOR_BINARY:-}"
    if [[ -n "$verilator_binary" ]]; then
      [[ -x "$verilator_binary" ]] || {
        printf 'A2_VERILATOR_BINARY is not executable: %s\n' "$verilator_binary" >&2
        exit 2
      }
    else
      verilator_obj="$out_dir/verilator-obj"
      mkdir -p "$verilator_obj"
      (cd "$PROJECT_ROOT" && verilator --binary --timing -Wno-fatal \
        -Wno-TIMESCALEMOD -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC \
        -Wno-BLKSEQ -Wno-SYNCASYNCNET --top-module aer_clean_tb \
        "-GNUM_SOURCES=$NUM_SOURCES" "-GADDR_WIDTH=$ADDR_WIDTH" \
        "-GRETIRE_LANES=$RETIRE_LANES" \
        "-DA2_RESERVOIR_DEPTH=$A2_RESERVOIR_DEPTH" \
        "-DA2_BANK_COUNT=$A2_BANK_COUNT" \
        "-DA2_ENTER_LEVEL=$A2_ENTER_LEVEL" \
        "-DA2_EXIT_LEVEL=$A2_EXIT_LEVEL" \
        "-DA2_QUIET_CYCLES=$A2_QUIET_CYCLES" \
        -f rtl/candidates/a2_adaptive_dual_path/a2_benchmark.f \
        --Mdir "$verilator_obj" -o aer_clean_a2)
      verilator_binary="$verilator_obj/aer_clean_a2"
    fi
    for test_name in "${tests[@]}"; do
      "$verilator_binary" "+CLEAN_TEST=$test_name" \
        "+CANDIDATE=$CANDIDATE" \
        "+METRICS=$out_dir/$test_name.csv" "+STIM_CYCLES=$STIM_CYCLES" \
        "+EVENT_METRICS=$out_dir/$test_name.events.csv" \
        "+LOAD_PCT=$LOAD_PCT" "+SEED=$SEED" "${trace_args[@]}" \
        > "$out_dir/$test_name.log" 2>&1
      tail -20 "$out_dir/$test_name.log"
    done
    ;;
  *)
    printf 'unsupported AER_SIMULATOR=%s\n' "$SIMULATOR" >&2
    exit 1
    ;;
esac

printf 'A2 clean AER benchmark complete: %s\n' "$out_dir"
