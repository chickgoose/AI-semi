#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
preflight="$root/scripts/physical/a7_w4_physical_preflight.py"
template="$root/physical/a7_event_triggered_ddr_burst_link_w4/site_manifest.template.json"
negative_log="${A7_W4_PREFLIGHT_LOG:-/tmp/a7-w4-unfilled-site-preflight.log}"

cd "$root"
"$preflight" --contract-only
if "$preflight" --site-manifest "$template" >"$negative_log" 2>&1; then
  printf 'unfilled site template unexpectedly passed\n' >&2
  exit 1
fi
rg -q 'A7_W4_PHYSICAL_HOLD' "$negative_log"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  tests/a7_event_triggered_ddr_burst_link_w4/test_physical_preflight.py

git diff --exit-code db3f04f -- tb/clean \
  benchmarks/clean_slate_aer/manifest.example.json \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/manifest.smoke.json \
  rtl/candidates/a7_parallel_event_compactor \
  rtl/candidates/a7_event_triggered_ddr_burst_link \
  rtl/candidates/a7_event_triggered_ddr_burst_link_w4
printf 'A7_W4_PHYSICAL_PROTECTED_DIFF_PASS base=db3f04f\n'
printf 'A7_W4_PHYSICAL_PREFLIGHT_REGRESSION_PASS\n'
