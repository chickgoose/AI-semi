#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

python3 -m unittest discover -s tests/k2_single_edge_endpoint -p 'test_*.py' -v
python3 physical/k2_single_edge_endpoint/flow.py static
python3 physical/k2_single_edge_endpoint/flow.py compatibility
