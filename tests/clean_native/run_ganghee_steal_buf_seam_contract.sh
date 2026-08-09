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
expected_sha256=(
  "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684"
  "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31"
  "56fdb33a634ea8716b60e3e3b8d54c3435a5d808785e097dbab5a3bdd6dddf96"
)
for source_index in "${!required[@]}"; do
  source_file="${required[$source_index]}"
  test -r "$source_file" || {
    printf 'missing Ganghee source: %s\n' "$source_file" >&2
    exit 1
  }
  actual_sha256="$(sha256sum "$source_file")"
  actual_sha256="${actual_sha256%% *}"
  test "$actual_sha256" = "${expected_sha256[$source_index]}" || {
    printf 'Ganghee snapshot SHA mismatch: %s\nexpected %s\nactual   %s\n' \
      "$source_file" "${expected_sha256[$source_index]}" "$actual_sha256" >&2
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
grep -Fqx 'SERVICE_READY_DUPLICATE outputs=3 logical_events=1' \
  "$output_dir/run.log"
grep -Fqx 'EDGE_STATE_FULL_LOSS prefill_admitted=8 outputs=8' \
  "$output_dir/run.log"
grep -Fqx 'STATELESS_ADMISSION_RETRY prefill_admitted=8 outputs=9' \
  "$output_dir/run.log"
grep -Fqx 'GANGHEE_STEAL_BUF_SEAM_CONTRACT_PASS' "$output_dir/run.log"

"$verilator_bin" --binary --timing --assert -Wall -Wno-fatal \
  -Wno-BLKSEQ --gate-stmts 0 \
  --top-module aer_ganghee_cluster2_steal_buf_real_direct_tb \
  --Mdir "$output_dir/direct-obj" \
  "${required[@]}" \
  "$project_root/tests/clean_native/aer_ganghee_cluster2_steal_buf_real_direct_tb.sv" \
  >"$output_dir/direct-compile.log" 2>&1
"$output_dir/direct-obj/Vaer_ganghee_cluster2_steal_buf_real_direct_tb" \
  >"$output_dir/direct-run.log" 2>&1
grep -Fqx 'GANGHEE_CLUSTER2_STEAL_BUF_REAL_DIRECT_PASS' \
  "$output_dir/direct-run.log"

printf 'GANGHEE_SNAPSHOT_SHA256_PASS\n'
tail -n 8 "$output_dir/run.log"
tail -n 6 "$output_dir/direct-run.log"
