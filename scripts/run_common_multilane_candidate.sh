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

python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.multilane-n16.json" \
  --output-dir "$trace_root"

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

while IFS= read -r event_path; do
  trace_stem="$(basename "$event_path" .events.jsonl)"
  run_manifest="$trace_root/$trace_stem.manifest.json"
  trace_out_root="$out_root/$trace_stem"
  case "$binding" in
    clean)
      AER_CLEAN_OUT="$trace_out_root" AER_NUM_SOURCES=16 \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_clean_benchmark.sh" "$candidate"
      ;;
    ganghee)
      AER_CLEAN_OUT="$trace_out_root" \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_ganghee_native_benchmark.sh"
      ;;
    ganghee-cluster2)
      AER_CLEAN_OUT="$trace_out_root" \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_ganghee_cluster2_benchmark.sh"
      ;;
    drec-prefix)
      AER_CLEAN_OUT="$trace_out_root" AER_A7_IMPL=prefix \
      AER_TRACE_JSONL="$event_path" AER_TRACE_MANIFEST="$run_manifest" \
        "$script_dir/run_a7_parallel_event_compactor.sh" "$candidate"
      ;;
  esac
done < <(find "$trace_root" -maxdepth 1 -name '*.events.jsonl' -print | sort)

printf 'common lane-capacity candidate run complete: %s\n' "$out_root"
