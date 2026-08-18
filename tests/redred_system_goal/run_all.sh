#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1

python3 contracts/redred_system_goal/verify_contract.py
python3 -m unittest discover -s tests/redred_system_goal -p 'test_*.py' -v
