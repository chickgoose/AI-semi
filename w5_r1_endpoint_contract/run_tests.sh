#!/usr/bin/env bash
set -euo pipefail

W5_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$W5_DIR/.." && pwd)"
BASE_COMMIT="${W5_PROTECTED_BASE:-8ecab30cff51bc07b4670fdd135861a52458add0}"

PYTHONDONTWRITEBYTECODE=1 python3 -B "$W5_DIR/contract_model.py"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$W5_DIR" \
  python3 -B -m unittest discover -s "$W5_DIR/tests" -v

git -C "$PROJECT_ROOT" diff --exit-code "$BASE_COMMIT" -- \
  tb/clean \
  benchmarks/clean_slate_aer \
  rtl/candidates

printf 'W5_R1_ENDPOINT_CONTRACT_SELFTEST_PASS scope=model-and-mutation-only qualification=NONE\n'
