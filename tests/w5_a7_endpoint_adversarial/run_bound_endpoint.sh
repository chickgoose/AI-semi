#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-normal}"
[[ "$mode" == "normal" || "$mode" == "--latency-mutant" ]] || {
  printf 'usage: %s [--latency-mutant]\n' "$0" >&2
  exit 2
}
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
if [[ "$mode" == "--latency-mutant" ]]; then
  python3 "$script_dir/mutate_plus3_latency.py" \
    "$work/owner/rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv"
fi
mapfile -t owner_sources < "$work/owner/bound_sources.list"
"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET --top-module a8_w5_bound_endpoint_tb \
  --Mdir "$work/obj" -o a8_w5_bound_endpoint \
  "${owner_sources[@]}" "$script_dir/a8_w5_bound_endpoint_tb.sv"
if [[ "$mode" == "normal" ]]; then
  "$work/obj/a8_w5_bound_endpoint"
  printf 'W5_A8_EXACT_SHA_DIRECT_NATIVE_PASS\n'
else
  set +e
  "$work/obj/a8_w5_bound_endpoint" >"$work/mutant.log" 2>&1
  mutant_rc=$?
  set -e
  if [[ $mutant_rc -eq 0 ]]; then
    printf 'plus3 latency mutant unexpectedly passed\n' >&2
    cat "$work/mutant.log" >&2
    exit 1
  fi
  if ! grep -Eq 'availability latency mismatch|sink latency mismatch' "$work/mutant.log"; then
    printf 'plus3 latency mutant failed for an unrelated reason\n' >&2
    cat "$work/mutant.log" >&2
    exit 1
  fi
  printf 'W5_A8_PLUS3_LATENCY_MUTANT_REJECTED rc=%d\n' "$mutant_rc"
fi
