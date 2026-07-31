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
  tb/filelists/improved.f scripts/drivers/genus.sh
  scripts/drivers/genus_synth.tcl scripts/drivers/extract_genus_metrics.sh
  scripts/config.ppa.sh scripts/compare_manifests.sh
  scripts/prepare_power_activity.sh
  constraints/aer_common.sdc results/README.md docs/server-environment.md
  docs/tasks/a3.md
)
for path in "${required[@]}"; do
  [[ -f "$PROJECT_ROOT/$path" ]] || { printf 'missing %s\n' "$path" >&2; exit 1; }
done

if rg -n '\bsequence\b' "$PROJECT_ROOT/tb/aer_tb.sv"; then
  printf 'Xcelium-reserved identifier sequence remains in aer_tb.sv\n' >&2
  exit 1
fi
rg -qx 'rtl/baseline/aer_baseline_core.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"
rg -qx 'rtl/baseline/aer_dut.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"
if rg -q 'aer_baseline_top.sv' "$PROJECT_ROOT/tb/filelists/baseline.f"; then
  printf 'legacy aer_baseline_top.sv remains in comparison file list\n' >&2
  exit 1
fi
rg -qx 'rtl/improved/aer_dut.sv' "$PROJECT_ROOT/tb/filelists/improved.f"
rg -q 'command=.*-timescale 1ns/1ps' "$PROJECT_ROOT/scripts/run_sim.sh" || {
  printf 'Xcelium command must set -timescale 1ns/1ps\n' >&2
  exit 1
}
example_config="$(bash -c 'source "$1"; printf "%s:%s:%s:%s:%s:%s" "$AER_BASELINE_TOP" "$AER_IMPROVED_TOP" "$AER_ADDR_WIDTH" "$AER_CLOCK_PORT" "$AER_RESET_PORT" "$AER_CORNER"' _ "$PROJECT_ROOT/scripts/config.example.sh")"
[[ "$example_config" == "aer_dut:aer_dut:16:clk:rst_n:gpdk045_slow_vdd1v0" ]] || {
  printf 'common config defaults changed: %s\n' "$example_config" >&2
  exit 1
}
frozen="$(AER_LIBRARY_FILE=/tmp/slow_vdd1v0_basicCells.lib bash -c 'source "$1"; printf "%s:%s:%s:%s:%s:%s:%s:%s:%s" "$AER_NUM_SOURCES" "$AER_ADDR_WIDTH" "$AER_FIFO_DEPTH" "$AER_CLOCK_PERIOD_NS" "$AER_CLOCK_PORT" "$AER_RESET_PORT" "$AER_CORNER" "$AER_POWER_MODE" "$AER_RUN_ID"' _ "$PROJECT_ROOT/scripts/config.ppa.sh")"
[[ "$frozen" == "4:16:4:5.000:clk:rst_n:gpdk045_slow_vdd1v0:genus_vectorless:ppa-20260731-slow1v0-5ns" ]] || {
  printf 'frozen PPA configuration changed: %s\n' "$frozen" >&2
  exit 1
}
printf 'structural self-check passed\n'
