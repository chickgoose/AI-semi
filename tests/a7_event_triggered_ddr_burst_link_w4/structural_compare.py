#!/usr/bin/env python3
"""Same-top generic synthesis proxy for parallel4, DDR2, and serial1 links."""

from __future__ import annotations

import argparse, csv, json, os, re, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    *sorted((ROOT / "rtl/candidates/a7_event_triggered_ddr_burst_link_w4").glob("*.sv")),
    ROOT / "tests/a7_event_triggered_ddr_burst_link_w4/a7_w4_structural_compare.sv",
]
STYLES = {"parallel4": (0, 5, 1.0), "ddr2": (1, 3, 1.0), "serial1": (2, 2, 0.5)}
DEPTH_RE = re.compile(r"Longest topological path .*\(length=(\d+)\)")


def run(yosys: str, name: str) -> dict[str, object]:
    style, pins, capacity = STYLES[name]
    with tempfile.TemporaryDirectory(prefix="a7-w4-yosys-") as tmp:
        stat_path = Path(tmp) / "stat.json"
        command = (
            "read_verilog -sv " + " ".join(map(str, SOURCES)) + "; "
            "hierarchy -top a7_w4_structural_compare_top "
            f"-chparam STYLE {style}; proc; flatten; opt; "
            f"tee -o {stat_path} stat -json -width; ltp -noff; techmap; opt; ltp -noff"
        )
        result = subprocess.run([yosys, "-Q", "-p", command], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(result.stdout[-5000:])
        depths = [int(x) for x in DEPTH_RE.findall(result.stdout)]
        document = json.loads(stat_path.read_text())
        module = next(iter(document["modules"].values()))
        hist = module["num_cells_by_type"]
        scope_cells = sum(v for k, v in hist.items() if "scopeinfo" in k.lower())
        reg_cells = sum(v for k, v in hist.items() if any(x in k.lower() for x in ("dff", "dlatch")))
        state_bits = sum(v * int(re.search(r"_(\d+)$", k).group(1)) for k, v in hist.items()
                         if any(x in k.lower() for x in ("dff", "dlatch")))
        return {"link": name, "physical_pins": pins,
                "logical_events_per_link_cycle": capacity,
                "functional_cells": module["num_cells"] - scope_cells,
                "register_or_latch_cells": reg_cells,
                "state_bits": state_bits, "operator_depth": depths[0],
                "generic_gate_depth": depths[1]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [run(args.yosys, name) for name in STYLES]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    for row in rows:
        print(" ".join(f"{k}={v}" for k, v in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
