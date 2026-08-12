#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
verilator_bin="${VERILATOR:-verilator}"
yosys_bin="${YOSYS:-yosys}"
out_dir="${A7_P6_TEST_OUT:-/tmp/a7-p6-exact-pair-endpoint}"
trace_dir="$out_dir/traces"

mkdir -p "$out_dir"
command -v "$verilator_bin" >/dev/null
command -v "$yosys_bin" >/dev/null

rtl_sources=(
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_launch.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_observer.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv"
  "$project_root/rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_parallel_reference.sv"
)
verilator_flags=(
  --binary --timing --assert -Wall -Wno-fatal -Wno-BLKSEQ
  -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL
)

python3 "$project_root/benchmarks/clean_slate_aer/neutrality_self_test.py" |
  tee "$out_dir/neutrality.log"
python3 -m unittest -v \
  tests.a7_p6_exact_pair_endpoint.test_p6_exact_pair_model 2>&1 |
  tee "$out_dir/model-unit.log"
python3 "$test_dir/p6_exact_pair_model.py" self-test |
  tee "$out_dir/model-exhaustive.log"

build_lockstep() {
  local name="$1"
  local define="${2:-}"
  local object_dir="$out_dir/$name-obj"
  mkdir -p "$object_dir"
  local define_args=()
  if [[ -n "$define" ]]; then
    define_args+=("-D$define")
  fi
  "$verilator_bin" "${verilator_flags[@]}" "${define_args[@]}" \
    --top-module a7_p6_exact_pair_lockstep_tb \
    --Mdir "$object_dir" -o lockstep \
    "${rtl_sources[@]}" "$test_dir/a7_p6_exact_pair_lockstep_tb.sv" \
    >"$out_dir/$name-build.log" 2>&1
}

build_lockstep baseline
"$out_dir/baseline-obj/lockstep" | tee "$out_dir/baseline-run.log"
grep -q 'A7_P6_LOCKSTEP_PASS' "$out_dir/baseline-run.log"

mutations=(
  "overflow:A7_P6_MUTATE_OVERFLOW_ACCEPT:A7_P6_OVERFLOW_MUTATION_CAUGHT"
  "stall:A7_P6_MUTATE_READY_DURING_ARM:A7_P6_STALL_MUTATION_CAUGHT"
  "reset:A7_P6_MUTATE_RESET_PHANTOM:A7_P6_RESET_MUTATION_CAUGHT"
  "order:A7_P6_MUTATE_SWAP_PAIR:A7_P6_ORDER_MUTATION_CAUGHT"
)
for mutation in "${mutations[@]}"; do
  IFS=: read -r name define marker <<<"$mutation"
  build_lockstep "$name" "$define"
  if "$out_dir/$name-obj/lockstep" >"$out_dir/$name-run.log" 2>&1; then
    printf 'mutation unexpectedly passed: %s\n' "$name" >&2
    exit 1
  fi
  grep -q "$marker" "$out_dir/$name-run.log"
  printf 'A7_P6_MUTATION_CAUGHT name=%s marker=%s\n' "$name" "$marker"
done

mkdir -p "$trace_dir"
python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --output-dir "$trace_dir" >"$out_dir/generate.log"
python3 "$test_dir/p6_exact_pair_model.py" prepare \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --trace-dir "$trace_dir" --bundle "$out_dir/frozen.bundle" \
  --expected "$out_dir/frozen.expected.json" | tee "$out_dir/prepare.log"

mkdir -p "$out_dir/replay-obj"
"$verilator_bin" "${verilator_flags[@]}" \
  --top-module a7_p6_exact_pair_replay_tb \
  --Mdir "$out_dir/replay-obj" -o replay \
  "${rtl_sources[@]:0:5}" "$test_dir/a7_p6_exact_pair_replay_tb.sv" \
  >"$out_dir/replay-build.log" 2>&1
"$out_dir/replay-obj/replay" \
  +BUNDLE="$out_dir/frozen.bundle" \
  +OBSERVED="$out_dir/frozen.observed.csv" | tee "$out_dir/replay-run.log"
grep -q 'A7_P6_FROZEN_REPLAY_PASS' "$out_dir/replay-run.log"
python3 "$test_dir/p6_exact_pair_model.py" check \
  --expected "$out_dir/frozen.expected.json" \
  --observed "$out_dir/frozen.observed.csv" | tee "$out_dir/replay-check.log"

python3 "$test_dir/structural_compare.py" --yosys "$yosys_bin" \
  --output "$out_dir/structural.csv" --log-dir "$out_dir/yosys-logs" |
  tee "$out_dir/structural.log"

git -C "$project_root" diff --exit-code -- \
  tb/clean benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  docs/TEAM_COMMON_WORKLOAD_GUIDE.md

sha256sum "$out_dir/frozen.expected.json" "$out_dir/frozen.observed.csv" \
  "$out_dir/structural.csv" >"$out_dir/result-sha256.txt"
printf 'A7_P6_ALL_PASS output=%s\n' "$out_dir"
