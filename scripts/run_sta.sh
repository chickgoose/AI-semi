#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 2 ]] || { printf 'usage: %s <baseline> <config.sh>\n' "$0" >&2; exit 2; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/run_stage.sh" sta "$1" "$2"
