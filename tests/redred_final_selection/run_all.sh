#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo"
PYTHONDONTWRITEBYTECODE=1 python3 contracts/redred_final_selection/verify_contract.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/redred_final_selection -p 'test_*.py' -v
