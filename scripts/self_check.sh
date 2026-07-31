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
[[ "$(bash -c 'source "$1"; printf "%s:%s" "$AER_BASELINE_TOP" "$AER_IMPROVED_TOP"' _ "$PROJECT_ROOT/scripts/config.example.sh")" == "aer_dut:aer_dut" ]] || {
  printf 'baseline/improved synthesis tops must both be aer_dut\n' >&2
  exit 1
}
printf 'structural self-check passed\n'
