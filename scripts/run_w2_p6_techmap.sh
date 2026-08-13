#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
test_dir="$project_root/tests/w2_p6_techmap"
rtl_dir="$project_root/rtl/technology/p6"
out_dir="${W2_P6_TEST_OUT:-/tmp/w2-p6-techmap}"
verilator_bin="${VERILATOR:-verilator}"
yosys_bin="${YOSYS:-yosys}"

mkdir -p "$out_dir"
command -v "$verilator_bin" >/dev/null
command -v "$yosys_bin" >/dev/null

python3 -m unittest -v tests.w2_p6_techmap.test_manifest 2>&1 |
  tee "$out_dir/manifest.log"

owner_sources=(
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_launch.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_observer.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv"
)
tech_sources=(
  "$rtl_dir/w2_p6_clock_boundary.sv"
  "$rtl_dir/w2_p6_mux2.sv"
  "$rtl_dir/w2_p6_posedge_capture.sv"
  "$rtl_dir/w2_p6_negedge_capture.sv"
  "$rtl_dir/w2_p6_pair_tx_tech.sv"
  "$rtl_dir/w2_p6_pair_rx_tech.sv"
  "$rtl_dir/w2_p6_exact_pair_endpoint_tech.sv"
)
verilator_flags=(
  --binary --timing --assert -Wall -Wno-fatal -Wno-BLKSEQ
  -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
  -I"$rtl_dir" --top-module w2_p6_owner_vs_tech_tb
)

run_lockstep() {
  local name="$1"
  shift
  local object_dir="$out_dir/$name-obj"
  mkdir -p "$object_dir"
  "$verilator_bin" "${verilator_flags[@]}" "$@" \
    --Mdir "$object_dir" -o sim \
    "${owner_sources[@]}" "${tech_sources[@]}" \
    "$test_dir/owner_vs_tech_tb.sv" \
    >"$out_dir/$name-build.log" 2>&1
  "$object_dir/sim" | tee "$out_dir/$name-run.log"
  grep -q W2_P6_OWNER_VS_TECH_PASS "$out_dir/$name-run.log"
}

run_lockstep generic -DW2_P6_TECH_GENERIC
run_lockstep gsclib045 -DW2_P6_TECH_GSCLIB045 -DW2_P6_TEST_ONLY \
  "$test_dir/gsclib045_test_models.sv"

# Invalid selection and an unbound production cell selection must fail closed.
if "$verilator_bin" --lint-only --timing -I"$rtl_dir" \
    --top-module w2_p6_exact_pair_endpoint_tech \
    "${owner_sources[@]:0:1}" "${owner_sources[@]:3:1}" "${tech_sources[@]}" \
    >"$out_dir/no-selection.log" 2>&1; then
  echo "missing technology selection unexpectedly passed" >&2
  exit 1
fi
if "$verilator_bin" --lint-only --timing -I"$rtl_dir" \
    -DW2_P6_TECH_GENERIC -DW2_P6_TECH_GSCLIB045 \
    --top-module w2_p6_exact_pair_endpoint_tech \
    "${owner_sources[@]:0:1}" "${owner_sources[@]:3:1}" "${tech_sources[@]}" \
    >"$out_dir/multiple-selection.log" 2>&1; then
  echo "multiple technology selections unexpectedly passed" >&2
  exit 1
fi
if "$verilator_bin" --lint-only --timing -I"$rtl_dir" \
    -DW2_P6_TECH_GSCLIB045 --top-module w2_p6_exact_pair_endpoint_tech \
    "${owner_sources[@]:0:1}" "${owner_sources[@]:3:1}" "${tech_sources[@]}" \
    >"$out_dir/unbound-gsclib045.log" 2>&1; then
  echo "unbound GSCLIB045 selection unexpectedly passed" >&2
  exit 1
fi
printf '%s\n' W2_P6_SELECTION_NEGATIVE_PASS

yosys_resolved="$(command -v "$yosys_bin")"
yosys_prefix="$(cd "$(dirname "$yosys_resolved")/.." && pwd)"
yosys_ld_path="${LD_LIBRARY_PATH:-}"
for candidate_lib_dir in "$yosys_prefix"/lib/*-linux-gnu; do
  if [[ -e "$candidate_lib_dir/libtcl8.6.so" ]]; then
    yosys_ld_path="$candidate_lib_dir${yosys_ld_path:+:$yosys_ld_path}"
    break
  fi
done

read_cmd="read_verilog -sv -I$rtl_dir -DW2_P6_TECH_GSCLIB045"
for source in "${owner_sources[@]:0:1}" "${owner_sources[@]:3:1}" \
              "${tech_sources[@]}"; do
  read_cmd+=" $source"
done
# Treat the guarded test models as black boxes for the structural receipt. This
# preserves the effective leaf-cell multiplicity after flattening without
# presenting behavioral stubs as synthesis implementations.
read_cmd+="; read_verilog -sv -lib -DW2_P6_TEST_ONLY $test_dir/gsclib045_test_models.sv"
env LD_LIBRARY_PATH="$yosys_ld_path" "$yosys_bin" -Q -p \
  "$read_cmd; hierarchy -check -top w2_p6_exact_pair_endpoint_tech; stat; proc; flatten; opt; check -assert; scc -expect 0; stat" \
  >"$out_dir/yosys-gsclib045.log" 2>&1
grep -Eq '^[[:space:]]+TLATNTSCAX2[[:space:]]+1$' "$out_dir/yosys-gsclib045.log"
grep -Eq '^[[:space:]]+MX2X1[[:space:]]+5$' "$out_dir/yosys-gsclib045.log"
grep -Eq '^[[:space:]]+DFFRHQX1[[:space:]]+5$' "$out_dir/yosys-gsclib045.log"
printf '%s\n' W2_P6_STRUCTURAL_PASS

printf 'W2_P6_TECHMAP_ALL_PASS output=%s\n' "$out_dir"
