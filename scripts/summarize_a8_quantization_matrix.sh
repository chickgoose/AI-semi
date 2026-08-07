#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RESULT_ROOT="${A8_MATRIX_OUT:-/tmp/a8-quantization-matrix}"

for source_count in 16 32 64; do
  trace_dir="$RESULT_ROOT/traces-n$source_count"
  timing_name="n${source_count}_timing_pair"
  for architecture in rr exact b1 b2 b4 b8; do
    result_dir="$RESULT_ROOT/$architecture-n$source_count"
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
    python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/aggregate.py" \
      "${metric_files[@]}" "${event_args[@]}" \
      --output "$result_dir/aggregate.csv" \
      --event-output "$result_dir/event-aggregate.csv"
    python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/timing_pair_metrics.py" \
      --trace "$trace_dir/$timing_name.events.jsonl" \
      --run-manifest "$trace_dir/$timing_name.manifest.json" \
      --events "$result_dir/$timing_name.events.csv" \
      --output "$result_dir/timing-pair.json"
  done
done

python3 "$PROJECT_ROOT/tests/a8_age_calendar_wheel/collect_matrix_summary.py" \
  --result-root "$RESULT_ROOT" \
  --output "$RESULT_ROOT/matrix-summary.csv"
printf 'A8 matrix summary: %s\n' "$RESULT_ROOT/matrix-summary.csv"
