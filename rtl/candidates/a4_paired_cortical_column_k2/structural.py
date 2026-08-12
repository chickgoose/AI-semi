#!/usr/bin/env python3
"""Fail-closed generic Yosys state/cell/depth/fanout/wiring proxies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import shutil
import subprocess
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RTL = HERE / "a4_paired_cortical_column_k2.sv"
TOP = "a4_paired_cortical_column_k2"
SEQUENTIAL_MARKERS = ("DFF", "LATCH")


class StructuralError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def sequential(cell_type: str) -> bool:
    upper = cell_type.upper()
    return any(marker in upper for marker in SEQUENTIAL_MARKERS)


def run(
    arguments: list[str], log: pathlib.Path, environment: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        arguments, cwd=ROOT, env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise StructuralError(f"command failed ({result.returncode}); see {log}")
    return result.stdout


def analyze_mapped(path: pathlib.Path) -> dict[str, Any]:
    module = json.loads(path.read_text())["modules"][TOP]
    cells = {name: cell for name, cell in module["cells"].items()
             if cell["type"] != "$scopeinfo"}
    seq = {name: cell for name, cell in cells.items() if sequential(cell["type"])}
    comb = {name: cell for name, cell in cells.items() if not sequential(cell["type"])}
    primary_inputs: set[int] = set()
    primary_outputs: list[int] = []
    ignored: set[int] = set()
    for name, port in module["ports"].items():
        bits = [bit for bit in port["bits"] if isinstance(bit, int)]
        if port["direction"] == "input":
            primary_inputs.update(bits)
            if name in ("clk", "rst_n"):
                ignored.update(bits)
        else:
            primary_outputs.extend(bits)
    seq_outputs: set[int] = set()
    endpoints = list(primary_outputs)
    for cell in seq.values():
        for port, direction in cell["port_directions"].items():
            bits = [bit for bit in cell["connections"][port] if isinstance(bit, int)]
            if direction == "output":
                seq_outputs.update(bits)
            elif port not in ("C", "R", "S"):
                endpoints.extend(bits)
    drivers: dict[int, list[int]] = {}
    for cell in comb.values():
        inputs = [bit for port, direction in cell["port_directions"].items()
                  if direction == "input" for bit in cell["connections"][port]
                  if isinstance(bit, int)]
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if isinstance(bit, int):
                        drivers[bit] = inputs
    memo: dict[int, int] = {}
    visiting: set[int] = set()

    def depth(bit: int) -> int:
        if bit in primary_inputs or bit in seq_outputs or bit not in drivers:
            return 0
        if bit in memo:
            return memo[bit]
        if bit in visiting:
            raise StructuralError(f"combinational loop at net {bit}")
        visiting.add(bit)
        value = 1 + max((depth(item) for item in drivers[bit]), default=0)
        visiting.remove(bit)
        memo[bit] = value
        return value

    fanout: dict[int, int] = {}
    sink_pin_count = 0
    for cell in cells.values():
        for port, direction in cell["port_directions"].items():
            if direction != "input" or (sequential(cell["type"]) and port in ("C", "R", "S")):
                continue
            for bit in cell["connections"][port]:
                if isinstance(bit, int):
                    fanout[bit] = fanout.get(bit, 0) + 1
                    sink_pin_count += 1
    for bit in primary_outputs:
        fanout[bit] = fanout.get(bit, 0) + 1
    fanout_values = [value for bit, value in fanout.items() if bit not in ignored]
    types: dict[str, int] = {}
    for cell in cells.values():
        types[cell["type"]] = types.get(cell["type"], 0) + 1
    return {
        "mapped_cells": len(cells),
        "mapped_comb_cells": len(comb),
        "mapped_state_bits": len(seq),
        "logic_depth_levels": max((depth(bit) for bit in endpoints), default=0),
        "fanout_proxy_max": max(fanout_values, default=0),
        "fanout_proxy_p95": percentile(fanout_values, 0.95),
        "nets_fanout_ge16": sum(value >= 16 for value in fanout_values),
        "sink_pin_wire_proxy": sink_pin_count,
        "mapped_cell_types": types,
    }


def analyze_generic(path: pathlib.Path) -> dict[str, int]:
    module = json.loads(path.read_text())["modules"][TOP]
    muxes = [cell for cell in module["cells"].values()
             if cell["type"] in ("$mux", "$pmux")]
    return {
        "generic_cells": len(module["cells"]),
        "generic_mux_cells": len(muxes),
        "generic_mux_select_bits": sum(
            len(cell["connections"].get("S", [])) for cell in muxes
        ),
        "generic_mux_data_input_bits": sum(
            len(cell["connections"].get("A", [])) +
            len(cell["connections"].get("B", [])) for cell in muxes
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--verilator", required=True)
    parser.add_argument("--work-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.work_dir.exists() or args.output.exists():
        raise SystemExit("structural analysis refuses existing output paths")
    verilator = shutil.which(args.verilator)
    yosys_found = shutil.which(args.yosys)
    if verilator is None or yosys_found is None:
        raise SystemExit("required Verilator/Yosys tool absent")
    yosys = pathlib.Path(yosys_found).resolve()
    yroot = yosys.parents[2]
    yenv = os.environ.copy()
    library = yroot / "usr/lib/x86_64-linux-gnu"
    yenv["LD_LIBRARY_PATH"] = str(library) + (
        ":" + yenv["LD_LIBRARY_PATH"] if yenv.get("LD_LIBRARY_PATH") else ""
    )
    yenv["YOSYS_DATDIR"] = str(yroot / "usr/share/yosys")
    args.work_dir.mkdir(parents=True)
    verilator_version = subprocess.check_output([verilator, "--version"], text=True).strip()
    yosys_version = subprocess.check_output([str(yosys), "-V"], env=yenv, text=True).strip()

    lint_output = run([
        verilator, "--lint-only", "--timing", "-Wall", "--top-module", TOP,
        str(RTL),
    ], args.work_dir / "lint.log")
    if "%Warning" in lint_output:
        raise StructuralError("Verilator lint emitted warnings")

    generic = args.work_dir / "generic.json"
    generic_script = "; ".join([
        f"read_verilog -sv -DSYNTHESIS {RTL}", f"hierarchy -check -top {TOP}",
        "proc", "flatten", "opt", "memory", "opt", f"write_json {generic}",
    ])
    run([str(yosys), "-Q", "-q", "-p", generic_script],
        args.work_dir / "generic-yosys.log", yenv)
    mapped = args.work_dir / "mapped.json"
    mapped_script = "; ".join([
        f"read_verilog -sv -DSYNTHESIS {RTL}", f"hierarchy -check -top {TOP}",
        "proc", "flatten", "opt", "memory", "opt", "techmap", "opt",
        "abc -g simple", "clean", "check", "stat", f"write_json {mapped}",
    ])
    run([str(yosys), "-Q", "-q", "-p", mapped_script],
        args.work_dir / "mapped-yosys.log", yenv)
    metrics = {**analyze_generic(generic), **analyze_mapped(mapped)}
    if metrics["mapped_state_bits"] != 49:
        raise StructuralError(
            f"unexpected state bits: {metrics['mapped_state_bits']} != 49"
        )
    document = {
        "schema": "a4_pcck2_structural_v1",
        "qualification": "GENERIC_SYNTHESIS_PROXY_ONLY",
        "ppa_qualification": "HOLD_FOR_LIBERTY_SYNTHESIS_AND_PLACE_ROUTE",
        "provenance": {
            "rtl_sha256": sha256(RTL), "verilator_version": verilator_version,
            "yosys_version": yosys_version,
            "yosys_passes": "proc; flatten; opt; memory; opt; techmap; opt; abc -g simple; clean; check; stat",
        },
        "architectural": {
            "policy_state_bits": 32,
            "atomic_hold_state_bits": 17,
            "total_state_bits": 49,
            "functional_input_bits_including_clock_reset": 19,
            "functional_output_bits": 27,
            "row_local_request_groups": 4,
            "requests_per_row_arbiter": 4,
            "ordered_bundle_address_bits": 8,
            "cross_row_request_bits": 0,
            "row_summary_track_estimate_lower": 6,
            "row_summary_track_estimate_upper": 24,
        },
        "yosys": metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        "A4_PCCK2_STRUCTURAL_PASS "
        f"state={metrics['mapped_state_bits']} cells={metrics['mapped_cells']} "
        f"depth={metrics['logic_depth_levels']} fanout={metrics['fanout_proxy_max']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
