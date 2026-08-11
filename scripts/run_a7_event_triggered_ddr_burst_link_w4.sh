#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
out="${A7_W4_OUT:-/tmp/a7-event-triggered-ddr-burst-link-w4}"
base="${A7_W4_BASE_COMMIT:-31947a7}"
mkdir -p "$out"
cd "$root"

if command -v verilator >/dev/null 2>&1; then verilator_bin="$(command -v verilator)"
else verilator_bin=/tmp/a7-sim-bin/verilator; fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }
if command -v yosys >/dev/null 2>&1; then yosys_bin="$(command -v yosys)"; yosys_lib=""
else yosys_bin=/tmp/a7-yosys/usr/bin/yosys; yosys_lib=/tmp/a7-yosys/usr/lib/x86_64-linux-gnu; fi
[[ -x "$yosys_bin" ]] || { printf 'yosys not found\n' >&2; exit 1; }

"$verilator_bin" --lint-only --timing -Wall -Wno-fatal \
  --top-module a7_event_triggered_ddr_burst_link_w4 \
  rtl/candidates/a7_event_triggered_ddr_burst_link_w4/*.sv

"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME \
  --top-module a7_event_triggered_ddr_burst_link_tb \
  --Mdir "$out/link-obj" -o a7_w4_link \
  -f tb/filelists/a7_event_triggered_ddr_burst_link_w4_unit.f
for ratio in 1 2 4; do
  "$out/link-obj/a7_w4_link" +TEST=normal "+LINK_RATIO=$ratio" \
    | tee "$out/normal-ratio${ratio}.log"
done
"$out/link-obj/a7_w4_link" +TEST=faults +LINK_RATIO=1 | tee "$out/faults.log"
printf 'A7_W4_LEGACY_DIRECTED_CHECKER_OBSERVER_ONLY\n'

"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  --top-module a7_w4_icg_boundary_tb --Mdir "$out/icg-obj" -o a7_w4_icg \
  -f tb/filelists/a7_event_triggered_ddr_burst_link_w4_icg.f
"$out/icg-obj/a7_w4_icg" | tee "$out/icg.log"

for style in 0 1 2; do
  "$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
    -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME \
    --top-module a7_w4_structural_compare_tb -GSTYLE="$style" \
    --Mdir "$out/style${style}-obj" -o a7_w4_style \
    -f tb/filelists/a7_event_triggered_ddr_burst_link_w4_structural.f
  "$out/style${style}-obj/a7_w4_style" | tee "$out/style${style}.log"
done

LD_LIBRARY_PATH="${yosys_lib}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  tests/a7_event_triggered_ddr_burst_link_w4/structural_compare.py \
  --yosys "$yosys_bin" --output "$out/structural.csv" | tee "$out/structural.log"
PYTHONDONTWRITEBYTECODE=1 tests/a7_event_triggered_ddr_burst_link_w4/contract_check.py \
  --structural-csv "$out/structural.csv"
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=tests/a7_event_triggered_ddr_burst_link_w4 \
  python3 -m unittest -v \
  tests/a7_event_triggered_ddr_burst_link_w4/test_strict_protocol_oracle.py
printf 'A7_W4_STRICT_ORACLE_MUTATIONS_PASS mutations=10\n'

git diff --exit-code "$base" -- tb/clean \
  benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor \
  rtl/candidates/a7_event_triggered_ddr_burst_link
printf 'A7_W4_PROTECTED_DIFF_PASS base=%s\n' "$base"
printf 'A7_W4_REGRESSION_PASS output=%s\n' "$out"
