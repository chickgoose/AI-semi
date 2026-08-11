#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SCRIPT_DIR" \
  python3 -m unittest -v "$SCRIPT_DIR/test_ddr_protocol_oracle.py"
printf 'W4_A8_A7_DDR_ORACLE_MUTATION_PASS\n'
