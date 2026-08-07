#!/usr/bin/env python3
"""Extract technology-neutral structural proxies from a Yosys JSON netlist."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


SEQUENTIAL = re.compile(r"DFF")


def is_sequential(cell: dict) -> bool:
    return bool(SEQUENTIAL.search(cell["type"]))


def input_bits(cell: dict, timing_only: bool = False):
    for port, direction in cell.get("port_directions", {}).items():
        if direction != "input":
            continue
        if timing_only and is_sequential(cell) and port not in ("D", "E"):
            continue
        yield from cell["connections"].get(port, [])


def output_bits(cell: dict):
    for port, direction in cell.get("port_directions", {}).items():
        if direction == "output":
            yield from cell["connections"].get(port, [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("netlist", type=Path)
    parser.add_argument("--implementation", required=True)
    parser.add_argument("--sources", type=int, required=True)
    parser.add_argument("--lanes", type=int, required=True)
    parser.add_argument("--header", action="store_true")
    args = parser.parse_args()

    design = json.loads(args.netlist.read_text(encoding="utf-8"))
    module = design["modules"]["a9_phase4_synth_top"]
    cells = module["cells"]
    real_cells = {name: cell for name, cell in cells.items()
                  if cell["type"] != "$scopeinfo"}
    sequential = {name: cell for name, cell in real_cells.items()
                  if is_sequential(cell)}
    combinational = {name: cell for name, cell in real_cells.items()
                     if not is_sequential(cell)}

    producer: dict[int | str, str] = {}
    for name, cell in real_cells.items():
        for bit in output_bits(cell):
            if isinstance(bit, int):
                producer[bit] = name

    cell_depth: dict[str, int] = {}
    visiting: set[str] = set()

    def depth_bit(bit) -> int:
        if not isinstance(bit, int) or bit not in producer:
            return 0
        name = producer[bit]
        if name in sequential:
            return 0
        if name in cell_depth:
            return cell_depth[name]
        if name in visiting:
            raise RuntimeError(f"combinational cycle through {name}")
        visiting.add(name)
        value = 1 + max((depth_bit(item) for item in
                         input_bits(combinational[name])), default=0)
        visiting.remove(name)
        cell_depth[name] = value
        return value

    timing_end_bits = []
    for cell in sequential.values():
        timing_end_bits.extend(input_bits(cell, timing_only=True))
    for port in module["ports"].values():
        if port["direction"] == "output":
            timing_end_bits.extend(port["bits"])
    logic_depth = max((depth_bit(bit) for bit in timing_end_bits), default=0)

    ready_bits = set(module["netnames"]["retire_ready_boundary_q"]["bits"])
    reachable_cell_depth: dict[str, int | None] = {}
    visiting.clear()

    def ready_depth(bit):
        if bit in ready_bits:
            return 0
        if not isinstance(bit, int) or bit not in producer:
            return None
        name = producer[bit]
        if name in sequential:
            return None
        if name in reachable_cell_depth:
            return reachable_cell_depth[name]
        if name in visiting:
            raise RuntimeError(f"ready-path combinational cycle through {name}")
        visiting.add(name)
        predecessors = [ready_depth(item) for item in
                        input_bits(combinational[name])]
        predecessors = [value for value in predecessors if value is not None]
        value = 1 + max(predecessors) if predecessors else None
        visiting.remove(name)
        reachable_cell_depth[name] = value
        return value

    valid_bits = module["netnames"]["retire_valid_boundary_d"]["bits"]
    ready_valid_depths = [ready_depth(bit) for bit in valid_bits]
    ready_valid_depths = [value for value in ready_valid_depths
                          if value is not None]
    ready_to_valid_depth = (max(ready_valid_depths)
                            if ready_valid_depths else 0)

    fanout = Counter()
    for cell in combinational.values():
        for bit in input_bits(cell):
            if isinstance(bit, int):
                fanout[bit] += 1
    for cell in sequential.values():
        for bit in input_bits(cell, timing_only=True):
            if isinstance(bit, int):
                fanout[bit] += 1
    for port in module["ports"].values():
        if port["direction"] == "output":
            for bit in port["bits"]:
                if isinstance(bit, int):
                    fanout[bit] += 1
    excluded = set(module["ports"]["clk_i"]["bits"])
    excluded.update(module["ports"]["rst_ni"]["bits"])
    for bit in excluded:
        fanout.pop(bit, None)
    max_fanout = max(fanout.values(), default=0)
    max_bits = {bit for bit, value in fanout.items() if value == max_fanout}
    names_by_bit: dict[int, list[str]] = defaultdict(list)
    for name, net in module["netnames"].items():
        for bit in net["bits"]:
            if bit in max_bits and not name.startswith("$"):
                names_by_bit[bit].append(name)
    fanout_name = "unknown"
    if max_bits:
        candidates = [name for bit in max_bits for name in names_by_bit[bit]]
        if candidates:
            fanout_name = min(candidates, key=lambda value: (len(value), value))

    source_width = max(1, (args.sources - 1).bit_length())
    boundary_state = (2 * args.sources + args.sources * 16 +
                      2 * args.lanes + args.lanes * 16 +
                      args.lanes * source_width)
    state_bits = len(sequential)
    counts = Counter(cell["type"] for cell in real_cells.values())
    row = {
        "implementation": args.implementation,
        "sources": args.sources,
        "lanes": args.lanes,
        "total_generic_cells": len(real_cells),
        "combinational_cells": len(combinational),
        "state_bits": state_bits,
        "boundary_state_bits": boundary_state,
        "core_state_bits": state_bits - boundary_state,
        "logic_depth": logic_depth,
        "max_data_control_fanout": max_fanout,
        "max_fanout_net": fanout_name,
        "ready_to_valid_depth": ready_to_valid_depth,
        "and_cells": counts["$_AND_"],
        "or_cells": counts["$_OR_"],
        "mux_cells": counts["$_MUX_"],
        "xor_cells": counts["$_XOR_"],
        "not_cells": counts["$_NOT_"],
    }
    writer = csv.DictWriter(sys.stdout, fieldnames=list(row))
    if args.header:
        writer.writeheader()
    writer.writerow(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
