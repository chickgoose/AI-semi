#!/usr/bin/env bash

# Shared fail-closed orchestration for the common 22-trace runners.  This file is
# sourced by runners that already use `set -euo pipefail`.

validate_official_multilane_traces() {
  local trace_root="$1"
  shift
  python3 - "$trace_root" "$@" <<'PY'
import collections
import pathlib
import sys

expected = {
    "core_simultaneous_identity",
    "pairwise_contention_identity",
    "pairwise_contention_affine",
    "uniform_l1p00_s2001", "uniform_l1p00_s2002", "uniform_l1p00_s2003",
    "uniform_l1p25_s2001", "uniform_l1p25_s2002", "uniform_l1p25_s2003",
    "uniform_l1p50_s2001", "uniform_l1p50_s2002", "uniform_l1p50_s2003",
    "uniform_l2p00_s2001", "uniform_l2p00_s2002", "uniform_l2p00_s2003",
    "shape_b4", "shape_b16", "global_fanin_identity",
    "phase_transition_s3501", "phase_transition_s3502",
    "mixed_phase_always_ready_identity",
    "mixed_phase_always_ready_bit_reverse",
}
root = pathlib.Path(sys.argv[1]).resolve()
paths = [pathlib.Path(value) for value in sys.argv[2:]]
stems = []
errors = []
for path in paths:
    if path.name.endswith(".events.jsonl"):
        stem = path.name.removesuffix(".events.jsonl")
    else:
        errors.append(f"not an events.jsonl path: {path}")
        continue
    if path.parent.resolve() != root:
        errors.append(f"trace escapes generated root: {path}")
    if not path.is_file():
        errors.append(f"trace is missing or not a regular file: {path}")
    manifest = root / f"{stem}.manifest.json"
    if not manifest.is_file() or manifest.stat().st_size == 0:
        errors.append(f"manifest is missing or empty: {manifest}")
    stems.append(stem)
counts = collections.Counter(stems)
duplicates = sorted(stem for stem, count in counts.items() if count != 1)
missing = sorted(expected - set(stems))
unexpected = sorted(set(stems) - expected)
if len(paths) != len(expected) or duplicates or missing or unexpected or errors:
    details = [
        f"count={len(paths)} expected={len(expected)}",
        f"duplicates={duplicates}",
        f"missing={missing}",
        f"unexpected={unexpected}",
        *errors,
    ]
    raise SystemExit("official 22-trace set validation failed: " + "; ".join(details))
PY
}

pairwise_cross_map_require_reports() {
  local identity_report="$1"
  local affine_report="$2"
  local scope="$3"
  if [[ -z "$identity_report" || -z "$affine_report" ]]; then
    printf 'pairwise cross-map requires identity and affine reports for %s\n' \
      "$scope" >&2
    return 2
  fi
}

pairwise_cross_map_compare() {
  local project_root="$1"
  local expected_candidate="$2"
  local freshness_marker="$3"
  local identity_manifest="$4"
  local identity_report="$5"
  local affine_manifest="$6"
  local affine_report="$7"
  local output="$8"
  local output_dir
  local temporary_output
  local validation_status

  [[ -n "$expected_candidate" ]] || {
    printf 'pairwise cross-map expected candidate is empty\n' >&2
    return 2
  }
  [[ -f "$freshness_marker" ]] || {
    printf 'pairwise cross-map freshness marker is missing: %s\n' \
      "$freshness_marker" >&2
    return 2
  }
  local input
  for input in "$identity_manifest" "$affine_manifest"; do
    [[ -s "$input" ]] || {
      printf 'pairwise cross-map manifest is missing: %s\n' "$input" >&2
      return 2
    }
  done
  for input in "$identity_report" "$affine_report"; do
    [[ -s "$input" && "$input" -nt "$freshness_marker" ]] || {
      printf 'pairwise cross-map report is missing or stale: %s\n' "$input" >&2
      return 2
    }
  done
  [[ ! -e "$output" && ! -L "$output" ]] || {
    printf 'pairwise cross-map output collision: %s\n' "$output" >&2
    return 2
  }
  output_dir="$(dirname "$output")"
  mkdir -p "$output_dir"
  temporary_output="$(mktemp "$output_dir/.identity-vs-affine.tmp.XXXXXXXX")" || {
    printf 'pairwise cross-map could not create secure temporary output\n' >&2
    return 2
  }

  python3 -c '
import json, pathlib, sys
expected = sys.argv[1]
for label, name in (("identity", sys.argv[2]), ("affine", sys.argv[3])):
    path = pathlib.Path(name)
    try:
        candidate = json.loads(path.read_text(encoding="utf-8")).get("candidate")
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} pairwise report cannot be read: {exc}")
    if candidate != expected:
        raise SystemExit(
            f"{label} pairwise candidate mismatch: {candidate!r} != {expected!r}"
        )
' "$expected_candidate" "$identity_report" "$affine_report" || return 2

  if ! python3 \
    "$project_root/benchmarks/clean_slate_aer/pairwise_cross_map_compare.py" \
    --identity-manifest "$identity_manifest" \
    --identity-report "$identity_report" \
    --affine-manifest "$affine_manifest" \
    --affine-report "$affine_report" \
    --output "$temporary_output"; then
    rm -f "$temporary_output"
    return 2
  fi

  validation_status=0
  if python3 -c '
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"cross-map temporary output is invalid JSON: {exc}")
if not isinstance(payload, dict):
    raise SystemExit("cross-map temporary output must be a JSON object")
if payload.get("candidate") != expected:
    raise SystemExit("cross-map temporary output candidate disagrees with runner scope")
rankable = payload.get("rankable")
if not isinstance(rankable, bool):
    raise SystemExit("cross-map temporary output has no boolean rankable field")
reasons = payload.get("rankability_reasons")
if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
    raise SystemExit("cross-map temporary output has invalid rankability_reasons")
raise SystemExit(0 if rankable else 3)
' "$temporary_output" "$expected_candidate"; then
    validation_status=0
  else
    validation_status="$?"
    if [[ "$validation_status" -ne 3 ]]; then
      rm -f "$temporary_output"
      return 2
    fi
  fi

  # Hard-link publication is atomic and refuses to overwrite an existing user
  # artifact. Both names live in the same result directory/filesystem.
  if ! ln "$temporary_output" "$output"; then
    printf 'pairwise cross-map output appeared concurrently: %s\n' "$output" >&2
    rm -f "$temporary_output"
    return 2
  fi
  rm -f "$temporary_output"

  if [[ "$validation_status" -eq 3 ]]; then
    python3 -c '
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    "PAIRWISE_CROSS_MAP_NON_RANKABLE "
    + ",".join(payload["rankability_reasons"]),
    file=sys.stderr,
)
' "$output"
    return 3
  fi
  return 0
}
