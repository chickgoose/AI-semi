#!/usr/bin/env python3
"""Common Yosys cells/state/depth accounting for P6 and its fair reference."""

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
    *sorted((ROOT / "rtl/candidates/a7_p6_exact_pair_endpoint").glob("*.sv")),
    ROOT / "tests/a7_p6_exact_pair_endpoint/a7_p6_structural_wrappers.sv",
]
TOPS = {
    "p6_ddr5": ("a7_p6_structural_top", 6, 5),
    "parallel_pair": ("a7_p6_parallel_structural_top", 10, 9),
}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")
WIDTH_RE = re.compile(r"_(\d+)$")


def width(cell_type: str) -> int:
    match = WIDTH_RE.search(cell_type)
    return int(match.group(1)) if match else 1


def synthesize(yosys: str, implementation: str, log_dir: Path) -> dict[str, object]:
    top, physical_signals, physical_data_signals = TOPS[implementation]
    with tempfile.TemporaryDirectory(prefix="a7-p6-yosys-") as directory:
        operator_stat = Path(directory) / "operator.json"
        gate_stat = Path(directory) / "gate.json"
        command = (
            "read_verilog -sv " + " ".join(map(str, SOURCES)) + "; "
            f"hierarchy -check -top {top}; proc; flatten; opt; check -assert; "
            f"tee -o {operator_stat} stat -json -width; ltp -noff; "
            "techmap; opt; check -assert; "
            f"tee -o {gate_stat} stat -json -width; ltp -noff"
        )
        result = subprocess.run(
            [yosys, "-Q", "-p", command], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"yosys-{implementation}.log").write_text(
            result.stdout, encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:])
        depths = [int(value) for value in DEPTH_RE.findall(result.stdout)]
        if len(depths) != 2:
            raise RuntimeError(f"missing two depth results for {implementation}")
        operator_module = next(iter(json.loads(operator_stat.read_text())["modules"].values()))
        operator_hist = operator_module["num_cells_by_type"]
        state_bits = sum(
            count * width(cell_type)
            for cell_type, count in operator_hist.items()
            if "dff" in cell_type.lower() or "dlatch" in cell_type.lower()
        )
        register_cells = sum(
            count for cell_type, count in operator_hist.items()
            if "dff" in cell_type.lower() or "dlatch" in cell_type.lower()
        )
        scope_cells = sum(
            count for cell_type, count in operator_hist.items()
            if "scopeinfo" in cell_type.lower()
        )
        gate_module = next(iter(json.loads(gate_stat.read_text())["modules"].values()))
        gate_hist = gate_module["num_cells_by_type"]
        generic_comb = sum(
            count for cell_type, count in gate_hist.items()
            if "dff" not in cell_type.lower()
            and "dlatch" not in cell_type.lower()
            and "scopeinfo" not in cell_type.lower()
        )
        return {
            "implementation": implementation,
            "physical_link_signals": physical_signals,
            "physical_data_or_control_signals": physical_data_signals,
            "forwarded_clock_signals": 1,
            "max_events_per_link_cell": 2,
            "operator_cells_excluding_scope": operator_module["num_cells"] - scope_cells,
            "generic_comb_gates": generic_comb,
            "register_or_latch_cells": register_cells,
            "state_bits": state_bits,
            "queue_state_bits": 0,
            "operator_depth": depths[0],
            "generic_gate_depth": depths[1],
            "physical_status": "HOLD",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = [synthesize(args.yosys, name, args.log_dir) for name in TOPS]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(" ".join(f"{key}={value}" for key, value in row.items()))
    print("A7_P6_STRUCTURAL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
