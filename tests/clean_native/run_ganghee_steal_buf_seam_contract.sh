#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
snapshot_dir="${GANGHEE_SNAPSHOT_DIR:-/tmp/team-latest-aer/ganghee}"
verilator_bin="${VERILATOR:-/tmp/a7-sim-bin/verilator}"
output_dir="${A3_SEAM_TEST_OUT:-/tmp/a3-steal-buf-seam-contract}"

required=(
  "$snapshot_dir/arbiter2.v"
  "$snapshot_dir/arbiter4_tree.v"
  "$snapshot_dir/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf.v"
)
for source_file in "${required[@]}"; do
  test -r "$source_file" || {
    printf 'missing Ganghee source: %s\n' "$source_file" >&2
    exit 1
  }
done
test -x "$verilator_bin" || {
  printf 'missing Verilator executable: %s\n' "$verilator_bin" >&2
  exit 1
}

mkdir -p "$output_dir"
"$verilator_bin" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ --gate-stmts 0 \
  --top-module aer_ganghee_steal_buf_seam_contract_tb \
  --Mdir "$output_dir/obj" \
  "${required[@]}" \
  "$project_root/tests/clean_native/aer_ganghee_steal_buf_seam_contract_tb.sv" \
  >"$output_dir/compile.log" 2>&1

"$output_dir/obj/Vaer_ganghee_steal_buf_seam_contract_tb" \
  >"$output_dir/run.log" 2>&1
grep -q 'SERVICE_READY_DUPLICATE outputs=3 logical_events=1' \
  "$output_dir/run.log"
grep -q 'EDGE_STATE_FULL_LOSS prefill_admitted=8 outputs=8' \
  "$output_dir/run.log"
grep -q 'STATELESS_ADMISSION_RETRY prefill_admitted=8 outputs=9' \
  "$output_dir/run.log"
grep -q 'GANGHEE_STEAL_BUF_SEAM_CONTRACT_PASS' "$output_dir/run.log"
tail -n 8 "$output_dir/run.log"
