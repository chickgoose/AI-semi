#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${A6_W4_DB3_OUT:-/tmp/a6-w4-db3f04f-followup}"
A1_ROOT="${A6_W4_A1_ROOT:-/home/chickgoose/projects/a1}"
A7_ROOT="${A6_W4_A7_ROOT:-/home/chickgoose/projects/a7}"
BOUND_COMMIT="db3f04fe0e01699e63c596145fe71effc601e57c"
GENERATOR="$A1_ROOT/benchmarks/clean_slate_aer/generate_trace.py"
FULL_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
CAP_MANIFEST="$A1_ROOT/benchmarks/clean_slate_aer/manifest.multilane-n16.json"
REGISTRY="$PROJECT_ROOT/benchmarks/clean_slate_aer/a6_w4_fixed_pin_registry_db3f04f.json"

mkdir -p "$OUT_DIR"
FULL_TRACES="$(mktemp -d "$OUT_DIR/full50.XXXXXX")"
CAP_TRACES="$(mktemp -d "$OUT_DIR/capacity22.XXXXXX")"
ORACLE_ROOT="$(mktemp -d "$OUT_DIR/oracle.XXXXXX")"

(
  cd "$PROJECT_ROOT"
  python3 -m unittest \
    benchmarks.clean_slate_aer.tests.test_a6_w4_fixed_pin_replay \
    benchmarks.clean_slate_aer.tests.test_a6_w4_fixed_pin_replay_db3f04f
  python3 "$GENERATOR" --manifest "$FULL_MANIFEST" --output-dir "$FULL_TRACES"
  python3 "$GENERATOR" --manifest "$CAP_MANIFEST" --output-dir "$CAP_TRACES"
  python3 benchmarks/clean_slate_aer/a6_w4_fixed_pin_replay_db3f04f.py \
    --registry "$REGISTRY" --generator "$GENERATOR" --a7-repo "$A7_ROOT" \
    --full-manifest "$FULL_MANIFEST" --full-trace-dir "$FULL_TRACES" \
    --cap-manifest "$CAP_MANIFEST" --cap-trace-dir "$CAP_TRACES" \
    --output "$OUT_DIR/replay.json"
)

git -C "$A7_ROOT" archive "$BOUND_COMMIT" \
  tests/a7_event_triggered_ddr_burst_link_w4/strict_protocol_oracle.py \
  tests/a7_event_triggered_ddr_burst_link_w4/test_strict_protocol_oracle.py \
  | tar -x -C "$ORACLE_ROOT"
PYTHONPATH="$ORACLE_ROOT/tests/a7_event_triggered_ddr_burst_link_w4" \
  PYTHONDONTWRITEBYTECODE=1 python3 \
  "$ORACLE_ROOT/tests/a7_event_triggered_ddr_burst_link_w4/test_strict_protocol_oracle.py"

python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["a7_bound_commit"] == "db3f04fe0e01699e63c596145fe71effc601e57c"; assert r["decision"] == "HOLD_PHYSICAL_AND_FULL_ENDPOINT_PPA"; assert len(r["runs"]) == 648; assert all(x["sequence_exact"] for x in r["runs"]); assert {x["link"]: x["fixed_link_state_bits"] for x in r["suite_summary"]} == {"parallel4": 11, "ddr2": 13, "serial1": 16}' "$OUT_DIR/replay.json"

printf 'A6 W4 db3f04f conservative exact replay HOLD validated: %s\n' \
  "$OUT_DIR/replay.json"
