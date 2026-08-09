#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERILATOR_BIN="${VERILATOR:-verilator}"
OUT_DIR="${AER_RESET_DRAIN_TEST_OUT:-/tmp/aer-clean-reset-drain-test}"

command -v "$VERILATOR_BIN" >/dev/null 2>&1 || {
  printf 'verilator not found: %s\n' "$VERILATOR_BIN" >&2
  exit 1
}

mkdir -p "$OUT_DIR"
(
  cd "$PROJECT_ROOT"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-BLKSEQ --top-module aer_clean_tb \
    --Mdir "$OUT_DIR/obj" \
    -f tb/clean/files.f
)

"$OUT_DIR/obj/Vaer_clean_tb" \
  +CLEAN_TEST=basic_reset_drain \
  +CANDIDATE=mock \
  "+METRICS=$OUT_DIR/basic_reset_drain.csv" \
  "+EVENT_METRICS=$OUT_DIR/basic_reset_drain.events.csv" \
  | tee "$OUT_DIR/run.log"

grep -q 'AER_RESET_DRAIN_PASS' "$OUT_DIR/run.log"
grep -q 'AER_CLEAN_TEST_PASS basic_reset_drain' "$OUT_DIR/run.log"

# Negative control: a candidate that asserts completion-valid during reset must
# fail before it can claim conformance.
(
  cd "$PROJECT_ROOT"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-BLKSEQ -DAER_CLEAN_RESET_FAULT --top-module aer_clean_tb \
    --Mdir "$OUT_DIR/fault-obj" \
    -f tb/clean/files.f \
    tests/clean_reset/aer_reset_fault_candidate.sv
)

if "$OUT_DIR/fault-obj/Vaer_clean_tb" \
  +CLEAN_TEST=basic_reset_drain \
  +CANDIDATE=reset-fault-negative-control \
  "+METRICS=$OUT_DIR/fault.csv" \
  "+EVENT_METRICS=$OUT_DIR/fault.events.csv" \
  >"$OUT_DIR/fault.log" 2>&1; then
  printf 'reset fault candidate unexpectedly passed\n' >&2
  exit 1
fi
grep -q 'CLEAN_ASSERT completion active during reset' "$OUT_DIR/fault.log"
printf 'RESET_DRAIN_NEGATIVE_CONTROL_PASS\n'

# Cross-epoch negative control: after a clean drain and second reset, inject a
# stale lower-half address during the no-traffic guard. It must be rejected;
# the legitimate post-reset epoch uses only upper-half addresses.
(
  cd "$PROJECT_ROOT"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-BLKSEQ -DAER_CLEAN_STALE_FAULT --top-module aer_clean_tb \
    --Mdir "$OUT_DIR/stale-fault-obj" \
    -f tb/clean/files.f \
    tests/clean_reset/aer_stale_fault_candidate.sv
)

if "$OUT_DIR/stale-fault-obj/Vaer_clean_tb" \
  +CLEAN_TEST=basic_reset_drain \
  +CANDIDATE=stale-pre-address-negative-control \
  "+METRICS=$OUT_DIR/stale-fault.csv" \
  "+EVENT_METRICS=$OUT_DIR/stale-fault.events.csv" \
  >"$OUT_DIR/stale-fault.log" 2>&1; then
  printf 'stale pre-reset address candidate unexpectedly passed\n' >&2
  exit 1
fi
grep -q 'phantom/duplicate event source=0 event=0x0' \
  "$OUT_DIR/stale-fault.log"
printf 'RESET_DRAIN_STALE_PRE_ADDRESS_NEGATIVE_CONTROL_PASS\n'

# Exercise the real native binding's fail-closed checks without requiring the
# external Ganghee RTL. One binary selects reset-valid or no-request phantom
# behavior by plusarg; both runs must exit nonzero because the binding uses
# $fatal rather than relying on a later log scraper.
(
  cd "$PROJECT_ROOT"
  "$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal \
    -Wno-BLKSEQ -DAER_CLEAN_GANGHEE_NATIVE \
    -DAER_GANGHEE_NATIVE_MODULE=ganghee_native_fault \
    -GNUM_SOURCES=16 -GRETIRE_LANES=1 --top-module aer_clean_tb \
    --Mdir "$OUT_DIR/native-fault-obj" \
    -f tb/clean/files.f \
    tb/clean/native/aer_ganghee_native_binding.sv \
    tests/clean_reset/ganghee_native_fault.sv
)

if "$OUT_DIR/native-fault-obj/Vaer_clean_tb" \
  +CLEAN_TEST=basic_reset_drain +NATIVE_RESET_FAULT \
  +CANDIDATE=native-reset-fault \
  "+METRICS=$OUT_DIR/native-reset-fault.csv" \
  "+EVENT_METRICS=$OUT_DIR/native-reset-fault.events.csv" \
  >"$OUT_DIR/native-reset-fault.log" 2>&1; then
  printf 'native reset-valid fault unexpectedly passed\n' >&2
  exit 1
fi
grep -q 'native valid active during reset' \
  "$OUT_DIR/native-reset-fault.log"
printf 'RESET_DRAIN_NATIVE_RESET_FAIL_CLOSED_PASS\n'

if "$OUT_DIR/native-fault-obj/Vaer_clean_tb" \
  +CLEAN_TEST=basic_reset_drain \
  +CANDIDATE=native-phantom-fault \
  "+METRICS=$OUT_DIR/native-phantom-fault.csv" \
  "+EVENT_METRICS=$OUT_DIR/native-phantom-fault.events.csv" \
  >"$OUT_DIR/native-phantom-fault.log" 2>&1; then
  printf 'native no-request phantom unexpectedly passed\n' >&2
  exit 1
fi
grep -q 'duplicate/phantom native result addr=0' \
  "$OUT_DIR/native-phantom-fault.log"
printf 'RESET_DRAIN_NATIVE_PHANTOM_FAIL_CLOSED_PASS\n'
