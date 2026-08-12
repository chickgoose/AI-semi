#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

python3 -m unittest -v test_contracts.py
python3 mutation_gate.py
python3 binding_adapter.py

echo "W8_A8_K2_CONTRACT_SUITE_PASS"
