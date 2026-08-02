#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$PROJECT_ROOT/results/sim/a3-bubble-free-compare"
mkdir -p "$OUT_DIR"

if ! command -v iverilog >/dev/null 2>&1; then
  printf 'iverilog is required for the local comparison test\n' >&2
  exit 1
fi

(
  cd "$PROJECT_ROOT"
  iverilog -g2012 -Wall -s a3_bubble_free_compare_tb \
    -o "$OUT_DIR/a3_bubble_free_compare.vvp" \
    -f tb/filelists/a3_bubble_free_compare.f
)
vvp "$OUT_DIR/a3_bubble_free_compare.vvp" | tee "$OUT_DIR/run.log"
