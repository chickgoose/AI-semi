#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VERILATOR_BIN="${VERILATOR:-verilator}"
OUT_DIR="${AER_CAUSAL_CREDIT_TEST_OUT:-/tmp/aer-cluster2-causal-credit-test}"

command -v "$VERILATOR_BIN" >/dev/null 2>&1 || {
  printf 'verilator not found: %s\n' "$VERILATOR_BIN" >&2
  exit 1
}
mkdir -p "$OUT_DIR"

sources=(
  "$PROJECT_ROOT/tests/clean_native/aer_cluster2_causal_credit_monitor.sv"
  "$PROJECT_ROOT/tests/clean_native/aer_cluster2_causal_credit_tb.sv"
)

build_and_run() {
  local name="$1"
  local define="${2:-}"
  local -a command=("$VERILATOR_BIN" --binary --timing --assert -Wall
    -Wno-fatal -Wno-BLKSEQ --top-module aer_cluster2_causal_credit_tb
    --Mdir "$OUT_DIR/$name-obj")
  [[ -z "$define" ]] || command+=("-D$define")
  command+=("${sources[@]}")
  "${command[@]}"
  "$OUT_DIR/$name-obj/Vaer_cluster2_causal_credit_tb" \
    >"$OUT_DIR/$name.log" 2>&1
}

build_and_run legal
grep -q \
  'GANGHEE_CLUSTER2_CAUSAL_CREDIT_PASS sampled=3 raw=3 accepted=3 reset_after_drain=1' \
  "$OUT_DIR/legal.log"

for fault in immediate_repeat delayed_stale; do
  case "$fault" in
    immediate_repeat) define=AER_CAUSAL_IMMEDIATE_REPEAT ;;
    delayed_stale) define=AER_CAUSAL_DELAYED_STALE ;;
  esac
  set +e
  build_and_run "$fault" "$define"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    printf '%s causal-credit fault unexpectedly passed\n' "$fault" >&2
    exit 1
  fi
  grep -q 'GANGHEE_CLUSTER2_CAUSAL_CREDIT raw_without_credit mask=0020 credit=0000' \
    "$OUT_DIR/$fault.log"
  printf 'GANGHEE_CLUSTER2_CAUSAL_CREDIT_FAIL_CLOSED_PASS fault=%s status=%d\n' \
    "$fault" "$status"
done
