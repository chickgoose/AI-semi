#!/usr/bin/env bash
# Candidate-private phase-2 shortlist configuration for head-controlled Genus.
A2_CONFIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A2_PROJECT_ROOT="$(cd "$A2_CONFIG_DIR/../../.." && pwd)"
source "$A2_PROJECT_ROOT/scripts/config.example.sh"

export AER_BASELINE_NAME="a2-phase2-b4-d16-e4-x0-q1"
export AER_BASELINE_TOP="a2_phase2_selected_core"
export AER_BASELINE_FILELIST="rtl/candidates/a2_adaptive_dual_path/a2_phase2_selected_synth.f"
export AER_NUM_SOURCES="${AER_NUM_SOURCES:-16}"
export AER_ADDR_WIDTH="16"
export AER_CLOCK_PORT="clk_i"
export AER_RESET_PORT="rst_ni"
export AER_CLOCK_PERIOD_NS="${AER_CLOCK_PERIOD_NS:-5.000}"
export AER_SDC="constraints/aer_common.sdc"
export AER_POWER_MODE="genus_vectorless"
export AER_GENUS_ACTIVITY_TCL=""
export AER_RESULTS_ROOT="${AER_RESULTS_ROOT:-results/a2-head-ppa-phase2}"
