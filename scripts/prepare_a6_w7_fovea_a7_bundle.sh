#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
owner_repo=${A6_W7_OWNER_REPO:-/home/chickgoose/projects/a1}
owner_commit=2a3a3be94be8f12585f484b5b1da2b372f7282d9
out=${1:?usage: prepare_a6_w7_fovea_a7_bundle.sh OUT_DIR}

test ! -e "$out" || { echo "refusing existing output path: $out" >&2; exit 2; }
git -C "$owner_repo" cat-file -e "$owner_commit^{commit}"
mkdir -p "$out/sources"

extract() {
  local source_path=$1
  local output_name=$2
  git -C "$owner_repo" show "$owner_commit:$source_path" > "$out/sources/$output_name"
}

extract rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv a7_weighted_fovea_ddr.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv a7_r1_candidate_endpoint.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv a7_r1_ddr_rx.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv a7_r1_ddr_tx.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv a7_r1_icg_boundary.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv a7_r1_launch_qualifier.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv a7_r1_parallel_reference_top.sv
extract rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv a7_r1_retire_observer.sv
extract tests/a5_fovea_a7_structural/a5_owner_semantics_parallel_top.sv a5_owner_semantics_parallel_top.sv
extract tests/a5_fovea_a7_structural/fixtures/aer_tx16_trad_rowcol_fovea.v aer_tx16_trad_rowcol_fovea.v
extract tests/a5_fovea_a7_structural/fixtures/arbiter2.v arbiter2.v
extract tests/a5_fovea_a7_structural/fixtures/arbiter4_tree.v arbiter4_tree.v

cp "$repo_root/physical/a6_w7_fovea_a7/"{ddr.sdc,parallel.sdc,genus.tcl,mmmc.tcl,innovus.tcl,run_server.sh,run_smoke.sh,qualify_result.sh,smoke_tb.sv,owner_registry.json,physical_contract.json} "$out/"
chmod +x "$out/run_server.sh"
chmod +x "$out/run_smoke.sh"
chmod +x "$out/qualify_result.sh"

python3 - "$out" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
registry = json.loads((root / "owner_registry.json").read_text())
expected = {pathlib.Path(k).name: v for k, v in registry["files"].items()}
actual_names = {p.name for p in (root / "sources").iterdir() if p.is_file()}
if actual_names != set(expected):
    raise SystemExit(f"source inventory mismatch: {sorted(actual_names ^ set(expected))}")
for name, digest in sorted(expected.items()):
    actual = hashlib.sha256((root / "sources" / name).read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit(f"SHA mismatch for {name}: {actual} != {digest}")
print(f"verified {len(expected)} owner files at {registry['owner_commit']}")
PY

# The owner sources above remain byte-identical and are the provenance anchor.
# Cadence's HDL front-end does not support $isunknown as a synthesizable
# predicate, so only the A6 staging copy used by synthesis is rewritten to the
# legal two-state contract: source_valid/fovea_valid are assumed 0/1 and an
# illegal unknown address is represented by protocol_fault through the normal
# source-valid check.  No owner file or common RTL is changed.
mkdir -p "$out/synth_sources"
cp "$out/sources"/* "$out/synth_sources/"
python3 - "$out/synth_sources/a7_weighted_fovea_ddr.sv" "$out/synth_sources/a5_owner_semantics_parallel_top.sv" <<'PY'
from pathlib import Path
import sys

for name in sys.argv[1:]:
    path = Path(name)
    text = path.read_text()
    text = text.replace(
        "if (fovea_valid && !$isunknown(fovea_addr))",
        "if (fovea_valid)",
    )
    text = text.replace(
        "      if ($isunknown(fovea_addr))\n        protocol_fault_o = 1'b1;\n      else if (!source_valid[fovea_addr])",
        "      if (!source_valid[fovea_addr])",
    )
    if "$isunknown" in text:
        raise SystemExit(f"2-state rewrite incomplete: {path}")
    path.write_text(text)
PY

# The PPA boundary includes the retire observer and drain output.  clock_o is
# structurally qualified by frame_active, so its negation is redundant in the
# drain conjunction.  Remove only that redundant generated-clock-as-data term
# from staged endpoint copies; owner sources remain the hash-checked anchor.
python3 - "$out/synth_sources/a7_r1_candidate_endpoint.sv" "$out/synth_sources/a7_r1_parallel_reference_top.sv" <<'PY'
from pathlib import Path
import sys

rewrites = {
    "a7_r1_candidate_endpoint.sv": (
        "~launch_fire & ~frame_active & ~burst_clk_o &",
        "~launch_fire & ~frame_active &",
    ),
    "a7_r1_parallel_reference_top.sv": (
        "~launch_fire & ~frame_active_q & ~link_strobe_o &",
        "~launch_fire & ~frame_active_q &",
    ),
}
for raw in sys.argv[1:]:
    path = Path(raw)
    old, new = rewrites[path.name]
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one staged drain rewrite in {path}")
    path.write_text(text.replace(old, new))
PY
