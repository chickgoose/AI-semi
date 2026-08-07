#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A8_TOGGLE_OUT:-/tmp/a8-toggle-proxy}"
VERILATOR="${A8_VERILATOR:-}"
VERILATOR_ROOT_VALUE="${A8_VERILATOR_ROOT:-}"

if [[ -z "$VERILATOR" ]]; then
  if command -v verilator >/dev/null 2>&1; then
    VERILATOR="$(command -v verilator)"
  elif [[ -x /tmp/a8-verilator/usr/bin/verilator ]]; then
    VERILATOR=/tmp/a8-verilator/usr/bin/verilator
    VERILATOR_ROOT_VALUE=/tmp/a8-verilator/usr/share/verilator
  else
    printf 'verilator not found; set A8_VERILATOR and optionally A8_VERILATOR_ROOT\n' >&2
    exit 1
  fi
fi

mkdir -p "$OUT_DIR"
printf 'architecture,source_count,cycles,toggles,toggles_per_cycle,toggles_per_accept,accepted,overrun\n' > "$OUT_DIR/toggle-summary.csv"
for source_count in 16 32 64; do
  build_dir="$(mktemp -d /tmp/a8-toggle-build.XXXXXXXX)"
  verilator_args=("$VERILATOR" --binary --timing -Wall -Wno-fatal
    --top-module a8_toggle_proxy_tb -GNUM_SOURCES="$source_count"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel_arbiter.sv"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_age_calendar_wheel.sv"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_exact_age_reference_arbiter.sv"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_exact_age_reference.sv"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_rr_reference_arbiter.sv"
    "$PROJECT_ROOT/rtl/candidates/a8_age_calendar_wheel/a8_rr_reference.sv"
    "$PROJECT_ROOT/tests/a8_age_calendar_wheel/a8_toggle_proxy_tb.sv"
    --Mdir "$build_dir" -o a8_toggle_proxy)
  if [[ -n "$VERILATOR_ROOT_VALUE" ]]; then
    VERILATOR_ROOT="$VERILATOR_ROOT_VALUE" "${verilator_args[@]}" > "$OUT_DIR/build-n$source_count.log" 2>&1
  else
    "${verilator_args[@]}" > "$OUT_DIR/build-n$source_count.log" 2>&1
  fi
  "$build_dir/a8_toggle_proxy" > "$OUT_DIR/run-n$source_count.log"
  sed -n '/^rr,/p;/^exact,/p;/^b1,/p;/^b2,/p;/^b4,/p;/^b8,/p' \
    "$OUT_DIR/run-n$source_count.log" >> "$OUT_DIR/toggle-summary.csv"
  if [[ "$build_dir" != /tmp/a8-toggle-build.* ]]; then
    printf 'refusing to clean unexpected build path: %s\n' "$build_dir" >&2
    exit 1
  fi
  rm -rf -- "$build_dir"
done
printf 'A8 toggle proxy: %s\n' "$OUT_DIR/toggle-summary.csv"
