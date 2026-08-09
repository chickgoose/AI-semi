#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s generate-only\n' "$0" >&2
  printf '       %s <drec-prefix|drec-replicated> <1|2|4|all>\n' "$0" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
mode="$1"
lane_selection="${2:-}"
[[ $# -le 2 ]] || usage

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
trace_root="${AER_COMMON_MULTILANE_TRACE_DIR:-/tmp/aer-common-multilane-n16}"
out_root="${AER_CLEAN_OUT:-$project_root/results/common-multilane}"

analyze_pairwise() {
  local trace_stem="$1"
  local event_path="$2"
  local run_manifest="$3"
  local result_root="$4"
  local event_results=()
  [[ "$trace_stem" == pairwise_contention_* ]] || return 0
  mapfile -t event_results < <(
    find "$result_root" -type f -name 'trace.events.csv' -print | sort
  )
  if [[ "${#event_results[@]}" -ne 1 ]]; then
    printf 'expected one pairwise event result under %s, found %d\n' \
      "$result_root" "${#event_results[@]}" >&2
    return 1
  fi
  python3 "$project_root/benchmarks/clean_slate_aer/pairwise_contention_metrics.py" \
    --trace "$event_path" --run-manifest "$run_manifest" \
    --events "${event_results[0]}" \
    --output "$result_root/$trace_stem.pairs.json"
}

python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.multilane-n16.json" \
  --output-dir "$trace_root"

mapfile -t generated_traces < <(
  python3 -c 'import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
index = json.loads((root / "generation-index.json").read_text(encoding="utf-8"))
for run in index["runs"]:
    print(root / run["trace_file"])' "$trace_root"
)
[[ "${#generated_traces[@]}" -eq 20 ]] || {
  printf 'expected exactly 20 indexed multi-lane traces, found %d\n' \
    "${#generated_traces[@]}" >&2
  exit 1
}

if [[ "$mode" == "generate-only" ]]; then
  [[ -z "$lane_selection" ]] || usage
  printf 'common multi-lane traces generated: %s\n' "$trace_root"
  exit 0
fi

case "$mode" in
  drec-prefix) implementation=prefix ;;
  drec-replicated) implementation=replicated ;;
  *) usage ;;
esac
case "$lane_selection" in
  1|2|4) lane_counts=("$lane_selection") ;;
  all) lane_counts=(1 2 4) ;;
  *) usage ;;
esac

for lane_count in "${lane_counts[@]}"; do
  if (( lane_count > 1 )); then
    AER_CLEAN_OUT="$out_root" AER_A7_IMPL="$implementation" \
      "$script_dir/run_a7_parallel_event_compactor.sh" "$lane_count" \
      optional_multilane_independent_stall
  fi

  for event_path in "${generated_traces[@]}"; do
    [[ -f "$event_path" ]] || {
      printf 'indexed trace is missing: %s\n' "$event_path" >&2
      exit 1
    }
    trace_stem="$(basename "$event_path" .events.jsonl)"
    run_manifest="$trace_root/$trace_stem.manifest.json"
    trace_result_root="$out_root/$implementation/k$lane_count/$trace_stem"
    freshness_marker="$(mktemp)"
    AER_CLEAN_OUT="$out_root" \
    AER_TRACE_JSONL="$event_path" \
    AER_TRACE_MANIFEST="$run_manifest" \
    AER_A7_IMPL="$implementation" \
      "$script_dir/run_a7_parallel_event_compactor.sh" "$lane_count"
    event_result="$trace_result_root/trace.events.csv"
    [[ -s "$event_result" && "$event_result" -nt "$freshness_marker" ]] || {
      printf 'candidate did not produce a fresh event result: %s\n' \
        "$event_result" >&2
      exit 1
    }
    analyze_pairwise "$trace_stem" "$event_path" "$run_manifest" \
      "$trace_result_root"
    rm -f "$freshness_marker"
  done
done

printf 'common multi-lane benchmark complete: %s\n' "$out_root"
