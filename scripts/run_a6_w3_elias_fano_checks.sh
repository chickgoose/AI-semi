#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W3_OUT:-/tmp/a6-w3-elias-fano-checks}"
IVERILOG="${AER_IVERILOG:-iverilog}"
VVP="${AER_VVP:-vvp}"
IVERILOG_BASE="${AER_IVERILOG_BASE:-}"
VERILATOR="${AER_VERILATOR:-verilator}"

mkdir -p "$OUT_DIR"
compiler=("$IVERILOG")
runtime=("$VVP")
if [[ -n "$IVERILOG_BASE" ]]; then
  compiler+=(-B "$IVERILOG_BASE")
  runtime+=(-M "$IVERILOG_BASE")
fi

(
  cd "$PROJECT_ROOT"
  python3 -m unittest benchmarks.clean_slate_aer.tests.test_a6_w3_elias_fano
  python3 benchmarks/clean_slate_aer/a6_w3_cycle_oracle.py \
    --output "$OUT_DIR/a6_ef_cycle_oracle.tsv"
  cmp "$OUT_DIR/a6_ef_cycle_oracle.tsv" \
    rtl/candidates/a6_elias_fano_monotone_link/a6_ef_cycle_oracle.tsv
)

for top in a6_ef_lockstep_tb a6_ef_cycle_lockstep_tb; do
  (
    cd "$PROJECT_ROOT"
    "${compiler[@]}" -g2012 -Wall -s "$top" \
      -f rtl/candidates/a6_elias_fano_monotone_link/a6_ef_lockstep.f \
      -o "$OUT_DIR/$top.vvp"
  )
  "${runtime[@]}" "$OUT_DIR/$top.vvp" | tee "$OUT_DIR/$top.iverilog.log"

  prefix="V${top}"
  mdir="$OUT_DIR/verilator-$top"
  (
    cd "$PROJECT_ROOT"
    "$VERILATOR" --Mdir "$mdir" --prefix "$prefix" --binary --timing \
      -Wno-fatal --top-module "$top" \
      -f rtl/candidates/a6_elias_fano_monotone_link/a6_ef_lockstep.f
  ) >"$OUT_DIR/$top.verilator-build.log" 2>&1
  "$mdir/$prefix" | tee "$OUT_DIR/$top.verilator-run.log"
done

grep -q '^A6_EF_LOCKSTEP_PASS ' \
  "$OUT_DIR/a6_ef_lockstep_tb.iverilog.log"
grep -q '^A6_EF_CYCLE_LOCKSTEP_PASS ' \
  "$OUT_DIR/a6_ef_cycle_lockstep_tb.iverilog.log"
grep -q '^A6_EF_LOCKSTEP_PASS ' \
  "$OUT_DIR/a6_ef_lockstep_tb.verilator-run.log"
grep -q '^A6_EF_CYCLE_LOCKSTEP_PASS ' \
  "$OUT_DIR/a6_ef_cycle_lockstep_tb.verilator-run.log"
printf 'A6 W3 checks complete: Python + Icarus + Verilator: %s\n' "$OUT_DIR"
