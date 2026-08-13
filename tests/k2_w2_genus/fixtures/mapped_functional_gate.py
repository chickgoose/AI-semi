#!/usr/bin/env python3
"""Test-only functional-gate fixture; never a production simulation proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--design", required=True)
parser.add_argument("--top", required=True)
parser.add_argument("--rtl-filelist", type=Path, required=True)
parser.add_argument("--netlist", type=Path, required=True)
parser.add_argument("--sdf", type=Path, required=True)
parser.add_argument("--model", type=Path, action="append", required=True)
parser.add_argument("--scenarios", required=True)
parser.add_argument("--xrun", type=Path, required=True)
parser.add_argument("--testbench", type=Path, required=True)
parser.add_argument("--define", action="append", default=[])
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--log", type=Path, required=True)
args = parser.parse_args()

args.log.write_text(
    "TEST_FIXTURE_ONLY staged_vs_mapped vectors equal; accepted/retired/order/reset\n")
document = {
    "schema": "k2_w2_mapped_functional_gate_v1",
    "status": "PASS",
    "design": args.design,
    "top": args.top,
    "mapped_netlist_sha256": digest(args.netlist),
    "method": "xcelium_vendor_models",
    "scenarios": args.scenarios.split(","),
    "checks": {
        "accepted": "EXACT", "retired": "EXACT", "global_order": "EXACT",
        "conservation": "EXACT", "protocol_error": "ZERO",
        "reset_and_drain": "PASS",
    },
    "log_sha256": digest(args.log),
    "model_sha256": {model.name: digest(model) for model in args.model},
    "sdf_status": "ANNOTATED",
    "sdf_sha256": digest(args.sdf),
}
mutation = os.environ.get("W2_FUNCTIONAL_FIXTURE_MUTATION", "")
if mutation == "unbound_netlist":
    document["mapped_netlist_sha256"] = "0" * 64
elif mutation == "bad_sdf":
    document["sdf_sha256"] = "0" * 64
elif mutation == "bad_scenarios":
    document["scenarios"] = list(reversed(document["scenarios"]))
elif mutation == "fabricated_log":
    document["log_sha256"] = "0" * 64
args.output.write_text(json.dumps(document, sort_keys=True) + "\n")
print("W2_MAPPED_FUNCTIONAL_PASS TEST_FIXTURE_ONLY")
