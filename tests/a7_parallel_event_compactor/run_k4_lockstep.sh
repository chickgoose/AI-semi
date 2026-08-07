#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
out_dir="${AER_A7_LOCKSTEP_OUT:-/tmp/a7-k4-lockstep}"
verilator_bin="${AER_VERILATOR:-}"

if [[ -z "$verilator_bin" ]]; then
  verilator_bin="$(command -v verilator || true)"
fi
if [[ -z "$verilator_bin" && -x /tmp/a7-verilator/usr/bin/verilator ]]; then
  verilator_bin=/tmp/a7-verilator/usr/bin/verilator
fi
if [[ -z "$verilator_bin" ]]; then
  printf 'verilator not found; set AER_VERILATOR=/absolute/path/to/verilator\n' >&2
  exit 1
fi

mkdir -p "$out_dir"
"$verilator_bin" --binary --timing -Wno-fatal \
  --top-module a7_k4_lockstep_tb \
  --Mdir "$out_dir/obj" -o lockstep \
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv" \
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv" \
  "$project_root/rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv" \
  "$test_dir/a7_k4_lockstep_tb.sv"
"$out_dir/obj/lockstep" | tee "$out_dir/lockstep.log"
