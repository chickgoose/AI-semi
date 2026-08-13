#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
verilator_bin=${VERILATOR:-}
if [[ -z "$verilator_bin" ]]; then
  if command -v verilator >/dev/null 2>&1; then
    verilator_bin=$(command -v verilator)
  else
    verilator_bin=/tmp/a7-sim-bin/verilator
  fi
fi
[[ -x "$verilator_bin" ]] || {
  printf 'verilator not found: %s\n' "$verilator_bin" >&2
  exit 2
}

cd "$root"
for design in \
  'k2_w2_fovea_a7_top physical/k2_w2_tops/filelists/fovea_a7.f' \
  'k2_w2_a2_p6_top physical/k2_w2_tops/filelists/a2_p6.f' \
  'k2_w2_a3_p6_top physical/k2_w2_tops/filelists/a3_p6.f'
do
  read -r top filelist <<< "$design"
  "$verilator_bin" --lint-only --timing --sv -DSYNTHESIS \
    -Wall -Wno-TIMESCALEMOD -Wno-UNOPTFLAT -Wno-SYNCASYNCNET \
    -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME \
    --top-module "$top" -f "$filelist"
  printf 'K2_W2_ELABORATION_LINT_PASS top=%s\n' "$top"
done

for design in \
  'aer_fovea_buffered physical/k2_w2_server_golden/fovea_buffered.f' \
  'aer_cluster2_buffered physical/k2_w2_server_golden/cluster2_buffered.f'
do
  read -r top filelist <<< "$design"
  # Verilator 5.032's gate optimizer aborts internally on the canonical
  # mutually expressed arbiter2 grant equations. Parsing, hierarchy, width,
  # and connectivity lint remain enabled; only that optimizer is disabled.
  "$verilator_bin" --lint-only --timing --sv -DSYNTHESIS \
    -fno-gate -Wall -Wno-TIMESCALEMOD -Wno-UNOPTFLAT -Wno-SYNCASYNCNET \
    -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME -Wno-PINCONNECTEMPTY \
    --top-module "$top" -f "$filelist"
  printf 'K2_W2_SERVER_GOLDEN_LINT_PASS top=%s\n' "$top"
done

for design in \
  'aer_tx16_trad_rowcol_fovea physical/k2_w2_raw_golden/fovea_raw.f' \
  'aer_tx16_trad_rowcol_fovea_cluster2 physical/k2_w2_raw_golden/cluster2_raw.f'
do
  read -r top filelist <<< "$design"
  "$verilator_bin" --lint-only --timing --sv -DSYNTHESIS \
    -fno-gate -Wall -Wno-TIMESCALEMOD -Wno-UNOPTFLAT \
    -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL -Wno-DECLFILENAME \
    --top-module "$top" -f "$filelist"
  printf 'K2_W2_RAW_GOLDEN_LINT_PASS top=%s\n' "$top"
done
