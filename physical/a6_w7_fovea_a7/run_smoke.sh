#!/usr/bin/env bash
set -euo pipefail

mapped_netlist=${1:-}
out=${2:?output directory}
pdk_verilog=${3:-}
variant=${4:-ddr}
bundle_root=$(cd "$(dirname "$0")" && pwd)
case $variant in ddr|parallel) ;; *) echo "invalid variant: $variant" >&2; exit 2;; esac
mkdir -p "$out/smoke"

owner_rtl=(
  "$bundle_root/sources/arbiter2.v"
  "$bundle_root/sources/arbiter4_tree.v"
  "$bundle_root/sources/aer_tx16_trad_rowcol_fovea.v"
  "$bundle_root/sources/a7_r1_launch_qualifier.sv"
  "$bundle_root/sources/a7_r1_icg_boundary.sv"
  "$bundle_root/sources/a7_r1_ddr_tx.sv"
  "$bundle_root/sources/a7_r1_ddr_rx.sv"
  "$bundle_root/sources/a7_r1_retire_observer.sv"
  "$bundle_root/sources/a7_r1_candidate_endpoint.sv"
  "$bundle_root/sources/a7_r1_parallel_reference_top.sv"
  "$bundle_root/sources/a7_weighted_fovea_ddr.sv"
  "$bundle_root/sources/a5_owner_semantics_parallel_top.sv"
)
staged_rtl=(
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

run_one() {
  local label=$1
  shift
  local define_arg=
  if [[ $variant == parallel ]]; then define_arg='-define W7_PARALLEL'; fi
  xrun -64bit -sv -timescale 1ns/1ps -clean -access +rwc $define_arg \
    -top a6_w7_smoke_tb \
    "$@" "$bundle_root/smoke_tb.sv" -l "$out/smoke/$label.log"
  grep -Fxq 'W7_HANDSHAKE_PASS accepted=36 retired=36 contention=all16 fault=0 drain=1' \
    "$out/smoke/$label.log"
  grep -E '^(CYCLE|EDGE|ACCEPT|RETIRE) ' "$out/smoke/$label.log" > "$out/smoke/$label.trace"
  test "$(grep -c '^ACCEPT ' "$out/smoke/$label.trace")" -eq 36
  test "$(grep -c '^RETIRE ' "$out/smoke/$label.trace")" -eq 36
}

run_one owner "${owner_rtl[@]}"
run_one staged "${staged_rtl[@]}"
diff -u "$out/smoke/owner.trace" "$out/smoke/staged.trace" \
  > "$out/smoke/owner-vs-staged.diff"

if [[ -n $mapped_netlist ]]; then
  test -s "$mapped_netlist"
  test -s "$pdk_verilog"
  run_one mapped "$mapped_netlist" "$pdk_verilog"
  diff -u "$out/smoke/owner.trace" "$out/smoke/mapped.trace" \
    > "$out/smoke/owner-vs-mapped.diff"
  printf 'W7 %s owner/staged/mapped exact handshake PASS\n' "$variant"
else
  printf 'W7 %s owner/staged exact handshake PASS\n' "$variant"
fi
