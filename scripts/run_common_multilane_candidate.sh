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
out_root="${AER_CLEAN_OUT:-$project_root/results/common-multilane-candidates}"
seed="${AER_SEED:-1}"

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
    ;;
  ganghee)
    [[ -z "$candidate" ]] || usage
    ;;
  ganghee-cluster2)
    [[ -z "$candidate" ]] || usage
    ;;
  drec-prefix)
    case "$candidate" in 1|2|4) ;; *) usage ;; esac
    ;;
  *) usage ;;
esac

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
  analyze_pairwise "$trace_stem" "$event_path" "$run_manifest" \
    "$candidate_result_root"
  analyze_mixed_phase "$trace_stem" "$event_result" "$run_manifest" \
    "$candidate_result_root"
  rm -f "$freshness_marker"
done

printf 'common lane-capacity candidate run complete: %s\n' "$out_root"
