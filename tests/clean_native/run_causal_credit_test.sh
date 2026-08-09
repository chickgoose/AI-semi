#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERILATOR_BIN="${VERILATOR:-verilator}"

command -v "$VERILATOR_BIN" >/dev/null 2>&1 || {
  printf 'verilator not found: %s\n' "$VERILATOR_BIN" >&2
  exit 1
}

if [[ -n "${AER_CAUSAL_CREDIT_TEST_OUT:-}" ]]; then
  mkdir -p "$AER_CAUSAL_CREDIT_TEST_OUT"
  OUT_DIR="$(mktemp -d "$AER_CAUSAL_CREDIT_TEST_OUT/run.XXXXXX")"
else
  OUT_DIR="$(mktemp -d /tmp/aer-cluster2-causal-credit-test.XXXXXX)"
fi
printf 'causal-credit self-test output: %s\n' "$OUT_DIR"

sources=(
  "$PROJECT_ROOT/tests/clean_native/aer_cluster2_causal_credit_monitor.sv"
  "$PROJECT_ROOT/tests/clean_native/aer_cluster2_causal_credit_tb.sv"
)

compile_case() {
  local name="$1"
  local define="${2:-}"
  local -a command=("$VERILATOR_BIN" --binary --timing --assert -Wall
    -Wno-fatal -Wno-BLKSEQ --top-module aer_cluster2_causal_credit_tb
    --Mdir "$OUT_DIR/$name-obj")
  [[ -z "$define" ]] || command+=("-D$define")
  command+=("${sources[@]}")
  "${command[@]}"
  if [[ ! -x "$OUT_DIR/$name-obj/Vaer_cluster2_causal_credit_tb" ]]; then
    printf 'compile succeeded without executable: %s\n' "$name" >&2
    exit 1
  fi
}

run_case() {
  local name="$1"
  "$OUT_DIR/$name-obj/Vaer_cluster2_causal_credit_tb" \
    >"$OUT_DIR/$name.log" 2>&1
}

compile_case legal
run_case legal
grep -Fq \
  'GANGHEE_CLUSTER2_CAUSAL_CREDIT_PASS sampled=3 raw=3 accepted=3 reset_after_drain=1' \
  "$OUT_DIR/legal.log"

for fault in immediate_repeat delayed_stale seam_overlap; do
  case "$fault" in
    immediate_repeat) define=AER_CAUSAL_IMMEDIATE_REPEAT ;;
    delayed_stale) define=AER_CAUSAL_DELAYED_STALE ;;
    seam_overlap) define=AER_CAUSAL_SEAM_OVERLAP ;;
  esac
  # Compilation is deliberately outside set +e. A syntax/build failure is a
  # runner failure and can never fall through to an old executable.
  compile_case "$fault" "$define"
  set +e
  run_case "$fault"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    printf '%s causal-credit fault unexpectedly passed\n' "$fault" >&2
    exit 1
  fi
  case "$fault" in
    immediate_repeat|delayed_stale)
      expected='GANGHEE_CLUSTER2_CAUSAL_CREDIT raw_without_credit mask=0020 credit=0000'
      ;;
    seam_overlap)
      expected='GANGHEE_CLUSTER2_CAUSAL_CREDIT seam_req_result_overlap mask=0020'
      ;;
  esac
  grep -Fq "$expected" "$OUT_DIR/$fault.log"
  printf 'GANGHEE_CLUSTER2_CAUSAL_CREDIT_FAIL_CLOSED_PASS fault=%s status=%d\n' \
    "$fault" "$status"
done
