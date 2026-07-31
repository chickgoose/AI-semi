#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { printf 'usage: %s <config.sh> <run-id>\n' "$0" >&2; exit 2; }
config="$1"
run_id="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
source "$config"

run_dir="$(aer_abs_path "${AER_RESULTS_ROOT:-results/runs}/$run_id")"
summary="$run_dir/summary.tsv"
mkdir -p "$run_dir"
printf 'design\tstage\tmetric\tvalue\tunit\n' > "$summary"
for design in "$AER_BASELINE_NAME" "$AER_IMPROVED_NAME"; do
  for stage in synth sta power; do
    metrics="$run_dir/$design/$stage/metrics.tsv"
    [[ -f "$metrics" ]] || aer_die "missing $metrics"
    while IFS=$'\t' read -r metric value unit; do
      [[ "$metric" == "metric" ]] && continue
      printf '%s\t%s\t%s\t%s\t%s\n' "$design" "$stage" "$metric" "$value" "$unit" >> "$summary"
    done < "$metrics"
  done
done
printf 'wrote %s\n' "$summary"
