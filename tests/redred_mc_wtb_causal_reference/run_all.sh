#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo"
python3 -m unittest discover -s tests/redred_mc_wtb_causal_reference -p 'test_*.py' -v
