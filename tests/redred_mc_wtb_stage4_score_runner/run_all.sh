#!/usr/bin/env bash
set -euo pipefail

python3 -m unittest discover \
  -s tests/redred_mc_wtb_stage4_score_runner \
  -p 'test_*.py' \
  -v
