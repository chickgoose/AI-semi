#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_root"
python3 -m unittest -v tests/a6_a234_k2_p6_same_flow/test_runner.py
echo "A6_A234_K2_P6_LOCAL_TEST_PASS"
