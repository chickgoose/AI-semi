#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_FILE="$(mktemp /tmp/a2-phase3-cache-reject.XXXXXX.log)"
trap 'rm -f "$LOG_FILE"' EXIT

set +e
A2_PHASE3_SKIP_YOSYS=1 A2_YOSYS=a2-deliberately-missing-yosys \
  "$PROJECT_ROOT/tests/a2/run_phase3_physical_proxy.sh" \
  >"$LOG_FILE" 2>&1
status=$?
set -e

if [[ "$status" -ne 2 ]]; then
  printf 'A2_PHASE3_CACHE_REJECTION_FAIL status=%s\n' "$status" >&2
  exit 1
fi
rg -q 'cached JSON is not self-authenticating' "$LOG_FILE"
printf 'A2_PHASE3_CACHE_REJECTION_PASS status=2\n'
