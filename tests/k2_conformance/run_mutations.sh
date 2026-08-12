#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
iverilog_bin=${K2_IVERILOG:-}
vvp_bin=${K2_VVP:-}
if [[ -z "$iverilog_bin" ]]; then
  iverilog_bin=$(command -v iverilog || true)
  [[ -n "$iverilog_bin" ]] || iverilog_bin=/tmp/a7-toolchain/usr/bin/iverilog
fi
if [[ -z "$vvp_bin" ]]; then
  vvp_bin=$(command -v vvp || true)
  [[ -n "$vvp_bin" ]] || vvp_bin=/tmp/a7-toolchain/usr/bin/vvp
fi
if [[ ! -x "$iverilog_bin" || ! -x "$vvp_bin" ]]; then
  echo "required Icarus tools not found; set K2_IVERILOG and K2_VVP" >&2
  exit 2
fi
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/k2-mutations.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT
killed=0

expect_atomic_failure() {
  local name=$1
  local latency=$2
  local mutation=$3
  local image="$work_dir/$name.vvp"
  local log="$work_dir/$name.log"
  "$iverilog_bin" -g2012 -Wall -I "$script_dir" \
    -DK2_EXPECT_LATENCY="$latency" -D"$mutation" \
    -s k2_atomic_conformance_tb -o "$image" \
    "$script_dir/reference/k2_reference_binding.sv" \
    "$script_dir/k2_conformance_oracle.sv" \
    "$script_dir/k2_atomic_conformance_tb.sv"
  if "$vvp_bin" "$image" >"$log" 2>&1; then
    echo "K2_MUTATION_SURVIVED name=$name mutation=$mutation" >&2
    sed -n '1,120p' "$log" >&2
    exit 1
  fi
  if grep -q "K2_ATOMIC_CONFORMANCE_PASS" "$log"; then
    echo "K2_MUTATION_FALSE_PASS name=$name mutation=$mutation" >&2
    exit 1
  fi
  killed=$((killed + 1))
  echo "K2_MUTATION_KILLED name=$name mutation=$mutation"
}

expect_link_failure() {
  local name=$1
  local mutation=$2
  local image="$work_dir/$name.vvp"
  local log="$work_dir/$name.log"
  "$iverilog_bin" -g2012 -Wall -D"$mutation" \
    -s k2_ordered_link_conformance_tb -o "$image" \
    "$script_dir/k2_ordered_link.sv" \
    "$script_dir/reference/k2_reference_link_binding.sv" \
    "$script_dir/k2_ordered_link_conformance_tb.sv"
  if "$vvp_bin" "$image" >"$log" 2>&1; then
    echo "K2_MUTATION_SURVIVED name=$name mutation=$mutation" >&2
    sed -n '1,120p' "$log" >&2
    exit 1
  fi
  if grep -q "K2_ORDERED_LINK_CONFORMANCE_PASS" "$log"; then
    echo "K2_MUTATION_FALSE_PASS name=$name mutation=$mutation" >&2
    exit 1
  fi
  killed=$((killed + 1))
  echo "K2_MUTATION_KILLED name=$name mutation=$mutation"
}

expect_atomic_failure count0_phantom       0 K2_MUT_BAD_COUNT0
expect_atomic_failure count1_corrupt       0 K2_MUT_BAD_COUNT1
expect_atomic_failure count2_truncate      0 K2_MUT_BAD_COUNT2
expect_atomic_failure count1_uses_lane1    0 K2_TB_MUT_COUNT1_USES_LANE1
expect_atomic_failure count2_partial       1 K2_MUT_PARTIAL_COUNT2
expect_atomic_failure held_offer_reorder   1 K2_MUT_HELD_REORDER
expect_atomic_failure duplicate            0 K2_MUT_DUPLICATE
expect_atomic_failure phantom              0 K2_MUT_PHANTOM
expect_atomic_failure reset_stale          1 K2_MUT_RESET_STALE
expect_atomic_failure exact_latency_shift  1 K2_MUT_LATENCY
expect_atomic_failure refill_bubble        1 K2_MUT_REFILL_BUBBLE

expect_link_failure younger_bypass K2_LINK_MUT_YOUNGER_BYPASS
expect_link_failure missing_compact K2_LINK_MUT_NO_COMPACT
expect_link_failure retire_reorder K2_LINK_MUT_REORDER
expect_link_failure link_refill_bubble K2_LINK_MUT_REFILL_BUBBLE
expect_link_failure link_reset_stale K2_LINK_MUT_RESET_STALE

echo "K2_MUTATION_SUITE_PASS killed=$killed survived=0"
