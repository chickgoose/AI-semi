#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1

python3 "$SCRIPT_DIR/fovea_a7_reference.py" --active-cycles 24
python3 -m unittest discover -v -s "$SCRIPT_DIR" -p 'test_*.py'
echo "A2_FOVEA_A7_REFERENCE_PASS"
