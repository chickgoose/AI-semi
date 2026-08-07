#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST="$PROJECT_ROOT/benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
TRACE_DIR="${AER_A9_TRACE_DIR:-/tmp/a9-neutrality-n16}"
OUT_DIR="${AER_A9_OUT:-$PROJECT_ROOT/results/a9-distributed-token-fabric-n16-l4}"
SIMULATOR="${AER_SIMULATOR:-}"
NUM_SOURCES="${AER_NUM_SOURCES:-16}"
ADDR_WIDTH="${AER_ADDR_WIDTH:-16}"
RETIRE_LANES="${AER_RETIRE_LANES:-4}"
IMPLEMENTATION="${AER_A9_IMPLEMENTATION:-distributed}"

case "$IMPLEMENTATION" in
  distributed)
    candidate_name="a9-distributed-token-fabric-l${RETIRE_LANES}"
    implementation_verilator=()
    implementation_xrun=()
    ;;
  centralized)
    candidate_name="a9-centralized-reference-l${RETIRE_LANES}"
    implementation_verilator=(-DA9_CENTRALIZED_REFERENCE)
    implementation_xrun=(-define A9_CENTRALIZED_REFERENCE)
    ;;
  diffusive)
    candidate_name="a9-neighbor-handoff-l${RETIRE_LANES}"
    implementation_verilator=(-DA9_NEIGHBOR_HANDOFF)
    implementation_xrun=(-define A9_NEIGHBOR_HANDOFF)
    ;;
  *)
    printf 'AER_A9_IMPLEMENTATION must be distributed, diffusive, or centralized\n' >&2
    exit 2
    ;;
esac

if [[ "$NUM_SOURCES" != 16 ]]; then
  printf 'frozen A9 benchmark binding requires AER_NUM_SOURCES=16\n' >&2
  exit 2
fi
if (( NUM_SOURCES % RETIRE_LANES != 0 )); then
  printf 'AER_RETIRE_LANES must evenly divide AER_NUM_SOURCES\n' >&2
  exit 2
fi

mkdir -p "$TRACE_DIR" "$OUT_DIR"
python3 "$PROJECT_ROOT/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$MANIFEST" --output-dir "$TRACE_DIR" \
  >"$OUT_DIR/generate.log"

if [[ $# -gt 0 ]]; then
  runs=("$@")
else
  mapfile -t runs < <(sed -n \
    's/.*{"name":"\([^"]*\)".*/\1/p' "$MANIFEST")
fi

common_params=(
  "aer_clean_tb.NUM_SOURCES=$NUM_SOURCES"
  "aer_clean_tb.ADDR_WIDTH=$ADDR_WIDTH"
  "aer_clean_tb.RETIRE_LANES=$RETIRE_LANES"
)

if [[ -z "$SIMULATOR" ]]; then
  if command -v verilator >/dev/null 2>&1; then
    SIMULATOR=verilator
  elif command -v xrun >/dev/null 2>&1; then
    SIMULATOR=xrun
  else
    printf 'no common-TB capable simulator found; set AER_SIMULATOR=verilator or xrun\n' >&2
    exit 1
  fi
fi

case "$SIMULATOR" in
  verilator)
    VERILATOR_BIN="${VERILATOR:-verilator}"
    compile=("$VERILATOR_BIN" --binary --timing --assert -Wall -Wno-fatal
      -Wno-BLKSEQ -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND
      -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL
      --top-module aer_clean_tb --Mdir "$OUT_DIR/obj"
      "-GNUM_SOURCES=$NUM_SOURCES" "-GADDR_WIDTH=$ADDR_WIDTH"
      "-GRETIRE_LANES=$RETIRE_LANES"
      "${implementation_verilator[@]}"
      "$PROJECT_ROOT/tb/clean/aer_bench_if.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_centralized_reference.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_clean_binding.sv"
      "$PROJECT_ROOT/tb/clean/aer_clean_assertions.sv"
      "$PROJECT_ROOT/tb/clean/aer_clean_tb.sv"
    )
    "${compile[@]}" 2>&1 | tee "$OUT_DIR/compile.log"
    ;;
  xrun)
    snapshot="a9_clean_n${NUM_SOURCES}_l${RETIRE_LANES}"
    compile=(xrun -64bit -sv -timescale 1ns/1ps -top aer_clean_tb
      -snapshot "$snapshot" -elaborate -xmlibdirname "$OUT_DIR/xcelium.d")
    for parameter in "${common_params[@]}"; do
      compile+=(-defparam "$parameter")
    done
    compile+=(
      "${implementation_xrun[@]}"
      "$PROJECT_ROOT/tb/clean/aer_bench_if.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_empty_slot_cell.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_distributed_token_fabric.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_neighbor_handoff_fabric.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_centralized_reference.sv"
      "$PROJECT_ROOT/rtl/candidates/a9_distributed_token_fabric/a9_clean_binding.sv"
      "$PROJECT_ROOT/tb/clean/aer_clean_assertions.sv"
      "$PROJECT_ROOT/tb/clean/aer_clean_tb.sv"
      -l "$OUT_DIR/compile.log"
    )
    (cd "$PROJECT_ROOT" && "${compile[@]}")
    ;;
  *)
    printf 'unsupported AER_SIMULATOR=%s\n' "$SIMULATOR" >&2
    exit 2
    ;;
esac

for run_name in "${runs[@]}"; do
  trace_jsonl="$TRACE_DIR/$run_name.events.jsonl"
  run_manifest="$TRACE_DIR/$run_name.manifest.json"
  prepared_trace="$OUT_DIR/$run_name.svtrace"
  [[ -f "$trace_jsonl" && -f "$run_manifest" ]] || {
    printf 'unknown generated trace: %s\n' "$run_name" >&2
    exit 2
  }
  prepare_output="$(python3 \
    "$PROJECT_ROOT/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
    --trace "$trace_jsonl" --run-manifest "$run_manifest" \
    --output "$prepared_trace" --addr-width "$ADDR_WIDTH")"
  printf '%s\n' "$prepare_output"
  report_name="${prepare_output##*report_group=}"
  report_name="${report_name%% *}"

  case "$SIMULATOR" in
    verilator)
      "$OUT_DIR/obj/Vaer_clean_tb" \
        "+CLEAN_TEST=trace" "+TRACE_NAME=$report_name" \
        "+TRACE_FILE=$prepared_trace" "+CANDIDATE=$candidate_name" \
        "+METRICS=$OUT_DIR/$run_name.csv" \
        "+EVENT_METRICS=$OUT_DIR/$run_name.events.csv" \
        | tee "$OUT_DIR/$run_name.log"
      ;;
    xrun)
      xrun -64bit -R -snapshot "$snapshot" \
        -xmlibdirname "$OUT_DIR/xcelium.d" \
        "+CLEAN_TEST=trace" "+TRACE_NAME=$report_name" \
        "+TRACE_FILE=$prepared_trace" "+CANDIDATE=$candidate_name" \
        "+METRICS=$OUT_DIR/$run_name.csv" \
        "+EVENT_METRICS=$OUT_DIR/$run_name.events.csv" \
        -l "$OUT_DIR/$run_name.log"
      ;;
  esac
done

printf 'A9 clean benchmark complete: %s (%0d traces)\n' "$OUT_DIR" "${#runs[@]}"
