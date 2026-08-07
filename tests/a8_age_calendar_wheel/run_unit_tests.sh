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

"$IVERILOG" "${iverilog_args[@]}" -g2012 -Wall \
  -s a8_scheduler_adversarial_tb \
  "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel_arbiter.sv" \
  "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_exact_age_reference_arbiter.sv" \
  "$SCRIPT_DIR/a8_scheduler_adversarial_tb.sv" \
  -o "$OUT_DIR/a8_scheduler_adversarial.vvp"
"$VVP" "${vvp_args[@]}" "$OUT_DIR/a8_scheduler_adversarial.vvp"

"$IVERILOG" "${iverilog_args[@]}" -g2012 -Wall \
  -s a8_unsafe_stall_param_tb \
  "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel_arbiter.sv" \
  "$SCRIPT_DIR/a8_unsafe_stall_param_tb.sv" \
  -o "$OUT_DIR/a8_unsafe_stall_param.vvp"
if "$VVP" "${vvp_args[@]}" "$OUT_DIR/a8_unsafe_stall_param.vvp" \
  > "$OUT_DIR/a8_unsafe_stall_param.log" 2>&1; then
  printf 'unsafe stall-bound parameter unexpectedly elaborated\n' >&2
  exit 1
fi
rg 'wheel horizon must exceed NUM_SOURCES-1\+MAX_STALL_CYCLES' \
  "$OUT_DIR/a8_unsafe_stall_param.log"
printf 'A8_UNSAFE_STALL_PARAM_REJECT_PASS\n'
