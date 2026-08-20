#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
python3 -m unittest discover -s tests/redred_uzh_mc_wtb_controls -p 'test_*.py' -v
