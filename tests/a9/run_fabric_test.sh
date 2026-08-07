#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A9_FABRIC_TEST_OUT:-/tmp/a9-distributed-token-fabric-test}"
IVERILOG_BIN="${IVERILOG:-iverilog}"
VVP_BIN="${VVP:-vvp}"

mkdir -p "$OUT_DIR"
"$IVERILOG_BIN" -g2012 -Wall -s a9_distributed_token_fabric_tb \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv" \
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv" \
  "$PROJECT_ROOT/tests/a9/a9_distributed_token_fabric_tb.sv" \
  -o "$OUT_DIR/a9_distributed_token_fabric.vvp"
"$VVP_BIN" "$OUT_DIR/a9_distributed_token_fabric.vvp" | tee "$OUT_DIR/run.log"
grep -q 'A9_DISTRIBUTED_TOKEN_FABRIC_PASS' "$OUT_DIR/run.log"
