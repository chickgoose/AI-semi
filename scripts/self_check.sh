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
  scripts/drivers/genus.sh
  scripts/drivers/genus_synth.tcl scripts/drivers/extract_genus_metrics.sh
  scripts/config.ppa.sh
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
rg -q 'set parameters \[list \$sources \$addr_w\]' "$PROJECT_ROOT/scripts/drivers/genus_synth.tcl" || {
  printf 'Genus top parameters must use positional source/address values\n' >&2
  exit 1
}
if rg -q 'git -C' "$PROJECT_ROOT/scripts/lib/common.sh"; then
  printf 'manifest commit lookup must support old Git without -C\n' >&2
  exit 1
fi
rg -q 'command=.*-timescale 1ns/1ps' "$PROJECT_ROOT/scripts/run_sim.sh" || {
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
printf 'structural self-check passed\n'
