#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
verilator_bin="${VERILATOR:-/tmp/a7-toolchain/usr/bin/verilator}"
yosys_bin="${YOSYS:-/tmp/a7-toolchain/usr/bin/yosys}"
out_dir="${A4_P6_TEST_OUT:-$project_root/build/a4-paired-cortical-column-k2-p6}"
filelist="$project_root/rtl/candidates/a4_paired_cortical_column_k2_p6/a4_paired_cortical_column_k2_p6.f"
owner_rtl="$project_root/rtl/candidates/a4_paired_cortical_column_k2/a4_paired_cortical_column_k2.sv"

mkdir -p "$out_dir"
[[ -x "$verilator_bin" ]]
[[ -x "$yosys_bin" ]]

owner_sha256="$(sha256sum "$owner_rtl")"
owner_sha256="${owner_sha256%% *}"
if [[ "$owner_sha256" != \
      "56bde1a765cd750e5b4581e51d90ec1cf6893bcea9cbe904b09aeeafe89a0185" ]]; then
  printf 'A4_P6_OWNER_PIN_FAIL actual=%s\n' "$owner_sha256" >&2
  exit 1
fi

rtl_sources=()
while IFS= read -r source; do
  [[ -z "$source" ]] || rtl_sources+=("$project_root/$source")
done < "$filelist"

verilator_flags=(
  --binary --timing --assert -Wall -Wno-fatal -Wno-BLKSEQ
  -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
  -Wno-SYNCASYNCNET
)

build_rtl() {
  local name="$1"
  local define="${2:-}"
  local obj_dir="$out_dir/$name-obj"
  local define_args=()
  if [[ -n "$define" ]]; then
    define_args+=("-D$define")
  fi
  mkdir -p "$obj_dir"
  "$verilator_bin" "${verilator_flags[@]}" "${define_args[@]}" \
    --top-module a4_paired_cortical_column_k2_p6_tb \
    --Mdir "$obj_dir" -o sim \
    "${rtl_sources[@]}" \
    "$test_dir/a4_paired_cortical_column_k2_p6_tb.sv" \
    >"$out_dir/$name-build.log" 2>&1
  if grep -Eq '%Warning-(UNOPTFLAT|DIDNOTCONVERGE)' \
      "$out_dir/$name-build.log"; then
    printf 'A4_P6_COMBINATIONAL_LOOP_WARNING mutation=%s\n' "$name" >&2
    exit 1
  fi
}

build_rtl baseline
"$out_dir/baseline-obj/sim" | tee "$out_dir/baseline-run.log"
grep -q 'A4_PAIRED_CORTICAL_COLUMN_K2_P6_ALL_PASS' \
  "$out_dir/baseline-run.log"

# These are compile-time changes in the synthesizable owner/P6 RTL, not
# scoreboard-only perturbations.  Each must be killed by its contract test.
mutations=(
  "flat_weight:A4_PCCK2_MUTATE_FLAT_WEIGHT:A4_P6_CONTINUOUS_FAIL"
  "stall_advance:A4_PCCK2_MUTATE_STALL_ADVANCE:A4_P6_STALL_FAIL"
  "reset_live:A4_PCCK2_MUTATE_RESET_LIVE:A4_P6_RESET_FAIL"
  "swap_pair:A7_P6_MUTATE_SWAP_PAIR:A4_P6_ORDER_FAIL"
  "partial_microstep:A7_P6_MUTATE_PARTIAL_PAIR_COMMIT:A4_P6_PROTOCOL_FAIL"
)
for mutation in "${mutations[@]}"; do
  IFS=: read -r name define marker <<<"$mutation"
  build_rtl "$name" "$define"
  if "$out_dir/$name-obj/sim" >"$out_dir/$name-run.log" 2>&1; then
    printf 'A4_P6_MUTATION_SURVIVED name=%s define=%s\n' \
      "$name" "$define" >&2
    exit 1
  fi
  grep -q "$marker" "$out_dir/$name-run.log"
  printf 'A4_P6_RTL_MUTATION_KILLED name=%s define=%s marker=%s\n' \
    "$name" "$define" "$marker"
done

yosys_prefix="$(cd "$(dirname "$yosys_bin")/.." && pwd)"
yosys_ld_path="$yosys_prefix/lib/x86_64-linux-gnu"
yosys_sources="${rtl_sources[*]}"
env LD_LIBRARY_PATH="$yosys_ld_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    YOSYS_DATDIR="$yosys_prefix/share/yosys" \
  "$yosys_bin" -q -l "$out_dir/yosys-scc.log" -p \
  "read_verilog -sv -DSYNTHESIS $yosys_sources; \
   hierarchy -check -top a4_paired_cortical_column_k2_p6_top; \
   proc; flatten; opt; scc -expect 0; check"
grep -q 'Found 0 SCCs' "$out_dir/yosys-scc.log"
printf '%s\n' 'A4_P6_YOSYS_SCC_PASS scc=0'

git -C "$project_root" diff --exit-code -- \
  rtl/common \
  rtl/candidates/a7_p6_exact_pair_endpoint \
  rtl/candidates/a4_paired_cortical_column_k2 \
  tb/clean constraints physical
printf '%s\n' 'A4_P6_FROZEN_BOUNDARIES_PASS'
printf 'A4_P6_OWNER_PIN_PASS commit=%s blob=%s sha256=%s\n' \
  0e613b6933f1bb92e9b2f75b79a50663187f17d3 \
  b3810b2233fdd47a138c9dda1c182fd5ca0374c8 \
  "$owner_sha256"
printf 'A4_P6_ALL_PASS mutations=%d seam_state_bits=0 output=%s\n' \
  "${#mutations[@]}" "$out_dir"
