#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/../../.." && pwd)

case "${1:-all}" in
  --capability)
    if [[ "${2:-}" != "independent-lane-stall" ]]; then
      echo "A2_K2_CAPABILITY_ERROR unknown=${2:-missing}" >&2
      exit 2
    fi
    echo "A2_K2_CAPABILITY_SKIP independent-lane-stall reason=official_binding_requires_uniform_always_ready"
    exit 77
    ;;
  --require-capability)
    if [[ "${2:-}" != "independent-lane-stall" ]]; then
      echo "A2_K2_CAPABILITY_ERROR unknown=${2:-missing}" >&2
      exit 2
    fi
    echo "A2_K2_CAPABILITY_HOLD independent-lane-stall unsupported" >&2
    exit 2
    ;;
  all)
    ;;
  *)
    echo "usage: $0 [all|--capability independent-lane-stall|--require-capability independent-lane-stall]" >&2
    exit 2
    ;;
esac

if [[ -n "${VERILATOR:-}" ]]; then
  VERILATOR_BIN=${VERILATOR}
elif command -v verilator >/dev/null 2>&1; then
  VERILATOR_BIN=$(command -v verilator)
elif [[ -x /tmp/a7-toolchain/usr/bin/verilator ]]; then
  VERILATOR_BIN=/tmp/a7-toolchain/usr/bin/verilator
else
  echo "A2_K2_TOOL_FAIL verilator-not-found" >&2
  exit 2
fi

RUN_ROOT=$(mktemp -d /tmp/a2-k2-official.XXXXXXXX)
cleanup() {
  case "${RUN_ROOT}" in
    /tmp/a2-k2-official.*) rm -rf -- "${RUN_ROOT}" ;;
    *) echo "A2_K2_CLEANUP_REFUSED path=${RUN_ROOT}" >&2 ;;
  esac
}
trap cleanup EXIT

cd "${REPO_ROOT}"
python3 "${SCRIPT_DIR}/tools/check_provenance.py" --repo "${REPO_ROOT}"
python3 "${SCRIPT_DIR}/tests/test_provenance.py"
echo "A2_K2_TOOL_RECEIPT $(${VERILATOR_BIN} --version)"

COMMON_FLAGS=(
  --binary --timing --assert -Wno-fatal -Wno-TIMESCALEMOD
  -Wno-DECLFILENAME -Wno-PINCONNECTEMPTY
)
DIRECT_TB="candidates/a2_batched_iwrr_k2/official_common/tb/a2_k2_official_direct_tb.sv"
FILELIST="candidates/a2_batched_iwrr_k2/official_common/official_common.f"

compile_direct() {
  local name=$1
  local define=${2:-}
  local mdir="${RUN_ROOT}/obj-direct-${name}"
  local log="${RUN_ROOT}/compile-direct-${name}.log"
  local args=("${COMMON_FLAGS[@]}" --top-module a2_k2_official_direct_tb
              --Mdir "${mdir}" -o "sim-${name}")
  if [[ -n "${define}" ]]; then
    args+=("-D${define}")
  fi
  args+=(tb/clean/aer_bench_if.sv -f "${FILELIST}" "${DIRECT_TB}")
  if ! "${VERILATOR_BIN}" "${args[@]}" >"${log}" 2>&1; then
    echo "A2_K2_COMPILE_FAIL case=${name}" >&2
    tail -80 "${log}" >&2
    exit 2
  fi
  printf '%s\n' "${mdir}/sim-${name}"
}

run_pass() {
  local binary=$1
  local name=$2
  local sentinel=$3
  local log="${RUN_ROOT}/run-${name}.log"
  if ! "${binary}" >"${log}" 2>&1; then
    echo "A2_K2_RUNTIME_FAIL case=${name}" >&2
    tail -80 "${log}" >&2
    exit 2
  fi
  if ! grep -Fq -- "${sentinel}" "${log}"; then
    echo "A2_K2_SENTINEL_FAIL case=${name} expected=${sentinel}" >&2
    tail -80 "${log}" >&2
    exit 2
  fi
  echo "A2_K2_CASE_PASS ${name}"
}

run_expected_failure() {
  local binary=$1
  local name=$2
  local sentinel=$3
  shift 3
  local log="${RUN_ROOT}/run-${name}.log"
  local status
  set +e
  "${binary}" "$@" >"${log}" 2>&1
  status=$?
  set -e
  if [[ ${status} -eq 0 ]]; then
    echo "A2_K2_MUTATION_FALSE_PASS case=${name}" >&2
    tail -80 "${log}" >&2
    exit 2
  fi
  if ! grep -Fq -- "${sentinel}" "${log}"; then
    echo "A2_K2_MUTATION_DIAGNOSTIC_FAIL case=${name} expected=${sentinel}" >&2
    tail -80 "${log}" >&2
    exit 2
  fi
  echo "A2_K2_MUTATION_PASS ${name} status=${status} diagnostic=${sentinel}"
}

normal_binary=$(compile_direct normal)
run_pass "${normal_binary}" direct-normal A2_K2_DIRECT_PASS

for mutation in \
  "swap-order:A2_K2_MUT_SWAP_ORDER:A2_K2_ASSERT_ORDER" \
  "duplicate-lane:A2_K2_MUT_DUPLICATE_LANE:A2_K2_ASSERT_ORDER" \
  "drop-credit:A2_K2_MUT_DROP_CREDIT:A2_K2_ASSERT_ATOMIC_CREDIT" \
  "event-corrupt:A2_K2_MUT_EVENT_CORRUPT:A2_K2_ASSERT_ORDER" \
  "reset-leak:A2_K2_MUT_RESET_LEAK:A2_K2_ASSERT_RESET_QUIET"
do
  IFS=: read -r name define sentinel <<<"${mutation}"
  binary=$(compile_direct "${name}" "${define}")
  run_expected_failure "${binary}" "${name}" "${sentinel}"
done

COMMON_MDIR="${RUN_ROOT}/obj-common"
COMMON_COMPILE_LOG="${RUN_ROOT}/compile-common.log"
if ! "${VERILATOR_BIN}" "${COMMON_FLAGS[@]}" \
    --top-module aer_clean_tb --Mdir "${COMMON_MDIR}" -o sim-common \
    -DAER_CLEAN_GANGHEE_NATIVE \
    -GNUM_SOURCES=16 -GADDR_WIDTH=16 -GRETIRE_LANES=2 -GFIFO_DEPTH=0 \
    tb/clean/aer_bench_if.sv -f "${FILELIST}" \
    tb/clean/aer_clean_assertions.sv tb/clean/aer_clean_tb.sv \
    >"${COMMON_COMPILE_LOG}" 2>&1; then
  echo "A2_K2_COMMON_COMPILE_FAIL" >&2
  tail -100 "${COMMON_COMPILE_LOG}" >&2
  exit 2
fi

for test_name in basic_single basic_sparse basic_simultaneous basic_reset_drain
do
  log="${RUN_ROOT}/common-${test_name}.log"
  metrics="${RUN_ROOT}/common-${test_name}-metrics.csv"
  events="${RUN_ROOT}/common-${test_name}-events.csv"
  if ! "${COMMON_MDIR}/sim-common" \
      +CLEAN_TEST="${test_name}" +CANDIDATE=a2_k2_official_always_ready \
      +METRICS="${metrics}" +EVENT_METRICS="${events}" \
      +STIM_CYCLES=96 +LOAD_PCT=4 +SEED=17 >"${log}" 2>&1; then
    echo "A2_K2_COMMON_RUNTIME_FAIL test=${test_name}" >&2
    tail -100 "${log}" >&2
    exit 2
  fi
  if ! grep -Fq -- "AER_CLEAN_TEST_PASS ${test_name}" "${log}"; then
    echo "A2_K2_COMMON_SENTINEL_FAIL test=${test_name}" >&2
    tail -100 "${log}" >&2
    exit 2
  fi
  echo "A2_K2_COMMON_PASS ${test_name}"
done

echo "A2_K2_CAPABILITY_SKIP independent-lane-stall reason=official_binding_requires_uniform_always_ready"
echo "A2_K2_OFFICIAL_ALWAYS_READY_PASS direct=1 mutations=5 common=4"
