#!/usr/bin/env python3
"""Run identical local lint/Yosys gates for A4 tree and flat RR references."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess


TOPS = {"quadtree": "a4_struct_quadtree_top", "flat": "a4_struct_flat_top"}
SEQUENTIAL_MARKERS = ("DFF", "LATCH")


def run(command: list[str], cwd: Path, log: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise SystemExit(f"command failed ({result.returncode}); see {log}")


def is_sequential(cell_type: str) -> bool:
    return any(marker in cell_type for marker in SEQUENTIAL_MARKERS)


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    return values[max(0, math.ceil(fraction * len(values)) - 1)]


def analyze_netlist(path: Path, top: str) -> dict[str, int | str]:
    design = json.loads(path.read_text(encoding="utf-8"))
    module = design["modules"][top]
    cells = {name: cell for name, cell in module["cells"].items()
             if cell["type"] != "$scopeinfo"}
    sequential = {name: cell for name, cell in cells.items() if is_sequential(cell["type"])}
    combinational = {name: cell for name, cell in cells.items() if not is_sequential(cell["type"])}

    primary_inputs: set[int] = set()
    primary_outputs: list[int] = []
    ignored_primary: set[int] = set()
    for name, port in module["ports"].items():
        integer_bits = [bit for bit in port["bits"] if isinstance(bit, int)]
        if port["direction"] == "input":
            primary_inputs.update(integer_bits)
            if name in ("clk", "rst_n"):
                ignored_primary.update(integer_bits)
        else:
            primary_outputs.extend(integer_bits)

    sequential_outputs: set[int] = set()
    for cell in sequential.values():
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                sequential_outputs.update(bit for bit in cell["connections"][port]
                                          if isinstance(bit, int))

    drivers: dict[int, tuple[str, list[int]]] = {}
    for name, cell in combinational.items():
        inputs = [bit for port, direction in cell["port_directions"].items()
                  if direction == "input" for bit in cell["connections"][port]
                  if isinstance(bit, int)]
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if isinstance(bit, int):
                        drivers[bit] = (name, inputs)

    memo: dict[int, int] = {}
    visiting: set[int] = set()

    def depth(bit: int | str) -> int:
        if not isinstance(bit, int) or bit in primary_inputs or bit in sequential_outputs:
            return 0
        if bit in memo:
            return memo[bit]
        if bit not in drivers:
            return 0
        if bit in visiting:
            raise AssertionError(f"combinational loop at net bit {bit}")
        visiting.add(bit)
        _, inputs = drivers[bit]
        value = 1 + max((depth(item) for item in inputs), default=0)
        visiting.remove(bit)
        memo[bit] = value
        return value

    endpoints = list(primary_outputs)
    fanout: dict[int, int] = {}
    for cell in cells.values():
        seq = is_sequential(cell["type"])
        for port, direction in cell["port_directions"].items():
            if direction != "input" or (seq and port in ("C", "R", "S")):
                continue
            for bit in cell["connections"][port]:
                if isinstance(bit, int):
                    fanout[bit] = fanout.get(bit, 0) + 1
                    if seq:
                        endpoints.append(bit)
    for bit in primary_outputs:
        fanout[bit] = fanout.get(bit, 0) + 1
    fanout_values = [count for bit, count in fanout.items() if bit not in ignored_primary]
    type_counts: dict[str, int] = {}
    for cell in cells.values():
        type_counts[cell["type"]] = type_counts.get(cell["type"], 0) + 1
    return {
        "mapped_cells": len(cells),
        "mapped_comb_cells": len(combinational),
        "mapped_state_bits": len(sequential),
        "logic_depth": max((depth(bit) for bit in endpoints), default=0),
        "fanout_proxy_max": max(fanout_values, default=0),
        "fanout_proxy_p95": percentile(fanout_values, 0.95),
        "nets_fanout_ge16": sum(value >= 16 for value in fanout_values),
        "cell_types": json.dumps(type_counts, sort_keys=True, separators=(",", ":")),
    }


def structural_proxies(design: str, n: int) -> dict[str, int | float]:
    source_width = (n - 1).bit_length()
    payload_width = 16 + source_width + 8
    ingress_bits = n * 17
    levels = round(math.log(n, 4))
    if design == "quadtree":
        nodes = (n - 1) // 3
        arbitration_bits = nodes * (payload_width + 1 + 2)
        edge_distance = sum((n // (4**level)) * (2**level) for level in range(levels))
        return {
            "ingress_state_bits": ingress_bits,
            "arbitration_state_bits": arbitration_bits,
            "architectural_state_bits": ingress_bits + arbitration_bits,
            "pipeline_stages": levels,
            "sparse_latency_cycles": levels,
            "merge_nodes": nodes,
            "merge_fanin": 4,
            "longest_wire_span": 2 ** (levels - 1),
            "control_wire_bit_grid": 2 * edge_distance,
            "full_channel_wire_bit_grid": (payload_width + 2) * edge_distance,
            "internal_links": n + nodes - 1,
        }
    average_span = math.isqrt(n) // 2
    edge_distance = n * average_span
    arbitration_bits = payload_width + 1 + source_width
    return {
        "ingress_state_bits": ingress_bits,
        "arbitration_state_bits": arbitration_bits,
        "architectural_state_bits": ingress_bits + arbitration_bits,
        "pipeline_stages": 1,
        "sparse_latency_cycles": 1,
        "merge_nodes": 1,
        "merge_fanin": n,
        "longest_wire_span": math.isqrt(n) - 1,
        "control_wire_bit_grid": 2 * edge_distance,
        "full_channel_wire_bit_grid": (payload_width + 2) * edge_distance,
        "internal_links": n,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default="/tmp/a7-yosys/usr/bin/yosys")
    parser.add_argument("--verilator", default="/tmp/a7-sim-bin/verilator")
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/a4-structural-gate"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    rtl = project / "rtl/candidates/a4_quadtree_fabric/structural/a4_structural_compare.sv"
    args.work_dir.mkdir(parents=True, exist_ok=True)
    yosys_path = Path(args.yosys).resolve()
    yosys_root = yosys_path.parents[2]
    yosys_env = os.environ.copy()
    library = yosys_root / "usr/lib/x86_64-linux-gnu"
    yosys_env["LD_LIBRARY_PATH"] = str(library) + (
        ":" + yosys_env["LD_LIBRARY_PATH"] if yosys_env.get("LD_LIBRARY_PATH") else "")
    yosys_env["YOSYS_DATDIR"] = str(yosys_root / "usr/share/yosys")
    yosys_version = subprocess.check_output([str(yosys_path), "-V"], env=yosys_env,
                                            text=True).strip()
    verilator_version = subprocess.check_output([args.verilator, "--version"],
                                                text=True).strip()
    rtl_sha256 = hashlib.sha256(rtl.read_bytes()).hexdigest()

    rows: list[dict[str, object]] = []
    for n in (16, 64):
        for design, top in TOPS.items():
            stem = f"{design}-n{n}"
            lint_log = args.work_dir / f"{stem}.verilator.log"
            lint_command = [args.verilator, "--lint-only", "--timing", "-Wall",
                            "-Wno-fatal", "-Wno-DECLFILENAME", "--top-module", top,
                            f"-GNUM_SOURCES={n}", str(rtl)]
            run(lint_command, project, lint_log)
            warning_count = lint_log.read_text(encoding="utf-8").count("%Warning")
            if warning_count:
                raise SystemExit(f"Verilator warnings in {lint_log}")

            netlist = args.work_dir / f"{stem}.json"
            yosys_script = args.work_dir / f"{stem}.ys"
            yosys_script.write_text("\n".join([
                f"read_verilog -sv -DSYNTHESIS {rtl}",
                f"hierarchy -check -top {top} -chparam NUM_SOURCES {n}",
                "proc", "flatten", "opt", "memory", "opt", "techmap", "opt",
                "abc -g simple", "clean", "check", "stat",
                f"write_json {netlist}", "",
            ]), encoding="utf-8")
            yosys_log = args.work_dir / f"{stem}.yosys.log"
            run([str(yosys_path), "-Q", "-q", "-l", str(yosys_log),
                 str(yosys_script)], project, args.work_dir / f"{stem}.yosys.stdout",
                yosys_env)
            metrics = analyze_netlist(netlist, top)
            proxies = structural_proxies(design, n)
            if metrics["mapped_state_bits"] != proxies["architectural_state_bits"]:
                raise AssertionError(f"state mismatch for {stem}: {metrics['mapped_state_bits']} "
                                     f"!= {proxies['architectural_state_bits']}")
            rows.append({
                "design": design, "sources": n, "event_width": 16,
                "source_width": (n - 1).bit_length(), "age_width": 8,
                "yosys_version": yosys_version, "verilator_version": verilator_version,
                "rtl_sha256": rtl_sha256,
                "verilator_warnings": warning_count, **proxies, **metrics,
            })
            print(f"A4_STRUCTURAL_CASE_PASS design={design} n={n} "
                  f"cells={metrics['mapped_cells']} depth={metrics['logic_depth']}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    comparison_rows: list[dict[str, object]] = []
    for n in (16, 64):
        tree = next(row for row in rows if row["design"] == "quadtree" and row["sources"] == n)
        flat = next(row for row in rows if row["design"] == "flat" and row["sources"] == n)

        def delta(metric: str) -> float:
            return round((float(tree[metric]) / float(flat[metric]) - 1.0) * 100.0, 6)

        comb_delta = delta("mapped_comb_cells")
        wire_delta = delta("full_channel_wire_bit_grid")
        fanout_delta = delta("fanout_proxy_max")
        state_delta = delta("mapped_state_bits")
        latency_delta = int(tree["sparse_latency_cycles"]) - int(flat["sparse_latency_cycles"])
        local_gate_pass = (comb_delta <= -30.0 and wire_delta <= -40.0 and
                           fanout_delta <= -75.0 and state_delta <= 60.0 and
                           latency_delta <= 2)
        comparison_rows.append({
            "sources": n,
            "tree_mapped_cells": tree["mapped_cells"],
            "flat_mapped_cells": flat["mapped_cells"],
            "mapped_cells_delta_percent": delta("mapped_cells"),
            "comb_cells_delta_percent": comb_delta,
            "state_bits_delta_percent": state_delta,
            "logic_depth_delta_percent": delta("logic_depth"),
            "max_fanout_delta_percent": fanout_delta,
            "full_wire_delta_percent": wire_delta,
            "internal_links_delta_percent": delta("internal_links"),
            "tree_extra_sparse_latency_cycles": latency_delta,
            "local_break_even_gate": "PASS" if local_gate_pass else "FAIL",
            "shortlist_decision": "CONDITIONAL_SHORTLIST" if local_gate_pass else "HOLD_FLAT",
        })
    with args.comparison_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparison_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(comparison_rows)
    print(f"A4_LOCAL_STRUCTURAL_GATE_PASS cases={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
