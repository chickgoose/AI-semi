#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$test_dir"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v test_gate.py
printf '%s\n' 'A2_MAPPED_XCELIUM_FOCUSED_TESTS_PASS endpoints=3'
