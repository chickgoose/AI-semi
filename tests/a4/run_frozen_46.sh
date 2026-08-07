#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s [a4|mock|both]\n' "$0" >&2
  exit 2
}

mode="${1:-both}"
case "$mode" in
  a4|mock|both) ;;
  *) usage ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/../.." && pwd)"
out_root="${A4_REGRESSION_OUT:-$project_root/results/a4-quadtree-regression}"
trace_dir="$out_root/traces"
xrun_bin="${AER_XRUN_BIN:-xrun}"

command -v "$xrun_bin" >/dev/null 2>&1 || {
  printf 'Xcelium xrun is required for the frozen interface/SVA testbench\n' >&2
  exit 1
}

mkdir -p "$out_root" "$trace_dir"
python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.neutrality-n16.json" \
  --output-dir "$trace_dir"

compile_design() {
  local design="$1"
  local design_out="$out_root/$design"
  local snapshot="aer_clean_${design//-/_}_n16"
  local -a command
  mkdir -p "$design_out"
  command=("$xrun_bin" -64bit -sv -timescale 1ns/1ps -top aer_clean_tb
    -snapshot "$snapshot" -elaborate
    -defparam aer_clean_tb.NUM_SOURCES=16
    -defparam aer_clean_tb.ADDR_WIDTH=16
    -defparam aer_clean_tb.RETIRE_LANES=2
    -xmlibdirname "$design_out/xcelium.d")
  if [[ "$design" == a4-quadtree ]]; then
    command+=(-f "$project_root/tests/a4/clean_tb.f"
      -f "$project_root/tb/filelists/a4_quadtree_fabric.f"
      "$project_root/tests/a4/a4_quadtree_properties.sv")
  else
    command+=(-f "$project_root/tb/clean/files.f")
  fi
  command+=(-l "$design_out/elaborate.log")
  (cd "$project_root" && "${command[@]}")
}

run_design() {
  local design="$1"
  local design_out="$out_root/$design"
  local snapshot="aer_clean_${design//-/_}_n16"
  local trace_path
  local stem
  local run_manifest
  local prepared_trace
  local prepare_output
  local report_group
  local -a summary_files=()
  local -a event_args=()

  for trace_path in "$trace_dir"/*.events.jsonl; do
    stem="$(basename "$trace_path" .events.jsonl)"
    run_manifest="$trace_dir/$stem.manifest.json"
    prepared_trace="$design_out/$stem.svtrace"
    prepare_output="$(python3 \
      "$project_root/benchmarks/clean_slate_aer/prepare_sv_trace.py" \
      --trace "$trace_path" --run-manifest "$run_manifest" \
      --output "$prepared_trace" --addr-width 16)"
    report_group="${prepare_output##*report_group=}"
    report_group="${report_group%% *}"

    (cd "$project_root" && "$xrun_bin" -64bit -R -snapshot "$snapshot" \
      -xmlibdirname "$design_out/xcelium.d" \
      +CLEAN_TEST=trace "+TRACE_FILE=$prepared_trace" \
      "+TRACE_NAME=$report_group" "+CANDIDATE=$design" \
      "+METRICS=$design_out/$stem.csv" \
      "+EVENT_METRICS=$design_out/$stem.events.csv" \
      +STIM_CYCLES=4096 +LOAD_PCT=0 +SEED=1 \
      -l "$design_out/$stem.log")
    summary_files+=("$design_out/$stem.csv")
    event_args+=(--events "$design_out/$stem.events.csv")
  done

  python3 "$project_root/benchmarks/clean_slate_aer/aggregate.py" \
    "${summary_files[@]}" "${event_args[@]}" \
    --output "$design_out/aggregate.csv" \
    --event-output "$design_out/event-runs.csv" \
    --fail-on-correctness
}

if [[ "$mode" == a4 || "$mode" == both ]]; then
  compile_design a4-quadtree
  run_design a4-quadtree
fi
if [[ "$mode" == mock || "$mode" == both ]]; then
  compile_design mock-flat-rr
  run_design mock-flat-rr
fi

printf 'A4 frozen-46 regression complete: %s\n' "$out_root"
