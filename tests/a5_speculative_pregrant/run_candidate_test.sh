#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT_DIR="${A5_TEST_OUT:-/tmp/a5-speculative-pregrant-candidate}"
VERILATOR="${VERILATOR:-verilator}"

mkdir -p "$OUT_DIR"
compile=("$VERILATOR" --binary --timing --assert -Wall -Wno-fatal
  -Wno-TIMESCALEMOD -Wno-BLKSEQ -Wno-UNUSEDSIGNAL
  --top-module aer_clean_tb -DAER_CLEAN_GANGHEE_NATIVE
  -GNUM_SOURCES=16 -GADDR_WIDTH=16 -GRETIRE_LANES=1
  -f "$PROJECT_ROOT/tb/clean/files.f"
  -f "$PROJECT_ROOT/rtl/candidates/a5_speculative_pregrant/a5_speculative_pregrant.f"
  "$SCRIPT_DIR/aer_a5_speculative_pregrant_binding.sv"
  --Mdir "$OUT_DIR/verilated" -o a5_clean)
(cd "$PROJECT_ROOT" && "${compile[@]}")

for test_name in basic_simultaneous basic_backpressure; do
  "$OUT_DIR/verilated/a5_clean" \
    "+CLEAN_TEST=$test_name" \
    +CANDIDATE=a5_speculative_pregrant \
    "+METRICS=$OUT_DIR/$test_name.csv" \
    "+EVENT_METRICS=$OUT_DIR/$test_name.events.csv" \
    +STIM_CYCLES=128 +SEED=1 | tee "$OUT_DIR/$test_name.log"
  rg -q "AER_CLEAN_TEST_PASS $test_name" "$OUT_DIR/$test_name.log"
  rg -q 'A5_PREDICTOR_METRICS' "$OUT_DIR/$test_name.log"
done
