#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
out="${A7_R1_OUT:-/tmp/a7-r1-candidate-endpoint}"
base="${A7_R1_BASE_COMMIT:-ab97aba}"
mkdir -p "$out"
cd "$root"

if command -v verilator >/dev/null 2>&1; then verilator_bin="$(command -v verilator)"
else verilator_bin=/tmp/a7-sim-bin/verilator; fi
if command -v yosys >/dev/null 2>&1; then yosys_bin="$(command -v yosys)"; yosys_lib=""
else yosys_bin=/tmp/a7-yosys/usr/bin/yosys; yosys_lib=/tmp/a7-yosys/usr/lib/x86_64-linux-gnu; fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }
[[ -x "$yosys_bin" ]] || { printf 'yosys not found\n' >&2; exit 1; }

for top in a7_r1_candidate_endpoint a7_r1_parallel_reference_top; do
  "$verilator_bin" --lint-only --timing -Wall -Wno-fatal \
    -Wno-SYNCASYNCNET --top-module "$top" \
    rtl/candidates/a7_r1_candidate_endpoint/*.sv
done

"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL \
  --top-module a7_r1_candidate_endpoint_tb \
  --Mdir "$out/unit-obj" -o a7_r1_unit \
  -f tb/filelists/a7_r1_candidate_endpoint_unit.f
"$out/unit-obj/a7_r1_unit" | tee "$out/unit.log"

LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  tests/a7_r1_candidate_endpoint/structural_compare.py \
  --yosys "$yosys_bin" --output "$out/structural.csv" \
  | tee "$out/structural.log"

git diff --exit-code "$base" -- tb/clean \
  benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor \
  rtl/candidates/a7_event_triggered_ddr_burst_link \
  rtl/candidates/a7_event_triggered_ddr_burst_link_w4
printf 'A7_R1_PROTECTED_DIFF_PASS base=%s\n' "$base"
printf 'A7_R1_DIGITAL_REGRESSION_PASS physical_status=HOLD output=%s\n' "$out"
