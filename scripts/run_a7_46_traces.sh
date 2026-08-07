#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
generated_root="${AER_A7_TRACE_DIR:-/tmp/a7-neutrality-n16-traces}"

python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --output-dir "$generated_root"
python3 "$project_root/benchmarks/clean_slate_aer/neutrality_self_test.py"

implementation="${AER_A7_IMPL:-prefix}"
for retire_lanes in 1 2 4; do
  while IFS= read -r event_path; do
    stem="$(basename "$event_path" .events.jsonl)"
    AER_TRACE_JSONL="$event_path" \
    AER_TRACE_MANIFEST="$generated_root/$stem.manifest.json" \
    AER_A7_IMPL="$implementation" \
      "$script_dir/run_a7_parallel_event_compactor.sh" "$retire_lanes"
  done < <(find "$generated_root" -maxdepth 1 -name '*.events.jsonl' -print | sort)
done
