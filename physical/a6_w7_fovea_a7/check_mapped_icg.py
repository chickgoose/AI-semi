#!/usr/bin/env python3
"""Fail-closed inventory check for the selected W7 mapped ICG."""

import pathlib
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: check_mapped_icg.py MAPPED_NETLIST REPORT")

netlist = pathlib.Path(sys.argv[1]).read_text()


def instance_count(cell: str) -> int:
    # Genus emits either ordinary or escaped Verilog instance identifiers.
    # Anchor at a statement line so comments, nets, and module names cannot
    # satisfy the inventory gate.
    identifier = r"(?:\\\S+|[A-Za-z_][A-Za-z0-9_$]*)"
    return len(re.findall(rf"^\s*{re.escape(cell)}\s+{identifier}\s*\(", netlist, re.MULTILINE))


counts = {cell: instance_count(cell) for cell in ("TLATNCAX2", "TLATNTSCAX2")}
pathlib.Path(sys.argv[2]).write_text(
    "W7_SELECTED_ICG=TLATNCAX2\n"
    f"W7_MAPPED_SELECTED_ICG_COUNT={counts['TLATNCAX2']}\n"
    f"W7_MAPPED_ALTERNATE_ICG_COUNT={counts['TLATNTSCAX2']}\n"
)
if counts != {"TLATNCAX2": 1, "TLATNTSCAX2": 0}:
    raise SystemExit(f"mapped ICG inventory mismatch: {counts}")
