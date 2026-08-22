#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/redred_mc_wtb_predictor_stage3_dspb -p 'test_*.py' -v
