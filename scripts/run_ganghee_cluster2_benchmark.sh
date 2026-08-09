#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    'usage: run_ganghee_cluster2_benchmark.sh [sink-always-ready tests ...]' \
    'set AER_GANGHEE_CLUSTER2_TOP and exactly one of:' \
    '  AER_GANGHEE_CLUSTER2_RTL=/absolute/native.sv' \
    '  AER_GANGHEE_CLUSTER2_FILELIST=/absolute/native.f' >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ROOT="${AER_CLEAN_OUT:-$PROJECT_ROOT/results/clean-benchmark}"
TOP="${AER_GANGHEE_CLUSTER2_TOP:-}"
RTL="${AER_GANGHEE_CLUSTER2_RTL:-}"
NATIVE_FILELIST="${AER_GANGHEE_CLUSTER2_FILELIST:-}"
TRACE_JSONL="${AER_TRACE_JSONL:-}"
TRACE_MANIFEST="${AER_TRACE_MANIFEST:-}"
SEED="${AER_SEED:-1}"

[[ "$TOP" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || usage
[[ -z "$RTL" || -z "$NATIVE_FILELIST" ]] || usage
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
  [[ -n "$TRACE_JSONL" && -n "$TRACE_MANIFEST" && $# -eq 0 ]] || usage
  tests=(trace)
else
  if [[ $# -gt 0 ]]; then tests=("$@"); else tests=("${core_tests[@]}"); fi
  for test_name in "${tests[@]}"; do
    is_core_test "$test_name" || {
      printf 'unsupported cluster2 test: %s (always-ready core only)\n' \
        "$test_name" >&2
      exit 2
    }
  done
fi

command -v xrun >/dev/null 2>&1 || {
  printf 'direct cluster2 qualification requires Xcelium xrun\n' >&2
  exit 1
}

out_dir="$OUT_ROOT/ganghee-cluster2-direct-n16-seed${SEED}"
mkdir -p "$out_dir"
if [[ -n "$TRACE_JSONL" ]]; then
  trace_stem="$(basename "$TRACE_JSONL" .events.jsonl)"
  prepared_trace="$out_dir/$trace_stem.svtrace"
  prepare_output="$(python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$TRACE_JSONL" --run-manifest "$TRACE_MANIFEST" \
    --output "$prepared_trace" --addr-width 16)"
  read -r trace_version _ _ trace_source_count _ trace_sink_mode _ _ _ \
    < "$prepared_trace"
  [[ "$trace_version" == 3 && "$trace_source_count" == 16 && \
     "$trace_sink_mode" == 0 ]] || {
    printf 'cluster2 requires v3 N=16 always-ready trace\n' >&2
    exit 2
  }
  trace_name="${AER_TRACE_NAME:-${prepare_output##*report_group=}}"
  trace_name="${trace_name%% *}"
  trace_args=("+TRACE_FILE=$prepared_trace" "+TRACE_NAME=$trace_name")
fi

snapshot=aer_clean_ganghee_cluster2_direct_n16
compile=(xrun -64bit -sv -timescale 1ns/1ps -top aer_clean_tb
  -snapshot "$snapshot" -elaborate -xmlibdirname "$out_dir/xcelium.d"
  -define AER_CLEAN_GANGHEE_CLUSTER2
  -define "AER_GANGHEE_CLUSTER2_MODULE=$TOP"
  -defparam aer_clean_tb.NUM_SOURCES=16
  -defparam aer_clean_tb.ADDR_WIDTH=16
  -defparam aer_clean_tb.RETIRE_LANES=8
  -f "$PROJECT_ROOT/tb/clean/files.f")
if [[ -n "$RTL" ]]; then compile+=("$RTL"); else compile+=(-f "$NATIVE_FILELIST"); fi
(cd "$PROJECT_ROOT" && "${compile[@]}")

for test_name in "${tests[@]}"; do
  run=(xrun -64bit -R -snapshot "$snapshot"
    -xmlibdirname "$out_dir/xcelium.d" "+CLEAN_TEST=$test_name"
    "+CANDIDATE=ganghee-cluster2-raw-direct"
    "+METRICS=$out_dir/$test_name.csv"
    "+EVENT_METRICS=$out_dir/$test_name.events.csv" "+SEED=$SEED")
  [[ -z "$TRACE_JSONL" ]] || run+=("${trace_args[@]}")
  (cd "$PROJECT_ROOT" && "${run[@]}")
done

printf 'Ganghee raw cluster2 direct benchmark complete: %s\n' "$out_dir"
