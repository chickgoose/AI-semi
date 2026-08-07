#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
out_root="${AER_A7_ADVERSARIAL_OUT:-/tmp/a7-adversarial}"

for implementation in prefix replicated; do
  reference=0
  [[ "$implementation" == replicated ]] && reference=1
  for lanes in 2 4 8; do
    out_dir="$out_root/$implementation-k$lanes"
    mkdir -p "$out_dir"
    verilator --binary --timing -Wno-fatal \
      --top-module a7_backpressure_adversarial_tb \
      -GK="$lanes" -GREFERENCE="$reference" --Mdir "$out_dir/obj" -o test \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv" \
      "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv" \
      "$test_dir/a7_backpressure_adversarial_tb.sv"
    "$out_dir/obj/test" | tee "$out_dir/test.log"
  done
done
