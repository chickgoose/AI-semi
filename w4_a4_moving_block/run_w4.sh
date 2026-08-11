#!/usr/bin/env bash
set -euo pipefail

W4_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$W4_DIR/.." && pwd)"
COMMON_ROOT="${W4_COMMON_ROOT:-/home/chickgoose/projects/a1}"
A4_ROOT="${W4_A4_ROOT:-/home/chickgoose/projects/a4}"
VERILATOR="${W4_VERILATOR:-$(command -v verilator || true)}"
OUTPUT="${W4_OUTPUT:-$W4_DIR/results/qualification.json}"
TMP_PARENT="${W4_TMP_ROOT:-/tmp}"

if [[ -z "$VERILATOR" || ! -x "$VERILATOR" ]]; then
  printf 'W4_TOOL_FAIL executable Verilator required; set W4_VERILATOR\n' >&2
  exit 2
fi
VERSION="$($VERILATOR --version 2>/dev/null || true)"
if [[ "$VERSION" != Verilator* ]]; then
  printf 'W4_TOOL_FAIL not a Verilator executable: %s\n' "$VERILATOR" >&2
  exit 2
fi

mkdir -p "$TMP_PARENT"
TMP_ROOT="$(mktemp -d "$TMP_PARENT/w4-a2-a4.XXXXXX")"
PINNED_RTL="$TMP_ROOT/a4_moving_block_tree.850fbcf.sv"
python3 "$W4_DIR/contracts.py" "$COMMON_ROOT" "$A4_ROOT" "$PINNED_RTL"
python3 -m unittest discover -s "$W4_DIR/tests" -v

FULL_TRACES="$TMP_ROOT/full50"
CAPACITY_TRACES="$TMP_ROOT/capacity22"
VECTORS="$TMP_ROOT/vectors"
INDEX="$TMP_ROOT/vector-index.json"
MDIR="$TMP_ROOT/obj"
COMPILE_LOG="$TMP_ROOT/verilator-build.log"

python3 "$COMMON_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$COMMON_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --output-dir "$FULL_TRACES"
python3 "$COMMON_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$COMMON_ROOT/benchmarks/clean_slate_aer/manifest.multilane-n16.json" \
  --output-dir "$CAPACITY_TRACES"

PYTHONDONTWRITEBYTECODE=1 python3 "$W4_DIR/reference/prepare_vectors.py" \
  --suite "full50=$FULL_TRACES" --suite "capacity22=$CAPACITY_TRACES" \
  --vectors "$VECTORS" --index "$INDEX"

if ! "$VERILATOR" --binary --timing -Wall --top-module a4_w4_common_tb \
  -Mdir "$MDIR" -j 0 \
  "$PINNED_RTL" \
  "$W4_DIR/rtl/a4_w4_zero_state_adapter.sv" \
  "$W4_DIR/tb/a4_w4_common_tb.sv" >"$COMPILE_LOG" 2>&1; then
  cat "$COMPILE_LOG" >&2
  printf 'W4_TOOL_FAIL Verilator compile failed\n' >&2
  exit 2
fi

python3 "$W4_DIR/execute_regression.py" \
  --binary "$MDIR/Va4_w4_common_tb" --index "$INDEX" \
  --output "$OUTPUT" --verilator-version "$VERSION"

printf 'W4_A2_PASS output=%s temp=%s\n' "$OUTPUT" "$TMP_ROOT"
