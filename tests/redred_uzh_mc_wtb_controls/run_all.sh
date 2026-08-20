#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s tests/redred_uzh_mc_wtb_controls -p 'test_*.py' -v
