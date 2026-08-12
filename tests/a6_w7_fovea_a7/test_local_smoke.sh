#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
iverilog_bin=${IVERILOG_BIN:-iverilog}
vvp_bin=${VVP_BIN:-vvp}
iverilog_base=${IVERILOG_BASE:-}
tmp_root=$(mktemp -d /tmp/a6-w7-cycle-smoke.XXXXXX)
if [[ ${W7_KEEP_TMP:-0} == 1 ]]; then
  echo "W7_LOCAL_SMOKE_ROOT=$tmp_root"
else
  trap 'rm -rf "$tmp_root"' EXIT
fi
"$repo_root/scripts/prepare_a6_w7_fovea_a7_bundle.sh" "$tmp_root/bundle"
cp -a "$tmp_root/bundle/synth_sources" "$tmp_root/mutant_ddr_sources"
cp -a "$tmp_root/bundle/synth_sources" "$tmp_root/mutant_parallel_sources"

# Negative proof for the exact staged rewrite location: the legal staged copy
# omits redundant ~burst_clk_o.  Reinsert the wrong-polarity burst_clk_o term;
# sample/link-edge drain observations must reject this non-equivalent mutant.
sed -i "s/~launch_fire & ~frame_active &/~launch_fire \& ~frame_active \& burst_clk_o \&/" \
  "$tmp_root/mutant_ddr_sources/a7_r1_candidate_endpoint.sv"
sed -i "s/~launch_fire & ~frame_active_q &/~launch_fire \& ~frame_active_q \& link_strobe_o \&/" \
  "$tmp_root/mutant_parallel_sources/a7_r1_parallel_reference_top.sv"
rg -q 'frame_active & burst_clk_o &' \
  "$tmp_root/mutant_ddr_sources/a7_r1_candidate_endpoint.sv"
rg -q 'frame_active_q & link_strobe_o &' \
  "$tmp_root/mutant_parallel_sources/a7_r1_parallel_reference_top.sv"

rtl_names=(
  arbiter2.v arbiter4_tree.v aer_tx16_trad_rowcol_fovea.v
  a7_r1_launch_qualifier.sv a7_r1_icg_boundary.sv a7_r1_ddr_tx.sv
  a7_r1_ddr_rx.sv a7_r1_retire_observer.sv a7_r1_candidate_endpoint.sv
  a7_r1_parallel_reference_top.sv a7_weighted_fovea_ddr.sv
  a5_owner_semantics_parallel_top.sv
)

run_one() {
  local label=$1 source_dir=$2 variant=$3
  local args=(-g2012 -s a6_w7_smoke_tb -o "$tmp_root/$label.vvp")
  if [[ $variant == parallel ]]; then args+=(-DW7_PARALLEL); fi
  if [[ -n $iverilog_base ]]; then args=(-B "$iverilog_base" "${args[@]}"); fi
  local name
  for name in "${rtl_names[@]}"; do args+=("$source_dir/$name"); done
  args+=("$tmp_root/bundle/smoke_tb.sv")
  "$iverilog_bin" "${args[@]}"
  local vvp_args=()
  if [[ -n $iverilog_base ]]; then vvp_args=(-M "$iverilog_base"); fi
  if ! "$vvp_bin" "${vvp_args[@]}" "$tmp_root/$label.vvp" > "$tmp_root/$label.log"; then
    [[ $label == mutant* ]] || return 1
  fi
  grep -E '^(CYCLE|EDGE|ACCEPT|RETIRE) ' "$tmp_root/$label.log" > "$tmp_root/$label.trace"
}

run_one owner "$tmp_root/bundle/sources" ddr
run_one staged "$tmp_root/bundle/synth_sources" ddr
run_one mutant "$tmp_root/mutant_ddr_sources" ddr
run_one owner_parallel "$tmp_root/bundle/sources" parallel
run_one staged_parallel "$tmp_root/bundle/synth_sources" parallel
run_one mutant_parallel "$tmp_root/mutant_parallel_sources" parallel
grep -Fxq 'W7_HANDSHAKE_PASS accepted=36 retired=36 contention=all16 fault=0 drain=1' "$tmp_root/owner.log"
grep -Fxq 'W7_HANDSHAKE_PASS accepted=36 retired=36 contention=all16 fault=0 drain=1' "$tmp_root/staged.log"
test "$(grep -c '^ACCEPT ' "$tmp_root/owner.trace")" -eq 36
test "$(grep -c '^RETIRE ' "$tmp_root/owner.trace")" -eq 36
diff -u "$tmp_root/owner.trace" "$tmp_root/staged.trace" > "$tmp_root/owner-vs-staged.diff"
if diff -q "$tmp_root/owner.trace" "$tmp_root/mutant.trace" >/dev/null; then
  echo 'non-equivalent staged drain mutation escaped cycle-exact trace' >&2
  exit 1
fi
grep -E '^EDGE edge=(sample|link)_' "$tmp_root/owner.trace" \
  > "$tmp_root/owner.midcycle"
grep -E '^EDGE edge=(sample|link)_' "$tmp_root/mutant.trace" \
  > "$tmp_root/mutant.midcycle"
if diff -q "$tmp_root/owner.midcycle" "$tmp_root/mutant.midcycle" >/dev/null; then
  echo 'exact burst-clock drain mutation escaped sample/link-edge observation' >&2
  exit 1
fi
grep -Fxq 'W7_HANDSHAKE_PASS accepted=36 retired=36 contention=all16 fault=0 drain=1' "$tmp_root/owner_parallel.log"
grep -Fxq 'W7_HANDSHAKE_PASS accepted=36 retired=36 contention=all16 fault=0 drain=1' "$tmp_root/staged_parallel.log"
diff -u "$tmp_root/owner_parallel.trace" "$tmp_root/staged_parallel.trace" \
  > "$tmp_root/owner-vs-staged-parallel.diff"
if diff -q "$tmp_root/owner_parallel.trace" "$tmp_root/mutant_parallel.trace" >/dev/null; then
  echo 'non-equivalent staged link_strobe drain mutation escaped edge-exact trace' >&2
  exit 1
fi
grep -E '^EDGE edge=(sample|link)_' "$tmp_root/owner_parallel.trace" \
  > "$tmp_root/owner_parallel.midcycle"
grep -E '^EDGE edge=(sample|link)_' "$tmp_root/mutant_parallel.trace" \
  > "$tmp_root/mutant_parallel.midcycle"
if diff -q "$tmp_root/owner_parallel.midcycle" "$tmp_root/mutant_parallel.midcycle" >/dev/null; then
  echo 'exact link-strobe drain mutation escaped sample/link-edge observation' >&2
  exit 1
fi
sha256sum "$tmp_root/owner.trace" "$tmp_root/staged.trace"
sha256sum "$tmp_root/owner_parallel.trace" "$tmp_root/staged_parallel.trace"
echo 'A6 W7 local edge-exact DDR/parallel owner/staged + exact drain-term mutations PASS'
