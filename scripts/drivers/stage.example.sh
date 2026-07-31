#!/usr/bin/env bash
set -euo pipefail

# Copy this outside the repository and adapt it to the official server flow.
# Inputs are exported by run_stage.sh:
# AER_DESIGN, AER_STAGE, AER_TOP, AER_RTL_FILELIST, AER_SDC, AER_OUTPUT_DIR,
# AER_CORNER, AER_CLOCK_PERIOD_NS and (for power) AER_POWER_ACTIVITY.
required=(AER_DESIGN AER_STAGE AER_TOP AER_RTL_FILELIST AER_SDC AER_OUTPUT_DIR AER_CORNER)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { printf 'missing %s\n' "$name" >&2; exit 2; }
done

printf 'Replace scripts/drivers/stage.example.sh with an official tool wrapper.\n' >&2
printf 'The wrapper must write a tab-separated %s/metrics.tsv.\n' "$AER_OUTPUT_DIR" >&2
exit 2
