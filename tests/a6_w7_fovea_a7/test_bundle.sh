#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
tmp_root=$(mktemp -d /tmp/a6-w7-bundle-test.XXXXXX)
trap 'rm -rf "$tmp_root"' EXIT

bundle=$tmp_root/bundle
"$repo_root/scripts/prepare_a6_w7_fovea_a7_bundle.sh" "$bundle"
test -s "$bundle/ddr.sdc"
test -s "$bundle/parallel.sdc"
test -x "$bundle/run_server.sh"
test -x "$bundle/run_smoke.sh"
test -x "$bundle/qualify_result.sh"
test -x "$bundle/check_mapped_icg.py"
test -s "$bundle/smoke_tb.sv"
test -s "$bundle/physical_contract.json"
test -s "$bundle/icg_selection.txt"
test -s "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
test -s "$bundle/icg_candidates/a7_r1_icg_boundary_tlatntsca.sv"
test "$(find "$bundle/sources" -maxdepth 1 -type f | wc -l)" -eq 12
test "$(find "$bundle/synth_sources" -maxdepth 1 -type f | wc -l)" -eq 12
grep -Fxq 'W7_SELECTED_ICG=TLATNCAX2' "$bundle/icg_selection.txt"
grep -Fxq 'W7_SELECTED_ICG_COUNT=1' "$bundle/icg_selection.txt"
grep -Fxq 'W7_ALTERNATE_ICG_COUNT=0' "$bundle/icg_selection.txt"
cmp "$bundle/synth_sources/a7_r1_icg_boundary.sv" \
  "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
! rg -q '\$isunknown' "$bundle/synth_sources"
rg -q 'if \(fovea_valid\)' "$bundle/synth_sources/a7_weighted_fovea_ddr.sv"
rg -q 'W7_HANDSHAKE_PASS accepted=%0d retired=%0d contention=all16 fault=0 drain=1' "$bundle/smoke_tb.sv"
rg -q 'while \(accepted_count == accepted_before\)' "$bundle/smoke_tb.sv"
rg -q 'send_all_contention' "$bundle/smoke_tb.sv"
rg -q 'CYCLE cycle=%0d valid=%04h ready=%04h drain=%0b fault=%0b' "$bundle/smoke_tb.sv"
rg -Fq 'task automatic emit_edge(input string edge_name);' "$bundle/smoke_tb.sv"
rg -Fq '"EDGE edge=%s time=%0t ready=%04h drain=%0b link=%0b valid=%04h"' \
  "$bundle/smoke_tb.sv"
for edge_name in sample_pos sample_neg link_pos link_neg; do
  rg -Fq "emit_edge(\"$edge_name\");" "$bundle/smoke_tb.sv"
  rg -Fq "grep -c '^EDGE edge=$edge_name '" "$bundle/run_smoke.sh"
done
rg -Fq '#1 rst_n = 1'"'"'b0;' "$bundle/smoke_tb.sv"
rg -Fq '#28 rst_n = 1'"'"'b1;' "$bundle/smoke_tb.sv"
rg -q '\$time % REF_PERIOD_TICKS' "$bundle/smoke_tb.sv"
rg -q 'RESET_RELEASE_PHASE_TICKS = 13' "$bundle/smoke_tb.sv"
rg -q 'SAMPLE_RISE_PHASE_TICKS = 4' "$bundle/smoke_tb.sv"
rg -q 'SAMPLE_FALL_PHASE_TICKS = 12' "$bundle/smoke_tb.sv"
if rg -n '\$time[^;\n]*%[^;\n]*(fs|ps|ns|us|ms|s)\b' "$bundle/smoke_tb.sv"; then
  echo 'time-unit literal used as modulus operand' >&2
  exit 1
fi
rg -q 'reset_active_ref_edges < 1' "$bundle/smoke_tb.sv"
rg -q 'W7_INNOVUS_CLEAN_END' "$bundle/innovus.tcl"
rg -q 'CoreSiteDouble' "$bundle/innovus.tcl"
rg -q 'onChipVariation' "$bundle/innovus.tcl"
rg -q 'cppr both' "$bundle/innovus.tcl"
rg -q 'W7_PG_FOLLOWPIN=sroute_corePin' "$bundle/innovus.tcl"
rg -q 'sroute' "$bundle/innovus.tcl"
rg -q 'saveNetlist' "$bundle/innovus.tcl"
! rg -q 'write_netlist' "$bundle/innovus.tcl"
check_reset_timing_tcl() {
  python3 - "$1" <<'PY'
import re
import sys

text = open(sys.argv[1]).read()
commands = []
pending = ""
for raw in text.splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    pending = f"{pending} {line}".strip()
    if pending.endswith("\\"):
        pending = pending[:-1].rstrip()
        continue
    commands.append(pending)
    pending = ""
if pending:
    commands.append(pending)

if "set_analysis_view -setup [list setup_view] -hold [list hold_view]" not in commands:
    raise SystemExit("setup/hold analysis views are not simultaneously active")
for command in commands:
    if "-check_type" in command and re.search(r"(?:^|\s)-(?:late|early)(?:\s|$)", command):
        raise SystemExit(f"Innovus 23.14-incompatible timing switches: {command}")
for check in ("recovery", "removal"):
    direct = [command for command in commands
              if command.startswith(f"report_timing -check_type {check} ")]
    if len(direct) != 1:
        raise SystemExit(f"expected one direct {check} report, found {len(direct)}")
    marker = f'puts "W7_{check.upper()}_ANALYSIS_VIEW='
    if not any(command.startswith(marker) for command in commands):
        raise SystemExit(f"missing {check} analysis-view proof marker")
PY
}
check_reset_timing_tcl "$bundle/innovus.tcl"
if check_reset_timing_tcl \
    "$repo_root/tests/a6_w7_fovea_a7/fixtures/innovus_23_14_illegal_recovery_removal.tcl" \
    >/dev/null 2>&1; then
  echo 'Innovus 23.14 illegal recovery/removal fixture was not rejected' >&2
  exit 1
fi
rg -q 'W7_TIMING_METRIC' "$bundle/innovus.tcl"
rg -q 'sizeof_collection' "$bundle/innovus.tcl"
rg -q 'generic_icg_latch_pins' "$bundle/innovus.tcl"
rg -q 'characterized_icg_enable_pins' "$bundle/innovus.tcl"
rg -q 'characterized_icg/E' "$bundle/innovus.tcl"
rg -q 'add_to_collection' "$bundle/innovus.tcl"
rg -Fq 'W7_INNOVUS_SELECTED_ICG_COUNT=$selected_icg_count' "$bundle/innovus.tcl"
rg -Fq 'W7_INNOVUS_ALTERNATE_ICG_COUNT=$alternate_icg_count' "$bundle/innovus.tcl"
rg -Fq 'TLATNCAX2 characterized_icg' \
  "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
rg -Fq '.CK  (clock_i)' "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
rg -Fq '.E   (gate_enable)' "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
rg -Fq '.ECK (clock_o)' "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv"
rg -Fq 'TLATNTSCAX2 characterized_icg' \
  "$bundle/icg_candidates/a7_r1_icg_boundary_tlatntsca.sv"
rg -Fq '.SE  (1'"'"'b0)' "$bundle/icg_candidates/a7_r1_icg_boundary_tlatntsca.sv"
check_icg_reset_contract() {
  rg -Fq 'wire gate_enable = enable_i & rst_n;' "$1" &&
    ! rg -q 'assign clock_o.*rst_n|set_false_path' "$1"
}
for candidate in \
  "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv" \
  "$bundle/icg_candidates/a7_r1_icg_boundary_tlatntsca.sv"; do
  check_icg_reset_contract "$candidate"
done
icg_reset_mutant=$tmp_root/icg_reset_mutant.sv
cp "$bundle/icg_candidates/a7_r1_icg_boundary_tlatnca.sv" "$icg_reset_mutant"
sed -i 's/enable_i & rst_n/enable_i/' "$icg_reset_mutant"
if check_icg_reset_contract "$icg_reset_mutant"; then
  echo 'ICG reset-cone mutation escaped structural contract gate' >&2
  exit 1
fi
rg -q 'reset_ref_recovery' "$bundle/innovus.tcl"
rg -q 'reset_sample_setup' "$bundle/innovus.tcl"
rg -q 'reset_link_recovery' "$bundle/innovus.tcl"
rg -q 'reset_release_clk' "$bundle/ddr.sdc" "$bundle/parallel.sdc"
! rg -q 'set_false_path.*rst_n' "$bundle"/*.sdc
rg -q '~frame_active & ~burst_clk_o' "$bundle/sources/a7_r1_candidate_endpoint.sv"
! rg -q '~frame_active & ~burst_clk_o' "$bundle/synth_sources/a7_r1_candidate_endpoint.sv"
rg -q '~frame_active_q & ~link_strobe_o' "$bundle/sources/a7_r1_parallel_reference_top.sv"
! rg -q '~frame_active_q & ~link_strobe_o' "$bundle/synth_sources/a7_r1_parallel_reference_top.sv"
rg -q 'get_db lib_cells \*SDFF\*' "$bundle/genus.tcl"
rg -Fq 'W7_MAPPED_SELECTED_ICG_COUNT=' "$bundle/check_mapped_icg.py"
rg -Fq 'W7_MAPPED_ALTERNATE_ICG_COUNT=' "$bundle/check_mapped_icg.py"
rg -Fq 'mapped ICG inventory mismatch' "$bundle/check_mapped_icg.py"
icg_mapping_report=$tmp_root/icg_mapping.rpt
"$bundle/check_mapped_icg.py" \
  "$repo_root/tests/a6_w7_fovea_a7/fixtures/mapped_icg_actual_format.v" \
  "$icg_mapping_report"
grep -Fxq 'W7_MAPPED_SELECTED_ICG_COUNT=1' "$icg_mapping_report"
grep -Fxq 'W7_MAPPED_ALTERNATE_ICG_COUNT=0' "$icg_mapping_report"
for mutation in missing alternate duplicate; do
  mutant_netlist=$tmp_root/icg_$mutation.v
  cp "$repo_root/tests/a6_w7_fovea_a7/fixtures/mapped_icg_actual_format.v" \
    "$mutant_netlist"
  case $mutation in
    missing) sed -i 's/TLATNCAX2/AND2X1/' "$mutant_netlist" ;;
    alternate) sed -i 's/TLATNCAX2/TLATNTSCAX2/' "$mutant_netlist" ;;
    duplicate) sed -i '/endmodule/i\  TLATNCAX2 duplicate_icg (.CK(sample_clk_i), .E(gate_enable), .ECK());' "$mutant_netlist" ;;
  esac
  if "$bundle/check_mapped_icg.py" "$mutant_netlist" \
      "$tmp_root/icg_$mutation.rpt" >/dev/null 2>&1; then
    echo "mapped ICG $mutation mutation escaped inventory gate" >&2
    exit 1
  fi
done
python3 - "$bundle/physical_contract.json" <<'PY'
import json, sys
contract = json.load(open(sys.argv[1]))
assert contract["reset_assertion"]["smoke_phase_ns"] == 1.0
assert contract["reset_assertion"]["required_sample_clock_level"] == 0
assert contract["reset_assertion"]["arbitrary_phase_async_assertion_supported"] is False
PY
cmp "$bundle/sources/a7_weighted_fovea_ddr.sv" "$bundle/synth_sources/a7_weighted_fovea_ddr.sv" && {
  echo "2-state staging copy unexpectedly equals owner source" >&2
  exit 1
} || true

if "$repo_root/scripts/prepare_a6_w7_fovea_a7_bundle.sh" "$bundle" >/dev/null 2>&1; then
  echo "existing-output fail-closed check unexpectedly passed" >&2
  exit 1
fi

printf 'mutation\n' >> "$bundle/sources/a7_r1_ddr_tx.sv"
if python3 - "$bundle" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
registry = json.loads((root / "owner_registry.json").read_text())
expected = {pathlib.Path(k).name: v for k, v in registry["files"].items()}
for name, digest in expected.items():
    if hashlib.sha256((root / "sources" / name).read_bytes()).hexdigest() != digest:
        raise SystemExit(1)
PY
then
  echo "source mutation was not rejected" >&2
  exit 1
fi

echo "A6 W7 bundle provenance tests PASS"
