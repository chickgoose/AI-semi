#!/usr/bin/env bash
# Copy outside version control if it contains server-specific paths.

# The two variants are deliberately driven by the same SDC and power workload.
export AER_BASELINE_NAME="${AER_BASELINE_NAME:-baseline}"
export AER_BASELINE_TOP="${AER_BASELINE_TOP:-aer_dut}"
export AER_BASELINE_FILELIST="${AER_BASELINE_FILELIST:-tb/filelists/baseline.f}"
export AER_IMPROVED_NAME="${AER_IMPROVED_NAME:-improved}"
export AER_IMPROVED_TOP="${AER_IMPROVED_TOP:-aer_dut}"
export AER_IMPROVED_FILELIST="${AER_IMPROVED_FILELIST:-tb/filelists/improved.f}"

export AER_NUM_SOURCES="${AER_NUM_SOURCES:-4}"
export AER_ADDR_WIDTH="${AER_ADDR_WIDTH:-16}"
export AER_FIFO_DEPTH="${AER_FIFO_DEPTH:-4}"
export AER_EVENTS_PER_SOURCE="${AER_EVENTS_PER_SOURCE:-32}"
export AER_SIMULATOR="${AER_SIMULATOR:-}"
export AER_XRUN_BIN="${AER_XRUN_BIN:-xrun}"
export AER_GENUS_BIN="${AER_GENUS_BIN:-genus}"
export AER_DUMP_VCD="${AER_DUMP_VCD:-0}"
export AER_ACTIVITY_TEST="${AER_ACTIVITY_TEST:-backpressure}"

export AER_SDC="${AER_SDC:-constraints/aer_common.sdc}"
export AER_CLOCK_PORT="${AER_CLOCK_PORT:-clk}"
export AER_RESET_PORT="${AER_RESET_PORT:-rst_n}"
export AER_CLOCK_PERIOD_NS="${AER_CLOCK_PERIOD_NS:-5.000}"
export AER_INPUT_DELAY_NS="${AER_INPUT_DELAY_NS:-0.250}"
export AER_OUTPUT_DELAY_NS="${AER_OUTPUT_DELAY_NS:-0.250}"
export AER_CLOCK_UNCERTAINTY_NS="${AER_CLOCK_UNCERTAINTY_NS:-0.100}"
export AER_DRIVER_CELL="${AER_DRIVER_CELL:-}"
export AER_LOAD_PF="${AER_LOAD_PF:-0.010}"
# This is a provisional comparison corner, not the official competition corner.
export AER_CORNER="${AER_CORNER:-gpdk045_slow_vdd1v0}"

# Set AER_STD_CELL_ROOT to the extracted .../gsclib045 directory on the server.
export AER_GPDK_ROOT="${AER_GPDK_ROOT:-}"
export AER_STD_CELL_ROOT="${AER_STD_CELL_ROOT:-}"
export AER_LIBRARY_FILE="${AER_LIBRARY_FILE:-${AER_STD_CELL_ROOT:+$AER_STD_CELL_ROOT/timing/slow_vdd1v0_basicCells.lib}}"
export AER_LEF_FILES="${AER_LEF_FILES:-}"
export AER_QRC_TECH_FILE="${AER_QRC_TECH_FILE:-}"

# Point these at local executable wrappers based on scripts/drivers/stage.example.sh.
# Keep PDK, standard-cell, license and tool-install paths out of this repository.
export AER_SYNTH_DRIVER="${AER_SYNTH_DRIVER:-scripts/drivers/genus.sh}"
export AER_STA_DRIVER="${AER_STA_DRIVER:-}"
export AER_POWER_DRIVER="${AER_POWER_DRIVER:-}"
export AER_POWER_ACTIVITY="${AER_POWER_ACTIVITY:-}"
export AER_BASELINE_POWER_ACTIVITY="${AER_BASELINE_POWER_ACTIVITY:-}"
export AER_IMPROVED_POWER_ACTIVITY="${AER_IMPROVED_POWER_ACTIVITY:-}"
export AER_GENUS_ACTIVITY_TCL="${AER_GENUS_ACTIVITY_TCL:-}"
export AER_POWER_MODE="${AER_POWER_MODE:-genus_vectorless}"
export AER_RESULTS_ROOT="${AER_RESULTS_ROOT:-results/runs}"
