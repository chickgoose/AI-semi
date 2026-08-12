#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
cd "$root"

if [[ -v A7_W7_OUT ]]; then
  out="$A7_W7_OUT"
  [[ ! -e "$out" ]] || { printf 'refusing to overwrite A7_W7_OUT=%s\n' "$out" >&2; exit 1; }
  mkdir -p "$out"
else
  out="$(mktemp -d /tmp/a7-w7-weighted-fovea-ddr.XXXXXXXX)"
fi

repo_fixture_dir="$root/tests/a5_fovea_a7_structural/fixtures"
sibling_fixture_dir=/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures
if [[ -n "${A7_W7_CANONICAL_DIR:-}" ]]; then
  fixture_dir="$A7_W7_CANONICAL_DIR"
elif [[ -d "$repo_fixture_dir" ]]; then
  fixture_dir="$repo_fixture_dir"
else
  fixture_dir="$sibling_fixture_dir"
fi

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
else
  verilator_bin=/tmp/a7-sim-bin/verilator
fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }

canonical=(
  "$fixture_dir/arbiter2.v"
  "$fixture_dir/arbiter4_tree.v"
  "$fixture_dir/aer_tx16_trad_rowcol_fovea.v"
)
expected=(
  25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684
  108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31
  353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e
)
for index in 0 1 2; do
  [[ -r "${canonical[$index]}" ]] || { printf 'missing canonical source: %s\n' "${canonical[$index]}" >&2; exit 1; }
  actual="$(sha256sum "${canonical[$index]}" | awk '{print $1}')"
  [[ "$actual" == "${expected[$index]}" ]] || {
    printf 'canonical SHA mismatch file=%s got=%s expected=%s\n' "${canonical[$index]}" "$actual" "${expected[$index]}" >&2
    exit 1
  }
done

owned_inputs=(
  constraints/a7_weighted_fovea_ddr_w7.sdc
  rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_w7.manifest.json
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_exhaustive_tb.sv
  tb/filelists/a7_weighted_fovea_ddr_w7_exhaustive.f
  tests/a7_weighted_fovea_ddr/submission_contract_check.py
  tests/a7_weighted_fovea_ddr/contract_mutation_gate.py
  tests/a7_weighted_fovea_ddr/validate_submission_evidence.py
  tests/a7_weighted_fovea_ddr/evidence_mutation_gate.py
  scripts/run_a7_weighted_fovea_ddr_w7_submission.sh
  docs/research/a7_weighted_fovea_ddr_w7.md
)
for source in "${owned_inputs[@]}"; do
  git ls-files --error-unmatch "$source" >/dev/null 2>&1 || {
    printf 'W7 execution input is not tracked: %s\n' "$source" >&2
    exit 1
  }
done
git diff --quiet HEAD -- "${owned_inputs[@]}" || {
  printf 'W7 execution inputs differ from git HEAD\n' >&2
  exit 1
}

python3 tests/a7_weighted_fovea_ddr/submission_contract_check.py | tee "$out/contract.log"
rg -Fxq 'A7_W7_SUBMISSION_CONTRACT_PASS scope=always_ready_phase_related_no_queue physical=HOLD' "$out/contract.log"
python3 tests/a7_weighted_fovea_ddr/contract_mutation_gate.py \
  --output "$out/contract-mutants" | tee "$out/contract-mutation.log"
rg -Fxq 'A7_W7_FIVE_CONTRACT_MUTANT_GATE_PASS count=5' "$out/contract-mutation.log"

exhaustive_obj="$out/exhaustive-obj"
"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-UNOPTFLAT \
  --top-module a7_weighted_fovea_ddr_exhaustive_tb \
  --Mdir "$exhaustive_obj" -o a7_w7_exhaustive \
  -DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea \
  -f tb/filelists/a7_weighted_fovea_ddr_w7_exhaustive.f \
  "${canonical[@]}" 2>&1 | tee "$out/exhaustive-build.log"
if rg -n '(^|[[:space:]])(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)' "$out/exhaustive-build.log"; then
  printf 'fail-closed diagnostic found in exhaustive build\n' >&2
  exit 1
fi
events_csv="$out/exhaustive.events.csv"
"$exhaustive_obj/a7_w7_exhaustive" \
  "+A7_W7_EVENTS_CSV=$events_csv" | tee "$out/exhaustive-run.log"
python3 tests/a7_weighted_fovea_ddr/validate_submission_evidence.py \
  --events-csv "$events_csv" --run-log "$out/exhaustive-run.log" \
  | tee "$out/evidence-validation.log"
rg -Fxq 'A7_W7_EVIDENCE_VALIDATION_PASS rows=65535 address_bound=1 exact_sentinel=1' \
  "$out/evidence-validation.log"
python3 tests/a7_weighted_fovea_ddr/evidence_mutation_gate.py \
  --events-csv "$events_csv" --run-log "$out/exhaustive-run.log" \
  --output "$out/evidence-mutants" | tee "$out/evidence-mutation.log"
rg -Fxq 'A7_W7_THREE_EVIDENCE_MUTANT_GATE_PASS count=3' "$out/evidence-mutation.log"

# Reuse the SHA-pinned W6 directed/Yosys/five-RTL-mutant gate unchanged.  Its
# marker remains deliberately directed; W7 does not turn it into a full50 claim.
A7_W6_CANONICAL_DIR="$fixture_dir" A7_W6_QUAL_OUT="$out/w6-directed" \
  scripts/run_a7_weighted_fovea_ddr_qualification.sh | tee "$out/w6-directed.log"
rg -Fq 'A7_W6_SHA_PINNED_DIRECTED_RUN_PASS physical_status=HOLD' "$out/w6-directed.log"

{
  printf 'registry_schema=a7_w7_digital_submission_v1\n'
  printf 'git_head=%s\n' "$(git rev-parse HEAD)"
  printf 'scope=mandatory_address_only_sink_always_ready_phase_related_r1\n'
  printf 'unsupported=output_backpressure,unrelated_clock_cdc,midtraffic_reset\n'
  printf 'queue_depth=0\n'
  printf 'physical_status=HOLD\n'
  sha256sum "$verilator_bin" "${canonical[@]}" "${owned_inputs[@]}"
} > "$out/evidence.registry.sha256"

{
  printf 'A7_W7_DIGITAL_SUBMISSION_PASS\n'
  printf 'canonical_sha_pinned=PASS\n'
  printf 'n16_bitmap_exhaustive=65536\n'
  printf 'rtl_mutants_expected_fail=5\n'
  printf 'contract_mutants_expected_fail=5\n'
  printf 'evidence_mutants_expected_fail=3\n'
  printf 'logical_source_retire_addr_binding=PASS\n'
  printf 'producer_rc0_requires_csv_and_exact_sentinel=PASS\n'
  printf 'output_backpressure=SKIP_UNSUPPORTED\n'
  printf 'unrelated_clock_cdc=SKIP_UNSUPPORTED\n'
  printf 'queue_depth=0\n'
  printf 'physical_status=HOLD\n'
  printf 'registry_sha256=%s\n' "$(sha256sum "$out/evidence.registry.sha256" | awk '{print $1}')"
} | tee "$out/final.status"
rg -Fxq 'A7_W7_DIGITAL_SUBMISSION_PASS' "$out/final.status"
printf 'A7_W7_SUBMISSION_RUN_PASS scope=digital-always-ready physical_status=HOLD output=%s\n' "$out"
