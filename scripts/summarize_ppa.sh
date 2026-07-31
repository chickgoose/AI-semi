#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] || { printf 'usage: %s <config.sh> <run-id>\n' "$0" >&2; exit 2; }
config="$1"
run_id="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "$config")"
source "$config"

run_dir="$(aer_abs_path "${AER_RESULTS_ROOT:-results/runs}/$run_id")"
summary="$run_dir/summary.tsv"
mkdir -p "$run_dir"
printf 'design\tarea_um2\twns_ns\ttns_ns\tfmax_mhz\ttotal_power_mw\tdynamic_power_mw\tleakage_power_mw\n' > "$summary"

is_number() {
  [[ "$1" =~ ^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$ ]]
}

validate_metrics_file() {
  local metrics_file="$1"
  local expected
  local metric
  local unit
  local count
  local value
  [[ -f "$metrics_file" ]] || aer_die "missing $metrics_file"
  [[ "$(sed -n '1p' "$metrics_file")" == $'metric\tvalue\tunit' ]] ||
    aer_die "invalid metrics header: $metrics_file"
  for expected in cell_area:um2 wns:ns tns:ns fmax:MHz \
                  total_power:mW dynamic_power:mW leakage_power:mW; do
    metric="${expected%%:*}"
    unit="${expected##*:}"
    count="$(awk -F '\t' -v wanted="$metric" -v expected_unit="$unit" \
      '$1 == wanted && $3 == expected_unit {count++} END {print count+0}' "$metrics_file")"
    [[ "$count" == "1" ]] || aer_die "expected one $metric ($unit) row: $metrics_file"
    value="$(awk -F '\t' -v wanted="$metric" '$1 == wanted {print $2; exit}' "$metrics_file")"
    is_number "$value" || aer_die "required metric $metric is not numeric ($value): $metrics_file"
  done
}

validate_metrics_file "$run_dir/$AER_BASELINE_NAME/synth/metrics.tsv"

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

metrics=(cell_area wns tns fmax total_power dynamic_power leakage_power)
declare -A baseline_values
for metric in "${metrics[@]}"; do
  baseline_values[$metric]="$(metric_value "$AER_BASELINE_NAME" "$metric")"
done

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$AER_BASELINE_NAME" "${baseline_values[cell_area]}" "${baseline_values[wns]}" \
  "${baseline_values[tns]}" "${baseline_values[fmax]}" "${baseline_values[total_power]}" \
  "${baseline_values[dynamic_power]}" "${baseline_values[leakage_power]}" >> "$summary"

printf 'wrote %s\n' "$summary"
