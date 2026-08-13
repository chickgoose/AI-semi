#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"

tests/k2_w2_tops/run_elaboration_lint.sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/k2_w2_tops/test_port_accounting.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/k2_w2_tops/test_loss_evidence.py
printf 'K2_W2_BOUNDARY_COHORTS_PASS count=3 cross_ranking=prohibited\n'
printf 'K2_W2_FUNCTIONAL_LOSS_EVIDENCE_PASS receipt=workspace-diff/non-official ppa=prohibited\n'
printf 'K2_W2_FAIR_TOPS_PASS\n'
