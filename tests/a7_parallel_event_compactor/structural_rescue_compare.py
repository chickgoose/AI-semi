#!/usr/bin/env python3
"""Yosys three-way A7 rescue comparison with net fanout proxy."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_parallel_prefix_count.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_prefix_count.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_shared_rank_index_select.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_radix4_segmented_event_compactor.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_parallel_event_compactor.sv",
    ROOT / "rtl/candidates/a7_parallel_event_compactor/a7_replicated_selector_reference.sv",
    ROOT / "tests/a7_parallel_event_compactor/a7_structural_wrappers.sv",
    ROOT / "tests/a7_parallel_event_compactor/a7_rescue_structural_wrapper.sv",
]
CONFIGS = [(n, k) for n in (16, 32, 64) for k in (2, 4)]
TOPS = {
    "prefix": "a7_prefix_structural_top",
    "segmented": "a7_segmented_structural_top",
    "replicated": "a7_replicated_structural_top",
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")
WIDTH_RE = re.compile(r"_(\d+)$")


def cell_width(cell_type: str) -> int:
    match = WIDTH_RE.search(cell_type)
    return int(match.group(1)) if match else 1


def fanout(netlist: dict[str, object]) -> tuple[int, int]:
    module = next(iter(netlist["modules"].values()))
    sinks: Counter[int] = Counter()
    for cell in module["cells"].values():
        directions = cell.get("port_directions", {})
        for port, bits in cell["connections"].items():
            if directions.get(port) != "input":
                continue
            for bit in bits:
                if isinstance(bit, int):
                    sinks[bit] += 1
    values = sorted(sinks.values())
    if not values:
        return 0, 0
    p95_index = max(0, math.ceil(0.95 * len(values)) - 1)
    return values[-1], values[p95_index]


def run_one(yosys: str, implementation: str, n: int, k: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="a7-rescue-yosys-") as directory:
        work = Path(directory)
        operator_stat = work / "operator-stat.json"
        gate_stat = work / "gate-stat.json"
        gate_netlist = work / "gate-netlist.json"
        command = (
            "read_verilog -sv " + " ".join(str(path) for path in SOURCES) + "; "
            f"hierarchy -top {TOPS[implementation]} -chparam N {n} -chparam K {k}; "
            "proc; flatten; opt; "
            f"tee -o {operator_stat} stat -json -width; ltp -noff; "
            "techmap; opt; "
            f"tee -o {gate_stat} stat -json -width; ltp -noff; "
            f"write_json {gate_netlist}"
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
            raise RuntimeError(f"missing depth impl={implementation} N={n} K={k}")
        operator_module = next(iter(json.loads(
            operator_stat.read_text(encoding="utf-8"))["modules"].values()))
        histogram: dict[str, int] = operator_module["num_cells_by_type"]
        register_cells = 0
        register_bits = 0
        operator_bit_proxy = 0
        for cell_type, count in histogram.items():
            width = cell_width(cell_type)
            if "dff" in cell_type.lower():
                register_cells += count
                register_bits += width * count
            else:
                operator_bit_proxy += width * count
        gate_module = next(iter(json.loads(
            gate_stat.read_text(encoding="utf-8"))["modules"].values()))
        generic_gates = sum(
            count for cell_type, count in gate_module["num_cells_by_type"].items()
            if "dff" not in cell_type.lower() and "scopeinfo" not in cell_type
        )
        max_fanout, p95_fanout = fanout(json.loads(
            gate_netlist.read_text(encoding="utf-8")))
        return {
            "implementation": implementation,
            "n": n,
            "k": k,
            "operator_bit_proxy": operator_bit_proxy,
            "depth_operator_levels": depths[0],
            "generic_comb_gates": generic_gates,
            "depth_generic_gates": depths[1],
            "max_cell_input_fanout": max_fanout,
            "p95_cell_input_fanout": p95_fanout,
            "register_cells": register_cells,
            "register_bits": register_bits,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [
        run_one(args.yosys, implementation, n, k)
        for n, k in CONFIGS for implementation in TOPS
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
