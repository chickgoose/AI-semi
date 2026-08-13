#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  printf 'usage: %s <fovea|a2|a3> <trace.jsonl> <run.manifest.json> <out-dir>\n' "$0" >&2
  exit 2
fi
candidate=$1; trace=$2; run_manifest=$3; out=$4
case "$candidate" in fovea|a2|a3) ;; *) exit 2 ;; esac
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
xrun_bin=${AER_XRUN_BIN:-/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun}
staged_root=${AER_STAGED_ROOT:-/tmp/k2-phys-w2-techmap}
staged_list="$staged_root/rtl/technology/physical_staging/filelists/${candidate}_generic.f"
require_sha() {
  local expected=$1 file=$2 actual
  actual=$(sha256sum "$file")
  actual=${actual%% *}
  [[ "$actual" == "$expected" ]] || {
    printf 'SHA mismatch: %s expected=%s actual=%s\n' "$file" "$expected" "$actual" >&2
    exit 1
  }
}
[[ -x "$xrun_bin" && -f "$trace" && -f "$run_manifest" &&
   -f "$staged_list" && ! -L "$staged_list" && ! -e "$out" ]] || exit 1
require_sha 27d9437a5179b0cb909d02edee1ac2f82ea6d20aeab9cfb64997b458192102a2 \
  "$root/tb/clean/aer_clean_tb.sv"
mkdir -p "$out"
cd "$root"
python3 physical/k2_w3_common_activity/resolve_staged_filelist.py \
  --root "$staged_root" --input "$staged_list" \
  --output "$out/staged.resolved.f"
python3 benchmarks/clean_slate_aer/prepare_sv_trace.py --trace "$trace" \
  --run-manifest "$run_manifest" --output "$out/input.svtrace" --addr-width 16 \
  >"$out/prepare.log"
snapshot="w3_staged_${candidate}_common_activity"
"$xrun_bin" -64bit -sv -timescale 1ns/1ps -top aer_clean_tb \
  -snapshot "$snapshot" -elaborate -access +r -xmlibdirname "$out/xcelium.d" \
  -defparam aer_clean_tb.NUM_SOURCES=16 -defparam aer_clean_tb.ADDR_WIDTH=16 \
  -defparam aer_clean_tb.RETIRE_LANES=2 -defparam aer_clean_tb.FIFO_DEPTH=0 \
  -f "$out/staged.resolved.f" \
  -f "physical/k2_w3_common_activity/filelists/$candidate.f" \
  -l "$out/elaborate.log"
"$xrun_bin" -64bit -R -snapshot "$snapshot" -xmlibdirname "$out/xcelium.d" \
  +CLEAN_TEST=trace +TRACE_NAME=final_common_activity \
  "+TRACE_FILE=$out/input.svtrace" "+METRICS=$out/summary.csv" \
  "+EVENT_METRICS=$out/events.csv" "+ACTIVITY_VCD=$out/raw.vcd" \
  "+ACTIVITY_WINDOW=$out/window.txt" -l "$out/run.log"
grep -q 'AER_CLEAN_TEST_PASS' "$out/run.log"
python3 physical/k2_w3_common_activity/rebase_vcd.py --input "$out/raw.vcd" \
  --window "$out/window.txt" --summary "$out/summary.csv" \
  --output "$out/activity.vcd" \
  --sha-output "$out/activity.sha256.txt"
python3 physical/k2_w3_common_activity/vcd_to_saif.py \
  --vcd "$out/activity.vcd" --output "$out/activity.saif"
sha256sum "$trace" "$run_manifest" tb/clean/aer_clean_tb.sv \
  benchmarks/clean_slate_aer/prepare_sv_trace.py \
  "$staged_list" \
  "$out/staged.resolved.f" \
  "physical/k2_w3_common_activity/filelists/$candidate.f" \
  "physical/k2_w3_common_activity/tb/${candidate}_staged_binding.sv" \
  physical/k2_w3_common_activity/resolve_staged_filelist.py \
  physical/k2_w3_common_activity/rebase_vcd.py \
  physical/k2_w3_common_activity/vcd_to_saif.py \
  physical/k2_w3_common_activity/run_xcelium_activity.sh \
  "$out/activity.vcd" "$out/activity.saif" >"$out/artifacts.sha256"
while IFS= read -r source; do
  [[ -z "$source" || "$source" == +* || "$source" == \#* ]] && continue
  sha256sum "$source" >>"$out/artifacts.sha256"
done <"$out/staged.resolved.f"
printf 'W3_STAGED_COMMON_ACTIVITY_PASS candidate=%s output=%s\n' "$candidate" "$out"
