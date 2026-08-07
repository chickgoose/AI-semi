#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A2_PHASE3_OUT:-/tmp/a2-phase3-physical}"
IVERILOG="${A2_IVERILOG:-iverilog}"
VVP="${A2_VVP:-vvp}"
YOSYS="${A2_YOSYS:-yosys}"

if [[ "${A2_PHASE3_SKIP_YOSYS:-0}" != "0" ]]; then
  printf 'A2_PHASE3_SKIP_YOSYS was removed: cached JSON is not self-authenticating.\n' >&2
  printf 'Run the full flow, or invoke scripts/a2_phase3_physical_proxy.py directly for manual diagnostics.\n' >&2
  exit 2
fi

command -v "$IVERILOG" >/dev/null 2>&1
command -v "$VVP" >/dev/null 2>&1
command -v "$YOSYS" >/dev/null 2>&1
mkdir -p "$OUT_DIR"
python3 "$PROJECT_ROOT/tests/a2/test_phase3_vcd_alias_filter.py"

for sources in 16 64; do
  equiv_binary="$OUT_DIR/packed-equiv-n$sources.vvp"
  (cd "$PROJECT_ROOT" && "$IVERILOG" -g2012 -Wall \
    -s a2_phase3_packed_equiv_tb \
    rtl/candidates/a2_adaptive_dual_path/a2_adaptive_dual_path_core.sv \
    rtl/candidates/a2_adaptive_dual_path/a2_phase2_selected_core.sv \
    rtl/candidates/a2_adaptive_dual_path/a2_phase3_selected_packed_core.sv \
    tests/a2/a2_phase3_packed_equiv_tb.sv \
    -P "a2_phase3_packed_equiv_tb.NUM_SOURCES=$sources" \
    -o "$equiv_binary")
  "$VVP" "$equiv_binary" >"$OUT_DIR/packed-equiv-n$sources.log" 2>&1
  rg -q 'A2_PHASE3_PACKED_EQUIV_PASS' "$OUT_DIR/packed-equiv-n$sources.log"
done

rtl_files=(
  rtl/candidates/a2_adaptive_dual_path/a2_phase3_selected_packed_core.sv
  rtl/candidates/a2_adaptive_dual_path/a2_phase3_reference_cores.sv
  rtl/candidates/a2_adaptive_dual_path/a2_phase3_physical_wrapper.sv
)
designs=(a2 flat_rr always_buffered)
models=(0 1 2)

for sources in 16 64; do
  for design_index in 0 1 2; do
    design="${designs[$design_index]}"
    model="${models[$design_index]}"
    json="$OUT_DIR/$design-n$sources.json"
    yosys_log="$OUT_DIR/$design-n$sources.yosys.log"
    yosys_script="read_verilog -sv -DSYNTHESIS ${rtl_files[*]}; hierarchy -top a2_phase3_physical_wrapper -chparam NUM_SOURCES $sources -chparam MODEL $model; proc; flatten; opt; memory_map; opt; techmap; opt; abc -fast -lut 4; clean; write_json $json"
    (cd "$PROJECT_ROOT" && "$YOSYS" -q -p "$yosys_script") >"$yosys_log" 2>&1

    binary="$OUT_DIR/$design-n$sources.vvp"
    (cd "$PROJECT_ROOT" && "$IVERILOG" -g2012 -Wall \
      -s a2_phase3_physical_tb -f tests/a2/a2_phase3_physical.f \
      -P "a2_phase3_physical_tb.NUM_SOURCES=$sources" \
      -P "a2_phase3_physical_tb.MODEL=$model" -o "$binary")
    for workload in sparse hotspot_fixed recurrence oscillate_4; do
      stem="$OUT_DIR/$design-n$sources-$workload"
      "$VVP" "$binary" "+WORKLOAD=$workload" "+VCD=$stem.vcd" \
        >"$stem.log" 2>&1
      rg -q 'A2_PHASE3_METRIC.*errors=0' "$stem.log"
    done
  done
done

python3 "$PROJECT_ROOT/scripts/a2_phase3_physical_proxy.py" \
  --output-dir "$OUT_DIR"
