#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W5_OUT:-/tmp/a6-w5-cdc-checks}"
A1_ROOT="${A6_W5_A1_ROOT:-/home/chickgoose/projects/a1}"
A7_ROOT="${A6_W5_A7_ROOT:-/home/chickgoose/projects/a7}"
BOUND_COMMIT="42377ca81340951bfcd453b3bd664e673091f9f3"
GENERATOR="$A1_ROOT/benchmarks/clean_slate_aer/generate_trace.py"
FULL_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
CAP_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.multilane-n16.json"
REGISTRY="$PROJECT_ROOT/benchmarks/clean_slate_aer/a6_w5_production_registry.json"
VERILATOR="${AER_VERILATOR:-/tmp/a6-verilator/usr/bin/verilator}"
YOSYS="${AER_YOSYS:-/tmp/a6-yosys/usr/bin/yosys}"
YOSYS_LIBS="${AER_YOSYS_LIBS:-/tmp/a6-yosys-libs/usr/lib/x86_64-linux-gnu:/tmp/a6-yosys/usr/lib/x86_64-linux-gnu}"

mkdir -p "$OUT_DIR"
FULL_TRACES="$(mktemp -d "$OUT_DIR/full50.XXXXXX")"
CAP_TRACES="$(mktemp -d "$OUT_DIR/capacity22.XXXXXX")"
SNAPSHOT="$(mktemp -d "$OUT_DIR/a7-42377ca.XXXXXX")"

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

python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); s={(x["suite"],x["link_ratio"]):x for x in r["suite_summary"]}; assert r["a7_bound_commit"] == "42377ca81340951bfcd453b3bd664e673091f9f3"; assert r["recommendation"] == "GO_PRODUCTION_PHASE_RELATED_R1_DIGITAL_ONLY"; assert r["physical_status"] == "HOLD"; assert r["arbitrary_clock_cdc_status"].startswith("HOLD"); assert r["production_fixed_endpoint_state_bits"]["ddr2_complete_endpoint"] == 20; assert r["production_fixed_endpoint_state_bits"]["parallel4_complete_endpoint"] == 18; assert r["production_structural_proxy"]["ddr2"]["charged_functional_cells"] == 29; assert r["production_structural_proxy"]["parallel4"]["charged_functional_cells"] == 27; assert s[("full50",1)]["phase_capture_exact_runs"] == 50; assert s[("capacity22",1)]["phase_capture_exact_runs"] == 22; assert not s[("full50",1)]["phase_capture_lost_by_toggle_alias"]; assert s[("full50",2)]["phase_capture_lost_by_toggle_alias"] > 0' "$OUT_DIR/evaluation.json"

git -C "$A7_ROOT" archive "$BOUND_COMMIT" \
  rtl/candidates/a7_r1_candidate_endpoint \
  tb/candidates/a7_r1_candidate_endpoint \
  tb/filelists/a7_r1_candidate_endpoint_unit.f \
  tests/a7_r1_candidate_endpoint/structural_compare.py \
  | tar -x -C "$SNAPSHOT"

(
  cd "$SNAPSHOT"
  "$VERILATOR" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
    -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL \
    --top-module a7_r1_candidate_endpoint_tb \
    --Mdir "$OUT_DIR/production-unit-obj" -o a7_r1_unit \
    -f tb/filelists/a7_r1_candidate_endpoint_unit.f
)
"$OUT_DIR/production-unit-obj/a7_r1_unit" | tee "$OUT_DIR/production-unit.log"

(
  cd "$SNAPSHOT"
  LD_LIBRARY_PATH="$YOSYS_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    python3 tests/a7_r1_candidate_endpoint/structural_compare.py \
    --yosys "$YOSYS" --output "$OUT_DIR/production-structural.csv"
) | tee "$OUT_DIR/production-structural.log"

python3 -c 'import csv,sys; rows={r["link"]:r for r in csv.DictReader(open(sys.argv[1]))}; assert int(rows["ddr2"]["state_bits"]) == 20; assert int(rows["parallel4"]["state_bits"]) == 18; assert int(rows["ddr2"]["charged_functional_cells"]) == 29; assert int(rows["parallel4"]["charged_functional_cells"]) == 27; assert int(rows["ddr2"]["drain_guard_cells"]) == int(rows["parallel4"]["drain_guard_cells"]) == 4; assert rows["ddr2"]["physical_status"] == rows["parallel4"]["physical_status"] == "HOLD"' "$OUT_DIR/production-structural.csv"

grep -q '^A7_R1_RESET_RELEASE_ARMING_PASS' "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_SAME_CYCLE_ADMISSION_RESET_BLOCK_PASS' \
  "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_OUTPUT_AVAILABLE_CYCLE1_PASS' "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_PENDING_OUTPUT_RESET_BLOCK_PASS' "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_CONSUMER_RETIRE_CYCLE2_PASS' "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_CONTINUOUS_VALID_CHANGING_ADDRESS_PASS events=16' \
  "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_EXACT_ONCE_ORDER_ADDRESS_PASS' "$OUT_DIR/production-unit.log"
grep -q '^A7_R1_ENDPOINT_REGRESSION_PASS' "$OUT_DIR/production-unit.log"
printf 'A6 W5 42377ca production R1 digital GO; physical/unrelated CDC HOLD: %s\n' \
  "$OUT_DIR"
