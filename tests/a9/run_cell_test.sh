#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A9_CELL_TEST_OUT:-/tmp/a9-empty-slot-cell-test}"
IVERILOG_BIN="${IVERILOG:-iverilog}"
VVP_BIN="${VVP:-vvp}"

mkdir -p "$OUT_DIR"
"$IVERILOG_BIN" -g2012 -Wall -s a9_empty_slot_cell_tb \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv" \
  "$PROJECT_ROOT/tests/a9/a9_empty_slot_cell_tb.sv" \
  -o "$OUT_DIR/a9_empty_slot_cell.vvp"
"$VVP_BIN" "$OUT_DIR/a9_empty_slot_cell.vvp" | tee "$OUT_DIR/run.log"
grep -q 'A9_EMPTY_SLOT_CELL_PASS' "$OUT_DIR/run.log"
