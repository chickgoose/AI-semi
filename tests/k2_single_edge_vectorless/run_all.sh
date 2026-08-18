#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$repo_root"
python3 -B -m unittest tests.k2_single_edge_vectorless.test_preflight -v
