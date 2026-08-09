#!/usr/bin/env bash

# Shared postprocessor for both trace-capable common runners.  Callers remove
# the three exact outputs before simulation; this function owns all analysis.
mixed_phase_clear_outputs() {
  local report_name="$1" summary_path="$2" event_path="$3" json_path="$4"
  [[ "$report_name" == "mixed_phase_always_ready" ]] || return 0
  rm -f "$summary_path" "$event_path" "$json_path"
}

mixed_phase_require_qualified() {
  local report_name="$1" project_root="$2" manifest_path="$3"
  local summary_path="$4" event_path="$5" json_path="$6"
  local analyzer_status=0
  [[ "$report_name" == "mixed_phase_always_ready" ]] || return 0

  python3 "$project_root/benchmarks/clean_slate_aer/mixed_phase_always_ready_metrics.py" \
    --run-manifest "$manifest_path" --events "$event_path" \
    --summary "$summary_path" --require-qualified --output "$json_path" || \
    analyzer_status=$?
  [[ -s "$summary_path" && -s "$event_path" && -s "$json_path" ]] || {
    printf 'mixed-phase run did not produce fresh nonempty summary/event/json outputs\n' >&2
    (( analyzer_status != 0 )) || analyzer_status=1
    return "$analyzer_status"
  }
  return "$analyzer_status"
}
