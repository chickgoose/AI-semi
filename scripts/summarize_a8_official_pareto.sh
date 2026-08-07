#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TRACE_DIR="${A8_OFFICIAL_TRACE_DIR:-/tmp/a8-age-calendar-wheel-traces}"
OUT_DIR="${A8_OFFICIAL_SUMMARY_OUT:-/tmp/a8-official-pareto}"

architectures=(rr exact b1 b2 b4 b8)
result_dirs=(
  /tmp/a8-rr-mock-regression
  /tmp/a8-official-exact-regression
  /tmp/a8-age-calendar-wheel-b1-regression
  /tmp/a8-official-b2-regression
  /tmp/a8-age-calendar-wheel-regression
  /tmp/a8-official-b8-regression
)

mkdir -p "$OUT_DIR"
for index in "${!architectures[@]}"; do
  architecture="${architectures[$index]}"
  result_dir="${result_dirs[$index]}"
  metric_files=()
  event_args=()
  for result_file in "$result_dir"/*.csv; do
    if [[ "$result_file" == *.events.csv ]]; then
      event_args+=(--events "$result_file")
    elif [[ "$result_file" != */aggregate.csv &&
            "$result_file" != */event-aggregate.csv ]]; then
      metric_files+=("$result_file")
    fi
  done
  if [[ ${#metric_files[@]} -ne 46 || ${#event_args[@]} -ne 92 ]]; then
    printf 'expected 46 metric and 46 event files for %s\n' "$architecture" >&2
    exit 1
  fi
  python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/aggregate.py" \
    "${metric_files[@]}" "${event_args[@]}" \
    --output "$result_dir/aggregate.csv" \
    --event-output "$result_dir/event-aggregate.csv"
  for seed in 3901 3902; do
    python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/timing_pair_metrics.py" \
      --trace "$TRACE_DIR/timing_pair_s$seed.events.jsonl" \
      --run-manifest "$TRACE_DIR/timing_pair_s$seed.manifest.json" \
      --events "$result_dir/timing_pair_s$seed.events.csv" \
      --output "$result_dir/timing_pair_s$seed.timing.json"
  done
done

python3 "$PROJECT_ROOT/tests/a8_age_calendar_wheel/collect_official_summary.py" \
  --output "$OUT_DIR/official-summary.csv"
printf 'A8 official Pareto summary: %s\n' "$OUT_DIR/official-summary.csv"
