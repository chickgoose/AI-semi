#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANDIDATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A5_TEST_OUT:-/tmp/a5-speculative-pregrant-unit}"
IVERILOG="${IVERILOG:-iverilog}"
VVP="${VVP:-vvp}"
IVERILOG_BASE="${IVERILOG_BASE:-}"
VVP_MODULE_DIR="${VVP_MODULE_DIR:-}"
IVERILOG_ARGS=()
VVP_ARGS=()
if [[ -n "$IVERILOG_BASE" ]]; then IVERILOG_ARGS=(-B "$IVERILOG_BASE"); fi
if [[ -n "$VVP_MODULE_DIR" ]]; then VVP_ARGS=(-M "$VVP_MODULE_DIR"); fi

mkdir -p "$OUT_DIR"
"$IVERILOG" "${IVERILOG_ARGS[@]}" -g2012 -Wall -s a5_transition_predictor_tb \
  "$CANDIDATE_DIR/a5_transition_predictor.sv" \
  "$CANDIDATE_DIR/a5_last_successor_predictor.sv" \
  "$SCRIPT_DIR/a5_transition_predictor_tb.sv" \
  -o "$OUT_DIR/a5_transition_predictor.vvp"
"$VVP" "${VVP_ARGS[@]}" "$OUT_DIR/a5_transition_predictor.vvp"

"$IVERILOG" "${IVERILOG_ARGS[@]}" -g2012 -Wall -s a5_speculative_pregrant_core_tb \
  "$CANDIDATE_DIR/a5_transition_predictor.sv" \
  "$CANDIDATE_DIR/a5_last_successor_predictor.sv" \
  "$CANDIDATE_DIR/a5_speculative_pregrant_core.sv" \
  "$SCRIPT_DIR/a5_speculative_pregrant_core_tb.sv" \
  -o "$OUT_DIR/a5_speculative_pregrant_core.vvp"
"$VVP" "${VVP_ARGS[@]}" "$OUT_DIR/a5_speculative_pregrant_core.vvp"
