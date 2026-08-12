#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s "$here" -p 'test_*.py' -v
printf 'A5_W7_FOVEA_CLUSTER2_EVALUATOR_TEST_PASS tests=11 mutations=9\n'
