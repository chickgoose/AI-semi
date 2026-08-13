#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo"
python3 -B -m unittest -v tests.k2_w2_genus.test_flow
printf 'K2_W2_GENUS_TESTS_PASS\n'
