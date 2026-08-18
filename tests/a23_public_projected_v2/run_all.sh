#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
projection_dir="${REDRED_UZH_PROJECTION_DIR:-/tmp/redred-uzh-shapes-projection-f59c10e}"
run_root="$(mktemp -d /tmp/a23-public-projected-v2.XXXXXXXX)"
trap 'rm -rf -- "$run_root"' EXIT

for execution in primary reproduction; do
  python3 "$test_dir/run.py" produce \
    --projection-dir "$projection_dir" \
    --work-dir "$run_root/$execution-work" \
    --output "$run_root/$execution-result.json" \
    --export-bundle "$run_root/$execution-export.tar.gz"
done

python3 - "$run_root/primary-result.json" "$run_root/reproduction-result.json" <<'PY'
import json
import sys
from pathlib import Path
values = [json.loads(Path(name).read_text(encoding="ascii")) for name in sys.argv[1:]]
digests = [value["semantic_reproducibility"]["semantic_sha256"] for value in values]
if len(set(digests)) != 1:
    raise SystemExit("semantic reproduction mismatch")
print(f"A23_PUBLIC_PROJECTED_V2_REPRODUCED semantic_sha256={digests[0]}")
PY

cp "$run_root/primary-result.json" "$test_dir/public_projected_v2_result.json"
cp "$run_root/primary-export.tar.gz" "$test_dir/public_projected_v2_export.tar.gz"
cp "$run_root/reproduction-result.json" "$test_dir/public_projected_v2_reproduction_result.json"

printf 'A23_PUBLIC_PROJECTED_V2_PAYLOADS_READY result=%s export=%s reproduction=%s\n' \
  "$test_dir/public_projected_v2_result.json" \
  "$test_dir/public_projected_v2_export.tar.gz" \
  "$test_dir/public_projected_v2_reproduction_result.json"
printf '%s\n' 'Commit those three payloads, then use run.py seal with that payload commit.'
