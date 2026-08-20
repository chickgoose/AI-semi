#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

PYTHONDONTWRITEBYTECODE=1 bash "$script_dir/run_native.sh"
PYTHONDONTWRITEBYTECODE=1 bash "$script_dir/run_independent.sh"
