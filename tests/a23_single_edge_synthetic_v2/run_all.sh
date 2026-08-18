#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
package="$root/tests/a23_single_edge_synthetic_v2"

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests/a23_single_edge_synthetic_v2/test_synthetic_v2.py

if [[ -f "$package/synthetic_v2_result.json" && \
      -f "$package/synthetic_v2_export.tar.gz" && \
      -f "$package/synthetic_v2_publication.json" ]]; then
  PYTHONDONTWRITEBYTECODE=1 python3 "$package/run_v2.py" validate \
    --result "$package/synthetic_v2_result.json" \
    --archive "$package/synthetic_v2_export.tar.gz" \
    --publication "$package/synthetic_v2_publication.json"
fi
