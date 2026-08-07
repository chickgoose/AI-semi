#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
simulator="${AER_SIMULATOR:-verilator}"
out_root="${AER_A7_UNIT_OUT:-/tmp/a7-parallel-event-compactor-unit}"

for lanes in 1 2 4; do
  out_dir="$out_root/k$lanes"
  mkdir -p "$out_dir"
  case "$simulator" in
    verilator)
      verilator --binary --timing -Wno-fatal \
        --top-module a7_parallel_event_compactor_tb -GK="$lanes" \
        --Mdir "$out_dir/obj" -o unit \
        "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv" \
        "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv" \
        "$test_dir/a7_parallel_event_compactor_tb.sv"
      "$out_dir/obj/unit" | tee "$out_dir/unit.log"
      ;;
    *) printf 'unit runner currently requires verilator\n' >&2; exit 2 ;;
  esac
done
