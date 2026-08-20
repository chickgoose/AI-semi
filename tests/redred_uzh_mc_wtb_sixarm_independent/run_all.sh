#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
production_root=${REDRED_SIXARM_PRODUCTION_ROOT:?set REDRED_SIXARM_PRODUCTION_ROOT to the implementation worktree}
export PYTHONPATH="$production_root${PYTHONPATH:+:$PYTHONPATH}"
cd "$repo_root"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s tests/redred_uzh_mc_wtb_sixarm_independent \
  -p 'test_sixarm_independent.py' -v
