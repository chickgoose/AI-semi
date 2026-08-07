#!/usr/bin/env python3
"""Candidate-only Yosys operator/depth comparison for A7 and its reference."""

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
CONFIGS = [(n, k) for n in (16, 32, 64) for k in (1, 2, 4, 8)]
TOPS = {
    "prefix": "a7_prefix_structural_top",
    "replicated": "a7_replicated_structural_top",
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")
WIDTH_RE = re.compile(r"_(\d+)$")


def cell_width(cell_type: str) -> int:
    match = WIDTH_RE.search(cell_type)
    return int(match.group(1)) if match else 1


def run_one(yosys: str, implementation: str, n: int, k: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="a7-yosys-") as directory:
        work = Path(directory)
        operator_stat_path = work / "operator-stat.json"
        gate_stat_path = work / "gate-stat.json"
        command = (
            "read_verilog -sv " + " ".join(str(path) for path in SOURCES) + "; "
            f"hierarchy -top {TOPS[implementation]} -chparam N {n} -chparam K {k}; "
            "proc; flatten; opt; "
            f"tee -o {operator_stat_path} stat -json -width; ltp -noff; "
            "techmap; opt; "
            f"tee -o {gate_stat_path} stat -json -width; ltp -noff"
        )
        result = subprocess.run(
            [yosys, "-Q", "-p", command], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if result.returncode:
            raise RuntimeError(
                f"Yosys failed impl={implementation} N={n} K={k}\n"
                + result.stdout[-4000:]
            )
        depths = [int(value) for value in DEPTH_RE.findall(result.stdout)]
        if len(depths) != 2:
            raise RuntimeError(f"missing ltp depth impl={implementation} N={n} K={k}")
        document = json.loads(operator_stat_path.read_text(encoding="utf-8"))
        module = next(iter(document["modules"].values()))
        histogram: dict[str, int] = module["num_cells_by_type"]
        register_cells = 0
        register_bits = 0
        operator_bit_proxy = 0
        for cell_type, count in histogram.items():
            width = cell_width(cell_type)
            if "dff" in cell_type:
                register_cells += count
                register_bits += width * count
            else:
                operator_bit_proxy += width * count
        gate_document = json.loads(gate_stat_path.read_text(encoding="utf-8"))
        gate_module = next(iter(gate_document["modules"].values()))
        gate_histogram: dict[str, int] = gate_module["num_cells_by_type"]
        generic_gates = sum(
            count for cell_type, count in gate_histogram.items()
            if "dff" not in cell_type.lower() and "scopeinfo" not in cell_type
        )
        return {
            "implementation": implementation,
            "n": n,
            "k": k,
            "yosys_cells": module["num_cells"],
            "combinational_cells": module["num_cells"] - register_cells,
            "operator_bit_proxy": operator_bit_proxy,
            "depth_operator_levels": depths[0],
            "generic_comb_gates": generic_gates,
            "depth_generic_gates": depths[1],
            "register_cells": register_cells,
            "register_bits": register_bits,
            "lane_capacity": k,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        run_one(args.yosys, implementation, n, k)
        for n, k in CONFIGS
        for implementation in TOPS
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(" ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
