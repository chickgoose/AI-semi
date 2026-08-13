#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
python3 -B -m unittest -q tests.k2_postroute_activity.test_postroute_activity

