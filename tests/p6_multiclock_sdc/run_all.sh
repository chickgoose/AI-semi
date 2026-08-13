#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
python3 "$repo_root/tests/p6_multiclock_sdc/test_constraints.py"
