#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -B -m unittest -v "$SCRIPT_DIR/test_core_cohort.py"
