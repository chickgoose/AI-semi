#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for script in "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/lib/*.sh "$SCRIPT_DIR"/drivers/*.sh; do
  bash -n "$script"
done

required=(
  tb/aer_if.sv tb/dut_adapter.sv tb/aer_protocol_assertions.sv
  tb/aer_scoreboard.sv tb/aer_tb.sv tb/filelists/baseline.f
  tb/filelists/a23_ee430.f
  tests/a23/a23_ee430_stream_tb.sv
  tests/a23/a23_ee430_contention_tb.sv
  scripts/run_a23_ee430_checks.sh
  docs/experiments/a23-ee430-core.md
  scripts/drivers/genus.sh
  scripts/drivers/genus_synth.tcl scripts/drivers/extract_genus_metrics.sh
  scripts/config.ppa.sh
  scripts/prepare_power_activity.sh
  constraints/aer_common.sdc results/README.md docs/server-environment.md
  docs/tasks/a3.md
  docs/verification/aer-clean-benchmark-spec.md
  docs/verification/aer-clean-benchmark-results.md
  docs/verification/aer-trace-loader.md
  docs/verification/aer-physical-ppa-contract.md
  docs/verification/aer-address-only-full-link-qualification.md
  docs/verification/aer-native-capability-profile.md
  docs/verification/aer-ganghee-native-binding.md
  benchmarks/clean_slate_aer/prepare_sv_trace.py
  benchmarks/clean_slate_aer/capabilities.py
  benchmarks/clean_slate_aer/manifest.smoke.json
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json
  benchmarks/clean_slate_aer/neutrality_self_test.py
  benchmarks/clean_slate_aer/phase_metrics.py
  benchmarks/clean_slate_aer/timing_pair_metrics.py
  benchmarks/clean_slate_aer/fixtures/neutrality_n16_golden.json
  benchmarks/clean_slate_aer/fixtures/capability_profile_ganghee_trad_rowcol_fovea.json
  benchmarks/clean_slate_aer/fixtures/capability_profile_baseline.json
  benchmarks/clean_slate_aer/fixtures/capability_profile_a23_ee430.json
  benchmarks/physical_ppa/bracket_fmax.py
  benchmarks/physical_ppa/full_link_qualification.schema.json
  benchmarks/physical_ppa/validate_full_link_qualification.py
  benchmarks/physical_ppa/fixtures/ganghee_fixed_netlist_example.csv
  tb/clean/aer_bench_if.sv
  tb/clean/aer_clean_mock_candidate.sv
  tb/clean/aer_legacy_candidate_adapter.sv
  tb/clean/aer_clean_assertions.sv
  tb/clean/aer_clean_tb.sv
  tb/clean/native/aer_ganghee_native_binding.sv
  tb/clean/files.f
  scripts/run_clean_benchmark.sh
  scripts/run_ganghee_native_benchmark.sh
  tests/clean_native/run_binding_test.sh
)
for path in "${required[@]}"; do
  [[ -f "$PROJECT_ROOT/$path" ]] || { printf 'missing %s\n' "$path" >&2; exit 1; }
done

if grep -En '\bsequence\b' "$PROJECT_ROOT/tb/aer_tb.sv"; then
  printf 'Xcelium-reserved identifier sequence remains in aer_tb.sv\n' >&2
  exit 1
fi
grep -Eqx 'rtl/baseline/aer_baseline_core.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"
grep -Eqx 'rtl/baseline/aer_dut.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"
grep -Eqx 'rtl/experiments/a23_ee430/a23_ee430_dut.sv' \
  "$PROJECT_ROOT/tb/filelists/a23_ee430.f"
grep -Eqx 'rtl/baseline/aer_rx.sv' "$PROJECT_ROOT/tb/filelists/a23_ee430.f"
if grep -ERn 'aer_sync_fifo|FIFO_DEPTH|occupancy|quota_q|aging_q' \
  "$PROJECT_ROOT/rtl/experiments/a23_ee430"; then
  printf 'forbidden buffering or scheduling state found in A23 experiment\n' >&2
  exit 1
fi
if grep -Eq 'aer_baseline_top.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"; then
  printf 'legacy aer_baseline_top.sv remains in comparison file list\n' >&2
  exit 1
fi
grep -Eq 'set parameters \[list \$sources \$addr_w\]' "$PROJECT_ROOT/scripts/drivers/genus_synth.tcl" || {
  printf 'Genus top parameters must use positional source/address values\n' >&2
  exit 1
}
if grep -Eq 'git -C' "$PROJECT_ROOT/scripts/lib/common.sh"; then
  printf 'manifest commit lookup must support old Git without -C\n' >&2
  exit 1
fi
grep -Eq 'command=.*-timescale 1ns/1ps' "$PROJECT_ROOT/scripts/run_sim.sh" || {
  printf 'Xcelium command must set -timescale 1ns/1ps\n' >&2
  exit 1
}
example_config="$(bash -c 'source "$1"; printf "%s:%s:%s:%s:%s" "$AER_BASELINE_TOP" "$AER_ADDR_WIDTH" "$AER_CLOCK_PORT" "$AER_RESET_PORT" "$AER_CORNER"' _ "$PROJECT_ROOT/scripts/config.example.sh")"
[[ "$example_config" == "aer_dut:16:clk:rst_n:PVT_0P9V_125C" ]] || {
  printf 'common config defaults changed: %s\n' "$example_config" >&2
  exit 1
}
frozen="$(AER_LIBRARY_FILE=/tmp/slow_vdd1v0_basicCells.lib bash -c 'source "$1"; printf "%s:%s:%s:%s:%s:%s:%s:%s:%s" "$AER_NUM_SOURCES" "$AER_ADDR_WIDTH" "$AER_FIFO_DEPTH" "$AER_CLOCK_PERIOD_NS" "$AER_CLOCK_PORT" "$AER_RESET_PORT" "$AER_CORNER" "$AER_POWER_MODE" "$AER_RUN_ID"' _ "$PROJECT_ROOT/scripts/config.ppa.sh")"
[[ "$frozen" == "4:16:4:5.000:clk:rst_n:PVT_0P9V_125C:genus_vectorless:ppa-20260801-pvt0p9v125c-5ns" ]] || {
  printf 'frozen PPA configuration changed: %s\n' "$frozen" >&2
  exit 1
}
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$PROJECT_ROOT/benchmarks/clean_slate_aer/self_test.py"
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$PROJECT_ROOT/benchmarks/clean_slate_aer/neutrality_self_test.py"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s "$PROJECT_ROOT/benchmarks/clean_slate_aer/tests"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s "$PROJECT_ROOT/benchmarks/physical_ppa/tests"
printf 'structural self-check passed\n'
