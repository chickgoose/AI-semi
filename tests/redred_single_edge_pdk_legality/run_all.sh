#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python3 -B "$repo_root/tests/redred_single_edge_pdk_legality/verify_matrix.py"
