#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W3_OUT:-/tmp/a6-w3-elias-fano-checks}"
IVERILOG="${AER_IVERILOG:-iverilog}"
VVP="${AER_VVP:-vvp}"
IVERILOG_BASE="${AER_IVERILOG_BASE:-}"
VERILATOR="${AER_VERILATOR:-verilator}"
GATE_MODE="${A6_W3_GATE_MODE:-research}"
CAP22_MANIFEST="${A6_W3_CAP22_MANIFEST:-/home/chickgoose/projects/a1/benchmarks/clean_slate_aer/manifest.multilane-n16.json}"
GENERATOR="${A6_W3_GENERATOR:-/home/chickgoose/projects/a1/benchmarks/clean_slate_aer/generate_trace.py}"
REGISTRY="$PROJECT_ROOT/rtl/candidates/a6_elias_fano_monotone_link/a6_w3_capacity22_registry.json"

mkdir -p "$OUT_DIR"
if [[ "$GATE_MODE" != "research" && "$GATE_MODE" != "qualification" ]]; then
  printf 'A6_W3_GATE_MODE must be research or qualification, got: %s\n' \
    "$GATE_MODE" >&2
  exit 64
fi
TRACE_DIR="$(mktemp -d "$OUT_DIR/capacity22.XXXXXX")"
CAP22_JSON="$OUT_DIR/cap22.$GATE_MODE.json"
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
  python3 benchmarks/clean_slate_aer/a6_w3_evaluate.py verify-contract \
    --manifest "$CAP22_MANIFEST" --generator "$GENERATOR" \
    --registry "$REGISTRY" --output "$OUT_DIR/cap22.contract.json"
  python3 "$GENERATOR" --manifest "$CAP22_MANIFEST" --output-dir "$TRACE_DIR"
)

cap22_args=(
  cap22 --manifest "$CAP22_MANIFEST" --trace-dir "$TRACE_DIR"
  --generator "$GENERATOR" --registry "$REGISTRY"
  --max-batch 16 --output "$CAP22_JSON"
)
if [[ "$GATE_MODE" == "qualification" ]]; then
  (
    cd "$PROJECT_ROOT"
    python3 benchmarks/clean_slate_aer/a6_w3_evaluate.py \
      "${cap22_args[@]}" --require-go
  )
else
  (
    cd "$PROJECT_ROOT"
    python3 benchmarks/clean_slate_aer/a6_w3_evaluate.py "${cap22_args[@]}"
    python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["decision"] == "HOLD_LATENCY_OR_LINK_GATE" and r["selected_gate"] is None and not any(g["gate_pass"] for g in r["gates"])' "$CAP22_JSON"
  )
fi

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
printf 'A6 W3 research HOLD validated: Python + cap22 + Icarus + Verilator: %s\n' \
  "$OUT_DIR"
