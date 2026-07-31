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

"$SCRIPT_DIR/compare_manifests.sh" "$config" "$run_id"

run_dir="$(aer_abs_path "${AER_RESULTS_ROOT:-results/runs}/$run_id")"
summary="$run_dir/summary.tsv"
comparison="$run_dir/comparison.tsv"
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
validate_metrics_file "$run_dir/$AER_IMPROVED_NAME/synth/metrics.tsv"

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
declare -A baseline_values improved_values
for metric in "${metrics[@]}"; do
  baseline_values[$metric]="$(metric_value "$AER_BASELINE_NAME" "$metric")"
  improved_values[$metric]="$(metric_value "$AER_IMPROVED_NAME" "$metric")"
done

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$AER_BASELINE_NAME" "${baseline_values[cell_area]}" "${baseline_values[wns]}" \
  "${baseline_values[tns]}" "${baseline_values[fmax]}" "${baseline_values[total_power]}" \
  "${baseline_values[dynamic_power]}" "${baseline_values[leakage_power]}" >> "$summary"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  "$AER_IMPROVED_NAME" "${improved_values[cell_area]}" "${improved_values[wns]}" \
  "${improved_values[tns]}" "${improved_values[fmax]}" "${improved_values[total_power]}" \
  "${improved_values[dynamic_power]}" "${improved_values[leakage_power]}" >> "$summary"

write_comparison() {
  local metric="$1"
  local unit="$2"
  local direction="$3"
  local baseline="${baseline_values[$metric]}"
  local improved="${improved_values[$metric]}"
  local delta="N/A"
  local improvement="N/A"
  if is_number "$baseline" && is_number "$improved"; then
    delta="$(awk -v b="$baseline" -v i="$improved" 'BEGIN {printf "%.6f", i-b}')"
    case "$direction" in
      lower)
        improvement="$(awk -v b="$baseline" -v i="$improved" 'BEGIN {if (b != 0) printf "%.4f", 100*(b-i)/b; else print "N/A"}')"
        ;;
      higher)
        improvement="$(awk -v b="$baseline" -v i="$improved" 'BEGIN {if (b != 0) printf "%.4f", 100*(i-b)/b; else print "N/A"}')"
        ;;
      toward_zero)
        improvement="$(awk -v b="$baseline" -v i="$improved" 'BEGIN {ab=(b<0?-b:b); ai=(i<0?-i:i); if (ab != 0) printf "%.4f", 100*(ab-ai)/ab; else print "N/A"}')"
        ;;
      delta_only) improvement="N/A" ;;
    esac
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$metric" "$baseline" "$improved" "$delta" "$improvement" "$unit" "$direction" >> "$comparison"
}

printf 'metric\tbaseline\timproved\tdelta_improved_minus_baseline\timprovement_percent\tunit\tbetter_direction\n' > "$comparison"
write_comparison cell_area um2 lower
write_comparison wns ns delta_only
write_comparison tns ns toward_zero
write_comparison fmax MHz higher
write_comparison total_power mW lower
write_comparison dynamic_power mW lower
write_comparison leakage_power mW lower

printf 'wrote %s\n' "$summary"
printf 'wrote %s\n' "$comparison"
