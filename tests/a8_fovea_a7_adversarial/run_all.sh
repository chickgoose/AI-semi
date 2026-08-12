#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s "$script_dir" -p 'test_*.py' -v
printf 'A8_FOVEA_A7_ADVERSARIAL_PASS mutants=7\n'
