#!/bin/sh
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover \
  -s "$repo_root/tests/redred_cluster2_cav_polarity_release" \
  -p 'test_*.py' -v
python3 "$repo_root/tests/redred_cluster2_cav_polarity_release/polarity_release_gate.py" \
  --root "$repo_root" --json || test "$?" -eq 2
