#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A8_NCF_OUT:-/tmp/a8-non-crossing-frontier}"

mkdir -p "$OUT_DIR"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  "$SCRIPT_DIR/test_frontier_model.py"

set +e
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$PROJECT_ROOT/rtl/candidates/a8_non_crossing_frontier/model/evaluate_frontier.py" \
  --output "$OUT_DIR/model-results.json" \
  > "$OUT_DIR/model-results.stdout"
evaluation_status=$?
set -e
if [[ "$evaluation_status" -ne 0 && "$evaluation_status" -ne 2 ]]; then
  exit "$evaluation_status"
fi
printf 'A8_NCF_MODEL_GATE_STATUS=%s\n' \
  "$(python3 "$SCRIPT_DIR/read_gate_status.py" "$OUT_DIR/model-results.json")"
