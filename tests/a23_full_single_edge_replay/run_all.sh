#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
run_root="$(mktemp -d /tmp/a23-full-single-edge-replay.XXXXXX)"
trap 'rm -rf -- "$run_root"' EXIT

python3 "$repo_root/tests/a23_full_single_edge_replay/run_replay.py" \
  --work-dir "$run_root/work" \
  --output "$run_root/result.json"

cp "$run_root/result.json" \
  "$repo_root/tests/a23_full_single_edge_replay/result.json"
