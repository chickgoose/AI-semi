#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { printf 'usage: %s <config.sh>\n' "$0" >&2; exit 2; }
config="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$config"

export AER_RUN_ID="${AER_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
for design in "$AER_BASELINE_NAME" "$AER_IMPROVED_NAME"; do
  for stage in synth sta power; do
    "$SCRIPT_DIR/run_stage.sh" "$stage" "$design" "$config"
  done
done

"$SCRIPT_DIR/summarize_ppa.sh" "$config" "$AER_RUN_ID"
