#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

canonical="$(python3 -B contracts/redred_single_edge_cdc_rdc/verify_contract.py)"
grep -Fq '"status": "PASS"' <<<"$canonical"
grep -Fq '"repository_commit": "4ce4836fab1309d3468db8e660d2da9af371f784"' <<<"$canonical"
grep -Fq '"reset_assertion_precondition": "drain_idle_o == 1"' <<<"$canonical"
grep -Fq 'REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1' <<<"$canonical"
if grep -Fq 'REDRED_SINGLE_EDGE_CDC_RDC_HOLD' <<<"$canonical"; then
  echo 'canonical source set unexpectedly held' >&2
  exit 1
fi

python3 -B -m unittest discover -s tests/redred_single_edge_cdc_rdc -p 'test_*.py' -v
printf '%s\n' 'REDRED_SINGLE_EDGE_CDC_RDC_ALL_PASS commit=4ce4836 mutations=18'
