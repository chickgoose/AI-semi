#!/usr/bin/env bash

# Shared fail-closed orchestration for the common 22-trace runners.  This file is
# sourced by runners that already use `set -euo pipefail`.

pairwise_cross_map_compare() {
  local project_root="$1"
  local expected_candidate="$2"
  local freshness_marker="$3"
  local identity_manifest="$4"
  local identity_report="$5"
  local affine_manifest="$6"
  local affine_report="$7"
  local output="$8"
  local temporary_output="${output}.tmp.$$"

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
  [[ ! -e "$output" && ! -e "$temporary_output" ]] || {
    printf 'pairwise cross-map output collision: %s\n' "$output" >&2
    return 2
  }
  mkdir -p "$(dirname "$output")"

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

  # Hard-link publication is atomic and refuses to overwrite an existing user
  # artifact. Both names live in the same result directory/filesystem.
  if ! ln "$temporary_output" "$output"; then
    printf 'pairwise cross-map output appeared concurrently: %s\n' "$output" >&2
    rm -f "$temporary_output"
    return 2
  fi
  rm -f "$temporary_output"

  if python3 -c '
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("candidate") != expected:
    raise SystemExit("cross-map output candidate disagrees with runner scope")
if payload.get("rankable") is True:
    raise SystemExit(0)
if payload.get("rankable") is False:
    print(
        "PAIRWISE_CROSS_MAP_NON_RANKABLE "
        + ",".join(payload.get("rankability_reasons", [])),
        file=sys.stderr,
    )
    raise SystemExit(3)
raise SystemExit("cross-map output has no boolean rankable field")
' "$output" "$expected_candidate"; then
    return 0
  else
    local status="$?"
    [[ "$status" -eq 3 ]] && return 3
    return 2
  fi
}
