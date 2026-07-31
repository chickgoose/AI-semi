#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 3 ]] || { printf 'usage: %s <synth|sta|power> <design> <config>\n' "$0" >&2; exit 2; }
stage="$1"
design="$2"
config="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
source "$config"

case "$design" in
  "$AER_BASELINE_NAME") AER_TOP="$AER_BASELINE_TOP"; AER_RTL_FILELIST="$AER_BASELINE_FILELIST" ;;
  "$AER_IMPROVED_NAME") AER_TOP="$AER_IMPROVED_TOP"; AER_RTL_FILELIST="$AER_IMPROVED_FILELIST" ;;
  *) aer_die "unknown design '$design'" ;;
esac

case "$stage" in
  synth) driver="${AER_SYNTH_DRIVER:-}" ;;
  sta) driver="${AER_STA_DRIVER:-}" ;;
  power) driver="${AER_POWER_DRIVER:-}" ;;
  *) aer_die "unknown stage '$stage'" ;;
esac

aer_require_var AER_RUN_ID
aer_require_var AER_SDC
aer_require_var AER_CORNER
aer_require_var AER_CLOCK_PERIOD_NS
[[ -n "$driver" ]] || aer_die "AER_${stage^^}_DRIVER is not configured"

AER_DESIGN="$design"
AER_STAGE="$stage"
AER_OUTPUT_DIR="$(aer_abs_path "${AER_RESULTS_ROOT:-results/runs}/$AER_RUN_ID/$design/$stage")"
AER_RTL_FILELIST="$(aer_abs_path "$AER_RTL_FILELIST")"
AER_SDC="$(aer_abs_path "$AER_SDC")"
driver="$(aer_abs_path "$driver")"
AER_DRIVER="$driver"
export AER_DESIGN AER_STAGE AER_TOP AER_RTL_FILELIST AER_SDC AER_OUTPUT_DIR AER_DRIVER
export AER_CLOCK_PORT AER_CLOCK_PERIOD_NS AER_INPUT_DELAY_NS
export AER_OUTPUT_DELAY_NS AER_CLOCK_UNCERTAINTY_NS AER_DRIVER_CELL
export AER_LOAD_PF AER_CORNER AER_POWER_ACTIVITY

mkdir -p "$AER_OUTPUT_DIR"
aer_record_manifest "$AER_OUTPUT_DIR/manifest.txt"
"$driver" 2>&1 | tee "$AER_OUTPUT_DIR/tool.log"
[[ -f "$AER_OUTPUT_DIR/metrics.tsv" ]] || aer_die "$stage driver did not create $AER_OUTPUT_DIR/metrics.tsv"
