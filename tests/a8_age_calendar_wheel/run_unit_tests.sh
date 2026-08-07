#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A8_UNIT_OUT:-/tmp/a8-age-calendar-wheel-unit}"
IVERILOG="${A8_IVERILOG:-iverilog}"
VVP="${A8_VVP:-vvp}"
IVERILOG_BASE="${A8_IVERILOG_BASE:-}"
VVP_MODULE_DIR="${A8_VVP_MODULE_DIR:-}"

iverilog_args=()
vvp_args=()
[[ -n "$IVERILOG_BASE" ]] && iverilog_args+=(-B "$IVERILOG_BASE")
[[ -n "$VVP_MODULE_DIR" ]] && vvp_args+=(-M "$VVP_MODULE_DIR")

mkdir -p "$OUT_DIR"
python3 "$SCRIPT_DIR/calendar_wheel_counterexample_test.py"

"$IVERILOG" "${iverilog_args[@]}" -g2012 -Wall \
  -s a8_age_calendar_wheel_arbiter_tb \
  "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel_arbiter.sv" \
  "$SCRIPT_DIR/a8_age_calendar_wheel_arbiter_tb.sv" \
  -o "$OUT_DIR/a8_age_calendar_wheel_arbiter.vvp"
"$VVP" "${vvp_args[@]}" "$OUT_DIR/a8_age_calendar_wheel_arbiter.vvp"
