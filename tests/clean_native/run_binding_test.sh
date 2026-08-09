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

# Negative control: a native DUT that repeats a completed address must trip the
# binding's unmasked duplicate/phantom check and return a failing process status.
"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ \
  -DAER_GANGHEE_NATIVE_MODULE=ganghee_native_duplicate_fault \
  --top-module aer_ganghee_native_binding_tb \
  --Mdir "$OUT_DIR/fault-obj" \
  "$PROJECT_ROOT/tb/clean/aer_bench_if.sv" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_native_duplicate_fault.sv" \
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_native_binding.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_native_binding_tb.sv"

set +e
"$OUT_DIR/fault-obj/Vaer_ganghee_native_binding_tb" \
  >"$OUT_DIR/duplicate-fault.log" 2>&1
fault_status=$?
set -e
if [[ "$fault_status" -eq 0 ]]; then
  printf 'native duplicate fault unexpectedly passed\n' >&2
  exit 1
fi
grep -q \
  'GANGHEE_NATIVE_BINDING duplicate/phantom native result addr=2' \
  "$OUT_DIR/duplicate-fault.log"
printf 'GANGHEE_NATIVE_DUPLICATE_FAIL_CLOSED_PASS status=%d\n' "$fault_status"
