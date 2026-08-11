#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)

cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest -v \
  tests/a9_w5_ddr_technology_boundary/test_technology_boundary.py
git diff --check
