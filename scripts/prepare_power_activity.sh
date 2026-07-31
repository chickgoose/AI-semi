#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { printf 'usage: %s <config.sh>\n' "$0" >&2; exit 2; }
config="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "$config")"
source "$config"

activity_test="${AER_ACTIVITY_TEST:-backpressure}"
sim_root="$(aer_abs_path "${AER_SIM_OUT:-results/sim}")"
output="$sim_root/activity-inputs.tsv"
printf 'design\tworkload\tformat\tpath\tsha256\n' > "$output"
design="$AER_BASELINE_NAME"
activity="$sim_root/$design/$activity_test.vcd"
[[ -s "$activity" ]] || aer_die "missing VCD; run AER_DUMP_VCD=1 scripts/run_sim.sh $design: $activity"
printf '%s\t%s\tVCD\t%s\t%s\n' "$design" "$activity_test" "$activity" \
  "$(sha256sum "$activity" | awk '{print $1}')" >> "$output"
printf 'recorded future activity-based power inputs: %s\n' "$output"
