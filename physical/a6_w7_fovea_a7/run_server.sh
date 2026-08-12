#!/usr/bin/env bash
set -euo pipefail

variant=${1:?usage: run_server.sh ddr|parallel}
case $variant in
  ddr) design=a7_weighted_fovea_ddr ;;
  parallel) design=a5_owner_semantics_parallel_top ;;
  *) echo "invalid variant: $variant" >&2; exit 2 ;;
esac

bundle_root=$(cd "$(dirname "$0")" && pwd)
pdk_root=${W7_PDK_ROOT:-/home/aiasic26911/gsclib045_all_v4.7/gsclib045}
result_root=${W7_RESULT_ROOT:-$bundle_root/results}
variant_root=$result_root/$variant
test ! -e "$variant_root" || {
  echo "refusing existing variant result path: $variant_root" >&2
  exit 2
}

lib_file=$pdk_root/timing/slow_vdd1v0_basicCells.lib
tech_lef=$pdk_root/lef/gsclib045_tech.lef
macro_lef=$pdk_root/lef/gsclib045_macro.lef
qrc_file=$pdk_root/qrc/qx/gpdk045.tch
pdk_verilog=$pdk_root/verilog/slow_vdd1v0_basicCells.v
for required in "$lib_file" "$tech_lef" "$macro_lef" "$qrc_file" "$pdk_verilog"; do
  test -s "$required" || { echo "missing physical input: $required" >&2; exit 2; }
done

rtl_files=(
  "$bundle_root/synth_sources/arbiter2.v"
  "$bundle_root/synth_sources/arbiter4_tree.v"
  "$bundle_root/synth_sources/aer_tx16_trad_rowcol_fovea.v"
  "$bundle_root/synth_sources/a7_r1_launch_qualifier.sv"
  "$bundle_root/synth_sources/a7_r1_icg_boundary.sv"
  "$bundle_root/synth_sources/a7_r1_ddr_tx.sv"
  "$bundle_root/synth_sources/a7_r1_ddr_rx.sv"
  "$bundle_root/synth_sources/a7_r1_retire_observer.sv"
  "$bundle_root/synth_sources/a7_r1_candidate_endpoint.sv"
  "$bundle_root/synth_sources/a7_r1_parallel_reference_top.sv"
  "$bundle_root/synth_sources/a7_weighted_fovea_ddr.sv"
  "$bundle_root/synth_sources/a5_owner_semantics_parallel_top.sv"
)
for required in "${rtl_files[@]}"; do
  test -s "$required" || { echo "missing staged source: $required" >&2; exit 2; }
done

synth_out=$variant_root/genus
pnr_out=$variant_root/innovus
mkdir -p "$synth_out" "$pnr_out"

# Source equivalence precedes synthesis.  For DDR this is also the mandatory
# minimal smoke; parallel is not launched until a separately qualified DDR run.
"$bundle_root/run_smoke.sh" "" "$synth_out" "$pdk_verilog" "$variant"

export W7_DESIGN=$design
export W7_SDC=$bundle_root/$variant.sdc
export W7_RTL_FILES="${rtl_files[*]}"
export W7_OUT=$synth_out
export W7_LIB=$lib_file
genus -batch -files "$bundle_root/genus.tcl" -log "$synth_out/genus.log"
grep -Fq "W7_GENUS_CLEAN_END design=$design" "$synth_out/genus.log"
if grep -Eq '(^|[^[:alnum:]_])SDFF[A-Z0-9_]*' "$synth_out/${design}_mapped.v"; then
  echo 'scan-prefixed cell remains in non-DFT mapped netlist' >&2
  exit 1
fi
echo 'W7_MAPPED_SDFF_COUNT=0' | tee "$synth_out/scan_mapping.rpt"
"$bundle_root/check_mapped_icg.py" "$synth_out/${design}_mapped.v" \
  "$synth_out/icg_mapping.rpt"

# Exact owner/staged/mapped conservation is the hard gate before P&R.
"$bundle_root/run_smoke.sh" "$synth_out/${design}_mapped.v" "$synth_out" \
  "$pdk_verilog" "$variant"

export W7_MAPPED_NETLIST=$synth_out/${design}_mapped.v
export W7_MAPPED_SDC=$synth_out/${design}_mapped.sdc
export W7_MMMC=$bundle_root/mmmc.tcl
export W7_QRC=$qrc_file
export W7_TECH_LEF=$tech_lef
export W7_MACRO_LEF=$macro_lef
export W7_OUT=$pnr_out
mkdir -p "$pnr_out/tmp"
export TMPDIR=$pnr_out/tmp
innovus -no_gui -files "$bundle_root/innovus.tcl" -log "$pnr_out/innovus.log"

"$bundle_root/qualify_result.sh" "$variant_root" "$design" | tee "$variant_root/qualification.txt"
