#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
if [[ -n "${A7_R1_BASE_COMMIT:-}" ]]; then
  base="$A7_R1_BASE_COMMIT"
elif git merge-base --is-ancestor ab97aba HEAD 2>/dev/null; then
  base=ab97aba
elif git merge-base --is-ancestor 95e0ab5 HEAD 2>/dev/null; then
  base=95e0ab5
else
  printf 'cannot resolve protected-diff baseline for this lineage\n' >&2
  exit 1
fi
if [[ -v A7_R1_OUT ]]; then
  out="$A7_R1_OUT"
  [[ ! -e "$out" ]] || {
    printf 'refusing to overwrite A7_R1_OUT=%s\n' "$out" >&2
    exit 1
  }
  mkdir -p "$out"
else
  out="$(mktemp -d /tmp/a7-r1-candidate-endpoint.XXXXXXXX)"
fi
cd "$root"

if command -v verilator >/dev/null 2>&1; then verilator_bin="$(command -v verilator)"
else verilator_bin=/tmp/a7-sim-bin/verilator; fi
if command -v yosys >/dev/null 2>&1; then yosys_bin="$(command -v yosys)"; yosys_lib=""
else yosys_bin=/tmp/a7-yosys/usr/bin/yosys; yosys_lib=/tmp/a7-yosys/usr/lib/x86_64-linux-gnu; fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }
[[ -x "$yosys_bin" ]] || { printf 'yosys not found\n' >&2; exit 1; }

scan_diagnostics() {
  local log="$1"
  if rg -n '(^|[[:space:]])(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)' "$log"; then
    printf 'fail-closed diagnostic found in %s\n' "$log" >&2
    exit 1
  fi
}

evidence_sources=(
  docs/research/a7_r1_candidate_endpoint_contract.md
  scripts/run_a7_r1_candidate_endpoint.sh
  tb/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint_tb.sv
  tb/filelists/a7_r1_candidate_endpoint_unit.f
  tests/a7_r1_candidate_endpoint/structural_compare.py
  rtl/candidates/a7_r1_candidate_endpoint/*.sv
)
{
  printf 'registry_schema=a7_r1_digital_evidence_v1\n'
  printf 'git_head=%s\n' "$(git rev-parse HEAD)"
  printf 'verilator_version=%s\n' "$($verilator_bin --version)"
  printf 'yosys_version=%s\n' "$(LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$yosys_bin" -V)"
  sha256sum "$verilator_bin" "$yosys_bin"
  sha256sum "${evidence_sources[@]}"
} > "$out/evidence.registry.sha256"

for top in a7_r1_candidate_endpoint a7_r1_parallel_reference_top; do
  "$verilator_bin" --lint-only --timing -Wall -Wno-fatal \
    -Wno-SYNCASYNCNET --top-module "$top" \
    rtl/candidates/a7_r1_candidate_endpoint/*.sv \
    2>&1 | tee "$out/lint-${top}.log"
  scan_diagnostics "$out/lint-${top}.log"
done

"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL \
  --top-module a7_r1_candidate_endpoint_tb \
  --Mdir "$out/unit-obj" -o a7_r1_unit \
  -f tb/filelists/a7_r1_candidate_endpoint_unit.f \
  2>&1 | tee "$out/unit-build.log"
scan_diagnostics "$out/unit-build.log"
"$out/unit-obj/a7_r1_unit" | tee "$out/unit.log"
rg -Fxq 'A7_R1_ENDPOINT_REGRESSION_PASS' "$out/unit.log" || {
  printf 'missing unit PASS sentinel\n' >&2
  exit 1
}

LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  tests/a7_r1_candidate_endpoint/structural_compare.py \
  --yosys "$yosys_bin" --output "$out/structural.csv" \
  --log-dir "$out/yosys-logs" \
  | tee "$out/structural.log"
for log in "$out"/yosys-logs/*.log; do scan_diagnostics "$log"; done
rg -Fxq 'A7_R1_STRUCTURAL_CONTRACT_PASS' "$out/structural.log" || {
  printf 'missing structural PASS sentinel\n' >&2
  exit 1
}

git diff --exit-code "$base" -- tb/clean \
  benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor \
  rtl/candidates/a7_event_triggered_ddr_burst_link \
  rtl/candidates/a7_event_triggered_ddr_burst_link_w4
printf 'A7_R1_PROTECTED_DIFF_PASS base=%s\n' "$base"
{
  printf 'A7_R1_HARDENED_EVIDENCE_PASS\n'
  printf 'physical_status=HOLD\n'
  printf 'registry_sha256=%s\n' "$(sha256sum "$out/evidence.registry.sha256" | awk '{print $1}')"
  printf 'unit_log_sha256=%s\n' "$(sha256sum "$out/unit.log" | awk '{print $1}')"
  printf 'structural_csv_sha256=%s\n' "$(sha256sum "$out/structural.csv" | awk '{print $1}')"
} | tee "$out/final.status"
rg -Fxq 'A7_R1_HARDENED_EVIDENCE_PASS' "$out/final.status" || {
  printf 'missing final PASS sentinel\n' >&2
  exit 1
}
printf 'A7_R1_DIGITAL_REGRESSION_PASS physical_status=HOLD output=%s\n' "$out"
