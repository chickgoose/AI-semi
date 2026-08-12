#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$script_dir/.." && pwd)"
if [[ -v A7_W6_FAULT_OUT ]]; then
  out="$A7_W6_FAULT_OUT"
  [[ ! -e "$out" ]] || {
    printf 'refusing to overwrite A7_W6_FAULT_OUT=%s\n' "$out" >&2
    exit 1
  }
  mkdir -p "$out"
else
  out="$(mktemp -d /tmp/a7-weighted-fovea-ddr-fault.XXXXXXXX)"
fi
cd "$root"

if command -v verilator >/dev/null 2>&1; then
  verilator_bin="$(command -v verilator)"
else
  verilator_bin=/tmp/a7-sim-bin/verilator
fi
[[ -x "$verilator_bin" ]] || { printf 'verilator not found\n' >&2; exit 1; }

"$verilator_bin" --binary --timing -Wall -Wno-fatal -Wno-BLKSEQ \
  -Wno-SYNCASYNCNET -Wno-UNUSEDSIGNAL \
  --top-module a7_weighted_fovea_ddr_fault_tb \
  --Mdir "$out/obj" -o a7_w6_fault \
  -DA7_WEIGHTED_FOVEA_MODULE=a7_weighted_fovea_stale_no_live_fixture \
  -f tb/filelists/a7_weighted_fovea_ddr_fault.f \
  >"$out/build.log" 2>&1
if rg -n '(^|[[:space:]])(%Warning|%Error|Warning:|ERROR:|FATAL:|FAILED:)' \
     "$out/build.log"; then
  printf 'fail-closed diagnostic found in negative build\n' >&2
  exit 1
fi

set +e
python3 - "$out/obj/a7_w6_fault" "$out/run.log" <<'PY'
import subprocess
import sys

with open(sys.argv[2], "wb") as log:
    result = subprocess.run([sys.argv[1]], stdout=log, stderr=subprocess.STDOUT,
                            check=False)
raise SystemExit(0 if result.returncode == 0 else 99)
PY
run_rc=$?
set -e
if [[ "$run_rc" -eq 0 ]]; then
  printf 'stale/no-live negative unexpectedly passed\n' >&2
  exit 1
fi
rg -Fq 'A7_W6_STALE_NO_LIVE_NEGATIVE_CAUGHT addr=a' "$out/run.log" || {
  printf 'missing exact stale/no-live failure diagnostic\n' >&2
  tail -40 "$out/run.log" >&2
  exit 1
}
printf 'A7_W6_STALE_NO_LIVE_EXPECTED_FAIL_PASS exit=%d output=%s\n' \
  "$run_rc" "$out"
