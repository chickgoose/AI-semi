#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$SCRIPT_DIR" -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 "$SCRIPT_DIR/run_mutation_suite.py"
printf '%s\n' 'A5_K2_COMMON_EVALUATOR_SELF_TEST_PASS no_owner_rtl_claim=1'
