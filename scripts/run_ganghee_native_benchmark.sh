#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    'usage: run_ganghee_native_benchmark.sh [sink-always-ready core tests ...]' \
    '' \
    'Required environment:' \
    '  AER_GANGHEE_TOP       native module name' \
    '  AER_GANGHEE_RTL       native RTL source path' \
    '    or' \
    '  AER_GANGHEE_FILELIST  native RTL file-list path (use absolute entries)' \
    '' \
    'Optional trace mode uses AER_TRACE_JSONL and AER_TRACE_MANIFEST together.' \
    'Only sink-always-ready traces are supported.' >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${AER_CLEAN_OUT:-$PROJECT_ROOT/results/clean-benchmark}"
TOP="${AER_GANGHEE_TOP:-}"
RTL="${AER_GANGHEE_RTL:-}"
NATIVE_FILELIST="${AER_GANGHEE_FILELIST:-}"
TRACE_JSONL="${AER_TRACE_JSONL:-}"
TRACE_MANIFEST="${AER_TRACE_MANIFEST:-}"
SEED="${AER_SEED:-1}"

[[ -n "$TOP" ]] || usage
[[ "$TOP" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
  printf 'invalid AER_GANGHEE_TOP=%s\n' "$TOP" >&2
  exit 2
}
if [[ -n "$RTL" && -n "$NATIVE_FILELIST" ]]; then
  printf 'set only one of AER_GANGHEE_RTL or AER_GANGHEE_FILELIST\n' >&2
  exit 2
fi
[[ -n "$RTL" || -n "$NATIVE_FILELIST" ]] || usage
[[ -z "$RTL" || -f "$RTL" ]] || { printf 'missing RTL: %s\n' "$RTL" >&2; exit 2; }
[[ -z "$NATIVE_FILELIST" || -f "$NATIVE_FILELIST" ]] || {
  printf 'missing file list: %s\n' "$NATIVE_FILELIST" >&2
  exit 2
}
core_tests=(
  basic_single basic_sparse basic_simultaneous
  limit_load limit_elephant_mouse limit_global_fanin limit_local_cluster
  limit_distributed_burst limit_retrigger limit_timing_fidelity
)
is_core_test() {
  local requested="$1" supported
  for supported in "${core_tests[@]}"; do
    [[ "$requested" == "$supported" ]] && return 0
  done
  return 1
}

trace_args=()
if [[ -n "$TRACE_JSONL" || -n "$TRACE_MANIFEST" ]]; then
  [[ -n "$TRACE_JSONL" && -n "$TRACE_MANIFEST" ]] || {
    printf 'AER_TRACE_JSONL and AER_TRACE_MANIFEST must be set together\n' >&2
    exit 2
  }
  [[ $# -eq 0 ]] || {
    printf 'explicit core tests cannot be combined with trace mode\n' >&2
    exit 2
  }
  tests=(trace)
else
  if [[ $# -gt 0 ]]; then
    tests=("$@")
  else
    tests=("${core_tests[@]}")
  fi
  for test_name in "${tests[@]}"; do
    is_core_test "$test_name" || {
      printf 'unsupported native-binding test: %s (sink-always-ready core only)\n' \
        "$test_name" >&2
      exit 2
    }
  done
fi

command -v xrun >/dev/null 2>&1 || {
  printf 'Ganghee native binding qualification requires Xcelium xrun\n' >&2
  exit 1
}

out_dir="$OUT_ROOT/ganghee-native-n16-seed${SEED}"
mkdir -p "$out_dir"

if [[ -n "$TRACE_JSONL" ]]; then
  trace_stem="$(basename "$TRACE_JSONL")"
  trace_stem="${trace_stem%.events.jsonl}"
  trace_report_name="${AER_TRACE_NAME:-$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("report_group", "trace"))' \
    "$TRACE_MANIFEST")}"
  prepared_trace="$out_dir/$trace_stem.svtrace"
  python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$TRACE_JSONL" --run-manifest "$TRACE_MANIFEST" \
    --output "$prepared_trace" --addr-width 16
  read -r trace_version trace_count trace_stim_cycles trace_source_count \
    trace_load_milli trace_sink_mode trace_sink_arg0 trace_sink_arg1 \
    trace_seed_name < "$prepared_trace"
  [[ "$trace_version" == 3 && "$trace_source_count" == 16 ]] || {
    printf 'prepared trace must be version 3 with 16 sources\n' >&2
    exit 2
  }
  [[ "$trace_sink_mode" == 0 ]] || {
    printf 'Ganghee native binding supports sink-always-ready traces only\n' >&2
    exit 2
  }
  trace_args=("+TRACE_FILE=$prepared_trace" "+TRACE_NAME=$trace_report_name")
fi

snapshot=aer_clean_ganghee_native_n16
compile_command=(xrun -64bit -sv -timescale 1ns/1ps
  -top aer_clean_tb -snapshot "$snapshot" -elaborate
  -xmlibdirname "$out_dir/xcelium.d"
  -define AER_CLEAN_GANGHEE_NATIVE
  -define "AER_GANGHEE_NATIVE_MODULE=$TOP"
  -defparam aer_clean_tb.NUM_SOURCES=16
  -defparam aer_clean_tb.ADDR_WIDTH=16
  -defparam aer_clean_tb.RETIRE_LANES=1
  -f "$PROJECT_ROOT/tb/clean/files.f"
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_native_binding.sv")
if [[ -n "$RTL" ]]; then
  compile_command+=("$RTL")
else
  compile_command+=(-f "$NATIVE_FILELIST")
fi
compile_command+=(-l "$out_dir/elaborate.log")
(cd "$PROJECT_ROOT" && "${compile_command[@]}")

for test_name in "${tests[@]}"; do
  run_command=(xrun -64bit -R -snapshot "$snapshot"
    -xmlibdirname "$out_dir/xcelium.d"
    "+CLEAN_TEST=$test_name" "+CANDIDATE=ganghee-native-coordinate-source-projection"
    "+METRICS=$out_dir/$test_name.csv"
    "+EVENT_METRICS=$out_dir/$test_name.events.csv"
    "+SEED=$SEED" -l "$out_dir/$test_name.log")
  if [[ -n "$TRACE_JSONL" ]]; then
    run_command+=("${trace_args[@]}")
  fi
  (cd "$PROJECT_ROOT" && "${run_command[@]}")
done

printf 'Ganghee native clean benchmark complete: %s\n' "$out_dir"
