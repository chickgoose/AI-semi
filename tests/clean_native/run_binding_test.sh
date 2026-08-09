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
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_cluster2_binding.sv" \
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
  -DAER_CLEAN_GANGHEE_CLUSTER2 \
  -DAER_GANGHEE_CLUSTER2_MODULE=ganghee_cluster2_protocol_mock \
  --top-module aer_ganghee_cluster2_binding_tb \
  --Mdir "$OUT_DIR/cluster2-binding-obj" \
  "$PROJECT_ROOT/tb/clean/aer_bench_if.sv" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_cluster2_protocol_mock.sv" \
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_cluster2_binding.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_cluster2_binding_tb.sv"

"$OUT_DIR/cluster2-binding-obj/Vaer_ganghee_cluster2_binding_tb" | \
  tee "$OUT_DIR/cluster2-binding-run.log"
grep -q \
  'GANGHEE_CLUSTER2_BINDING_HELD_ACK_PASS ack=1 retire=1 phantom=0' \
  "$OUT_DIR/cluster2-binding-run.log"

"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-UNUSEDSIGNAL \
  -DAER_CLEAN_GANGHEE_CLUSTER2 \
  -DAER_GANGHEE_CLUSTER2_MODULE=ganghee_cluster2_protocol_mock \
  -DAER_CLUSTER2_MOCK_REPEAT \
  -GREPEAT_EACH_RESULT=1 \
  --top-module aer_ganghee_cluster2_binding_tb \
  --Mdir "$OUT_DIR/cluster2-binding-repeat-obj" \
  "$PROJECT_ROOT/tb/clean/aer_bench_if.sv" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_cluster2_protocol_mock.sv" \
  "$PROJECT_ROOT/tb/clean/native/aer_ganghee_cluster2_binding.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_cluster2_binding_tb.sv"

"$OUT_DIR/cluster2-binding-repeat-obj/Vaer_ganghee_cluster2_binding_tb" | \
  tee "$OUT_DIR/cluster2-binding-repeat-run.log"
grep -q \
  'GANGHEE_CLUSTER2_BINDING_PHANTOM_VISIBLE_PASS ack=1 retire=2 phantom=1' \
  "$OUT_DIR/cluster2-binding-repeat-run.log"

"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-UNUSEDSIGNAL \
  --top-module aer_ganghee_cluster2_direct_tb \
  --Mdir "$OUT_DIR/cluster2-obj" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_cluster2_protocol_mock.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_cluster2_direct_tb.sv"

"$OUT_DIR/cluster2-obj/Vaer_ganghee_cluster2_direct_tb" | \
  tee "$OUT_DIR/cluster2-run.log"
grep -q \
  'GANGHEE_CLUSTER2_HELD_ACK_PASS raw=6 ack=6 phantom=0 masked=6' \
  "$OUT_DIR/cluster2-run.log"

"$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ -Wno-UNUSEDSIGNAL \
  -GREPEAT_EACH_RESULT=1 \
  --top-module aer_ganghee_cluster2_direct_tb \
  --Mdir "$OUT_DIR/cluster2-repeat-obj" \
  "$PROJECT_ROOT/tests/clean_native/ganghee_cluster2_protocol_mock.sv" \
  "$PROJECT_ROOT/tests/clean_native/aer_ganghee_cluster2_direct_tb.sv"

"$OUT_DIR/cluster2-repeat-obj/Vaer_ganghee_cluster2_direct_tb" | \
  tee "$OUT_DIR/cluster2-repeat-run.log"
grep -q \
  'GANGHEE_CLUSTER2_PHANTOM_VISIBLE_PASS raw=12 ack=6 phantom=6 masked=12' \
  "$OUT_DIR/cluster2-repeat-run.log"
