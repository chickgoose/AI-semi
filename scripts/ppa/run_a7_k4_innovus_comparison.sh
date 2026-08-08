#!/usr/bin/env bash
set -euo pipefail

BUNDLE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${AER_GENUS_RESULT_ROOT:?AER_GENUS_RESULT_ROOT is required}"
: "${AER_INNOVUS_RUN_ID:?AER_INNOVUS_RUN_ID is required}"
: "${AER_LIBRARY_FILE:?AER_LIBRARY_FILE is required}"
: "${AER_QRC_TECH:?AER_QRC_TECH is required}"
: "${AER_TECH_LEF:?AER_TECH_LEF is required}"
: "${AER_MACRO_LEF:?AER_MACRO_LEF is required}"

INNOVUS_BIN="${AER_INNOVUS_BIN:-innovus}"
PNR_TCL="$BUNDLE_ROOT/scripts/ppa/a7_k4_innovus_pnr.tcl"
MMMC_TCL="$BUNDLE_ROOT/scripts/ppa/a7_k4_innovus_mmmc.tcl"
RESULT_ROOT="$BUNDLE_ROOT/results/innovus/$AER_INNOVUS_RUN_ID"

command -v "$INNOVUS_BIN" >/dev/null 2>&1 || {
  printf 'Innovus not found: %s\n' "$INNOVUS_BIN" >&2
  exit 2
}
for required in "$AER_LIBRARY_FILE" "$AER_QRC_TECH" "$AER_TECH_LEF" \
  "$AER_MACRO_LEF" "$PNR_TCL" "$MMMC_TCL"; do
  [[ -f "$required" ]] || {
    printf 'required file not found: %s\n' "$required" >&2
    exit 2
  }
done
[[ -f "$BUNDLE_ROOT/bundle-files.sha256" ]] || {
  printf 'bundle checksum manifest not found: %s\n' \
    "$BUNDLE_ROOT/bundle-files.sha256" >&2
  exit 2
}
(cd "$BUNDLE_ROOT" && sha256sum -c bundle-files.sha256 >/dev/null) || {
  printf 'bundle checksum verification failed: %s\n' "$BUNDLE_ROOT" >&2
  exit 2
}
[[ ! -e "$RESULT_ROOT" ]] || {
  printf 'result root already exists: %s\n' "$RESULT_ROOT" >&2
  exit 2
}
mkdir -p "$RESULT_ROOT"

printf 'design\tstatus\ttool_exit\n' > "$RESULT_ROOT/comparison.tsv"
any_fail=0

while IFS=$'\t' read -r design commit top filelist; do
  [[ "$design" != design ]] || continue
  genus_design="$AER_GENUS_RESULT_ROOT/$design"
  netlist="$genus_design/netlist/$top.v"
  sdc="$genus_design/netlist/$top.sdc"
  output="$RESULT_ROOT/$design"
  mkdir -p "$output"

  for required in "$netlist" "$sdc" "$genus_design/manifest.txt"; do
    [[ -f "$required" ]] || {
      printf 'missing Genus artifact: %s\n' "$required" >&2
      exit 2
    }
  done
  grep -qx 'status=PASS' "$genus_design/manifest.txt" || {
    printf 'Genus source is not PASS: %s\n' "$genus_design" >&2
    exit 2
  }

  export AER_TOP="$top"
  export AER_PNR_NETLIST="$netlist"
  export AER_PNR_SDC="$sdc"
  export AER_PNR_MMMC="$MMMC_TCL"
  export AER_PNR_OUTPUT_DIR="$output"

  {
    printf 'design=%s\n' "$design"
    printf 'git_commit=%s\n' "$commit"
    printf 'top=%s\n' "$top"
    printf 'genus_result_root=%s\n' "$AER_GENUS_RESULT_ROOT"
    printf 'genus_netlist_sha256=%s\n' "$(sha256sum "$netlist" | awk '{print $1}')"
    printf 'genus_sdc_sha256=%s\n' "$(sha256sum "$sdc" | awk '{print $1}')"
    printf 'library_sha256=%s\n' "$(sha256sum "$AER_LIBRARY_FILE" | awk '{print $1}')"
    printf 'qrc_sha256=%s\n' "$(sha256sum "$AER_QRC_TECH" | awk '{print $1}')"
    printf 'tech_lef_sha256=%s\n' "$(sha256sum "$AER_TECH_LEF" | awk '{print $1}')"
    printf 'macro_lef_sha256=%s\n' "$(sha256sum "$AER_MACRO_LEF" | awk '{print $1}')"
    printf 'pnr_tcl_sha256=%s\n' "$(sha256sum "$PNR_TCL" | awk '{print $1}')"
    printf 'mmmc_tcl_sha256=%s\n' "$(sha256sum "$MMMC_TCL" | awk '{print $1}')"
    printf 'flow=fixed_genus_netlist_place_cts_route_extract\n'
    printf 'floorplan=aspect:1.0,target_utilization:0.50,margin_um:10\n'
    printf 'power_mode=innovus_vectorless_screening_only\n'
    printf 'analysis_scope=single_slow_lib_typical_rc_fixed_netlist_diagnostic\n'
    printf 'timing_qualification=NOT_QUALIFIED_UNPARSED_WNS_AND_COVERAGE\n'
    printf 'hold_qualification=NOT_QUALIFIED_NO_FAST_HOLD_VIEW\n'
    printf 'pin_placement_qualification=NOT_QUALIFIED_NO_FROZEN_COMMON_PIN_CONSTRAINT\n'
    printf 'drc_antenna_qualification=NOT_QUALIFIED_REPORT_PRESENCE_ONLY\n'
  } > "$output/manifest.txt"

  set +e
  (cd "$BUNDLE_ROOT" && "$INNOVUS_BIN" -no_gui -files "$PNR_TCL") \
    > "$output/tool.log" 2>&1
  tool_status=$?
  set -e
  status=COMPLETE_SINGLE_CORNER_DIAGNOSTIC
  if [[ "$tool_status" -ne 0 ]]; then
    status="FAIL_INNOVUS_$tool_status"
  elif grep -Eiq '(^|[[:space:]])(ERROR|FATAL)(:|[[:space:]])|\*\*(ERROR|FATAL):' \
      "$output/tool.log"; then
    status=FAIL_LOG_ERRORS
  elif [[ ! -s "$output/reports/setup_timing.rpt" ||
          ! -s "$output/reports/hold_timing.rpt" ||
          ! -s "$output/reports/area.rpt" ||
          ! -s "$output/reports/power.rpt" ||
          ! -s "$output/reports/check_design_pre_place.rpt" ||
          ! -s "$output/reports/check_design_post_route.rpt" ||
          ! -s "$output/reports/check_timing.rpt" ||
          ! -s "$output/reports/connectivity.rpt" ||
          ! -s "$output/reports/drc.rpt" ||
          ! -s "$output/reports/antenna.rpt" ||
          ! -s "$output/reports/route.rpt" ||
          ! -s "$output/status/FLOW_SUCCESS" ]]; then
    status=FAIL_MISSING_REPORTS
  fi
  if [[ "$status" != COMPLETE_SINGLE_CORNER_DIAGNOSTIC ]]; then
    any_fail=1
  fi
  printf 'tool_exit=%s\nstatus=%s\n' "$tool_status" "$status" \
    >> "$output/manifest.txt"
  printf '%s\t%s\t%s\n' "$design" "$status" "$tool_status" \
    >> "$RESULT_ROOT/comparison.tsv"
  printf 'A7_K4_INNOVUS_RESULT design=%s status=%s\n' "$design" "$status"
done < "$BUNDLE_ROOT/designs.tsv"

if [[ "$any_fail" -ne 0 ]]; then
  printf 'A7_K4_INNOVUS_COMPARISON_FAILED results=%s\n' "$RESULT_ROOT" >&2
  exit 1
fi
printf 'A7_K4_INNOVUS_COMPARISON_DONE results=%s\n' "$RESULT_ROOT"
