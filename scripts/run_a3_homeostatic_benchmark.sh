#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="${A3_MANIFEST:-$PROJECT_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json}"
OUT_DIR="${A3_OUT:-$PROJECT_ROOT/results/a3-homeostatic-inhibition}"
VERILATOR="${A3_VERILATOR:-$(command -v verilator || true)}"

if [[ -z "$VERILATOR" ]]; then
  printf 'verilator is required; set A3_VERILATOR\n' >&2
  exit 1
fi

mkdir -p "$OUT_DIR"/{build,traces,prepared,summaries,events,logs,vcd,activity,phase,timing}

python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$MANIFEST" --output-dir "$OUT_DIR/traces"

"$VERILATOR" --binary --timing --assert --trace -Wno-fatal \
  -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-BLKSEQ \
  --top-module aer_clean_tb -GNUM_SOURCES=16 -GADDR_WIDTH=16 -GRETIRE_LANES=1 \
  -f "$PROJECT_ROOT/rtl/candidates/a3_homeostatic_inhibition/files.f" \
  --Mdir "$OUT_DIR/build/obj" -o "$OUT_DIR/build/a3-clean-sim"

mapfile -t run_names < <(python3 -c \
  'import json,sys; print("\n".join(run["name"] for run in json.load(open(sys.argv[1]))["runs"]))' \
  "$MANIFEST")

for name in "${run_names[@]}"; do
  trace="$OUT_DIR/traces/$name.events.jsonl"
  run_manifest="$OUT_DIR/traces/$name.manifest.json"
  prepared="$OUT_DIR/prepared/$name.svtrace"
  python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$trace" --run-manifest "$run_manifest" --output "$prepared" \
    --addr-width 16
  "$OUT_DIR/build/a3-clean-sim" \
    "+TRACE_FILE=$prepared" "+TRACE_NAME=$name" "+CLEAN_TEST=trace" \
    "+METRICS=$OUT_DIR/summaries/$name.csv" \
    "+EVENT_METRICS=$OUT_DIR/events/$name.csv" \
    "+A3_VCD=$OUT_DIR/vcd/$name.vcd" \
    "+CANDIDATE=a3-homeostatic-inhibition" \
    >"$OUT_DIR/logs/$name.log" 2>&1
  tail -4 "$OUT_DIR/logs/$name.log"

  read -r stim_cycles delivered < <(python3 -c \
    'import csv,sys; r=next(csv.DictReader(open(sys.argv[1]))); print(r["stim_cycles"],r["delivered"])' \
    "$OUT_DIR/summaries/$name.csv")
  python3 "$PROJECT_ROOT/scripts/analyze_a3_vcd.py" \
    --vcd "$OUT_DIR/vcd/$name.vcd" --name "$name" --cycles "$stim_cycles" \
    --delivered "$delivered" --output "$OUT_DIR/activity/$name.csv"

  case "$name" in
    phase_transition_*)
      python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/phase_metrics.py" \
        --trace "$trace" --run-manifest "$run_manifest" \
        --events "$OUT_DIR/events/$name.csv" --output "$OUT_DIR/phase/$name.csv"
      ;;
    timing_pair_*)
      python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/timing_pair_metrics.py" \
        --trace "$trace" --run-manifest "$run_manifest" \
        --events "$OUT_DIR/events/$name.csv" --output "$OUT_DIR/timing/$name.csv"
      ;;
  esac
done

summary_inputs=("$OUT_DIR"/summaries/*.csv)
event_args=()
for event_csv in "$OUT_DIR"/events/*.csv; do
  event_args+=(--events "$event_csv")
done
python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/aggregate.py" \
  "${summary_inputs[@]}" "${event_args[@]}" \
  --output "$OUT_DIR/aggregate.csv" \
  --event-output "$OUT_DIR/event-runs.csv" --fail-on-correctness

python3 -c \
  'import csv,glob,sys; rows=[]
for path in glob.glob(sys.argv[1]+"/*.csv"): rows.extend(csv.DictReader(open(path)))
with open(sys.argv[2],"w",newline="") as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)' \
  "$OUT_DIR/activity" "$OUT_DIR/activity.csv"

python3 "$PROJECT_ROOT/tests/a3_homeostatic_inhibition/stability_sweep.py" \
  --output "$OUT_DIR/stability-sweep.csv"

printf 'A3 frozen benchmark complete: %s\n' "$OUT_DIR"
