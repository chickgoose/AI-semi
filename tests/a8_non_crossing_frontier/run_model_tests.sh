#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A8_NCF_OUT:-/tmp/a8-non-crossing-frontier}"
require_go=0

if [[ "${1:-}" == "--require-go" ]]; then
  require_go=1
  shift
fi
if [[ "$#" -ne 0 ]]; then
  printf 'usage: %s [--require-go]\n' "$0" >&2
  exit 64
fi

mkdir -p "$OUT_DIR"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  "$SCRIPT_DIR/test_frontier_model.py"

PYTHONDONTWRITEBYTECODE=1 python3 \
  "$PROJECT_ROOT/rtl/candidates/a8_non_crossing_frontier/model/evaluate_frontier.py" \
  --output "$OUT_DIR/model-results.json" \
  > "$OUT_DIR/model-results.stdout"

reader_args=("$OUT_DIR/model-results.json")
if [[ "$require_go" -eq 1 ]]; then
  reader_args+=(--require-go)
fi
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$SCRIPT_DIR/read_gate_status.py" "${reader_args[@]}"
