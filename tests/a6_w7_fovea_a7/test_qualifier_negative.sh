#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
tmp_root=$(mktemp -d /tmp/a6-w7-qualifier-test.XXXXXX)
trap 'rm -rf "$tmp_root"' EXIT
design=a7_weighted_fovea_ddr

make_fixture() {
  local root=$1
  mkdir -p "$root/genus" "$root/innovus/timing_setup" "$root/innovus/timing_hold"
  printf 'W7_GENUS_CLEAN_END design=%s\n' "$design" > "$root/genus/genus.log"
  printf 'clean design\n' > "$root/genus/check_design.rpt"
  printf '%s\n' \
    'Sequential clock pins without clock waveform 0' \
    'Inputs without clocked external delays 0' \
    'Outputs without clocked external delays 0' \
    'Inputs without external driver/transition 0' \
    'Outputs without external load 0' > "$root/genus/check_timing_intent.rpt"
  printf 'setup paths present\n' > "$root/genus/timing_setup.rpt"
  printf 'unconstrained diagnostic present, count 0\n' > "$root/genus/timing_unconstrained.rpt"
  printf 'module %s; endmodule\n' "$design" > "$root/genus/${design}_mapped.v"
  printf 'create_clock -period 16 ref_clk_i\n' > "$root/genus/${design}_mapped.sdc"
  printf 'W7_MAPPED_SDFF_COUNT=0\n' > "$root/genus/scan_mapping.rpt"
  printf 'W7_SCAN_LIB_MATCH_COUNT=17\n' >> "$root/genus/genus.log"
  printf '%s\n' \
    'Analysis Mode: MMMC OCV' 'CPPR enabled' 'W7_PG_FOLLOWPIN=sroute_corePin' \
    'sroute completed' 'W7_UNPLACED_INSTS=0' 'W7_UNPLACED_PORTS=0' \
    'W7_UNCONSTRAINED_PATHS=0' 'W7_RECOVERY_ANALYSIS_VIEW=setup_view' \
    'W7_REMOVAL_ANALYSIS_VIEW=hold_view' > "$root/innovus/innovus.log"
  printf '%s\n' 'Check Timing Report' 'Unconstrained endpoints : 0' 'No clock waveform : 0' > "$root/innovus/check_timing.rpt"
  printf '%s\n' 'CheckPlace Report' 'Total placement violations: 0' > "$root/innovus/check_place.rpt"
  printf '%s\n' 'VERIFY DRC SUMMARY' 'Total DRC violations: 0' > "$root/innovus/drc.rpt"
  printf '%s\n' 'VERIFY CONNECTIVITY SUMMARY' 'Total connectivity violations: 0' > "$root/innovus/connectivity.rpt"
  printf 'recovery path slack 1.0\n' > "$root/innovus/timing_recovery.rpt"
  printf 'removal path slack 1.0\n' > "$root/innovus/timing_removal.rpt"
  for check in setup hold recovery removal reset_ref_recovery reset_ref_removal reset_sample_setup reset_sample_hold reset_link_recovery reset_link_removal; do
    printf 'W7_TIMING_METRIC check=%s paths=1 violations=0 wns=1.000000 tns=0.000000\n' "$check"
  done > "$root/innovus/timing_metrics.rpt"
  printf 'setup\n' > "$root/innovus/timing_setup/report.rpt"
  printf 'hold\n' > "$root/innovus/timing_hold/report.rpt"
  printf 'module %s; endmodule\n' "$design" > "$root/innovus/${design}_postroute.v"
  printf 'SDF\n' > "$root/innovus/${design}_postroute.sdf"
  printf 'W7_INNOVUS_CLEAN_END design=%s\n' "$design" > "$root/innovus/W7_INNOVUS_CLEAN_END"
}

expect_reject() {
  local name=$1 root=$2
  if "$repo_root/physical/a6_w7_fovea_a7/qualify_result.sh" "$root" "$design" \
      > "$tmp_root/$name.stdout" 2> "$tmp_root/$name.stderr"; then
    echo "qualifier unexpectedly accepted negative fixture: $name" >&2
    exit 1
  fi
}

base=$tmp_root/base
make_fixture "$base"
"$repo_root/physical/a6_w7_fovea_a7/qualify_result.sh" "$base" "$design" \
  > "$tmp_root/base.stdout"

negative_names=(
  tool_error metric_setup metric_hold metric_recovery metric_removal
  metric_wns metric_tns metric_violations hold_actual_negative reset_analysis_view
  unconstrained no_clock no_input_delay
  no_output_delay no_drive empty_removal drc_late_nonzero
  connectivity_late_nonzero checkplace_late_nonzero scan_not_avoided scan_mapped
  abort reset_coverage_zero reset_sample_coverage_zero no_load
)
for name in "${negative_names[@]}"; do
  cp -a "$base" "$tmp_root/$name"
done
printf 'Error   : injected Genus-style tool failure\n' >> "$tmp_root/tool_error/genus/genus.log"
for check in setup hold recovery removal; do
  sed -i "s/check=$check paths=1 violations=0 wns=1.000000 tns=0.000000/check=$check paths=1 violations=1 wns=-0.100000 tns=-0.100000/" \
    "$tmp_root/metric_$check/innovus/timing_metrics.rpt"
done
sed -i 's/check=hold paths=1 violations=0 wns=1.000000 tns=0.000000/check=hold paths=1 violations=0 wns=-0.010000 tns=0.000000/' \
  "$tmp_root/metric_wns/innovus/timing_metrics.rpt"
sed -i 's/check=recovery paths=1 violations=0 wns=1.000000 tns=0.000000/check=recovery paths=1 violations=0 wns=1.000000 tns=-0.010000/' \
  "$tmp_root/metric_tns/innovus/timing_metrics.rpt"
sed -i 's/check=removal paths=1 violations=0 wns=1.000000 tns=0.000000/check=removal paths=1 violations=2 wns=1.000000 tns=0.000000/' \
  "$tmp_root/metric_violations/innovus/timing_metrics.rpt"
sed -i 's/check=hold paths=1 violations=0 wns=1.000000 tns=0.000000/check=hold paths=80 violations=3 wns=-0.413000 tns=-0.443000/' \
  "$tmp_root/hold_actual_negative/innovus/timing_metrics.rpt"
sed -i '/W7_REMOVAL_ANALYSIS_VIEW=hold_view/d' \
  "$tmp_root/reset_analysis_view/innovus/innovus.log"
sed -i 's/W7_UNCONSTRAINED_PATHS=0/W7_UNCONSTRAINED_PATHS=1/' \
  "$tmp_root/unconstrained/innovus/innovus.log"
sed -i 's/Sequential clock pins without clock waveform 0/Sequential clock pins without clock waveform 1/' \
  "$tmp_root/no_clock/genus/check_timing_intent.rpt"
sed -i 's/Inputs without clocked external delays 0/Inputs without clocked external delays 1/' \
  "$tmp_root/no_input_delay/genus/check_timing_intent.rpt"
sed -i 's/Outputs without clocked external delays 0/Outputs without clocked external delays 1/' \
  "$tmp_root/no_output_delay/genus/check_timing_intent.rpt"
sed -i 's/Inputs without external driver\/transition 0/Inputs without external driver\/transition 1/' \
  "$tmp_root/no_drive/genus/check_timing_intent.rpt"
sed -i 's/Outputs without external load 0/Outputs without external load 1/' \
  "$tmp_root/no_load/genus/check_timing_intent.rpt"
: > "$tmp_root/empty_removal/innovus/timing_removal.rpt"
printf 'Total DRC violations: 3\n' >> "$tmp_root/drc_late_nonzero/innovus/drc.rpt"
printf 'Total connectivity violations: 2\n' >> "$tmp_root/connectivity_late_nonzero/innovus/connectivity.rpt"
printf 'Total placement violations: 4\n' >> "$tmp_root/checkplace_late_nonzero/innovus/check_place.rpt"
sed -i 's/W7_SCAN_LIB_MATCH_COUNT=17/W7_SCAN_LIB_MATCH_COUNT=0/' "$tmp_root/scan_not_avoided/genus/genus.log"
sed -i 's/W7_MAPPED_SDFF_COUNT=0/W7_MAPPED_SDFF_COUNT=1/' "$tmp_root/scan_mapped/genus/scan_mapping.rpt"
: > "$tmp_root/abort/innovus/W7_INNOVUS_CLEAN_END"
sed -i 's/check=reset_link_removal paths=1/check=reset_link_removal paths=0/' \
  "$tmp_root/reset_coverage_zero/innovus/timing_metrics.rpt"
sed -i 's/check=reset_sample_hold paths=1/check=reset_sample_hold paths=0/' \
  "$tmp_root/reset_sample_coverage_zero/innovus/timing_metrics.rpt"

for name in "${negative_names[@]}"; do
  expect_reject "$name" "$tmp_root/$name"
done

echo "A6 W7 qualifier negative tests PASS (${#negative_names[@]}/${#negative_names[@]} rejected)"
