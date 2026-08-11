#!/usr/bin/env bash
set -euo pipefail

ECRF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ECRF_DIR/.." && pwd)"
COMMON_ROOT="${ECRF_COMMON_ROOT:-$PROJECT_ROOT}"
BENCH_ROOT="$COMMON_ROOT/benchmarks/clean_slate_aer"
GENERATOR="$BENCH_ROOT/generate_trace.py"
FULL_MANIFEST="$BENCH_ROOT/manifest.neutrality-n16.json"
CAPACITY_MANIFEST="$BENCH_ROOT/manifest.multilane-n16.json"
OUT_DIR="${ECRF_OUT:-$ECRF_DIR/results}"
TMP_PARENT="${ECRF_TMP_ROOT:-/tmp}"
TMP_ROOT="$(mktemp -d "$TMP_PARENT/ecrf-w3.XXXXXX")"
FULL_TRACES="$TMP_ROOT/full50"
CAPACITY_TRACES="$TMP_ROOT/capacity22"

for required in "$GENERATOR" "$FULL_MANIFEST" "$CAPACITY_MANIFEST"; do
  if [[ ! -f "$required" ]]; then
    printf 'ECRF missing required common input: %s\n' "$required" >&2
    printf 'Set ECRF_COMMON_ROOT to a read-only generator-v4 common checkout.\n' >&2
    exit 1
  fi
done

python3 -m unittest discover -s "$ECRF_DIR/tests" -v

python3 "$GENERATOR" --manifest "$FULL_MANIFEST" --output-dir "$FULL_TRACES"
python3 "$GENERATOR" --manifest "$CAPACITY_MANIFEST" --output-dir "$CAPACITY_TRACES"

python3 "$ECRF_DIR/reference/ecrf_reference.py" \
  --output-dir "$OUT_DIR" \
  --trace-suite "full50=$FULL_TRACES" \
  --trace-suite "capacity22=$CAPACITY_TRACES"

python3 - "$OUT_DIR/w3_summary.json" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    "ECRF_W3_GATE "
    f"decision={summary['decision']} rtl_permitted={int(summary['rtl_permitted'])}"
)
PY

printf 'ECRF temporary traces: %s\n' "$TMP_ROOT"
printf 'ECRF committed-size results: %s\n' "$OUT_DIR"

