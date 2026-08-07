#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A9_PHASE3_COMPARE_OUT:-/tmp/a9-phase3-comparison}"
VERILATOR_BIN="${VERILATOR:-verilator}"

mkdir -p "$OUT_DIR"
: >"$OUT_DIR/results.log"
for implementation in static diffusive centralized; do
  defines=()
  case "$implementation" in
    diffusive) defines=(-DA9_COMPARE_DIFFUSIVE) ;;
    centralized) defines=(-DA9_COMPARE_CENTRAL) ;;
  esac
  mkdir -p "$OUT_DIR/$implementation/obj"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-BLKSEQ \
    -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
    --top-module a9_phase3_compare_tb \
    --Mdir "$OUT_DIR/$implementation/obj" "${defines[@]}" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv" \
    "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_centralized_reference.sv" \
    "$PROJECT_ROOT/tests/a9/a9_phase3_compare_tb.sv" \
    >"$OUT_DIR/$implementation/compile.log" 2>&1
  for workload in 0 1 2 3; do
    "$OUT_DIR/$implementation/obj/Va9_phase3_compare_tb" \
      "+WORKLOAD=$workload" | tee "$OUT_DIR/$implementation/workload-$workload.log"
    grep 'A9_PHASE3_RESULT' "$OUT_DIR/$implementation/workload-$workload.log" \
      >>"$OUT_DIR/results.log"
  done
done

test "$(grep -c 'A9_PHASE3_RESULT' "$OUT_DIR/results.log")" -eq 12
printf 'A9 phase-3 comparison complete: %s\n' "$OUT_DIR/results.log"
