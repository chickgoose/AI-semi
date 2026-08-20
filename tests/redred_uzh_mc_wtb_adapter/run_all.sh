#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
production_root=${REDRED_ADAPTER_PRODUCTION_ROOT:-}
if [[ -n "$production_root" ]]; then
  export PYTHONPATH="$production_root${PYTHONPATH:+:$PYTHONPATH}"
fi
cd "$repo_root"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s tests/redred_uzh_mc_wtb_adapter -p 'test_*.py' -v
