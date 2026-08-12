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
test -s "$bundle/smoke_tb.sv"
test -s "$bundle/physical_contract.json"
test "$(find "$bundle/sources" -maxdepth 1 -type f | wc -l)" -eq 12
test "$(find "$bundle/synth_sources" -maxdepth 1 -type f | wc -l)" -eq 12
! rg -q '\$isunknown' "$bundle/synth_sources"
rg -q 'if \(fovea_valid\)' "$bundle/synth_sources/a7_weighted_fovea_ddr.sv"
rg -q 'W7_HANDSHAKE_PASS accepted=%0d retired=%0d contention=all16 fault=0 drain=1' "$bundle/smoke_tb.sv"
rg -q 'while \(accepted_count == accepted_before\)' "$bundle/smoke_tb.sv"
rg -q 'send_all_contention' "$bundle/smoke_tb.sv"
rg -q 'CYCLE cycle=%0d valid=%04h ready=%04h drain=%0b fault=%0b' "$bundle/smoke_tb.sv"
rg -q 'EDGE edge=sample_pos' "$bundle/smoke_tb.sv"
rg -q 'EDGE edge=sample_neg' "$bundle/smoke_tb.sv"
rg -q 'EDGE edge=link_pos' "$bundle/smoke_tb.sv"
rg -q 'reset release must be 13ns' "$bundle/smoke_tb.sv"
rg -q 'W7_INNOVUS_CLEAN_END' "$bundle/innovus.tcl"
rg -q 'CoreSiteDouble' "$bundle/innovus.tcl"
rg -q 'onChipVariation' "$bundle/innovus.tcl"
rg -q 'cppr both' "$bundle/innovus.tcl"
rg -q 'W7_PG_FOLLOWPIN=sroute_corePin' "$bundle/innovus.tcl"
rg -q 'sroute' "$bundle/innovus.tcl"
rg -q 'saveNetlist' "$bundle/innovus.tcl"
! rg -q 'write_netlist' "$bundle/innovus.tcl"
rg -q 'report_timing -late -check_type recovery' "$bundle/innovus.tcl"
rg -q 'report_timing -early -check_type removal' "$bundle/innovus.tcl"
rg -q 'W7_TIMING_METRIC' "$bundle/innovus.tcl"
rg -q 'sizeof_collection' "$bundle/innovus.tcl"
rg -q 'icg_latch_pins' "$bundle/innovus.tcl"
rg -q 'reset_release_clk' "$bundle/ddr.sdc" "$bundle/parallel.sdc"
! rg -q 'set_false_path.*rst_n' "$bundle"/*.sdc
rg -q '~frame_active & ~burst_clk_o' "$bundle/sources/a7_r1_candidate_endpoint.sv"
! rg -q '~frame_active & ~burst_clk_o' "$bundle/synth_sources/a7_r1_candidate_endpoint.sv"
rg -q '~frame_active_q & ~link_strobe_o' "$bundle/sources/a7_r1_parallel_reference_top.sv"
! rg -q '~frame_active_q & ~link_strobe_o' "$bundle/synth_sources/a7_r1_parallel_reference_top.sv"
rg -q 'get_db lib_cells \*SDFF\*' "$bundle/genus.tcl"
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
