#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s clean <run_clean_benchmark design>\n' "$0" >&2
  printf '       %s ganghee\n' "$0" >&2
  printf '       %s ganghee-cluster2\n' "$0" >&2
  printf '       %s drec-prefix <1|2|4>\n' "$0" >&2
  exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
binding="$1"
candidate="${2:-}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
trace_root="${AER_COMMON_MULTILANE_TRACE_DIR:-/tmp/aer-common-multilane-n16}"
out_parent="${AER_CLEAN_OUT:-$project_root/results/common-multilane-candidates}"
seed="${AER_SEED:-1}"
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
[[ "${#generated_traces[@]}" -eq 22 ]] || {
  printf 'expected exactly 22 indexed multi-lane traces, found %d\n' \
    "${#generated_traces[@]}" >&2
  exit 1
}

case "$binding" in
  clean)
    [[ -n "$candidate" ]] || usage
    expected_report_candidate="$candidate"
    candidate_scope="clean-$candidate-n16-seed$seed"
    ;;
  ganghee)
    [[ -z "$candidate" ]] || usage
    expected_report_candidate="ganghee-native-coordinate-source-projection"
    candidate_scope="ganghee-native-n16-seed$seed"
    ;;
  ganghee-cluster2)
    [[ -z "$candidate" ]] || usage
    expected_report_candidate="ganghee-cluster2-row-bitmap"
    candidate_scope="ganghee-cluster2-n16-seed$seed"
    ;;
  drec-prefix)
    case "$candidate" in 1|2|4) ;; *) usage ;; esac
    expected_report_candidate="a7_prefix_k$candidate"
    candidate_scope="drec-prefix-k$candidate-n16-seed$seed"
    ;;
  *) usage ;;
esac

mkdir -p "$out_parent"
out_root="$(mktemp -d "$out_parent/run.XXXXXXXX")"
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
  trace_out_root="$out_root/$trace_stem"
  freshness_marker="$(mktemp)"
  case "$binding" in
    clean)
      candidate_result_root="$trace_out_root/$candidate-n16-seed$seed"
      AER_CLEAN_OUT="$trace_out_root" AER_NUM_SOURCES=16 \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_clean_benchmark.sh" "$candidate"
      ;;
    ganghee)
      candidate_result_root="$trace_out_root/ganghee-native-n16-seed$seed"
      AER_CLEAN_OUT="$trace_out_root" \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_ganghee_native_benchmark.sh"
      ;;
    ganghee-cluster2)
      candidate_result_root="$trace_out_root/ganghee-cluster2-n16-seed$seed"
      AER_CLEAN_OUT="$trace_out_root" \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_ganghee_cluster2_benchmark.sh"
      ;;
    drec-prefix)
      candidate_result_root="$trace_out_root/prefix/k$candidate/$trace_stem"
      AER_CLEAN_OUT="$trace_out_root" AER_A7_IMPL=prefix \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_a7_parallel_event_compactor.sh" "$candidate"
      ;;
  esac
  event_result="$candidate_result_root/trace.events.csv"
  [[ -s "$event_result" && "$event_result" -nt "$freshness_marker" ]] || {
    printf 'candidate did not produce a fresh event result: %s\n' \
      "$event_result" >&2
    exit 1
  }
  pair_report="$(analyze_pairwise "$trace_stem" "$event_path" \
    "$event_result" "$run_manifest" "$candidate_result_root")"
  case "$trace_stem" in
    pairwise_contention_identity) identity_pair_report="$pair_report" ;;
    pairwise_contention_affine) affine_pair_report="$pair_report" ;;
  esac
  analyze_mixed_phase "$trace_stem" "$event_result" "$run_manifest" \
    "$candidate_result_root"
  rm -f "$freshness_marker"
done

if [[ -n "$identity_pair_report" && -n "$affine_pair_report" ]]; then
  cross_output="$out_root/pairwise-cross-map/$candidate_scope/identity-vs-affine.json"
  if pairwise_cross_map_compare "$project_root" "$expected_report_candidate" \
    "$pairwise_marker" \
    "$trace_root/pairwise_contention_identity.manifest.json" \
    "$identity_pair_report" \
    "$trace_root/pairwise_contention_affine.manifest.json" \
    "$affine_pair_report" "$cross_output"; then
    pairwise_status=0
  else
    pairwise_status="$?"
    [[ "$pairwise_status" -eq 3 ]] || exit "$pairwise_status"
  fi
elif [[ -n "$identity_pair_report" || -n "$affine_pair_report" ]]; then
  printf 'pairwise cross-map requires both identity and affine reports\n' >&2
  exit 2
else
  pairwise_status=0
fi
rm -f "$pairwise_marker"

printf 'common lane-capacity candidate run complete: %s\n' "$out_root"
exit "$pairwise_status"
