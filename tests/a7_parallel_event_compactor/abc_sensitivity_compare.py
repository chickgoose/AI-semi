#!/usr/bin/env python3
"""Map the N=16/K=4 A7 pair through a second generic Yosys/ABC flow."""

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
SOURCES = [
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv",
    ROOT / "tests/a7_parallel_event_compactor/a7_structural_wrappers.sv",
]
TOPS = {
    "prefix-k4": "a7_prefix_structural_top",
    "replicated-k4": "a7_replicated_structural_top",
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")


def run_one(yosys: str, implementation: str, top: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="a7-abc-sensitivity-") as directory:
        stat_path = Path(directory) / "stat.json"
        command = (
            "read_verilog -sv " + " ".join(str(path) for path in SOURCES) + "; "
            f"hierarchy -top {top}; proc; flatten; opt; techmap; opt; "
            f"abc -fast; clean; tee -o {stat_path} stat -json; ltp -noff"
        )
        result = subprocess.run(
            [yosys, "-Q", "-p", command],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Yosys/ABC failed implementation={implementation}\n"
                + result.stdout[-4000:]
            )
        depths = DEPTH_RE.findall(result.stdout)
        if len(depths) != 1:
            raise RuntimeError(f"missing LTP depth for {implementation}")
        document = json.loads(stat_path.read_text(encoding="utf-8"))
        module = next(iter(document["modules"].values()))
        histogram: dict[str, int] = module["num_cells_by_type"]
        sequential = sum(
            count for cell_type, count in histogram.items()
            if "dff" in cell_type.lower()
        )
        scope = histogram.get("$scopeinfo", 0)
        return {
            "implementation": implementation,
            "n": 16,
            "k": 4,
            "mapping": "yosys_abc_fast_default_generic",
            "total_cells": module["num_cells"],
            "sequential_cells": sequential,
            "combinational_cells": module["num_cells"] - sequential - scope,
            "depth_generic_gates": int(depths[0]),
            "register_bits": 104,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [run_one(args.yosys, name, top) for name, top in TOPS.items()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(" ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
