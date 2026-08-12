#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
verilator_bin="${VERILATOR:-verilator}"
yosys_bin="${YOSYS:-yosys}"
out_dir="${A3_P6_TEST_OUT:-/tmp/a3-exact-scalar-prefix-k2-p6}"
filelist="$project_root/rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6.f"
provenance="$project_root/rtl/candidates/a3_exact_scalar_prefix_k2_p6/provenance.json"
provenance_check="$test_dir/verify_provenance.py"

mkdir -p "$out_dir"
command -v "$verilator_bin" >/dev/null
command -v "$yosys_bin" >/dev/null

python3 "$provenance_check" --project-root "$project_root" \
  --provenance "$provenance" --self-test | tee "$out_dir/provenance.log"

rtl_sources=()
while IFS= read -r source; do
  [[ -z "$source" ]] || rtl_sources+=("$project_root/$source")
done < "$filelist"

verilator_flags=(
  --binary --timing --assert -Wall -Wno-fatal -Wno-BLKSEQ
  -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
)

mkdir -p "$out_dir/obj"
"$verilator_bin" "${verilator_flags[@]}" \
  --top-module a3_exact_scalar_prefix_k2_p6_tb \
  --Mdir "$out_dir/obj" -o sim \
  "${rtl_sources[@]}" "$test_dir/a3_exact_scalar_prefix_k2_p6_tb.sv" \
  >"$out_dir/verilator-build.log" 2>&1
"$out_dir/obj/sim" | tee "$out_dir/rtl-run.log"
grep -q 'A3_EXACT_SCALAR_PREFIX_K2_P6_ALL_PASS' "$out_dir/rtl-run.log"

yosys_sources="${rtl_sources[*]}"
yosys_resolved="$(command -v "$yosys_bin")"
yosys_prefix="$(cd "$(dirname "$yosys_resolved")/.." && pwd)"
yosys_ld_path="${LD_LIBRARY_PATH:-}"
for candidate_lib_dir in "$yosys_prefix"/lib/*-linux-gnu; do
  if [[ -e "$candidate_lib_dir/libtcl8.6.so" ]]; then
    yosys_ld_path="$candidate_lib_dir${yosys_ld_path:+:$yosys_ld_path}"
    break
  fi
done
env LD_LIBRARY_PATH="$yosys_ld_path" \
  "$yosys_bin" -q -l "$out_dir/yosys-loop-check.log" -p \
  "read_verilog -sv -DSYNTHESIS $yosys_sources; \
   hierarchy -check -top a3_exact_scalar_prefix_k2_p6_top; \
   proc; flatten; opt; scc -expect 0; check"
printf '%s\n' 'A3_P6_COMBINATIONAL_LOOP_CHECK_PASS scc=0'

git -C "$project_root" diff --exit-code -- \
  rtl/common tb/clean benchmarks/clean_slate_aer \
  docs/TEAM_COMMON_WORKLOAD_GUIDE.md
printf '%s\n' 'A3_P6_FROZEN_COMMON_PASS'
printf 'A3_P6_ALL_PASS output=%s\n' "$out_dir"
