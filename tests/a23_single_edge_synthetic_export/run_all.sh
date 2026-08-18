#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
package="$repo_root/tests/a23_single_edge_synthetic_export"
temporary="$(mktemp -d /tmp/a23-single-edge-synthetic-export-test.XXXXXX)"
trap 'rm -rf -- "$temporary"' EXIT

python3 -m unittest \
  tests/a23_single_edge_synthetic_export/test_export_preserved.py

set +e
python3 "$package/export_preserved.py" \
  --run-root /tmp/a23-full-single-edge-replay.IdAjj6 \
  --status-output "$temporary/status.json" \
  --archive-output "$temporary/export.tar.gz"
status=$?
set -e

test "$status" -eq 3
test ! -e "$temporary/export.tar.gz"
cmp "$temporary/status.json" "$package/preserved_run_status.json"
echo "A23_SYNTHETIC_EXPORT_TEST_PASS expected_status=HOLD"
