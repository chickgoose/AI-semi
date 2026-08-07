#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A9_PHASE4_STALL_OUT:-/tmp/a9-phase4-stall-sweep}"
VERILATOR_BIN="${VERILATOR:-verilator}"

mkdir -p "$OUT_DIR"
: >"$OUT_DIR/results.log"
for implementation in static diffusive centralized; do
  defines=()
  case "$implementation" in
    diffusive) defines=(-DA9_SWEEP_DIFFUSIVE) ;;
    centralized) defines=(-DA9_SWEEP_CENTRAL) ;;
  esac
  mkdir -p "$OUT_DIR/$implementation/obj"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-BLKSEQ \
    -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
    --top-module a9_phase4_stall_sweep_tb \
    --Mdir "$OUT_DIR/$implementation/obj" "${defines[@]}" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_centralized_reference.sv" \
    "$PROJECT_ROOT/tests/a9/a9_phase4_stall_sweep_tb.sv" \
    >"$OUT_DIR/$implementation/compile.log" 2>&1
  for stall_pct in 0 25 50 75 100; do
    "$OUT_DIR/$implementation/obj/Va9_phase4_stall_sweep_tb" \
      "+STALL_PCT=$stall_pct" | tee "$OUT_DIR/$implementation/stall-$stall_pct.log"
    grep 'A9_PHASE4_STALL_RESULT' \
      "$OUT_DIR/$implementation/stall-$stall_pct.log" >>"$OUT_DIR/results.log"
  done
done

test "$(grep -c 'A9_PHASE4_STALL_RESULT' "$OUT_DIR/results.log")" -eq 15
printf 'A9 phase-4 stall sweep complete: %s\n' "$OUT_DIR/results.log"
