#!/usr/bin/env python3
"""Count the complete W5 digital endpoints with a common Yosys flow."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = sorted((ROOT / "rtl/candidates/a7_r1_candidate_endpoint").glob("*.sv"))
TOPS = {
    "ddr2": ("a7_r1_candidate_endpoint", 3),
    "parallel4": ("a7_r1_parallel_reference_top", 5),
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")


def synthesize(yosys: str, link: str) -> dict[str, object]:
    top, physical_pins = TOPS[link]
    with tempfile.TemporaryDirectory(prefix="a7-r1-yosys-") as tmp:
        stat_path = Path(tmp) / "stat.json"
        command = (
            "read_verilog -sv " + " ".join(map(str, SOURCES)) + "; "
            f"hierarchy -top {top}; proc; flatten; opt; "
            f"tee -o {stat_path} stat -json -width; ltp -noff; "
            "techmap; opt; ltp -noff"
        )
        result = subprocess.run(
            [yosys, "-Q", "-p", command], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:])
        document = json.loads(stat_path.read_text())
        module = next(iter(document["modules"].values()))
        histogram = module["num_cells_by_type"]
        depths = [int(value) for value in DEPTH_RE.findall(result.stdout)]
        if len(depths) != 2:
            raise RuntimeError("expected operator and generic-gate depth")

        state_types = {
            name: count for name, count in histogram.items()
            if "dff" in name.lower() or "dlatch" in name.lower()
        }
        state_bits = 0
        for name, count in state_types.items():
            width = re.search(r"_(\d+)$", name)
            state_bits += count * (int(width.group(1)) if width else 1)

        return {
            "link": link,
            "physical_link_pins": physical_pins,
            "functional_cells": module["num_cells"],
            "register_or_latch_cells": sum(state_types.values()),
            "state_bits": state_bits,
            "operator_depth": depths[0],
            "generic_gate_depth": depths[1],
            "physical_status": "HOLD",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = [synthesize(args.yosys, link) for link in TOPS]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(" ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
