#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
yosys=${YOSYS:-/tmp/a7-toolchain/usr/bin/yosys}
out=${A7_K2_COST_TEST_OUT:-/tmp/a7-k2-cost-closure-test}

test ! -e "$out"
mkdir -p "$out"
python3 -m unittest -v tests.a7_k2_cost_closure.test_cost_closure
python3 "$root/audits/a7_k2_cost_closure/build_integration_receipts.py" \
  --yosys "$yosys" --output-dir "$out/rebuilt"
cmp "$root/audits/a7_k2_cost_closure/receipts/a2_p6_integration.json" \
    "$out/rebuilt/a2_p6_integration.json"
cmp "$root/audits/a7_k2_cost_closure/receipts/a3_p6_integration.json" \
    "$out/rebuilt/a3_p6_integration.json"
cmp "$root/audits/a7_k2_cost_closure/receipts/p6_endpoint.json" \
    "$out/rebuilt/p6_endpoint.json"
python3 "$root/audits/a7_k2_cost_closure/generate_report.py" \
  --output "$out/report.json"
cmp "$root/audits/a7_k2_cost_closure/result.json" "$out/report.json"
grep -Fq '"status": "STRUCTURAL_PROXY_COMPLETE_PHYSICAL_HOLD"' "$out/report.json"
printf '%s\n' 'A7_K2_COST_CLOSURE_TEST_PASS receipts=5 physical=HOLD'
