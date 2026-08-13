#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

check_log="$(mktemp)"
if python3 physical/k2_w2_campaign/launch_campaign.py check --repo-root "$root" \
    >"$check_log" 2>&1; then
  printf 'campaign check unexpectedly reported READY\n' >&2
  exit 1
fi
grep -q '^K2_W2_CAMPAIGN_HOLD ' "$check_log"
python3 -m unittest discover -s tests/k2_w2_campaign -p 'test_*.py' -v

printf 'K2_W2_CAMPAIGN_TESTS_PASS server_executed=false shared_rc=typical\n'
