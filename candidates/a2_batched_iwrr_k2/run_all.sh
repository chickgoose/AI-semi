#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
A1_REPO="${A2_K2_A1_REPO:-/home/chickgoose/projects/a1}"

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

TMP_ROOT="$(mktemp -d /tmp/a2-batched-iwrr-k2.XXXXXX)"
trap 'rm -rf -- "$TMP_ROOT"' EXIT

cd "$PROJECT_ROOT"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  candidates/a2_batched_iwrr_k2/tests/test_model.py
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/run_model_mutations.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/generate_lockstep_vectors.py" \
  --cycles 20000 --output "$TMP_ROOT/vectors.txt"

"$VERILATOR_BIN" --binary --timing --assert -Wall \
  -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL \
  --Mdir "$TMP_ROOT/obj" -o sim \
  "$ROOT/rtl/a2_batched_iwrr_k2.sv" \
  "$ROOT/tb/a2_batched_iwrr_k2_lockstep_tb.sv" \
  --top-module a2_batched_iwrr_k2_lockstep_tb >"$TMP_ROOT/verilator-build.log" 2>&1
"$TMP_ROOT/obj/sim" "+VECTORS=$TMP_ROOT/vectors.txt"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/run_rtl_mutations.py" \
  --verilator "$VERILATOR_BIN" --vectors "$TMP_ROOT/vectors.txt"

PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/run_frozen_v4_replay.py" \
  --a1-repo "$A1_REPO" --output "$TMP_ROOT/frozen_v4_replay.json"
yosys_args=(--yosys "$YOSYS_BIN" --output "$TMP_ROOT/yosys_proxy.json")
if [[ -n "$YOSYS_LIB" ]]; then yosys_args+=(--library-dir "$YOSYS_LIB"); fi
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/yosys_proxy.py" "${yosys_args[@]}"

verilator_version="$($VERILATOR_BIN --version)"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/tools/make_receipt.py" \
  --replay "$TMP_ROOT/frozen_v4_replay.json" \
  --yosys "$TMP_ROOT/yosys_proxy.json" \
  --verilator-version "$verilator_version" \
  --output "$TMP_ROOT/qualification.json"

cmp "$TMP_ROOT/frozen_v4_replay.json" "$ROOT/results/frozen_v4_replay.json"
cmp "$TMP_ROOT/yosys_proxy.json" "$ROOT/results/yosys_proxy.json"
cmp "$TMP_ROOT/qualification.json" "$ROOT/results/qualification.json"
printf 'A2_BATCHED_IWRR_K2_ALL_PASS candidate=%s\n' "$ROOT"
