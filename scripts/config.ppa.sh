#!/usr/bin/env bash
# Frozen exploratory PPA configuration for the 2026-07-31 comparison run.
# The official competition corner remains TBD.

CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$CONFIG_DIR/config.example.sh"

export AER_RUN_ID="${AER_RUN_ID:-ppa-20260731-slow1v0-5ns}"
export AER_BASELINE_NAME="baseline"
export AER_BASELINE_TOP="aer_dut"
export AER_BASELINE_FILELIST="tb/filelists/baseline.f"
export AER_IMPROVED_NAME="improved"
export AER_IMPROVED_TOP="aer_dut"
export AER_IMPROVED_FILELIST="tb/filelists/improved.f"

export AER_NUM_SOURCES="4"
export AER_ADDR_WIDTH="16"
export AER_FIFO_DEPTH="4"
export AER_EVENTS_PER_SOURCE="32"
export AER_CLOCK_PERIOD_NS="5.000"
export AER_CLOCK_PORT="clk"
export AER_RESET_PORT="rst_n"
export AER_SDC="constraints/aer_common.sdc"
export AER_INPUT_DELAY_NS="0.250"
export AER_OUTPUT_DELAY_NS="0.250"
export AER_CLOCK_UNCERTAINTY_NS="0.100"
export AER_LOAD_PF="0.010"
export AER_DRIVER_CELL=""
export AER_CORNER="PVT_0P9V_125C"

# Supply one absolute server path. AER_STD_CELL_ROOT should name the extracted
# .../gsclib045 directory, not the archive and not a copied repository file.
if [[ -z "${AER_LIBRARY_FILE:-}" && -n "${AER_STD_CELL_ROOT:-}" ]]; then
  export AER_LIBRARY_FILE="$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib"
fi
if [[ -z "${AER_LIBRARY_FILE:-}" || "$AER_LIBRARY_FILE" != /* ]]; then
  printf 'config.ppa.sh: set absolute AER_LIBRARY_FILE or AER_STD_CELL_ROOT\n' >&2
  return 2 2>/dev/null || exit 2
fi
if [[ "${AER_LIBRARY_FILE##*/}" != "slow_vdd1v0_basicCells.lib" ]]; then
  printf 'config.ppa.sh: expected slow_vdd1v0_basicCells.lib, got %s\n' "$AER_LIBRARY_FILE" >&2
  return 2 2>/dev/null || exit 2
fi

export AER_POWER_MODE="genus_vectorless"
export AER_POWER_ACTIVITY=""
export AER_BASELINE_POWER_ACTIVITY=""
export AER_IMPROVED_POWER_ACTIVITY=""
export AER_GENUS_ACTIVITY_TCL=""

# VCD generation is prepared for the later activity-based power run, but the
# frozen exploratory Genus run above deliberately does not consume it.
export AER_DUMP_VCD="1"
export AER_ACTIVITY_TEST="backpressure"
