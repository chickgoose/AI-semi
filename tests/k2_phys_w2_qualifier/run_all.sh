#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_root"
python3 -B -m unittest -v \
  tests.k2_phys_w2_qualifier.test_qualify_raw \
  tests.k2_phys_w2_qualifier.test_ganghee_golden
printf 'K2_PHYSICAL_W2_TESTS_PASS\n'
