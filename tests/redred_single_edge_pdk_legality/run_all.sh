#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python3 -B "$repo_root/tests/redred_single_edge_pdk_legality/verify_matrix.py"
python3 -B -m unittest -v \
  "$repo_root/tests/redred_single_edge_pdk_legality/test_verify_matrix.py"
