#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s [--manifest MANIFEST | --trace JSONL RUN_MANIFEST]\n' "$0" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A8_CLEAN_OUT:-/tmp/a8-age-calendar-wheel-regression}"
TRACE_DIR="${A8_TRACE_OUT:-/tmp/a8-age-calendar-wheel-traces}"
NUM_SOURCES="${A8_NUM_SOURCES:-16}"
ADDR_WIDTH="${A8_ADDR_WIDTH:-16}"
RETIRE_LANES="${A8_RETIRE_LANES:-2}"
BUCKET_CYCLES="${A8_BUCKET_CYCLES:-4}"
EPOCH_COUNT="${A8_EPOCH_COUNT:-8}"
CANDIDATE_NAME="${A8_CANDIDATE_NAME:-a8-age-calendar-wheel}"

manifest=""
single_trace=""
single_run_manifest=""
if [[ $# -eq 0 ]]; then
  manifest="$PROJECT_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
elif [[ $# -eq 2 && "$1" == "--manifest" ]]; then
  manifest="$2"
elif [[ $# -eq 3 && "$1" == "--trace" ]]; then
  single_trace="$2"
  single_run_manifest="$3"
else
  usage
fi

mkdir -p "$OUT_DIR" "$TRACE_DIR"

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

build_dir="$OUT_DIR/verilator-build"
verilator_command=("$VERILATOR" --binary --timing --assert -Wall -Wno-fatal
  --top-module aer_clean_tb
  -GNUM_SOURCES="$NUM_SOURCES" -GADDR_WIDTH="$ADDR_WIDTH"
  -GRETIRE_LANES="$RETIRE_LANES"
  -DA8_BUCKET_CYCLES="$BUCKET_CYCLES" -DA8_EPOCH_COUNT="$EPOCH_COUNT"
  -f "$PROJECT_ROOT/tests/a8_age_calendar_wheel/a8_clean.f"
  --Mdir "$build_dir" -o a8_clean_sim)
if [[ -n "$VERILATOR_ROOT_VALUE" ]]; then
  (cd "$PROJECT_ROOT" && VERILATOR_ROOT="$VERILATOR_ROOT_VALUE" \
    "${verilator_command[@]}") > "$OUT_DIR/build.log" 2>&1
else
  (cd "$PROJECT_ROOT" && "${verilator_command[@]}") \
    > "$OUT_DIR/build.log" 2>&1
fi

if [[ -n "$manifest" ]]; then
  python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
    --manifest "$manifest" --output-dir "$TRACE_DIR"
  mapfile -t run_names < <(python3 \
    "$PROJECT_ROOT/tests/a8_age_calendar_wheel/manifest_run_names.py" \
    "$manifest")
else
  run_names=("$(basename "$single_trace" .events.jsonl)")
fi

for run_name in "${run_names[@]}"; do
  trace="$single_trace"
  run_manifest="$single_run_manifest"
  if [[ -n "$manifest" ]]; then
    trace="$TRACE_DIR/$run_name.events.jsonl"
    run_manifest="$TRACE_DIR/$run_name.manifest.json"
  fi
  prepared="$OUT_DIR/$run_name.svtrace"
  prepare_output="$(python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$trace" --run-manifest "$run_manifest" \
    --output "$prepared" --addr-width "$ADDR_WIDTH")"
  report_name="${prepare_output##*report_group=}"
  report_name="${report_name%% *}"
  "$build_dir/a8_clean_sim" \
    +CLEAN_TEST=trace "+CANDIDATE=$CANDIDATE_NAME" \
    "+TRACE_FILE=$prepared" "+TRACE_NAME=$report_name" \
    "+METRICS=$OUT_DIR/$run_name.csv" \
    "+EVENT_METRICS=$OUT_DIR/$run_name.events.csv" \
    > "$OUT_DIR/$run_name.log"
  rg 'AER_CLEAN_METRICS|AER_CLEAN_TEST_PASS' "$OUT_DIR/$run_name.log"
done

printf 'A8 clean regression complete: %s\n' "$OUT_DIR"
