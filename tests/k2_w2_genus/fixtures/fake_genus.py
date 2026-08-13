#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import sys


if sys.argv[1:] == ["-version"]:
    print("Fake Genus W2 fixture 1.0")
    raise SystemExit(0)

if len(sys.argv) != 3 or sys.argv[1] != "-batch" or sys.argv[2] != "-files":
    # Real invocation is: -batch -files driver.tcl (four arguments after argv0).
    if len(sys.argv) != 4 or sys.argv[1:3] != ["-batch", "-files"]:
        print("unsupported fake Genus invocation", file=sys.stderr)
        raise SystemExit(64)

required = ("W2_TOP", "W2_SOURCES", "W2_DEFINES", "W2_LIBRARY", "W2_SDC", "W2_OUTPUT")
if any(not os.environ.get(name) for name in required):
    print("missing W2 environment", file=sys.stderr)
    raise SystemExit(65)

top = os.environ["W2_TOP"]
output = Path(os.environ["W2_OUTPUT"])
reports = output / "reports"
reports.mkdir(parents=True, exist_ok=True)
mode = os.environ.get("W2_FAKE_GENUS_MODE", "pass")

cell = "DFFX1"
if mode == "blackbox":
    cell = "UNRESOLVEDX1"
elif mode == "scan":
    cell = "SDFFX1"

prefix = ""
if mode == "defined_blackbox":
    cell = "DEFINED_BLACKBOX"
    prefix = "(* blackbox *) module DEFINED_BLACKBOX(input CK,D, output Q); endmodule\n"
(output / "mapped.v").write_text(prefix +
    f"module {top}(input wire CK, input wire D, output wire Q);\n"
    f"  {cell} u_state (.CK(CK), .D(D), .Q(Q));\n"
    "endmodule\n")
(output / "mapped.sdc").write_text("# fake mapped SDC\n")
for report in (
        "check_elaborated.rpt", "check_mapped.rpt", "area.rpt", "qor.rpt",
        "timing.rpt", "clocks.rpt", "clock_gating.rpt", "power_vectorless.rpt"):
    if mode == "missing_report" and report == "check_mapped.rpt":
        continue
    (reports / report).write_text(f"FAKE_REPORT {report} top={top}\n")
if mode != "missing_sentinel":
    (output / "genus.complete").write_text(f"W2_GENUS_COMPLETE top={top}\n")
if mode != "missing_pass":
    print(f"W2_GENUS_PASS top={top}")
raise SystemExit(0)
