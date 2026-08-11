#!/usr/bin/env bash
set -euo pipefail

ECRF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ECRF_DIR/.." && pwd)"
REQUIRE_GO=0
if [[ $# -gt 1 ]] || [[ $# -eq 1 && "$1" != "--require-go" ]]; then
  printf 'usage: %s [--require-go]\n' "$0" >&2
  exit 64
fi
if [[ $# -eq 1 ]]; then
  REQUIRE_GO=1
fi

COMMON_ROOT="${ECRF_COMMON_ROOT:-$PROJECT_ROOT}"
BENCH_ROOT="$COMMON_ROOT/benchmarks/clean_slate_aer"
GENERATOR="$BENCH_ROOT/generate_trace.py"
FULL_MANIFEST="$BENCH_ROOT/manifest.neutrality-n16.json"
CAPACITY_MANIFEST="$BENCH_ROOT/manifest.multilane-n16.json"
OUT_DIR="${ECRF_OUT:-$ECRF_DIR/results}"
TMP_PARENT="${ECRF_TMP_ROOT:-/tmp}"

for required in "$GENERATOR" "$FULL_MANIFEST" "$CAPACITY_MANIFEST"; do
  if [[ ! -f "$required" ]]; then
    printf 'ECRF missing required common input: %s\n' "$required" >&2
    printf 'Set ECRF_COMMON_ROOT to a read-only generator-v4 common checkout.\n' >&2
    exit 1
  fi
done

python3 "$ECRF_DIR/tools/contracts.py" inputs --common-root "$COMMON_ROOT"

TMP_ROOT="$(mktemp -d "$TMP_PARENT/ecrf-w3.XXXXXX")"
FULL_TRACES="$TMP_ROOT/full50"
CAPACITY_TRACES="$TMP_ROOT/capacity22"

python3 -m unittest discover -s "$ECRF_DIR/tests" -v

python3 "$GENERATOR" --manifest "$FULL_MANIFEST" --output-dir "$FULL_TRACES"
python3 "$GENERATOR" --manifest "$CAPACITY_MANIFEST" --output-dir "$CAPACITY_TRACES"

python3 "$ECRF_DIR/reference/ecrf_reference.py" \
  --output-dir "$OUT_DIR" \
  --trace-suite "full50=$FULL_TRACES" \
  --trace-suite "capacity22=$CAPACITY_TRACES"

printf 'ECRF temporary traces: %s\n' "$TMP_ROOT"
printf 'ECRF committed-size results: %s\n' "$OUT_DIR"

DECISION_ARGS=(decision --summary "$OUT_DIR/w3_summary.json")
if [[ "$REQUIRE_GO" -eq 1 ]]; then
  DECISION_ARGS+=(--require-go)
fi
python3 "$ECRF_DIR/tools/contracts.py" "${DECISION_ARGS[@]}"
