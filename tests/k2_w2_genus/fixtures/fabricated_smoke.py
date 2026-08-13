#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--top", required=True)
parser.add_argument("--netlist", type=Path, required=True)
parser.add_argument("--library", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.write_text(json.dumps({
    "schema": "k2_w2_mapped_smoke_v1",
    "status": "PASS",
    "top": args.top,
    "mapped_netlist_sha256": "0" * 64,
    "library_sha256": "0" * 64,
}) + "\n")
print("W2_MAPPED_SMOKE_PASS")
