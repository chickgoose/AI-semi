#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
python3 -m unittest discover -s tests/k2_w2_5ns_common_activity -p 'test_*.py' -v
