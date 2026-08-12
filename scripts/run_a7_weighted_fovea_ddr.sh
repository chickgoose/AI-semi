#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
if [[ -n "${A7_W6_BASE_COMMIT:-}" ]]; then
  base="$A7_W6_BASE_COMMIT"
elif git merge-base --is-ancestor 0f2db4b HEAD 2>/dev/null; then
  base=0f2db4b
elif git merge-base --is-ancestor 229df7b HEAD 2>/dev/null; then
  base=229df7b
else
  printf 'cannot resolve W6 protected-diff baseline for this lineage\n' >&2
  exit 1
fi

if [[ -v A7_W6_OUT ]]; then
  out="$A7_W6_OUT"
  [[ ! -e "$out" ]] || {
    printf 'refusing to overwrite A7_W6_OUT=%s\n' "$out" >&2
    exit 1
  }
  mkdir -p "$out"
else
  out="$(mktemp -d /tmp/a7-weighted-fovea-ddr.XXXXXXXX)"
fi
cd "$root"

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
else
  verilator_bin=/tmp/a7-sim-bin/verilator
fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }

canonical_top=a7_weighted_fovea_weight_contract_fixture
mode=UNIT_MODEL_ONLY

scan_diagnostics() {
  local log="$1"
  if rg -n '(^|[[:space:]])(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)' "$log"; then
    printf 'fail-closed diagnostic found in %s\n' "$log" >&2
    exit 1
  fi
}

python3 tests/a7_weighted_fovea_ddr/contract_check.py | tee "$out/contract.log"
rg -Fxq 'A7_W6_COMPOSITION_CONTRACT_PASS' "$out/contract.log" || {
  printf 'missing composition contract PASS sentinel\n' >&2
  exit 1
}

common_sources=(
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv
  rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv
)
test_sources=(
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_weight_contract_fixture.sv
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv
)

verilator_args=(
  --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL
  --top-module a7_weighted_fovea_ddr_tb
  --Mdir "$out/unit-obj" -o a7_w6_unit
  "-DA7_WEIGHTED_FOVEA_MODULE=$canonical_top"
  "${common_sources[@]}"
)
verilator_args+=("${test_sources[@]}")

"$verilator_bin" "${verilator_args[@]}" 2>&1 | tee "$out/unit-build.log"
scan_diagnostics "$out/unit-build.log"
"$out/unit-obj/a7_w6_unit" | tee "$out/unit.log"

sentinels=(
  'A7_W6_WEIGHT_1_5_5_1_PASS rows=10:50:50:10'
  'A7_W6_CONTINUOUS_FULL_CONTENTION_PASS events=120'
  'A7_W6_ONE_EACH_ORDER_PASS events=16'
  'A7_W6_RESET_DRAIN_PASS pre_and_post_epochs_clean'
  'A7_W6_NO_DUP_ORDER_ADDRESS_PASS accepted=140 delivered=140'
  'A7_W6_WEIGHTED_FOVEA_DDR_REGRESSION_PASS'
)
for sentinel in "${sentinels[@]}"; do
  rg -Fxq "$sentinel" "$out/unit.log" || {
    printf 'missing unit PASS sentinel: %s\n' "$sentinel" >&2
    exit 1
  }
done

evidence_sources=(
  docs/research/a7_weighted_fovea_ddr_w6.md
  scripts/run_a7_weighted_fovea_ddr.sh
  tests/a7_weighted_fovea_ddr/contract_check.py
  rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv
  "${common_sources[@]}"
)
evidence_sources+=(tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_weight_contract_fixture.sv)
{
  printf 'registry_schema=a7_w6_weighted_fovea_ddr_v1\n'
  printf 'mode=%s\n' "$mode"
  printf 'canonical_top=%s\n' "$canonical_top"
  printf 'git_head=%s\n' "$(git rev-parse HEAD)"
  printf 'verilator_version=%s\n' "$($verilator_bin --version)"
  sha256sum "$verilator_bin" "${evidence_sources[@]}"
} > "$out/evidence.registry.sha256"

# These paths are outside W6 ownership and must remain byte-identical to base.
git diff --exit-code "$base" -- tb/clean benchmarks/clean_slate_aer \
  rtl/candidates/a7_r1_candidate_endpoint
printf 'A7_W6_PROTECTED_DIFF_PASS base=%s\n' "$base"

{
  printf 'A7_W6_UNIT_MODEL_ONLY_PASS\n'
  printf 'mode=%s\n' "$mode"
  printf 'physical_status=HOLD\n'
  printf 'unit_log_sha256=%s\n' "$(sha256sum "$out/unit.log" | awk '{print $1}')"
  printf 'registry_sha256=%s\n' "$(sha256sum "$out/evidence.registry.sha256" | awk '{print $1}')"
} | tee "$out/final.status"
rg -Fxq 'A7_W6_UNIT_MODEL_ONLY_PASS' "$out/final.status" || {
  printf 'missing final PASS sentinel\n' >&2
  exit 1
}
printf 'A7_W6_UNIT_RUN_PASS mode=%s physical_status=HOLD output=%s\n' "$mode" "$out"
