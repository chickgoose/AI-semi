#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
source_base=0f2db4b460fab0e45c4c22756209cad400789944
integration_base=229df7b6c838431dbd662f927a230188de1e9d9c
if [[ -n "${A7_W6_BASE_COMMIT:-}" ]]; then
  base="$(git rev-parse --verify "${A7_W6_BASE_COMMIT}^{commit}" 2>/dev/null)" || {
    printf 'invalid W6 protected-diff baseline\n' >&2
    exit 1
  }
  [[ "$base" == "$source_base" || "$base" == "$integration_base" ]] || {
    printf 'W6 protected-diff baseline is not in the frozen allowlist: %s\n' "$base" >&2
    exit 1
  }
  git merge-base --is-ancestor "$base" HEAD 2>/dev/null || {
    printf 'W6 protected-diff baseline is not an ancestor: %s\n' "$base" >&2
    exit 1
  }
elif git merge-base --is-ancestor 0f2db4b HEAD 2>/dev/null; then
  base="$source_base"
elif git merge-base --is-ancestor 229df7b HEAD 2>/dev/null; then
  base="$integration_base"
else
  printf 'cannot resolve W6 protected-diff baseline for this lineage\n' >&2
  exit 1
fi
repo_fixture_dir="$root/tests/a5_fovea_a7_structural/fixtures"
sibling_fixture_dir=/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures
if [[ -n "${A7_W6_CANONICAL_DIR:-}" ]]; then
  fixture_dir="$A7_W6_CANONICAL_DIR"
elif [[ -d "$repo_fixture_dir" ]]; then
  fixture_dir="$repo_fixture_dir"
else
  fixture_dir="$sibling_fixture_dir"
fi

if [[ -v A7_W6_QUAL_OUT ]]; then
  out="$A7_W6_QUAL_OUT"
  [[ ! -e "$out" ]] || {
    printf 'refusing to overwrite A7_W6_QUAL_OUT=%s\n' "$out" >&2
    exit 1
  }
  mkdir -p "$out"
else
  out="$(mktemp -d /tmp/a7-weighted-fovea-ddr-qualification.XXXXXXXX)"
fi
cd "$root"

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
else
  verilator_bin=/tmp/a7-sim-bin/verilator
fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }
if command -v yosys >/dev/null 2>&1; then
  yosys_bin="$(command -v yosys)"
  yosys_lib=""
else
  yosys_bin=/tmp/a7-yosys/usr/bin/yosys
  yosys_lib=/tmp/a7-yosys/usr/lib/x86_64-linux-gnu
fi
[[ -x "$yosys_bin" ]] || { printf 'yosys not found\n' >&2; exit 1; }
verilator_version="$($verilator_bin --version 2>&1)" || {
  printf 'verilator version query failed\n' >&2
  exit 1
}
[[ "$verilator_version" =~ ^Verilator[[:space:]][0-9] ]] || {
  printf 'unexpected verilator identity: %s\n' "$verilator_version" >&2
  exit 1
}
yosys_version="$(LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$yosys_bin" -V 2>&1)" || {
  printf 'yosys version query failed\n' >&2
  exit 1
}
[[ "$yosys_version" =~ ^Yosys[[:space:]][0-9] ]] || {
  printf 'unexpected yosys identity: %s\n' "$yosys_version" >&2
  exit 1
}

arbiter2="$fixture_dir/arbiter2.v"
arbiter4="$fixture_dir/arbiter4_tree.v"
fovea="$fixture_dir/aer_tx16_trad_rowcol_fovea.v"
expected_hashes=(
  '25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684'
  '108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31'
  '353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e'
)
canonical_sources=("$arbiter2" "$arbiter4" "$fovea")
for index in 0 1 2; do
  [[ -r "${canonical_sources[$index]}" ]] || {
    printf 'missing canonical source: %s\n' "${canonical_sources[$index]}" >&2
    exit 1
  }
  actual="$(sha256sum "${canonical_sources[$index]}" | awk '{print $1}')"
  [[ "$actual" == "${expected_hashes[$index]}" ]] || {
    printf 'canonical SHA mismatch file=%s got=%s expected=%s\n' \
      "${canonical_sources[$index]}" "$actual" "${expected_hashes[$index]}" >&2
    exit 1
  }
done
printf '%s\n' "${canonical_sources[@]}" > "$out/canonical-three-file.f"
printf 'A7_W6_CANONICAL_THREE_SHA_PASS\n' | tee "$out/hash.log"

scan_diagnostics() {
  local log="$1"
  if rg -n '(^|[[:space:]])(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)' "$log"; then
    printf 'fail-closed diagnostic found in %s\n' "$log" >&2
    exit 1
  fi
}

python3 tests/a7_weighted_fovea_ddr/contract_check.py | tee "$out/contract.log"
rg -Fxq 'A7_W6_COMPOSITION_CONTRACT_PASS' "$out/contract.log"

common_sources=(
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv
  rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv
  rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv
)
provenance_sources=(
  "${common_sources[@]}"
  docs/research/a7_weighted_fovea_ddr_w6.md
  scripts/run_a7_weighted_fovea_ddr_fault.sh
  scripts/run_a7_weighted_fovea_ddr_qualification.sh
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_fault_tb.sv
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_stale_no_live_fixture.sv
  tb/filelists/a7_weighted_fovea_ddr_fault.f
  tests/a7_weighted_fovea_ddr/contract_check.py
  tests/a7_weighted_fovea_ddr/mutation_gate.py
)
for source in "${provenance_sources[@]}"; do
  git ls-files --error-unmatch "$source" >/dev/null 2>&1 || {
    printf 'SHA-pinned directed input is not tracked: %s\n' "$source" >&2
    exit 1
  }
done
git diff --quiet HEAD -- "${provenance_sources[@]}" || {
  printf 'SHA-pinned directed inputs differ from git HEAD\n' >&2
  exit 1
}
# Verilator flattens the canonical nested arbiter2 grant dependencies into an
# UNOPTFLAT cycle report even though each grant equation is acyclic.  Suppress
# only that named diagnostic; every remaining warning/error stays fail-closed.
"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-UNOPTFLAT \
  --top-module a7_weighted_fovea_ddr_tb \
  --Mdir "$out/unit-obj" -o a7_w6_sha_pinned_directed \
  -DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea \
  "${common_sources[@]}" -f "$out/canonical-three-file.f" \
  tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv \
  2>&1 | tee "$out/build.log"
scan_diagnostics "$out/build.log"
printf 'A7_W6_BASELINE_COMPILE_PASS\n' | tee -a "$out/build.log"
"$out/unit-obj/a7_w6_sha_pinned_directed" | tee "$out/run.log"

sentinels=(
  'A7_W6_WEIGHT_1_5_5_1_PASS rows=10:50:50:10'
  'A7_W6_CONTINUOUS_FULL_CONTENTION_PASS events=120'
  'A7_W6_FULL_CONTENTION_1_PER_CYCLE_PASS intervals=119'
  'A7_W6_ONE_EACH_ORDER_PASS events=16'
  'A7_W6_RESET_DRAIN_PASS pre_and_post_epochs_clean'
  'A7_W6_RESET_R0_R2_TIMELINE_PASS first_accept_cycle=R2'
  'A7_W6_RESET_DISJOINT_EPOCH_PASS P=1,4,9,14 Q=0,5,10,15'
  'A7_W6_SAME_ADDRESS_RETRIGGER_PASS addr=6 events=2'
  'A7_W6_OUTPUT_AVAILABLE_CYCLE1_PASS events=146'
  'A7_W6_CONSUMER_RETIRE_CYCLE2_PASS events=146'
  'A7_W6_DRAIN_GUARDS_PASS live_launch_pending=1'
  'A7_W6_NO_DUP_ORDER_ADDRESS_PASS accepted=146 available=146 retired=146'
  'A7_W6_WEIGHTED_FOVEA_DDR_DIRECTED_RTL_REGRESSION_PASS'
)
for sentinel in "${sentinels[@]}"; do
  rg -Fxq "$sentinel" "$out/run.log" || {
    printf 'missing SHA-pinned directed PASS sentinel: %s\n' "$sentinel" >&2
    exit 1
  }
done

python3 tests/a7_weighted_fovea_ddr/mutation_gate.py \
  --verilator "$verilator_bin" --canonical-dir "$fixture_dir" \
  --output "$out/mutants" | tee "$out/mutation.log"
rg -Fxq 'A7_W6_FIVE_MUTANT_GATE_PASS count=5' "$out/mutation.log" || {
  printf 'missing five-mutant gate PASS sentinel\n' >&2
  exit 1
}

A7_W6_FAULT_OUT="$out/fault-negative" \
  scripts/run_a7_weighted_fovea_ddr_fault.sh | tee "$out/fault-negative.log"
rg -Fq 'A7_W6_STALE_NO_LIVE_EXPECTED_FAIL_PASS' "$out/fault-negative.log" || {
  printf 'missing stale/no-live expected-fail PASS sentinel\n' >&2
  exit 1
}

yosys_sources="${canonical_sources[*]} ${common_sources[*]}"
yosys_netlist="$out/yosys-netlist.json"
LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$yosys_bin" -q -p \
  "read_verilog -sv -DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea $yosys_sources; hierarchy -check -top a7_weighted_fovea_ddr; proc; check -assert; write_json $yosys_netlist" \
  2>&1 | tee "$out/yosys-check.log"
scan_diagnostics "$out/yosys-check.log"
[[ -s "$yosys_netlist" ]] || {
  printf 'yosys did not produce a nonempty structural netlist\n' >&2
  exit 1
}
rg -Fq '"a7_weighted_fovea_ddr"' "$yosys_netlist" || {
  printf 'yosys netlist is missing the composition top\n' >&2
  exit 1
}
printf 'A7_W6_YOSYS_HIERARCHY_CHECK_PASS\n' | tee -a "$out/yosys-check.log"

{
  printf 'registry_schema=a7_w6_sha_pinned_directed_rtl_v2\n'
  printf 'git_head=%s\n' "$(git rev-parse HEAD)"
  printf 'verilator_version=%s\n' "$verilator_version"
  printf 'yosys_version=%s\n' "$yosys_version"
  sha256sum "$verilator_bin" "$yosys_bin" "${canonical_sources[@]}" \
    "${common_sources[@]}" \
    tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_tb.sv \
    tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr_fault_tb.sv \
    tb/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_stale_no_live_fixture.sv \
    tb/filelists/a7_weighted_fovea_ddr_fault.f \
    tests/a7_weighted_fovea_ddr/contract_check.py \
    tests/a7_weighted_fovea_ddr/mutation_gate.py \
    docs/research/a7_weighted_fovea_ddr_w6.md \
    scripts/run_a7_weighted_fovea_ddr_fault.sh \
    scripts/run_a7_weighted_fovea_ddr_qualification.sh
} > "$out/evidence.registry.sha256"

git diff --exit-code "$base" -- tb/clean benchmarks/clean_slate_aer \
  rtl/candidates/a7_r1_candidate_endpoint
printf 'A7_W6_PROTECTED_DIFF_PASS base=%s\n' "$base"
{
  printf 'A7_W6_SHA_PINNED_DIRECTED_RTL_PASS\n'
  printf 'canonical_source_count=3\n'
  printf 'synthesizable_hierarchy_check=PASS\n'
  printf 'physical_status=HOLD\n'
  printf 'run_log_sha256=%s\n' "$(sha256sum "$out/run.log" | awk '{print $1}')"
  printf 'fault_log_sha256=%s\n' "$(sha256sum "$out/fault-negative/run.log" | awk '{print $1}')"
  printf 'yosys_netlist_sha256=%s\n' "$(sha256sum "$yosys_netlist" | awk '{print $1}')"
  printf 'mutation_log_sha256=%s\n' "$(sha256sum "$out/mutation.log" | awk '{print $1}')"
  printf 'registry_sha256=%s\n' "$(sha256sum "$out/evidence.registry.sha256" | awk '{print $1}')"
} | tee "$out/final.status"
rg -Fxq 'A7_W6_SHA_PINNED_DIRECTED_RTL_PASS' "$out/final.status"
printf 'A7_W6_SHA_PINNED_DIRECTED_RUN_PASS physical_status=HOLD output=%s\n' "$out"
