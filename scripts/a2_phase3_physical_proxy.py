#!/usr/bin/env python3
"""Extract A2 phase-3 Yosys structure, RTL-VCD activity, and keep gates."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path


DESIGNS = ("a2", "flat_rr", "always_buffered")
PRESSURE = ("hotspot_fixed", "recurrence")
METRIC_RE = re.compile(r"A2_PHASE3_METRIC (?P<body>.*)")


def is_sequential(cell_type: str) -> bool:
    upper = cell_type.upper()
    return "DFF" in upper or "LATCH" in upper


def yosys_metrics(path: Path) -> dict[str, int | str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    module = data["modules"]["a2_phase3_physical_wrapper"]
    cells = {name: cell for name, cell in module["cells"].items()
             if cell["type"] != "$scopeinfo"}
    sequential = {name for name, cell in cells.items()
                  if is_sequential(cell["type"])}
    state_bits = 0
    for name in sequential:
        cell = cells[name]
        for port, direction in cell["port_directions"].items():
            if direction == "output" and port.upper().startswith("Q"):
                state_bits += sum(isinstance(bit, int)
                                  for bit in cell["connections"][port])

    fanout: Counter[int] = Counter()
    for cell in cells.values():
        for port, direction in cell["port_directions"].items():
            if direction == "input":
                fanout.update(bit for bit in cell["connections"][port]
                              if isinstance(bit, int))
    for port in module["ports"].values():
        if port["direction"] == "output":
            fanout.update(bit for bit in port["bits"] if isinstance(bit, int))

    driver: dict[int, str] = {}
    for name, cell in cells.items():
        if name in sequential:
            continue
        for port, direction in cell["port_directions"].items():
            if direction == "output":
                for bit in cell["connections"][port]:
                    if isinstance(bit, int):
                        driver[bit] = name

    memo: dict[str, int] = {}
    active: set[str] = set()

    def depth(name: str) -> int:
        if name in memo:
            return memo[name]
        if name in active:
            return 0
        active.add(name)
        dependencies: list[int] = []
        cell = cells[name]
        for port, direction in cell["port_directions"].items():
            if direction != "input":
                continue
            for bit in cell["connections"][port]:
                if isinstance(bit, int) and bit in driver:
                    dependencies.append(depth(driver[bit]))
        active.remove(name)
        memo[name] = 1 + max(dependencies, default=0)
        return memo[name]

    logic_depth = max((depth(name) for name in cells if name not in sequential),
                      default=0)
    clock_reset_bits = {
        bit for name in ("clk_i", "rst_ni")
        for bit in module["ports"][name]["bits"] if isinstance(bit, int)
    }
    data_control_fanout = Counter({
        bit: count for bit, count in fanout.items() if bit not in clock_reset_bits
    })
    return {
        "lut4_ff_cells": len(cells),
        "state_bits": state_bits,
        "lut_depth": logic_depth,
        "max_fanout_all": max(fanout.values(), default=0),
        "max_data_control_fanout": max(data_control_fanout.values(), default=0),
    }


def parse_vcd(path: Path) -> int:
    widths: dict[str, int] = {}
    included: set[str] = set()
    hard_excluded: set[str] = set()
    scope: list[str] = []
    header = True
    previous: dict[str, str] = {}
    toggles = 0
    top_dut = ("a2_phase3_physical_tb", "dut")
    top_inputs = {"source_valid_i", "source_event_i", "retire_ready_i"}
    wrapper_aliases = {
        "source_ready_o", "retire_valid_o", "retire_event_o",
        "retire_source_o", "core_source_ready", "core_retire_valid",
        "core_retire_event", "core_retire_source",
    }
    core_input_ports = {"source_valid_i", "source_event_i", "retire_ready_i"}

    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if header:
                fields = line.split()
                if line.startswith("$scope") and len(fields) >= 3:
                    scope.append(fields[2])
                elif line.startswith("$upscope"):
                    if scope:
                        scope.pop()
                elif line.startswith("$var") and len(fields) >= 6:
                    kind, width, identifier, name = fields[1:5]
                    if kind in {"wire", "reg"}:
                        widths[identifier] = int(width)
                        canonical_declaration = True
                        if name in {"clk_i", "rst_ni"}:
                            hard_excluded.add(identifier)
                        if tuple(scope) == top_dut and name in top_inputs:
                            canonical_declaration = False
                        if tuple(scope) == top_dut and name in wrapper_aliases:
                            canonical_declaration = False
                        if scope and scope[-1] == "core" and name in core_input_ports:
                            canonical_declaration = False
                        if canonical_declaration:
                            included.add(identifier)
                elif line.startswith("$enddefinitions"):
                    header = False
                continue
            if not line or line[0] in "#$" or line.startswith("r"):
                continue
            if line[0] in "01xXzZ":
                value, identifier = line[0], line[1:]
            elif line[0] in "bB":
                fields = line.split()
                if len(fields) != 2:
                    continue
                value, identifier = fields[0][1:], fields[1]
            else:
                continue
            if identifier not in included or identifier in hard_excluded:
                continue
            width = widths[identifier]
            if any(bit in value.lower() for bit in "xz"):
                continue
            value = value.zfill(width)[-width:]
            old = previous.get(identifier)
            if old is not None:
                toggles += sum(left != right for left, right in zip(old, value))
            previous[identifier] = value
    return toggles


def parse_metric(path: Path) -> dict[str, int | str | float]:
    match = None
    for line in path.read_text(encoding="utf-8").splitlines():
        found = METRIC_RE.search(line)
        if found:
            match = found
    if match is None:
        raise RuntimeError(f"metric missing from {path}")
    row: dict[str, int | str | float] = {}
    for token in match.group("body").split():
        key, value = token.split("=", 1)
        row[key] = value if key in {"design", "workload"} else int(value)
    return row


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    out = args.output_dir

    physical: list[dict[str, object]] = []
    physical_by: dict[tuple[int, str], dict[str, object]] = {}
    for sources in (16, 64):
        for design in DESIGNS:
            row: dict[str, object] = {"sources": sources, "design": design}
            row.update(yosys_metrics(out / f"{design}-n{sources}.json"))
            physical.append(row)
            physical_by[(sources, design)] = row

    activity: list[dict[str, object]] = []
    activity_by: dict[tuple[int, str, str], dict[str, object]] = {}
    for sources in (16, 64):
        for design in DESIGNS:
            for workload in ("sparse", "hotspot_fixed", "recurrence", "oscillate_4"):
                stem = f"{design}-n{sources}-{workload}"
                row = parse_metric(out / f"{stem}.log")
                toggles = parse_vcd(out / f"{stem}.vcd")
                cells = int(physical_by[(sources, design)]["lut4_ff_cells"])
                delivered = int(row["delivered"])
                if delivered == 0:
                    raise RuntimeError(f"zero delivered denominator in {stem}")
                stim_cycles = int(row["stim_cycles"])
                row.update({
                    "alias_filtered_rtl_vcd_bit_toggles": toggles,
                    "alias_filtered_rtl_toggle_per_delivered": toggles / delivered,
                    "activity_validity": "diagnostic_only_not_decision_grade",
                    "workload_validity": (
                        "same_cycle_duplicate_source_artifact"
                        if sources == 16 and workload == "recurrence"
                        else "representative"
                    ),
                    "fixed_throughput": int(row["fixed_delivered"]) / stim_cycles,
                    "events_per_cycle_per_cell":
                        int(row["fixed_delivered"]) / stim_cycles / cells,
                })
                activity.append(row)
                activity_by[(sources, design, workload)] = row

    decisions: list[dict[str, object]] = []
    for sources in (16, 64):
        def rows(design: str) -> list[dict[str, object]]:
            return [activity_by[(sources, design, workload)] for workload in PRESSURE]

        a2_rows = rows("a2")
        always_rows = rows("always_buffered")
        flat_rows = rows("flat_rr")
        a2_physical = physical_by[(sources, "a2")]
        always_physical = physical_by[(sources, "always_buffered")]

        def aggregate_epcc(selected: list[dict[str, object]], design: str) -> float:
            delivered = sum(int(row["fixed_delivered"]) for row in selected)
            cycles = sum(int(row["stim_cycles"]) for row in selected)
            return delivered / cycles / int(
                physical_by[(sources, design)]["lut4_ff_cells"])

        def aggregate_toggle(selected: list[dict[str, object]]) -> float:
            return sum(int(row["alias_filtered_rtl_vcd_bit_toggles"])
                       for row in selected) / sum(int(row["delivered"])
                                                  for row in selected)

        recovery = []
        for workload in PRESSURE:
            a2_epcc = float(activity_by[(sources, "a2", workload)]
                            ["events_per_cycle_per_cell"])
            for reference in ("flat_rr", "always_buffered"):
                ref_epcc = float(activity_by[(sources, reference, workload)]
                                 ["events_per_cycle_per_cell"])
                if a2_epcc >= ref_epcc:
                    recovery.append({"workload": workload, "reference": reference,
                                     "ratio": a2_epcc / ref_epcc})

        observations = {
            "functional": all(int(row["errors"]) == 0 and
                              int(row["accepted"]) == int(row["delivered"])
                              and int(row["generated"]) ==
                              int(row["accepted"]) + int(row["overrun"])
                              for row in activity if int(row["n"]) == sources),
            "pressure_backpressure_overrun":
                sum(int(row["backpressure_overrun"]) for row in a2_rows) <=
                sum(int(row["backpressure_overrun"]) for row in always_rows),
            "tail": all(int(a2["p99"]) <= int(always["p99"])
                        and int(a2["p99"]) <= int(flat["p99"]) + 16
                        for a2, always, flat in zip(a2_rows, always_rows, flat_rows)),
            "data_control_fanout": int(a2_physical["max_data_control_fanout"]) <=
                                   1.25 * int(always_physical["max_data_control_fanout"]),
            "alias_filtered_sparse_toggle_ratio":
                float(activity_by[(sources, "a2", "sparse")]
                      ["alias_filtered_rtl_toggle_per_delivered"]) / float(
                          activity_by[(sources, "always_buffered", "sparse")]
                          ["alias_filtered_rtl_toggle_per_delivered"]),
            "alias_filtered_pressure_toggle_ratio":
                aggregate_toggle(a2_rows) / aggregate_toggle(always_rows),
            "activity_decision_grade": False,
        }
        independent_gates = {
            "pressure_epcc": aggregate_epcc(a2_rows, "a2") >=
                             0.98 * aggregate_epcc(always_rows, "always_buffered"),
            "lut_depth": int(a2_physical["lut_depth"]) <=
                         1.25 * int(always_physical["lut_depth"]),
            "recovery_region": bool(recovery),
        }
        decisions.append({
            "sources": sources,
            "decision": "keep" if all(independent_gates.values()) else "reject",
            "independent_gates": independent_gates,
            "observations_not_reject_basis": observations,
            "recovery_regions": recovery,
            "pressure_overrun": {
                design: {
                    "total": sum(int(row["overrun"]) for row in selected),
                    "same_cycle_duplicate": sum(int(row["duplicate_overrun"])
                                                for row in selected),
                    "backpressure": sum(int(row["backpressure_overrun"])
                                        for row in selected),
                }
                for design, selected in (("a2", a2_rows), ("flat_rr", flat_rows),
                                         ("always_buffered", always_rows))
            },
            "pressure_epcc": {
                "a2": aggregate_epcc(a2_rows, "a2"),
                "flat_rr": aggregate_epcc(flat_rows, "flat_rr"),
                "always_buffered": aggregate_epcc(always_rows, "always_buffered"),
            },
            "pressure_toggle_per_delivered": {
                "a2": aggregate_toggle(a2_rows),
                "flat_rr": aggregate_toggle(flat_rows),
                "always_buffered": aggregate_toggle(always_rows),
            },
            "activity_status": "alias_filtered_RTL_diagnostic_only_not_power",
        })

    write_csv(out / "physical.csv", physical)
    write_csv(out / "activity.csv", activity)
    (out / "decision.json").write_text(
        json.dumps(decisions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = " ".join(f"n{row['sources']}={row['decision']}" for row in decisions)
    print(f"A2_PHASE3_ANALYSIS_PASS {summary} output={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
