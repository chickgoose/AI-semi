#!/usr/bin/env bash
# Copy outside version control if it contains server-specific paths.

# The two variants are deliberately driven by the same SDC and power workload.
export AER_BASELINE_NAME="baseline"
export AER_BASELINE_TOP="aer_baseline"
export AER_BASELINE_FILELIST="rtl/baseline/files.f"
export AER_IMPROVED_NAME="improved"
export AER_IMPROVED_TOP="aer_improved"
export AER_IMPROVED_FILELIST="rtl/improved/files.f"

export AER_SDC="constraints/aer_common.sdc"
export AER_CLOCK_PORT="clk"
export AER_CLOCK_PERIOD_NS="5.000"
export AER_INPUT_DELAY_NS="0.250"
export AER_OUTPUT_DELAY_NS="0.250"
export AER_CLOCK_UNCERTAINTY_NS="0.100"
export AER_DRIVER_CELL=""
export AER_LOAD_PF="0.010"
export AER_CORNER="REPLACE_WITH_OFFICIAL_CORNER"

# Point these at local executable wrappers based on scripts/drivers/stage.example.sh.
# Keep PDK, standard-cell, license and tool-install paths out of this repository.
export AER_SYNTH_DRIVER=""
export AER_STA_DRIVER=""
export AER_POWER_DRIVER=""
export AER_POWER_ACTIVITY=""
export AER_RESULTS_ROOT="results/runs"
