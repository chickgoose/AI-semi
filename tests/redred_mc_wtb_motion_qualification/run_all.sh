#!/usr/bin/env bash
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo"

python3 -m unittest discover \
  -s tests/redred_mc_wtb_motion_qualification -p 'test_*.py' -v

if command -v iverilog >/dev/null 2>&1; then
  build_dir=$(mktemp -d)
  trap 'rm -rf "$build_dir"' EXIT
  iverilog -g2012 -s mc_wtb_motion_qualifier_tb \
    -o "$build_dir/motion_qualifier.vvp" \
    rtl/candidates/mc_wtb_motion_qualification/mc_wtb_motion_qualifier.sv \
    tests/redred_mc_wtb_motion_qualification/tb.sv
  vvp "$build_dir/motion_qualifier.vvp"
  iverilog -g2012 -s mc_wtb_epoch_route_interlock_tb \
    -o "$build_dir/epoch_route_interlock.vvp" \
    rtl/candidates/mc_wtb_motion_qualification/mc_wtb_epoch_route_interlock.sv \
    tests/redred_mc_wtb_motion_qualification/tb_interlock.sv
  vvp "$build_dir/epoch_route_interlock.vvp"
else
  echo "SKIP_IVERILOG_NOT_INSTALLED"
fi
