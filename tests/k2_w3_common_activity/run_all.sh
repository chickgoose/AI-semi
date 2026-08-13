#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
python3 -m unittest -v \
  tests.k2_w3_common_activity.test_activity \
  tests.k2_w3_common_activity.test_scale_vcd_timestamps
bash -n physical/k2_w3_common_activity/run_xcelium_activity.sh
bash -n physical/k2_w3_common_activity/run_three_xcelium_activity.sh
python3 -m py_compile physical/k2_w3_common_activity/*.py
git diff --check
