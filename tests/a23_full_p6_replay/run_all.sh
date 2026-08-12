#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
run_root="${A23_FULL_P6_RUN_ROOT:-$(mktemp -d /tmp/a23-full-p6-replay.XXXXXXXX)}"
verilator="${VERILATOR:-/tmp/a7-toolchain/usr/bin/verilator}"

python3 "$test_dir/run_replay.py" \
  --work-dir "$run_root/work" \
  --output "$run_root/result.json" \
  --verilator "$verilator"

printf 'A23_FULL_P6_REPLAY_RESULT path=%s\n' "$run_root/result.json"
