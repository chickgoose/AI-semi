#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W3_OUT:-/tmp/a6-w3-elias-fano-checks}"
IVERILOG="${AER_IVERILOG:-iverilog}"
VVP="${AER_VVP:-vvp}"
IVERILOG_BASE="${AER_IVERILOG_BASE:-}"

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
)

(
  cd "$PROJECT_ROOT"
  "${compiler[@]}" -g2012 -Wall -s a6_ef_lockstep_tb \
    -f rtl/candidates/a6_elias_fano_monotone_link/a6_ef_lockstep.f \
    -o "$OUT_DIR/a6_ef_lockstep.vvp"
)
"${runtime[@]}" "$OUT_DIR/a6_ef_lockstep.vvp" |
  tee "$OUT_DIR/a6_ef_lockstep.log"
grep -q '^A6_EF_LOCKSTEP_PASS ' "$OUT_DIR/a6_ef_lockstep.log"
printf 'A6 W3 Elias-Fano checks complete: %s\n' "$OUT_DIR"
