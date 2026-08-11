#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$script_dir/run_monitor_tests.sh"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  "$script_dir/test_bound_snapshot.py"
"$script_dir/run_bound_endpoint.sh"
"$script_dir/run_bound_endpoint.sh" --latency-mutant
printf 'W5_A8_ADVERSARIAL_VERIFICATION_PASS\n'
