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
printf 'design\tarea_um2\twns_ns\ttns_ns\tfmax_mhz\ttotal_power_mw\tdynamic_power_mw\tleakage_power_mw\n' > "$summary"

metric_value() {
  local design="$1"
  local metric="$2"
  local stage
  local value
  for stage in power sta synth; do
    [[ -f "$run_dir/$design/$stage/metrics.tsv" ]] || continue
    value="$(awk -F '\t' -v wanted="$metric" '$1 == wanted {print $2; exit}' "$run_dir/$design/$stage/metrics.tsv")"
    if [[ -n "$value" && "$value" != "N/A" ]]; then
      printf '%s\n' "$value"
      return
    fi
  done
  printf 'N/A\n'
}

for design in "$AER_BASELINE_NAME" "$AER_IMPROVED_NAME"; do
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$design" \
    "$(metric_value "$design" cell_area)" \
    "$(metric_value "$design" wns)" \
    "$(metric_value "$design" tns)" \
    "$(metric_value "$design" fmax)" \
    "$(metric_value "$design" total_power)" \
    "$(metric_value "$design" dynamic_power)" \
    "$(metric_value "$design" leakage_power)" >> "$summary"
done
printf 'wrote %s\n' "$summary"
