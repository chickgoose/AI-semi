#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"

find_verilator() {
  if [[ -n "${A2_K2_VERILATOR:-}" ]]; then
    [[ -x "$A2_K2_VERILATOR" ]] || {
      printf 'A2_K2_ADAPTER_TOOL_MISSING verilator=%s\n' "$A2_K2_VERILATOR" >&2
      return 1
    }
    printf '%s\n' "$A2_K2_VERILATOR"
  elif command -v verilator >/dev/null 2>&1; then
    command -v verilator
  elif [[ -x /tmp/a7-toolchain/usr/bin/verilator ]]; then
    printf '%s\n' /tmp/a7-toolchain/usr/bin/verilator
  else
    printf 'A2_K2_ADAPTER_TOOL_MISSING verilator\n' >&2
    return 1
  fi
}

VERILATOR_BIN="$(find_verilator)"
RTL_ROOT="${A2_K2_NORMALIZED_RTL_ROOT:-$ROOT/rtl}"
TMP_ROOT="$(mktemp -d /tmp/a2-k2-adapter-properties.XXXXXX)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

cd "$PROJECT_ROOT"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  candidates/a2_batched_iwrr_k2/tests/test_normalized_adapter.py
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$ROOT/tools/generate_adapter_vectors.py" \
  --random-cycles 12000 --output "$TMP_ROOT/vectors.txt"

"$VERILATOR_BIN" --binary --timing --assert -Wall \
  -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD \
  -Wno-UNOPTFLAT -Wno-DECLFILENAME \
  --Mdir "$TMP_ROOT/obj" -o sim \
  "$RTL_ROOT/a2_batched_iwrr_k2.sv" \
  "$RTL_ROOT/a2_k2_ordered_link_adapter.sv" \
  "$RTL_ROOT/a2_batched_iwrr_k2_normalized.sv" \
  "$ROOT/tb/a2_batched_iwrr_k2_adapter_lockstep_tb.sv" \
  --top-module a2_batched_iwrr_k2_adapter_lockstep_tb \
  >"$TMP_ROOT/verilator-build.log" 2>&1
"$TMP_ROOT/obj/sim" "+VECTORS=$TMP_ROOT/vectors.txt"

PYTHONDONTWRITEBYTECODE=1 python3 \
  "$ROOT/tools/run_adapter_mutations.py" \
  --verilator "$VERILATOR_BIN" --vectors "$TMP_ROOT/vectors.txt" \
  --rtl-root "$RTL_ROOT"

printf 'A2_K2_ADAPTER_PROPERTIES_ALL_PASS candidate=%s\n' "$ROOT"
