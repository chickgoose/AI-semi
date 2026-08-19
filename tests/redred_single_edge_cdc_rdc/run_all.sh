#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

canonical="$(python3 -B contracts/redred_single_edge_cdc_rdc/verify_contract.py)"
grep -Fq '"status": "PASS"' <<<"$canonical"
grep -Fq '"repository_commit": "eb298fe1416a4312269a6f9232e1445f8958dda2"' <<<"$canonical"
grep -Fq '"integration_commit": "bfb4b998049bbf9c66c4af9ffabba2c8ff096363"' <<<"$canonical"
grep -Fq '"reset_assertion_precondition": "drain_idle_o == 1"' <<<"$canonical"
grep -Fq 'REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1' <<<"$canonical"
if grep -Fq 'REDRED_SINGLE_EDGE_CDC_RDC_HOLD' <<<"$canonical"; then
  echo 'canonical source set unexpectedly held' >&2
  exit 1
fi

python3 -B -m unittest discover -s tests/redred_single_edge_cdc_rdc -p 'test_*.py' -v
printf '%s\n' 'REDRED_SINGLE_EDGE_CDC_RDC_ALL_PASS source=eb298fe integration=bfb4b99 mutations=22'
