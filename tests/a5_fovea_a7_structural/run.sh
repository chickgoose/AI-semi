#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${A5_FOVEA_A7_OUT:-}"
[[ -n "$out" ]] || {
  printf 'A5_FOVEA_A7_OUT is required and must name a new directory\n' >&2
  exit 2
}
[[ ! -e "$out" ]] || {
  printf 'refusing to overwrite A5_FOVEA_A7_OUT=%s\n' "$out" >&2
  exit 2
}
exec python3 "$here/structural_compare.py" \
  --a7-repo "${A7_REPO:-$here/../../../a7}" \
  --yosys "${YOSYS:-yosys}" --output "$out"
