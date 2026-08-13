#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo"
python3 -B -m unittest -v tests.physical_w2_innovus.test_w2_innovus
printf 'W2_INNOVUS_LOCAL_TESTS_PASS server_run=NOT_RUN\n'
