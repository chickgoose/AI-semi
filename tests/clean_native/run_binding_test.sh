#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERILATOR_BIN="${VERILATOR:-verilator}"
OUT_DIR="${AER_NATIVE_BINDING_TEST_OUT:-/tmp/aer-ganghee-native-binding-test}"

command -v "$VERILATOR_BIN" >/dev/null 2>&1 || {
  printf 'verilator not found: %s\n' "$VERILATOR_BIN" >&2
  exit 1
}

mkdir -p "$OUT_DIR"
"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ \
  -DAER_GANGHEE_NATIVE_MODULE=ganghee_native_protocol_mock \
  --top-module aer_ganghee_native_binding_tb \
  --Mdir "$OUT_DIR/obj" \
  "$PROJECT_ROOT/tb/clean/aer_bench_if.sv" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_native_protocol_mock.sv" \
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_native_binding.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_native_binding_tb.sv"

"$OUT_DIR/obj/Vaer_ganghee_native_binding_tb" | tee "$OUT_DIR/run.log"
grep -q \
  'GANGHEE_NATIVE_BINDING_PASS issued=5 acknowledgements=5 native_results=5 masked_sampling_edges=5 duplicates=0' \
  "$OUT_DIR/run.log"
