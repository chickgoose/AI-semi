#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$test_dir/../.." && pwd)"
projection_dir="${REDRED_UZH_PROJECTION_DIR:-/tmp/redred-uzh-shapes-projection-f59c10e}"
run_root="$(mktemp -d /tmp/a23-public-projected-replay.XXXXXXXX)"
trap 'rm -rf -- "$run_root"' EXIT

python3 "$test_dir/run_public_projected_extension.py" \
  --projection-dir "$projection_dir" \
  --work-dir "$run_root/work" \
  --output "$run_root/public_projected_result.json" \
  --export-bundle "$run_root/public_projected_export.tar.gz" \
  --publication "$run_root/public_projected_publication.json"

cp "$run_root/public_projected_result.json" \
  "$test_dir/public_projected_result.json"
cp "$run_root/public_projected_export.tar.gz" \
  "$test_dir/public_projected_export.tar.gz"
cp "$run_root/public_projected_publication.json" \
  "$test_dir/public_projected_publication.json"

printf 'A23_PUBLIC_PROJECTED_EXPORT result=%s bundle=%s publication=%s\n' \
  "$test_dir/public_projected_result.json" \
  "$test_dir/public_projected_export.tar.gz" \
  "$test_dir/public_projected_publication.json"
