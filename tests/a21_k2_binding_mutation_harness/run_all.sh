#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

python3 -B -m unittest -v test_mutation_harness.py
python3 -B run_mutation_suite.py

echo "A21_K2_BINDING_HARNESS_ALL_PASS"
