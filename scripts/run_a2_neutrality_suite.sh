#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TRACE_DIR="${A2_TRACE_DIR:-/tmp/a2-neutrality-n16-traces}"
OUT_ROOT="${A2_SUITE_OUT:-$PROJECT_ROOT/results/a2-neutrality-n16}"

python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$PROJECT_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --output-dir "$TRACE_DIR"

prebuilt_verilator="${A2_VERILATOR_BINARY:-}"
for trace_path in "$TRACE_DIR"/*.events.jsonl; do
  trace_file="$(basename "$trace_path")"
  trace_name="${trace_file%.events.jsonl}"
  manifest_path="$TRACE_DIR/$trace_name.manifest.json"
  [[ -f "$manifest_path" ]] || {
    printf 'missing generated run manifest: %s\n' "$manifest_path" >&2
    exit 1
  }
  AER_NUM_SOURCES=16 AER_RETIRE_LANES=1 \
    AER_TRACE_JSONL="$trace_path" AER_TRACE_MANIFEST="$manifest_path" \
    AER_TRACE_NAME="$trace_name" AER_CLEAN_OUT="$OUT_ROOT/$trace_name" \
    A2_VERILATOR_BINARY="$prebuilt_verilator" \
    "$PROJECT_ROOT/scripts/run_a2_adaptive_dual_path.sh"
  if [[ -z "$prebuilt_verilator" && "${AER_SIMULATOR:-}" == "verilator" ]]; then
    prebuilt_verilator="$OUT_ROOT/$trace_name/n16-seed1/verilator-obj/aer_clean_a2"
  fi
done

printf 'A2 frozen neutrality suite complete: %s\n' "$OUT_ROOT"
