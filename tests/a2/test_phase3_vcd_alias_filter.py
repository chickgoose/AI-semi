#!/usr/bin/env python3
"""Unit check that phase-3 VCD aliases contribute one representative toggle."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANALYZER = PROJECT_ROOT / "scripts" / "a2_phase3_physical_proxy.py"
SPEC = importlib.util.spec_from_file_location("a2_phase3_proxy", ANALYZER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

VCD = """$timescale 1ns $end
$scope module a2_phase3_physical_tb $end
$scope module dut $end
$var wire 1 ! ingress_valid $end
$var wire 1 \" source_valid_i $end
$var wire 1 # core_retire_valid $end
$var reg 1 % retire_valid_q $end
$var wire 1 & retire_valid_o $end
$var wire 1 ' clk_i $end
$scope begin g_a2 $end
$scope module core $end
$var wire 1 # retire_valid_o $end
$var wire 1 $ source_valid_i $end
$upscope $end
$upscope $end
$upscope $end
$upscope $end
$enddefinitions $end
#0
0!
0\"
0#
0%
0&
0'
0$
#5
1!
1\"
1#
1%
1&
1'
1$
"""

with tempfile.TemporaryDirectory(prefix="a2-phase3-vcd-") as directory:
    path = Path(directory) / "aliases.vcd"
    path.write_text(VCD, encoding="utf-8")
    toggles = MODULE.parse_vcd(path)

if toggles != 3:
    raise SystemExit(f"A2_PHASE3_VCD_ALIAS_FILTER_FAIL toggles={toggles} expected=3")
print("A2_PHASE3_VCD_ALIAS_FILTER_PASS toggles=3")
