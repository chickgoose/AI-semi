#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A2_TEST_OUT:-$PROJECT_ROOT/results/a2-directed}"
SIMULATOR="${AER_SIMULATOR:-}"

mkdir -p "$OUT_DIR"
if [[ -z "$SIMULATOR" ]]; then
  if command -v xrun >/dev/null 2>&1; then
    SIMULATOR=xrun
  elif command -v iverilog >/dev/null 2>&1; then
    SIMULATOR=iverilog
  else
    printf 'no supported simulator found; set AER_SIMULATOR=xrun or iverilog\n' >&2
    exit 1
  fi
fi

case "$SIMULATOR" in
  xrun)
    (cd "$PROJECT_ROOT" && xrun -64bit -sv -timescale 1ns/1ps \
      -top a2_adaptive_dual_path_tb -f tests/a2/a2_directed.f \
      -xmlibdirname "$OUT_DIR/xcelium.d" -l "$OUT_DIR/xrun.log")
    ;;
  iverilog)
    (cd "$PROJECT_ROOT" && iverilog -g2012 -Wall \
      -s a2_adaptive_dual_path_tb -f tests/a2/a2_directed.f \
      -o "$OUT_DIR/a2_directed.vvp")
    vvp "$OUT_DIR/a2_directed.vvp" | tee "$OUT_DIR/iverilog.log"
    ;;
  *)
    printf 'unsupported AER_SIMULATOR=%s\n' "$SIMULATOR" >&2
    exit 1
    ;;
esac
