#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

python3 -B contracts/redred_diagnostic_candidate_selection/verify_contract.py
python3 -B -m unittest -v tests/redred_diagnostic_candidate_selection/test_contract.py
echo "REDRED_DIAGNOSTIC_CANDIDATE_SELECTION_ALL_PASS canonical=1 mutations=9 official=NONE release=HOLD"
