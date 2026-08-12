#!/usr/bin/env bash
set -euo pipefail

variant_root=${1:?usage: qualify_result.sh VARIANT_RESULT_ROOT DESIGN}
design=${2:?design}
genus_root=$variant_root/genus
pnr_root=$variant_root/innovus

fail() { echo "$*" >&2; exit 1; }
require_zero_summary() {
  local label=$1 pattern=$2 file=$3
  grep -Ei "$pattern" "$file" | grep -Eq '[[:space:]]0[[:space:]]*$' || \
    fail "$label is absent or nonzero in $file"
}

required_nonempty=(
  "$genus_root/genus.log"
  "$genus_root/check_design.rpt"
  "$genus_root/check_timing_intent.rpt"
  "$genus_root/timing_setup.rpt"
  "$genus_root/timing_unconstrained.rpt"
  "$genus_root/${design}_mapped.v"
  "$genus_root/${design}_mapped.sdc"
  "$genus_root/scan_mapping.rpt"
  "$pnr_root/innovus.log"
  "$pnr_root/check_timing.rpt"
  "$pnr_root/check_place.rpt"
  "$pnr_root/drc.rpt"
  "$pnr_root/connectivity.rpt"
  "$pnr_root/timing_recovery.rpt"
  "$pnr_root/timing_removal.rpt"
  "$pnr_root/timing_metrics.rpt"
  "$pnr_root/${design}_postroute.v"
  "$pnr_root/${design}_postroute.sdf"
  "$pnr_root/W7_INNOVUS_CLEAN_END"
)
for artifact in "${required_nonempty[@]}"; do
  test -s "$artifact" || fail "empty required artifact: $artifact"
done
find "$pnr_root/timing_setup" -type f -size +0c -print -quit | grep -q . || \
  fail 'empty setup timing directory'
find "$pnr_root/timing_hold" -type f -size +0c -print -quit | grep -q . || \
  fail 'empty hold timing directory'

grep -Fq "W7_GENUS_CLEAN_END design=$design" "$genus_root/genus.log" || \
  fail 'missing Genus clean-end marker'
grep -Eq 'W7_SCAN_LIB_MATCH_COUNT=[1-9][0-9]*' "$genus_root/genus.log" || \
  fail 'scan library avoidance matched no cells'
grep -Fxq 'W7_MAPPED_SDFF_COUNT=0' "$genus_root/scan_mapping.rpt" || \
  fail 'mapped scan-prefixed count is not zero'
grep -Fxq "W7_INNOVUS_CLEAN_END design=$design" "$pnr_root/W7_INNOVUS_CLEAN_END" || \
  fail 'missing Innovus clean-end marker'
grep -Fq 'W7_UNPLACED_INSTS=0' "$pnr_root/innovus.log" || fail 'unplaced instances'
grep -Fq 'W7_UNPLACED_PORTS=0' "$pnr_root/innovus.log" || fail 'unplaced IO ports'
grep -Fq 'W7_UNCONSTRAINED_PATHS=0' "$pnr_root/innovus.log" || fail 'unconstrained paths'
grep -Eiq 'on.?chip.?variation|analysisType[[:space:]]+onChipVariation|MMMC[[:space:]]+OCV' "$pnr_root/innovus.log" || fail 'OCV not proven'
grep -Eiq 'cppr' "$pnr_root/innovus.log" || fail 'CPPR not proven'
grep -Eiq 'followpin' "$pnr_root/innovus.log" || fail 'followpin PG not proven'
grep -Eiq 'sroute|special route' "$pnr_root/innovus.log" || fail 'sroute PG not proven'

if grep -Eiq '(^|[*[:space:]])(ERROR|FATAL)[[:space:]]*:' \
    "$genus_root/genus.log" "$pnr_root/innovus.log"; then
  fail 'fatal/error diagnostic found in tool log'
fi

for spec in \
  'no_clock:Sequential clock pins without clock waveform' \
  'no_input_delay:Inputs without clocked external delays' \
  'no_output_delay:Outputs without clocked external delays' \
  'no_drive:Inputs without external driver/transition' \
  'no_load:Outputs without external load'; do
  require_zero_summary "${spec%%:*}" "${spec#*:}" "$genus_root/check_timing_intent.rpt"
done
if grep -Eiq '(unconstrained|no[[:space:]_-]*clock|no[[:space:]_-]*(input|output)[[:space:]_-]*delay|no[[:space:]_-]*(drive|driver|transition|load))[^0-9]*[1-9][0-9]*' \
    "$pnr_root/check_timing.rpt"; then
  fail 'Innovus timing coverage failure'
fi

python3 - "$pnr_root/timing_metrics.rpt" <<'PY' || fail 'timing metric gate failed'
import math, re, sys
lines = open(sys.argv[1]).read().splitlines()
required = {"setup", "hold", "recovery", "removal", "reset_ref_recovery", "reset_ref_removal", "reset_sample_setup", "reset_sample_hold", "reset_link_recovery", "reset_link_removal"}
rx = re.compile(r"^W7_TIMING_METRIC check=([a-z_]+) paths=(\d+) violations=(\d+) wns=(-?\d+(?:\.\d+)?) tns=(-?\d+(?:\.\d+)?)$")
metrics = {}
for line in lines:
    match = rx.fullmatch(line)
    if match:
        label, paths, violations, wns, tns = match.groups()
        if label in metrics:
            raise SystemExit(f"duplicate metric {label}")
        metrics[label] = (int(paths), int(violations), float(wns), float(tns))
if set(metrics) != required:
    raise SystemExit(f"timing metric inventory mismatch: missing={sorted(required-set(metrics))} extra={sorted(set(metrics)-required)}")
for label, (paths, violations, wns, tns) in metrics.items():
    if paths <= 0 or violations != 0 or not math.isfinite(wns + tns) or wns < 0.0 or tns < 0.0:
        raise SystemExit(f"failing {label}: paths={paths} violations={violations} wns={wns} tns={tns}")
PY

if grep -Eiq '([1-9][0-9]*)[[:space:]]+(unplaced|not placed|placement violations?)|(unplaced|not placed|placement violations?)[^0-9]*[1-9][0-9]*' \
    "$pnr_root/check_place.rpt"; then
  fail 'nonzero unplaced/placement violations found'
fi
if grep -Eiq '([1-9][0-9]*)[[:space:]]+(drc[[:space:]]+)?violations?|(drc[[:space:]]+)?violations?[^0-9]*[1-9][0-9]*' "$pnr_root/drc.rpt"; then
  fail 'nonzero DRC violations found'
fi
grep -Eiq '(0[[:space:]]+(drc[[:space:]]+)?violations?|(drc[[:space:]]+)?violations?[^0-9]*0|no[[:space:]]+(drc[[:space:]]+)?violations)' \
  "$pnr_root/drc.rpt" || fail 'DRC report does not prove zero violations'
if grep -Eiq '([1-9][0-9]*)[[:space:]]+(opens?|unconnected|connectivity[[:space:]]+violations?)|(opens?|unconnected|connectivity[[:space:]]+violations?)[^0-9]*[1-9][0-9]*' \
    "$pnr_root/connectivity.rpt"; then
  fail 'nonzero connectivity opens/violations found'
fi
grep -Eiq '(0[[:space:]]+(opens?|unconnected|connectivity[[:space:]]+violations?)|(opens?|unconnected|connectivity[[:space:]]+violations?)[^0-9]*0|no[[:space:]]+(open|unconnected|connectivity[[:space:]]+violation))' \
  "$pnr_root/connectivity.rpt" || fail 'connectivity report does not prove zero opens/violations'

printf 'W7_PHYSICAL_QUALIFICATION_PASS variant=%s design=%s\n' \
  "$(basename "$variant_root")" "$design"
