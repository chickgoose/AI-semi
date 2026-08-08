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

python3 "$project_root/benchmarks/clean_slate_aer/generate_trace.py" \
  --manifest "$project_root/benchmarks/clean_slate_aer/manifest.multilane-n16.json" \
  --output-dir "$trace_root"

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

  while IFS= read -r event_path; do
    trace_stem="$(basename "$event_path" .events.jsonl)"
    AER_CLEAN_OUT="$out_root" \
    AER_TRACE_JSONL="$event_path" \
    AER_TRACE_MANIFEST="$trace_root/$trace_stem.manifest.json" \
    AER_A7_IMPL="$implementation" \
      "$script_dir/run_a7_parallel_event_compactor.sh" "$lane_count"
  done < <(find "$trace_root" -maxdepth 1 -name '*.events.jsonl' -print | sort)
done

printf 'common multi-lane benchmark complete: %s\n' "$out_root"
