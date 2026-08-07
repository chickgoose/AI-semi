#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A9_HANDOFF_TEST_OUT:-/tmp/a9-neighbor-handoff-test}"
VERILATOR_BIN="${VERILATOR:-verilator}"

mkdir -p "$OUT_DIR/obj"
"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-BLKSEQ \
  -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
  --top-module a9_neighbor_handoff_fabric_tb --Mdir "$OUT_DIR/obj" \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv" \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv" \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv" \
  "$PROJECT_ROOT/tests/a9/a9_neighbor_handoff_fabric_tb.sv"
"$OUT_DIR/obj/Va9_neighbor_handoff_fabric_tb" | tee "$OUT_DIR/run.log"
grep -q 'A9_NEIGHBOR_HANDOFF_PASS' "$OUT_DIR/run.log"
