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
PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m unittest -v test_address_derivation_lint
python3 "$SCRIPT_DIR/lint_address_derivation.py" \
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_native_binding.sv" \
  "$PROJECT_ROOT/tb/clean/aer_clean_tb.sv"

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

"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-UNUSEDSIGNAL \
  --top-module aer_ganghee_cluster2_direct_tb \
  --Mdir "$OUT_DIR/cluster2-obj" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_cluster2_protocol_mock.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_cluster2_direct_tb.sv"

"$OUT_DIR/cluster2-obj/Vaer_ganghee_cluster2_direct_tb" | \
  tee "$OUT_DIR/cluster2-run.log"
grep -q \
  'GANGHEE_CLUSTER2_DIRECT_ANTI_RECONSTRUCTION_PASS seen=6' \
  "$OUT_DIR/cluster2-run.log"
