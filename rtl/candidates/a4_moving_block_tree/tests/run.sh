#!/usr/bin/env bash
set -euo pipefail

candidate_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

requested_verilator="${AER_VERILATOR:-${VERILATOR:-verilator}}"
resolved_verilator="$(command -v "$requested_verilator" 2>/dev/null || true)"
if [[ -z "$resolved_verilator" || ! -x "$resolved_verilator" ]]; then
  printf 'A4 moving-block qualification requires Verilator; cannot resolve: %s\n' \
    "$requested_verilator" >&2
  exit 2
fi
if ! "$resolved_verilator" --version 2>/dev/null | grep -q '^Verilator '; then
  printf 'A4 moving-block qualification rejected non-Verilator executable: %s\n' \
    "$resolved_verilator" >&2
  exit 2
fi
export AER_VERILATOR_RESOLVED="$resolved_verilator"
python3 -m unittest discover -s "$candidate_dir/tests" -p 'test_*.py' -v
