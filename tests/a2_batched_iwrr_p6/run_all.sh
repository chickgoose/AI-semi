#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
verilator_bin="${VERILATOR:-verilator}"
out_dir="${A2_P6_TEST_OUT:-/tmp/a2-batched-iwrr-p6}"
obj_dir="$out_dir/obj"
owner_rtl="$project_root/rtl/candidates/a2_batched_iwrr_k2/a2_batched_iwrr_k2.sv"

mkdir -p "$obj_dir"
command -v "$verilator_bin" >/dev/null

owner_sha256="$(sha256sum "$owner_rtl" | awk '{print $1}')"
test "$owner_sha256" = "800d320cdb82a53ce84e4bace69f27a241eef1aaebf447025394574b994a135d"

mapfile -t rtl_sources < <(sed "s#^#$project_root/#" \
  "$project_root/rtl/candidates/a2_batched_iwrr_p6/a2_batched_iwrr_p6.f")

"$verilator_bin" --binary --timing --assert -Wall \
  -Wno-BLKSEQ -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL \
  -Wno-TIMESCALEMOD -Wno-SYNCASYNCNET \
  --top-module a2_batched_iwrr_p6_tb \
  --Mdir "$obj_dir" -o sim_a2_p6 \
  "${rtl_sources[@]}" "$test_dir/a2_batched_iwrr_p6_tb.sv" \
  >"$out_dir/build.log" 2>&1

if grep -Eq '%Warning-(UNOPTFLAT|DIDNOTCONVERGE)' "$out_dir/build.log"; then
  printf '%s\n' 'combinational loop warning in integrated top' >&2
  exit 1
fi

"$obj_dir/sim_a2_p6" | tee "$out_dir/run.log"

for marker in \
  A2_P6_CONTINUOUS_PASS A2_P6_COUNT0_PASS A2_P6_COUNT1_PASS \
  A2_P6_COUNT2_PASS A2_P6_STALL_PASS A2_P6_DRAIN_PASS \
  A2_P6_RESET_PASS A2_P6_ORDER_PASS A2_P6_CONSERVATION_PASS \
  A2_P6_ALL_PASS; do
  grep -q "$marker" "$out_dir/run.log"
done

git -C "$project_root" diff --exit-code -- \
  rtl/common tb/clean constraints physical

printf 'A2_P6_OWNER_PIN_PASS commit=%s blob=%s sha256=%s\n' \
  d74ff962aaf07c5209f1a1d1c69832735c654a0d \
  8ea7be42b4fe4fbcb414ff1947ddeabbcbf9ec85 \
  "$owner_sha256"
printf 'A2_P6_RTL_ALL_PASS output=%s\n' "$out_dir"
