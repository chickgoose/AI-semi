#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover \
  -s "$script_dir" \
  -p 'test_*.py' \
  -v
