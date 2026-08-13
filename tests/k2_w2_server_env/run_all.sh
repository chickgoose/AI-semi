#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
python3 -B "$repo_root/tests/k2_w2_server_env/test_preflight.py"
