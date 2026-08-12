#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"

find_tool() {
  local override="$1" name="$2" fallback="$3"
  if [[ -n "$override" ]]; then
    [[ -x "$override" ]] || { printf 'A2_K2_TOOL_MISSING %s=%s\n' "$name" "$override" >&2; return 1; }
    printf '%s\n' "$override"
  elif command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
  elif [[ -x "$fallback" ]]; then
    printf '%s\n' "$fallback"
  else
    printf 'A2_K2_TOOL_MISSING %s\n' "$name" >&2
    return 1
  fi
}

VERILATOR_BIN="$(find_tool "${A2_K2_VERILATOR:-}" verilator /tmp/a7-toolchain/usr/bin/verilator)"
YOSYS_BIN="$(find_tool "${A2_K2_YOSYS:-}" yosys /tmp/a7-toolchain/usr/bin/yosys)"
YOSYS_LIB="${A2_K2_YOSYS_LIB:-}"
if [[ -z "$YOSYS_LIB" && "$YOSYS_BIN" == /tmp/a7-toolchain/* ]]; then
  YOSYS_LIB=/tmp/a7-toolchain/usr/lib/x86_64-linux-gnu
fi

TMP_ROOT="$(mktemp -d /tmp/a2-batched-iwrr-k2-normalized.XXXXXX)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT
cd "$PROJECT_ROOT"

verilator_flags=(--binary --timing --assert -Wall -Wno-fatal
  -Wno-TIMESCALEMOD -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC
  -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-DECLFILENAME -Wno-BLKSEQ)

"$VERILATOR_BIN" "${verilator_flags[@]}" \
  --Mdir "$TMP_ROOT/directed-obj" -o sim \
  -f candidates/a2_batched_iwrr_k2/tb/normalized.f \
  --top-module a2_batched_iwrr_k2_normalized_tb \
  >"$TMP_ROOT/directed-build.log" 2>&1
"$TMP_ROOT/directed-obj/sim"

"$VERILATOR_BIN" "${verilator_flags[@]}" -Wno-SYNCASYNCNET \
  --Mdir "$TMP_ROOT/common-obj" -o sim \
  -GNUM_SOURCES=16 -GADDR_WIDTH=16 -GRETIRE_LANES=2 \
  -f candidates/a2_batched_iwrr_k2/a2_benchmark.f \
  --top-module aer_clean_tb >"$TMP_ROOT/common-build.log" 2>&1

common_tests=(
  basic_single basic_simultaneous basic_backpressure basic_reset_drain
  limit_retrigger limit_backpressure_shock
)
for test_name in "${common_tests[@]}"; do
  "$TMP_ROOT/common-obj/sim" \
    "+CLEAN_TEST=$test_name" "+CANDIDATE=a2-batched-iwrr-k2" \
    "+METRICS=$TMP_ROOT/$test_name.csv" \
    "+EVENT_METRICS=$TMP_ROOT/$test_name.events.csv" \
    +STIM_CYCLES=128 +LOAD_PCT=20 +SEED=17
done

yosys_command='read_verilog -sv candidates/a2_batched_iwrr_k2/rtl/a2_batched_iwrr_k2.sv candidates/a2_batched_iwrr_k2/rtl/a2_k2_ordered_link_adapter.sv candidates/a2_batched_iwrr_k2/rtl/a2_batched_iwrr_k2_normalized.sv; synth -top a2_batched_iwrr_k2_normalized; check; stat'
if [[ -n "$YOSYS_LIB" ]]; then
  LD_LIBRARY_PATH="$YOSYS_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$YOSYS_BIN" -p "$yosys_command" >"$TMP_ROOT/yosys.log" 2>&1
else
  "$YOSYS_BIN" -p "$yosys_command" >"$TMP_ROOT/yosys.log" 2>&1
fi
rg -q 'Found and reported 0 problems' "$TMP_ROOT/yosys.log"

printf 'A2_K2_NORMALIZED_ALL_PASS directed=%d common=%d yosys_check=0\n' \
  1 "${#common_tests[@]}"
