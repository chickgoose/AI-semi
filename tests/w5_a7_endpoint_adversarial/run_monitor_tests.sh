#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$script_dir" \
  python3 -m unittest -v "$script_dir/test_live_trace_monitor.py"
printf 'W5_A8_LIVE_TRACE_MONITOR_MUTATION_PASS\n'
