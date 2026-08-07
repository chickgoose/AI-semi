#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
out_root="${AER_A7_RESCUE_EQ_OUT:-/tmp/a7-rescue-equivalence}"

for sources in 16 32 64; do
  for lanes in 2 4; do
    out_dir="$out_root/n${sources}-k${lanes}"
    mkdir -p "$out_dir"
    verilator --binary --timing -Wno-fatal \
      --top-module a7_rescue_equivalence_tb -GN="$sources" -GK="$lanes" \
      --Mdir "$out_dir/obj" -o test \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_prefix_count.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_shared_rank_index_select.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_event_compactor.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv" \
      "$test_dir/a7_rescue_equivalence_tb.sv"
    "$out_dir/obj/test" | tee "$out_dir/test.log"
  done
done
