#!/usr/bin/env bash
set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${AER_LIBRARY_FILE:?AER_LIBRARY_FILE is required}"
: "${AER_COMPARISON_RUN_ID:?AER_COMPARISON_RUN_ID is required}"
GENUS_BIN="${AER_GENUS_BIN:-genus}"
CLOCK_PERIOD="${AER_CLOCK_PERIOD_NS:-5.000}"
export AER_CLOCK_PERIOD_NS="$CLOCK_PERIOD"
export AER_CLOCK_PORT="${AER_CLOCK_PORT:-clk}"
export AER_RESET_PORT="${AER_RESET_PORT:-rst_n}"
export AER_INPUT_DELAY_NS="${AER_INPUT_DELAY_NS:-0.250}"
export AER_OUTPUT_DELAY_NS="${AER_OUTPUT_DELAY_NS:-0.250}"
export AER_CLOCK_UNCERTAINTY_NS="${AER_CLOCK_UNCERTAINTY_NS:-0.100}"
export AER_LOAD_PF="${AER_LOAD_PF:-0.010}"
export AER_DRIVER_CELL="${AER_DRIVER_CELL:-}"

RESULT_ROOT="$BUNDLE_ROOT/results/$AER_COMPARISON_RUN_ID"
SDC="$BUNDLE_ROOT/common/constraints/aer_common.sdc"
TCL="$BUNDLE_ROOT/common/a7_k4_genus_compare.tcl"

command -v "$GENUS_BIN" >/dev/null 2>&1 || {
  printf 'Genus not found: %s\n' "$GENUS_BIN" >&2
  exit 2
}
[[ -f "$AER_LIBRARY_FILE" ]] || {
  printf 'library not found: %s\n' "$AER_LIBRARY_FILE" >&2
  exit 2
}
[[ ! -e "$RESULT_ROOT" ]] || {
  printf 'result root already exists: %s\n' "$RESULT_ROOT" >&2
  exit 2
}
mkdir -p "$RESULT_ROOT"

printf 'design\tstatus\tcell_area_um2\tseq_cells\tcomb_cells\twns_ns\tfmax_mhz\ttotal_power_mw\tdynamic_power_mw\tleakage_power_mw\tcritical_startpoint\tcritical_endpoint\tlatch_cells\tunresolved\terror_fatal_lines\n' \
  > "$RESULT_ROOT/comparison.tsv"

while IFS=$'\t' read -r design commit top filelist; do
  [[ "$design" != "design" ]] || continue
  source_root="$BUNDLE_ROOT/sources/$design"
  output="$RESULT_ROOT/$design"
  mkdir -p "$output"
  source_hash="$(find "$source_root" -type f -exec sha256sum {} \; | sort | sha256sum | awk '{print $1}')"
  {
    printf 'design=%s\n' "$design"
    printf 'git_commit=%s\n' "$commit"
    printf 'top=%s\n' "$top"
    printf 'rtl_filelist=%s\n' "$filelist"
    printf 'source_sha256=%s\n' "$source_hash"
    printf 'num_sources=16\nretire_lanes=4\naddr_width=16\n'
    printf 'registered_state_bits=104\nfunctional_pin_bits=376\n'
    printf 'clock_period_ns=%s\n' "$CLOCK_PERIOD"
    printf 'sdc_sha256=%s\n' "$(sha256sum "$SDC" | awk '{print $1}')"
    printf 'library_sha256=%s\n' "$(sha256sum "$AER_LIBRARY_FILE" | awk '{print $1}')"
    printf 'tcl_sha256=%s\n' "$(sha256sum "$TCL" | awk '{print $1}')"
    printf 'effort=generic:medium,map:medium,opt:medium\n'
    printf 'power_mode=genus_vectorless_screening_only\n'
    printf 'throughput_claim=none_same_k_same_service_contract\n'
  } > "$output/manifest.txt"

  export AER_TOP="$top"
  export AER_RTL_FILELIST="$source_root/$filelist"
  export AER_SDC="$SDC"
  export AER_OUTPUT_DIR="$output"

  set +e
  (cd "$source_root" && "$GENUS_BIN" -batch -files "$TCL") \
    > "$output/tool.log" 2>&1
  genus_status=$?
  set -e
  status=PASS
  if [[ "$genus_status" -ne 0 ]]; then
    status="FAIL_GENUS_$genus_status"
  elif ! "$BUNDLE_ROOT/common/extract_genus_metrics.sh" \
    "$output" "$CLOCK_PERIOD" >> "$output/tool.log" 2>&1; then
    status=FAIL_METRICS
  elif ! "$BUNDLE_ROOT/common/parse_genus_detail.sh" \
    "$output" "$AER_LIBRARY_FILE" "$top" 1.0 \
    >> "$output/tool.log" 2>&1; then
    status=FAIL_DETAILS
  else
    metric_wns="$(awk -F'\t' '$1 == "wns" {print $2}' "$output/metrics.tsv")"
    metric_fmax="$(awk -F'\t' '$1 == "fmax" {print $2}' "$output/metrics.tsv")"
    error_count="$(awk -F'\t' '$1 == "error_fatal_lines" {print $2}' "$output/details.tsv")"
    unresolved_count="$(awk -F'\t' '$1 == "unresolved_references" {print $2}' "$output/details.tsv")"
    if [[ -z "$metric_wns" || "$metric_wns" == N/A ||
          -z "$metric_fmax" || "$metric_fmax" == N/A ]]; then
      status=FAIL_TIMING_METRICS
    elif [[ "$error_count" -ne 0 || "$unresolved_count" -ne 0 ]]; then
      status=FAIL_QUALITY_CHECKS
    fi
  fi
  printf 'status=%s\n' "$status" >> "$output/manifest.txt"

  if [[ "$status" == PASS ]]; then
    metric() { awk -F'\t' -v key="$1" '$1 == key {print $2}' "$output/metrics.tsv"; }
    detail() { awk -F'\t' -v key="$1" '$1 == key {print $2}' "$output/details.tsv"; }
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$design" "$status" "$(metric cell_area)" \
      "$(detail sequential_cells)" "$(detail combinational_cells)" \
      "$(metric wns)" "$(metric fmax)" "$(metric total_power)" \
      "$(metric dynamic_power)" "$(metric leakage_power)" \
      "$(detail critical_startpoint)" "$(detail critical_endpoint)" \
      "$(detail latch_cells)" "$(detail unresolved_references)" \
      "$(detail error_fatal_lines)" >> "$RESULT_ROOT/comparison.tsv"
  else
    printf '%s\t%s\n' "$design" "$status" >> "$RESULT_ROOT/comparison.tsv"
  fi
  printf 'A7_K4_GENUS_RESULT design=%s status=%s\n' "$design" "$status"
done < "$BUNDLE_ROOT/designs.tsv"

printf 'A7_K4_GENUS_COMPARISON_DONE results=%s\n' "$RESULT_ROOT"
