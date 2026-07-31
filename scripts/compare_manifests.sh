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
baseline_manifest="$run_dir/$AER_BASELINE_NAME/synth/manifest.txt"
improved_manifest="$run_dir/$AER_IMPROVED_NAME/synth/manifest.txt"
[[ -f "$baseline_manifest" ]] || aer_die "missing $baseline_manifest"
[[ -f "$improved_manifest" ]] || aer_die "missing $improved_manifest"

manifest_value() {
  local manifest="$1"
  local field="$2"
  awk -F= -v wanted="$field" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$manifest"
}

config_sha256="$(sha256sum "$config" | awk '{print $1}')"
config_base_sha256="$(sha256sum "$AER_PROJECT_ROOT/scripts/config.example.sh" | awk '{print $1}')"
sdc="$(aer_abs_path "$AER_SDC")"
[[ -f "$sdc" ]] || aer_die "missing SDC: $sdc"
sdc_sha256="$(sha256sum "$sdc" | awk '{print $1}')"
[[ -f "$AER_LIBRARY_FILE" ]] || aer_die "missing Liberty: $AER_LIBRARY_FILE"
library_sha256="$(sha256sum "$AER_LIBRARY_FILE" | awk '{print $1}')"
driver="$(aer_abs_path "$AER_SYNTH_DRIVER")"

expected_value() {
  case "$1" in
    run_id) printf '%s\n' "$run_id" ;;
    git_commit) printf '__MATCH_ONLY__\n' ;;
    stage) printf 'synth\n' ;;
    top) printf '%s\n' "$AER_BASELINE_TOP" ;;
    config_file) printf '%s\n' "$config" ;;
    config_sha256) printf '%s\n' "$config_sha256" ;;
    config_base_sha256) printf '%s\n' "$config_base_sha256" ;;
    sdc) printf '%s\n' "$sdc" ;;
    sdc_sha256) printf '%s\n' "$sdc_sha256" ;;
    corner) printf '%s\n' "$AER_CORNER" ;;
    clock_period_ns) printf '%s\n' "$AER_CLOCK_PERIOD_NS" ;;
    clock_port) printf '%s\n' "$AER_CLOCK_PORT" ;;
    reset_port) printf '%s\n' "$AER_RESET_PORT" ;;
    input_delay_ns) printf '%s\n' "$AER_INPUT_DELAY_NS" ;;
    output_delay_ns) printf '%s\n' "$AER_OUTPUT_DELAY_NS" ;;
    clock_uncertainty_ns) printf '%s\n' "$AER_CLOCK_UNCERTAINTY_NS" ;;
    load_pf) printf '%s\n' "$AER_LOAD_PF" ;;
    driver_cell) printf '%s\n' "$AER_DRIVER_CELL" ;;
    num_sources) printf '%s\n' "$AER_NUM_SOURCES" ;;
    addr_width) printf '%s\n' "$AER_ADDR_WIDTH" ;;
    fifo_depth) printf '%s\n' "$AER_FIFO_DEPTH" ;;
    events_per_source) printf '%s\n' "$AER_EVENTS_PER_SOURCE" ;;
    library_file) printf '%s\n' "$AER_LIBRARY_FILE" ;;
    library_sha256) printf '%s\n' "$library_sha256" ;;
    power_mode) printf '%s\n' "$AER_POWER_MODE" ;;
    power_activity) printf '%s\n' "${AER_POWER_ACTIVITY:-}" ;;
    driver) printf '%s\n' "$driver" ;;
    *) aer_die "no expected value mapping for $1" ;;
  esac
}

fields=(run_id git_commit stage top config_file config_sha256 config_base_sha256 sdc sdc_sha256
  corner clock_period_ns clock_port reset_port input_delay_ns output_delay_ns
  clock_uncertainty_ns load_pf driver_cell num_sources addr_width fifo_depth
  events_per_source library_file
  library_sha256 power_mode power_activity driver)
comparison="$run_dir/manifest-comparison.tsv"
printf 'field\texpected\tbaseline\timproved\tmatch\n' > "$comparison"
mismatches=0
for field in "${fields[@]}"; do
  expected="$(expected_value "$field")"
  baseline_value="$(manifest_value "$baseline_manifest" "$field")"
  improved_value="$(manifest_value "$improved_manifest" "$field")"
  match="yes"
  if [[ "$baseline_value" != "$improved_value" ]] ||
     [[ "$expected" == "__MATCH_ONLY__" && -z "$baseline_value" ]] ||
     [[ "$expected" != "__MATCH_ONLY__" && "$baseline_value" != "$expected" ]]; then
    match="no"
    mismatches=$((mismatches + 1))
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$field" "$expected" "$baseline_value" "$improved_value" "$match" >> "$comparison"
done

if (( mismatches != 0 )); then
  printf 'manifest comparison failed with %d mismatch(es): %s\n' "$mismatches" "$comparison" >&2
  exit 1
fi
printf 'manifest comparison passed: %s\n' "$comparison"
