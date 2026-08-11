#!/usr/bin/env python3
"""Run always-ready vectors against actual RTL and emit scoped evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

SCOPED_PASS = re.compile(
    r"W4_A4_ALWAYS_READY_GENERATOR_V4_TRACE_LOCKSTEP_PASS rows=(\d+) offered=(\d+) "
    r"moving=(\d+),(\d+),(\d+),(\d+),(\d+),(\d+) "
    r"fixed=(\d+),(\d+),(\d+),(\d+),(\d+),(\d+)"
)


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else 0


def aggregate(runs: list[dict], model: str) -> dict:
    latencies = [value for run in runs for value in run[f"{model}_latencies"]]
    return {
        "accepted": sum(run[model]["accepted"] for run in runs),
        "retired": sum(run[model]["retired"] for run in runs),
        "overrun": sum(run[model]["overrun"] for run in runs),
        "fixed_window_delivered": sum(run[model]["fixed_window_delivered"] for run in runs),
        "mean_occurrence_to_delivery": sum(latencies) / len(latencies),
        "p95_occurrence_to_delivery": percentile(latencies, 0.95),
        "p99_occurrence_to_delivery": percentile(latencies, 0.99),
        "max_occurrence_to_delivery": max(latencies, default=0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verilator-version", required=True)
    args = parser.parse_args()
    index = json.loads(args.index.read_text())
    actual_rows = []
    for number, run in enumerate(index["runs"], 1):
        result = subprocess.run(
            [str(args.binary), f"+VECTORS={run['vector']}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise SystemExit(
                f"W4 RTL FAIL {run['suite']}/{run['trace']} exit={result.returncode}\n"
                f"{result.stdout}{result.stderr}"
            )
        match = SCOPED_PASS.search(result.stdout)
        if match is None:
            raise SystemExit(
                "W4 missing always-ready generator-v4 actual-RTL lockstep "
                f"sentinel: {run['suite']}/{run['trace']}"
            )
        values = list(map(int, match.groups()))
        rows, offered = values[:2]
        moving = values[2:8]
        fixed = values[8:14]
        expected_moving = [
            run["moving"][key] for key in (
                "accepted", "retired", "fixed_window_delivered", "latency_sum",
                "max_occurrence_to_delivery", "overrun",
            )
        ]
        expected_fixed = [
            run["fixed"][key] for key in (
                "accepted", "retired", "fixed_window_delivered", "latency_sum",
                "max_occurrence_to_delivery", "overrun",
            )
        ]
        if rows != run["vector_cycles"] or offered != run["offered"]:
            raise SystemExit(f"W4 count mismatch: {run['suite']}/{run['trace']}")
        if moving != expected_moving or fixed != expected_fixed:
            raise SystemExit(f"W4 aggregate lockstep mismatch: {run['suite']}/{run['trace']}")
        actual_rows.append({
            "suite": run["suite"], "trace": run["trace"],
            "trace_sha256": run["trace_sha256"], "vector_sha256": run["vector_sha256"],
            "cycles": rows, "moving": run["moving"], "fixed": run["fixed"],
        })
        print(
            "W4_ALWAYS_READY_GENERATOR_V4_ACTUAL_RTL_LOCKSTEP_PASS "
            f"{number}/{len(index['runs'])} {run['suite']}/{run['trace']}"
        )

    suites = {}
    for suite in ("full50", "capacity22"):
        selected = [run for run in index["runs"] if run["suite"] == suite]
        suites[suite] = {
            "runs": len(selected),
            "moving_max_advance_2": aggregate(selected, "moving"),
            "fixed_max_advance_1": aggregate(selected, "fixed"),
        }
    receipt = {
        "schema": "w4-a2-a4-scoped-lockstep-evidence-v2",
        "decision": "HOLD",
        "complete_common_qualification": "HOLD",
        "evidence_result": "PASS",
        "evidence_scope": "always-ready generator-v4 full50+capacity22 actual-RTL lockstep",
        "economic_gate": "NO-GO",
        "missing_qualification_evidence": [
            "mandatory direct-SV basic_reset_drain",
            "immutable simulator executable/package/tool-invocation receipt",
        ],
        "actual_rtl": True,
        "address_semantics": "source-address-only",
        "adapter_state_bits": 0,
        "cycle_lockstep_runs": len(actual_rows),
        "provenance": {
            "common_commit": "47e1f2ff2aeb9d902e6f8bf0f1998b95579bd3be",
            "generator_sha256": "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50",
            "full50_manifest_sha256": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
            "capacity22_manifest_sha256": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
            "a4_commit": "850fbcfa4ad168b1250223610780f11378f6c391",
            "a4_rtl_sha256": "18e00a2acba587af7f81f2f1608268f4c37d9068a3e7e3f2b29611c4f8ea5677",
            "adapter_sha256": hashlib.sha256(
                (Path(__file__).parent / "rtl/a4_w4_zero_state_adapter.sv").read_bytes()
            ).hexdigest(),
            "reference_sha256": hashlib.sha256(
                (Path(__file__).parent / "reference/model.py").read_bytes()
            ).hexdigest(),
            "testbench_sha256": hashlib.sha256(
                (Path(__file__).parent / "tb/a4_w4_common_tb.sv").read_bytes()
            ).hexdigest(),
            "verilator_version": args.verilator_version,
            "tool_receipt_status": "MISSING_IMMUTABLE_TOOL_RECEIPT_VERSION_STRING_ONLY",
        },
        "suites": suites,
        "runs": actual_rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(
        "W4_A4_ALWAYS_READY_GENERATOR_V4_FULL50_CAP22_ACTUAL_RTL_LOCKSTEP_PASS "
        f"runs={len(actual_rows)} complete_common_qualification=HOLD"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
