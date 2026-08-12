#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --binding FILE --latency 0|1 [--rtl FILE ...] [--link-binding FILE --link-rtl FILE ...]"
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
binding=""
latency=""
link_binding=""
rtl_files=()
link_rtl_files=()

while (($#)); do
  case "$1" in
    --binding) binding=$2; shift 2 ;;
    --latency) latency=$2; shift 2 ;;
    --rtl) rtl_files+=("$2"); shift 2 ;;
    --link-binding) link_binding=$2; shift 2 ;;
    --link-rtl) link_rtl_files+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$binding" || ("$latency" != 0 && "$latency" != 1) ]]; then
  usage >&2
  exit 2
fi

iverilog_bin=${K2_IVERILOG:-}
vvp_bin=${K2_VVP:-}
if [[ -z "$iverilog_bin" ]]; then
  iverilog_bin=$(command -v iverilog || true)
  [[ -n "$iverilog_bin" ]] || iverilog_bin=/tmp/a7-toolchain/usr/bin/iverilog
fi
if [[ -z "$vvp_bin" ]]; then
  vvp_bin=$(command -v vvp || true)
  [[ -n "$vvp_bin" ]] || vvp_bin=/tmp/a7-toolchain/usr/bin/vvp
fi
if [[ ! -x "$iverilog_bin" || ! -x "$vvp_bin" ]]; then
  echo "required Icarus tools not found; set K2_IVERILOG and K2_VVP" >&2
  exit 2
fi
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/k2-conformance.XXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT

"$iverilog_bin" -g2012 -Wall -I "$script_dir" \
  -DK2_EXPECT_LATENCY="$latency" -s k2_atomic_conformance_tb \
  -o "$work_dir/atomic.vvp" \
  "${rtl_files[@]}" "$binding" \
  "$script_dir/k2_conformance_oracle.sv" \
  "$script_dir/k2_atomic_conformance_tb.sv"
"$vvp_bin" "$work_dir/atomic.vvp" | tee "$work_dir/atomic.log"
grep -q "K2_ATOMIC_CONFORMANCE_PASS latency=$latency" "$work_dir/atomic.log"

if [[ -n "$link_binding" ]]; then
  "$iverilog_bin" -g2012 -Wall -s k2_ordered_link_conformance_tb \
    -o "$work_dir/link.vvp" \
    "${link_rtl_files[@]}" "$link_binding" \
    "$script_dir/k2_ordered_link_conformance_tb.sv"
  "$vvp_bin" "$work_dir/link.vvp" | tee "$work_dir/link.log"
  grep -q "K2_ORDERED_LINK_CONFORMANCE_PASS" "$work_dir/link.log"
fi

echo "K2_CANDIDATE_CONFORMANCE_PASS latency=$latency root=$repo_root"
