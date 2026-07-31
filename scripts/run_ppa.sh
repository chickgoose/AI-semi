#!/usr/bin/env bash
set -euo pipefail

[[ $# -le 1 ]] || { printf 'usage: %s [config.sh]\n' "$0" >&2; exit 2; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/common.sh"
aer_init
config="$(aer_resolve_config "${1:-${AER_CONFIG:-}}")"
source "$config"

export AER_RUN_ID="${AER_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
design="$AER_BASELINE_NAME"
"$SCRIPT_DIR/run_stage.sh" synth "$design" "$config"
[[ -n "${AER_STA_DRIVER:-}" ]] && "$SCRIPT_DIR/run_stage.sh" sta "$design" "$config"
[[ -n "${AER_POWER_DRIVER:-}" ]] && "$SCRIPT_DIR/run_stage.sh" power "$design" "$config"

"$SCRIPT_DIR/summarize_ppa.sh" "$config" "$AER_RUN_ID"
