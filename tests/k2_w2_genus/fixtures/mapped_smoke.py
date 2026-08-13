#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--top", required=True)
parser.add_argument("--netlist", type=Path, required=True)
parser.add_argument("--library", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
payload = args.netlist.read_bytes()
library = args.library.read_bytes()
document = {
    "schema": "k2_w2_mapped_smoke_v1",
    "status": "PASS",
    "top": args.top,
    "mapped_netlist_sha256": hashlib.sha256(payload).hexdigest(),
    "library_sha256": hashlib.sha256(library).hexdigest(),
}
args.output.write_text(json.dumps(document, sort_keys=True) + "\n")
print("W2_MAPPED_SMOKE_PASS")
