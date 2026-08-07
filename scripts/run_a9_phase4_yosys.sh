#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A9_PHASE4_YOSYS_OUT:-/tmp/a9-phase4-yosys-results}"
YOSYS_BIN="${YOSYS:-yosys}"

mkdir -p "$OUT_DIR"
summary="$OUT_DIR/summary.csv"
: >"$summary"
first_row=1

rtl_files=(
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv"
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv"
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv"
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_centralized_reference.sv"
  "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_phase4_synth_top.sv"
)

for geometry in 16:4 64:8; do
  sources="${geometry%%:*}"
  lanes="${geometry##*:}"
  for implementation in static diffusive centralized; do
    define=""
    case "$implementation" in
      diffusive) define="-DA9_PHASE4_DIFFUSIVE" ;;
      centralized) define="-DA9_PHASE4_CENTRAL" ;;
    esac
    run_dir="$OUT_DIR/n${sources}-l${lanes}-$implementation"
    mkdir -p "$run_dir"
    read_command="read_verilog -sv -DSYNTHESIS -DA9_YOSYS $define ${rtl_files[*]}"
    flow="$read_command; chparam -set NUM_SOURCES $sources -set RETIRE_LANES $lanes a9_phase4_synth_top; hierarchy -top a9_phase4_synth_top; proc; flatten; opt; memory; opt; techmap; opt; abc -g simple; clean; tee -o $run_dir/stat.json stat -json; write_json $run_dir/netlist.json"
    "$YOSYS_BIN" -ql "$run_dir/yosys.log" -p "$flow"
    header=()
    if (( first_row )); then
      header=(--header)
      first_row=0
    fi
    python3 "$PROJECT_ROOT/scripts/analyze_a9_yosys_json.py" \
      "$run_dir/netlist.json" --implementation "$implementation" \
      --sources "$sources" --lanes "$lanes" "${header[@]}" >>"$summary"
  done
done

printf 'A9 phase-4 Yosys comparison complete: %s\n' "$summary"
