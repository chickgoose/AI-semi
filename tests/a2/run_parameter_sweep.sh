#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A2_PHASE2_RTL_OUT:-/tmp/a2-phase2-rtl-sweep}"
IVERILOG="${A2_IVERILOG:-iverilog}"
VVP="${A2_VVP:-vvp}"

command -v "$IVERILOG" >/dev/null 2>&1 || {
  printf 'Icarus not found: %s\n' "$IVERILOG" >&2
  exit 1
}
command -v "$VVP" >/dev/null 2>&1 || {
  printf 'vvp not found: %s\n' "$VVP" >&2
  exit 1
}
mkdir -p "$OUT_DIR"

run_count=0
for sources in 16 32 64; do
  for banks in 1 2 4; do
    for depth in 4 8 16; do
      (( banks <= depth )) || continue
      binary="$OUT_DIR/a2-n${sources}-b${banks}-d${depth}.vvp"
      log="$OUT_DIR/a2-n${sources}-b${banks}-d${depth}.log"
      (cd "$PROJECT_ROOT" && "$IVERILOG" -g2012 -Wall \
        -s a2_parameter_sweep_tb -f tests/a2/a2_parameter_sweep.f \
        -P "a2_parameter_sweep_tb.NUM_SOURCES=$sources" \
        -P "a2_parameter_sweep_tb.BANK_COUNT=$banks" \
        -P "a2_parameter_sweep_tb.RESERVOIR_DEPTH=$depth" \
        -P "a2_parameter_sweep_tb.ENTER_LEVEL=$((depth/2))" \
        -P "a2_parameter_sweep_tb.EXIT_LEVEL=1" \
        -P "a2_parameter_sweep_tb.QUIET_CYCLES=3" \
        -o "$binary")
      "$VVP" "$binary" > "$log" 2>&1
      rg -q 'A2_PARAMETER_PASS' "$log"
      rg 'A2_PARAMETER_PASS' "$log"
      run_count=$((run_count + 1))
    done
  done
done

for enter in 2 4 6; do
  for exit_level in 0 1 2; do
    (( exit_level < enter )) || continue
    for dwell in 1 3 7; do
      binary="$OUT_DIR/a2-control-e${enter}-x${exit_level}-q${dwell}.vvp"
      log="$OUT_DIR/a2-control-e${enter}-x${exit_level}-q${dwell}.log"
      (cd "$PROJECT_ROOT" && "$IVERILOG" -g2012 -Wall \
        -s a2_parameter_sweep_tb -f tests/a2/a2_parameter_sweep.f \
        -P "a2_parameter_sweep_tb.NUM_SOURCES=16" \
        -P "a2_parameter_sweep_tb.BANK_COUNT=2" \
        -P "a2_parameter_sweep_tb.RESERVOIR_DEPTH=8" \
        -P "a2_parameter_sweep_tb.ENTER_LEVEL=$enter" \
        -P "a2_parameter_sweep_tb.EXIT_LEVEL=$exit_level" \
        -P "a2_parameter_sweep_tb.QUIET_CYCLES=$dwell" \
        -o "$binary")
      "$VVP" "$binary" > "$log" 2>&1
      rg -q 'A2_PARAMETER_PASS' "$log"
      rg 'A2_PARAMETER_PASS' "$log"
      run_count=$((run_count + 1))
    done
  done
done

printf 'A2_PARAMETER_SWEEP_PASS runs=%0d out=%s\n' "$run_count" "$OUT_DIR"
