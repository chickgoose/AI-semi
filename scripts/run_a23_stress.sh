#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULTS_ROOT="${A23_RESULTS_ROOT:-/tmp/a23-stress-results}"
SEEDS="${A23_SEEDS:-1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20}"
SOURCE_COUNTS="${A23_NUM_SOURCES:-1 3 4}"
SIMULATORS="${A23_SIMULATORS:-iverilog verilator}"
TRACE="${A23_TRACE:-0}"
FILELIST="$PROJECT_ROOT/tests/a23_stress/a23_stress.f"

mkdir -p "$RESULTS_ROOT"

find_tool() {
  local requested="$1"
  local fallback="$2"
  if [[ -n "$requested" ]]; then
    printf '%s\n' "$requested"
  elif command -v "$fallback" >/dev/null 2>&1; then
    command -v "$fallback"
  else
    printf 'missing tool: %s\n' "$fallback" >&2
    return 1
  fi
}

run_iverilog() {
  local iverilog_bin vvp_bin sources seed out_dir binary log wave
  iverilog_bin="$(find_tool "${A23_IVERILOG_BIN:-}" iverilog)"
  vvp_bin="$(find_tool "${A23_VVP_BIN:-}" vvp)"
  for sources in $SOURCE_COUNTS; do
    out_dir="$RESULTS_ROOT/iverilog/n$sources"
    mkdir -p "$out_dir"
    binary="$out_dir/a23_stress.vvp"
    (
      cd "$PROJECT_ROOT"
      "$iverilog_bin" -g2012 -Wall -s a23_stress_tb \
        -P "a23_stress_tb.NUM_SOURCES=$sources" \
        -o "$binary" -f "$FILELIST"
    ) 2>&1 | tee "$out_dir/compile.log"
    for seed in $SEEDS; do
      log="$out_dir/seed-$seed.log"
      if [[ "$TRACE" == "1" ]]; then
        wave="$out_dir/seed-$seed.vcd"
        "$vvp_bin" "$binary" "+SEED=$seed" "+WAVE=$wave" 2>&1 | tee "$log"
      else
        "$vvp_bin" "$binary" "+SEED=$seed" 2>&1 | tee "$log"
      fi
    done
  done
}

run_verilator() {
  local verilator_bin sources seed out_dir object_dir binary log wave
  local -a trace_args
  verilator_bin="$(find_tool "${A23_VERILATOR_BIN:-}" verilator)"
  trace_args=()
  [[ "$TRACE" == "1" ]] && trace_args+=(--trace)
  for sources in $SOURCE_COUNTS; do
    out_dir="$RESULTS_ROOT/verilator/n$sources"
    object_dir="$out_dir/obj_dir"
    mkdir -p "$out_dir"
    (
      cd "$PROJECT_ROOT"
      "$verilator_bin" --binary --timing --assert -Wall -Wno-fatal \
        --top-module a23_stress_tb "-GNUM_SOURCES=$sources" \
        --Mdir "$object_dir" "${trace_args[@]}" -f "$FILELIST"
    ) 2>&1 | tee "$out_dir/compile.log"
    binary="$object_dir/Va23_stress_tb"
    for seed in $SEEDS; do
      log="$out_dir/seed-$seed.log"
      if [[ "$TRACE" == "1" ]]; then
        wave="$out_dir/seed-$seed.vcd"
        "$binary" "+SEED=$seed" "+WAVE=$wave" 2>&1 | tee "$log"
      else
        "$binary" "+SEED=$seed" 2>&1 | tee "$log"
      fi
    done
  done
}

for simulator in $SIMULATORS; do
  case "$simulator" in
    iverilog) run_iverilog ;;
    verilator) run_verilator ;;
    *) printf 'unsupported simulator: %s\n' "$simulator" >&2; exit 2 ;;
  esac
done

printf 'A23 stress regression complete: %s\n' "$RESULTS_ROOT"
