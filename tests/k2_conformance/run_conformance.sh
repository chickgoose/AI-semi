#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for latency in 0 1; do
  "$script_dir/run_candidate.sh" \
    --binding "$script_dir/reference/k2_reference_binding.sv" \
    --latency "$latency"
done

"$script_dir/run_candidate.sh" \
  --binding "$script_dir/reference/k2_reference_binding.sv" \
  --latency 0 \
  --link-rtl "$script_dir/k2_ordered_link.sv" \
  --link-binding "$script_dir/reference/k2_reference_link_binding.sv"

echo "K2_CONFORMANCE_SELF_TEST_PASS latencies=0,1 ordered_link=1"
