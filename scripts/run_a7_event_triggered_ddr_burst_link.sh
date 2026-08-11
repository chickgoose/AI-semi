#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
out_root="${A7_DDR_OUT:-/tmp/a7-event-triggered-ddr-burst-link}"
build_dir="$out_root/build"
base_commit="${A7_DDR_BASE_COMMIT:-1d2c786}"
cd "$project_root"

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
elif [[ -x /tmp/a7-sim-bin/verilator ]]; then
  verilator_bin=/tmp/a7-sim-bin/verilator
else
  printf 'verilator not found (also checked /tmp/a7-sim-bin/verilator)\n' >&2
  exit 1
fi

mkdir -p "$build_dir"

"$verilator_bin" --lint-only --timing -Wall -Wno-fatal \
  --top-module a7_event_triggered_ddr_burst_link \
  "$project_root/rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_tx.sv" \
  "$project_root/rtl/candidates/a7_event_triggered_ddr_burst_link/a7_ddr_burst_rx.sv" \
  "$project_root/rtl/candidates/a7_event_triggered_ddr_burst_link/a7_event_triggered_ddr_burst_link.sv"

"$verilator_bin" --binary --timing -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL \
  --top-module a7_event_triggered_ddr_burst_link_tb \
  --Mdir "$build_dir/obj" -o a7_ddr_unit \
  -f "$project_root/tb/filelists/a7_event_triggered_ddr_burst_link_unit.f"

for ratio in 1 2 4; do
  "$build_dir/obj/a7_ddr_unit" "+TEST=normal" "+LINK_RATIO=$ratio" \
    | tee "$out_root/normal-ratio${ratio}.log"
  python3 "$project_root/tests/a7_event_triggered_ddr_burst_link/link_metrics.py" \
    --link-ratio "$ratio" > "$out_root/link-metrics-ratio${ratio}.csv"
done

"$build_dir/obj/a7_ddr_unit" +TEST=faults +LINK_RATIO=1 \
  | tee "$out_root/faults.log"

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$project_root/tests/a7_event_triggered_ddr_burst_link" \
  python3 -m unittest -v \
  "$project_root/tests/a7_event_triggered_ddr_burst_link/test_link_metrics.py"

# Protect frozen common scoring inputs and every pre-existing A7 candidate.
git -C "$project_root" diff --exit-code "$base_commit" -- \
  tb/clean \
  benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor

printf 'A7_DDR_PROTECTED_DIFF_PASS base=%s\n' "$base_commit"
printf 'A7_DDR_REGRESSION_PASS output=%s\n' "$out_root"
