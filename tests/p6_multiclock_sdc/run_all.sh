#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
P6_GANGHEE_GOLDEN_ROOT=${P6_GANGHEE_GOLDEN_ROOT:-/tmp/ganghee-pnr-golden-20260813} \
P6_GANGHEE_GOLDEN_ARCHIVE=${P6_GANGHEE_GOLDEN_ARCHIVE:-/tmp/ganghee-pnr-golden-20260813.tar.gz} \
  python3 -B "$repo_root/tests/p6_multiclock_sdc/test_constraints.py"
