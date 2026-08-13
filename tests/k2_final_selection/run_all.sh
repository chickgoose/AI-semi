#!/usr/bin/env bash
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${K2_FINAL_SELECTION_OUT:-/tmp/k2-final-selection-result.json}

python3 -B -m unittest -v tests.k2_final_selection.test_selection
test ! -e "$out"
python3 -B "$repo/audits/k2_final_selection/generate_selection.py" \
  --repo-root "$repo" --output "$out"
cmp "$out" "$repo/audits/k2_final_selection/result.json"
grep -q '"selected_key": "a2"' "$out"
grep -q '"standard_cell_area_fmax_power_energy_routing": "HOLD"' "$out"
printf 'K2_FINAL_SELECTION_ALL_PASS selected=a2 fallback=a3 physical=HOLD\n'
