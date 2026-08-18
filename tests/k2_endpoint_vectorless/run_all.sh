#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 -m unittest tests.k2_endpoint_vectorless.test_vectorless -v
