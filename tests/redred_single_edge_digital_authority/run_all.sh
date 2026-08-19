#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

python3 -B contracts/redred_single_edge_digital_authority/verify_contract.py
python3 -B -m unittest -v tests/redred_single_edge_digital_authority/test_contract.py
echo "REDRED_SINGLE_EDGE_DIGITAL_AUTHORITY_ALL_PASS canonical=1 mutations=9 release=HOLD"
