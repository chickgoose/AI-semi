#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d /tmp/a8-w5-bound.XXXXXX)"
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
else
  verilator_bin=/tmp/a7-sim-bin/verilator
fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }

python3 "$script_dir/prepare_bound_snapshot.py" \
  --binding "$script_dir/a7_w5_binding.json" --output-dir "$work/owner"
mapfile -t owner_sources < "$work/owner/bound_sources.list"
"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET --top-module a8_w5_bound_endpoint_tb \
  --Mdir "$work/obj" -o a8_w5_bound_endpoint \
  "${owner_sources[@]}" "$script_dir/a8_w5_bound_endpoint_tb.sv"
"$work/obj/a8_w5_bound_endpoint"
printf 'W5_A8_EXACT_SHA_DIRECT_NATIVE_PASS\n'
