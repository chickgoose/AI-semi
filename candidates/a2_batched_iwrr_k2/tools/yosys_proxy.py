#!/usr/bin/env python3
"""Run a deterministic candidate-only Yosys LUT4 structure proxy."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "rtl/a2_batched_iwrr_k2.sv"
TOP = "a2_batched_iwrr_k2"


def combinational_depth(module: dict) -> int:
    cells = module.get("cells", {})
    driver: dict[int, tuple[str, str]] = {}
    inputs = set()
    for port in module.get("ports", {}).values():
        if port.get("direction") == "input":
            inputs.update(bit for bit in port["bits"] if isinstance(bit, int))
    sequential = {"$_DFF_P_", "$_SDFF_PP0_", "$_SDFF_PP1_", "$_DFFE_PP_"}
    for name, cell in cells.items():
        directions = cell.get("port_directions", {})
        for port, bits in cell.get("connections", {}).items():
            if directions.get(port) == "output":
                for bit in bits:
                    if isinstance(bit, int):
                        driver[bit] = (name, cell["type"])
    memo: dict[int, int] = {}

    def bit_depth(bit: int, visiting: set[int]) -> int:
        if bit in memo:
            return memo[bit]
        if bit in inputs or bit not in driver or bit in visiting:
            return 0
        name, kind = driver[bit]
        if kind in sequential or "DFF" in kind:
            memo[bit] = 0
            return 0
        cell = cells[name]
        dependencies = []
        for port, bits in cell.get("connections", {}).items():
            if cell.get("port_directions", {}).get(port) == "input":
                dependencies.extend(value for value in bits if isinstance(value, int))
        value = 1 + max((bit_depth(dep, visiting | {bit}) for dep in dependencies), default=0)
        memo[bit] = value
        return value

    endpoints = []
    for port in module.get("ports", {}).values():
        if port.get("direction") == "output":
            endpoints.extend(bit for bit in port["bits"] if isinstance(bit, int))
    for cell in cells.values():
        if "DFF" in cell["type"]:
            endpoints.extend(bit for bit in cell["connections"].get("D", []) if isinstance(bit, int))
    return max((bit_depth(bit, set()) for bit in endpoints), default=0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--library-dir", type=Path)
    args = parser.parse_args()
    if not args.yosys.is_file() or not os.access(args.yosys, os.X_OK):
        raise SystemExit("A2_K2_YOSYS_FAIL Yosys unavailable")
    env = os.environ.copy()
    if args.library_dir:
        old = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(args.library_dir) + ((":" + old) if old else "")
    with tempfile.TemporaryDirectory(prefix="a2-k2-yosys-") as temporary:
        netlist = Path(temporary) / "netlist.json"
        script = (
            f"read_verilog -sv {RTL}; hierarchy -check -top {TOP}; "
            "proc; flatten; opt; memory_map; opt; techmap; opt; "
            f"abc -fast -lut 4; clean; write_json {netlist}"
        )
        run = subprocess.run([str(args.yosys), "-q", "-p", script], env=env,
                             text=True, capture_output=True)
        if run.returncode:
            raise SystemExit(f"A2_K2_YOSYS_FAIL {run.stderr.strip()[-2000:]}")
        document = json.loads(netlist.read_text(encoding="utf-8"))
        module = document["modules"][TOP]
        counts = Counter(cell["type"] for cell in module.get("cells", {}).values())
        state_cells = sum(count for kind, count in counts.items() if "DFF" in kind)
        result = {
            "schema": "a2_batched_iwrr_k2_yosys_proxy_v1",
            "yosys_version": subprocess.run([str(args.yosys), "-V"], env=env,
                                              text=True, capture_output=True, check=True).stdout.strip(),
            "mapping": "abc_fast_lut4_generic_proxy_not_physical_PPA",
            "top": TOP,
            "state_bits": state_cells,
            "cell_count": sum(counts.values()),
            "lut_cells": counts.get("$lut", 0),
            "combinational_depth_cells": combinational_depth(module),
            "cell_types": dict(sorted(counts.items())),
        }
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"A2_K2_YOSYS_PASS state_bits={state_cells} cells={result['cell_count']} "
              f"depth={result['combinational_depth_cells']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
