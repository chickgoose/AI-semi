#!/usr/bin/env bash
set -euo pipefail

candidate_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m unittest discover -s "$candidate_dir/tests" -p 'test_*.py' -v
