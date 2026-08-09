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
out_parent="${AER_CLEAN_OUT:-$project_root/results/common-multilane}"
source "$script_dir/lib/pairwise_cross_map_common.sh"

analyze_pairwise() {
  local trace_stem="$1"
  local trace_path="$2"
  local event_result="$3"
  local run_manifest="$4"
  local result_root="$5"
  [[ "$trace_stem" == pairwise_contention_* ]] || return 0
  local pair_report="$result_root/$trace_stem.pairs.json"
  python3 "$project_root/benchmarks/clean_slate_aer/pairwise_contention_metrics.py" \
    --trace "$trace_path" --run-manifest "$run_manifest" \
    --events "$event_result" --output "$pair_report"
  printf '%s\n' "$pair_report"
}

analyze_mixed_phase() {
  local trace_stem="$1"
  local event_path="$2"
  local run_manifest="$3"
  local result_root="$4"
  local summary_path="$result_root/trace.csv"
  [[ "$trace_stem" == mixed_phase_always_ready_* ]] || return 0
  [[ -s "$summary_path" ]] || {
    printf 'expected mixed-phase summary result: %s\n' "$summary_path" >&2
    return 1
  }
  python3 "$project_root/benchmarks/clean_slate_aer/mixed_phase_always_ready_metrics.py" \
    --run-manifest "$run_manifest" --events "$event_path" \
    --summary "$summary_path" \
    --output "$result_root/$trace_stem.mixed.json"
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
validate_official_multilane_traces "$trace_root" "${generated_traces[@]}" || exit 2

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

mkdir -p "$out_parent"
out_root="$(mktemp -d "$out_parent/run.XXXXXXXX")"
overall_pairwise_status=0

for lane_count in "${lane_counts[@]}"; do
  if (( lane_count > 1 )); then
    AER_CLEAN_OUT="$out_root" AER_A7_IMPL="$implementation" \
      "$script_dir/run_a7_parallel_event_compactor.sh" "$lane_count" \
      optional_multilane_independent_stall
  fi

  pairwise_marker="$(mktemp)"
  identity_pair_report=""
  affine_pair_report=""
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
    pair_report="$(analyze_pairwise "$trace_stem" "$event_path" \
      "$event_result" "$run_manifest" "$trace_result_root")"
    case "$trace_stem" in
      pairwise_contention_identity) identity_pair_report="$pair_report" ;;
      pairwise_contention_affine) affine_pair_report="$pair_report" ;;
    esac
    analyze_mixed_phase "$trace_stem" "$event_result" "$run_manifest" \
      "$trace_result_root"
    rm -f "$freshness_marker"
  done

  pairwise_cross_map_require_reports "$identity_pair_report" \
    "$affine_pair_report" "lane $lane_count" || exit 2
  if [[ -n "$identity_pair_report" && -n "$affine_pair_report" ]]; then
    cross_output="$out_root/$implementation/k$lane_count/pairwise-cross-map/identity-vs-affine.json"
    if pairwise_cross_map_compare "$project_root" \
      "a7_${implementation}_k${lane_count}" "$pairwise_marker" \
      "$trace_root/pairwise_contention_identity.manifest.json" \
      "$identity_pair_report" \
      "$trace_root/pairwise_contention_affine.manifest.json" \
      "$affine_pair_report" "$cross_output"; then
      :
    else
      status="$?"
      if [[ "$status" -eq 3 ]]; then
        overall_pairwise_status=3
      else
        exit "$status"
      fi
    fi
  fi
  rm -f "$pairwise_marker"
done

printf 'common multi-lane benchmark complete: %s\n' "$out_root"
exit "$overall_pairwise_status"
