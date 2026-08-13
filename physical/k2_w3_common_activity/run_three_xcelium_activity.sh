#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s <new-output-root>\n' "$0" >&2
  exit 2
fi
out=$1
[[ ! -e "$out" ]] || { printf 'refusing existing output: %s\n' "$out" >&2; exit 1; }
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
require_sha() {
  local expected=$1 file=$2 actual
  actual=$(sha256sum "$file")
  actual=${actual%% *}
  [[ "$actual" == "$expected" ]] || {
    printf 'SHA mismatch: %s expected=%s actual=%s\n' "$file" "$expected" "$actual" >&2
    exit 1
  }
}
mkdir -p "$out"
cd "$root"
trace_root="$out/official-full50"
require_sha 9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9 \
  benchmarks/clean_slate_aer/manifest.neutrality-n16.json
require_sha 59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50 \
  benchmarks/clean_slate_aer/generate_trace.py
python3 benchmarks/clean_slate_aer/generate_trace.py \
  --manifest benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  --output-dir "$trace_root" >"$out/generate.log"
name=mixed_phase_always_ready_identity
trace="$trace_root/$name.events.jsonl"
run_manifest="$trace_root/$name.manifest.json"
require_sha 9fde0ee816a80975d219b57e9799e73c198efc85d6e9aec4cb2a2e4816974705 \
  "$trace"
require_sha 5ecd7d07e906e20a92e18103b72bd4fd0d74099547e57a357e7376657fee8372 \
  "$run_manifest"
for candidate in fovea a2 a3; do
  physical/k2_w3_common_activity/run_xcelium_activity.sh "$candidate" \
    "$trace" "$run_manifest" "$out/$candidate"
done
sha256sum benchmarks/clean_slate_aer/manifest.neutrality-n16.json \
  benchmarks/clean_slate_aer/generate_trace.py \
  physical/k2_w3_common_activity/run_three_xcelium_activity.sh \
  "$trace" "$run_manifest" "$out"/*/activity.vcd "$out"/*/activity.saif \
  >"$out/common-artifacts.sha256"
printf 'W3_STAGED_THREE_COMMON_ACTIVITY_PASS output=%s\n' "$out"
