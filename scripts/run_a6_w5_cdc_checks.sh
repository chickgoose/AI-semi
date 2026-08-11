#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W5_OUT:-/tmp/a6-w5-cdc-checks}"
A1_ROOT="${A6_W5_A1_ROOT:-/home/chickgoose/projects/a1}"
A7_ROOT="${A6_W5_A7_ROOT:-/home/chickgoose/projects/a7}"
GENERATOR="$A1_ROOT/benchmarks/clean_slate_aer/generate_trace.py"
FULL_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
CAP_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.multilane-n16.json"
REGISTRY="$PROJECT_ROOT/benchmarks/clean_slate_aer/a6_w4_fixed_pin_registry_db3f04f.json"
IVERILOG="${AER_IVERILOG:-/tmp/a6-iverilog/usr/bin/iverilog}"
VVP="${AER_VVP:-/tmp/a6-iverilog/usr/bin/vvp}"
IVERILOG_BASE="${AER_IVERILOG_BASE:-/tmp/a6-iverilog/usr/lib/x86_64-linux-gnu/ivl}"
VERILATOR="${AER_VERILATOR:-/tmp/a6-verilator/usr/bin/verilator}"
YOSYS="${AER_YOSYS:-/tmp/a6-yosys/usr/bin/yosys}"
YOSYS_LIBS="${AER_YOSYS_LIBS:-/tmp/a6-yosys-libs/usr/lib/x86_64-linux-gnu:/tmp/a6-yosys/usr/lib/x86_64-linux-gnu}"

mkdir -p "$OUT_DIR"
FULL_TRACES="$(mktemp -d "$OUT_DIR/full50.XXXXXX")"
CAP_TRACES="$(mktemp -d "$OUT_DIR/capacity22.XXXXXX")"

(
  cd "$PROJECT_ROOT"
  python3 -m unittest benchmarks.clean_slate_aer.tests.test_a6_w5_cdc_evaluate
  python3 "$GENERATOR" --manifest "$FULL_MANIFEST" --output-dir "$FULL_TRACES"
  python3 "$GENERATOR" --manifest "$CAP_MANIFEST" --output-dir "$CAP_TRACES"
  python3 benchmarks/clean_slate_aer/a6_w5_cdc_evaluate.py \
    --registry "$REGISTRY" --generator "$GENERATOR" --a7-repo "$A7_ROOT" \
    --full-manifest "$FULL_MANIFEST" --full-trace-dir "$FULL_TRACES" \
    --cap-manifest "$CAP_MANIFEST" --cap-trace-dir "$CAP_TRACES" \
    --output "$OUT_DIR/evaluation.json"
)

python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); s={(x["suite"],x["link_ratio"]):x for x in r["suite_summary"]}; assert r["recommendation"] == "GO_RESTRICTED_PHASE_RELATED_R1_ONLY"; assert r["arbitrary_clock_cdc_status"].startswith("HOLD"); assert s[("full50",1)]["phase_capture_exact_runs"] == 50; assert s[("capacity22",1)]["phase_capture_exact_runs"] == 22; assert not s[("full50",1)]["phase_capture_lost_by_toggle_alias"]; assert not s[("capacity22",1)]["phase_capture_lost_by_toggle_alias"]; assert s[("full50",2)]["phase_capture_lost_by_toggle_alias"] > 0; assert s[("capacity22",4)]["phase_capture_lost_by_toggle_alias"] > 0' "$OUT_DIR/evaluation.json"

(
  cd "$PROJECT_ROOT"
  "$IVERILOG" -B "$IVERILOG_BASE" -g2012 -Wall \
    -s a6_w5_phase_related_rx_boundary_tb \
    -f rtl/candidates/a6_w5_rx_core_boundary/a6_w5_phase_related_rx_boundary.f \
    -o "$OUT_DIR/a6_w5_phase_related.vvp"
)
"$VVP" -M "$IVERILOG_BASE" "$OUT_DIR/a6_w5_phase_related.vvp" \
  | tee "$OUT_DIR/iverilog.log"

(
  cd "$PROJECT_ROOT"
  "$VERILATOR" --Mdir "$OUT_DIR/verilator-obj" --binary --timing \
    -Wall -Wno-fatal -Wno-BLKSEQ \
    --top-module a6_w5_phase_related_rx_boundary_tb \
    -f rtl/candidates/a6_w5_rx_core_boundary/a6_w5_phase_related_rx_boundary.f
)
"$OUT_DIR/verilator-obj/Va6_w5_phase_related_rx_boundary_tb" \
  | tee "$OUT_DIR/verilator.log"

(
  cd "$PROJECT_ROOT"
  LD_LIBRARY_PATH="$YOSYS_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$YOSYS" -Q -p \
    "read_verilog -sv rtl/candidates/a6_w5_rx_core_boundary/a6_w5_phase_related_rx_boundary.sv; hierarchy -top a6_w5_phase_related_rx_boundary; proc; opt; tee -o $OUT_DIR/synth-stat.json stat -json -width"
) >"$OUT_DIR/yosys.log"
python3 -c 'import json,re,sys; d=json.load(open(sys.argv[1])); m=next(iter(d["modules"].values())); h=m["num_cells_by_type"]; bits=sum(v*int(re.search(r"_(\d+)$",k).group(1)) for k,v in h.items() if "dff" in k.lower()); assert bits == 6, bits' "$OUT_DIR/synth-stat.json"

grep -q '^A6_W5_PHASE_RELATED_R1_PASS delivered=18 state_bits=6' \
  "$OUT_DIR/iverilog.log"
grep -q '^A6_W5_PHASE_RELATED_R1_PASS delivered=18 state_bits=6' \
  "$OUT_DIR/verilator.log"
printf 'A6 W5 restricted phase-related R1 GO; unrelated CDC HOLD: %s\n' \
  "$OUT_DIR"
