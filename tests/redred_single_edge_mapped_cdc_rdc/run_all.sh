#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

canonical="$(python3 -B contracts/redred_single_edge_mapped_cdc_rdc/verify_contract.py)"
grep -Fq '"status": "DIAGNOSTIC_PASS_RELEASE_HOLD"' <<<"$canonical"
grep -Fq '"mapped_cdc_rdc_diagnostic_status": "PASS"' <<<"$canonical"
grep -Fq '"final_cdc_rdc_gate": "HOLD"' <<<"$canonical"
grep -Fq '"producer_authenticated": false' <<<"$canonical"
grep -Fq 'REDRED_SINGLE_EDGE_MAPPED_CDC_RDC_DIAGNOSTIC_PASS_RELEASE_HOLD' <<<"$canonical"

python3 -B -m unittest discover -s tests/redred_single_edge_mapped_cdc_rdc -p 'test_*.py' -v
printf '%s\n' 'REDRED_SINGLE_EDGE_MAPPED_CDC_RDC_ALL_PASS canonical=1 mutations=14 release=HOLD'
