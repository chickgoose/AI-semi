#!/usr/bin/env python3
"""Local generic structural proxy for ceiling/Pareto configurations."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import re
import subprocess
import sys


def metric(pattern: str, report: str) -> int:
    matches = re.findall(pattern, report, flags=re.MULTILINE)
    if not matches:
        raise RuntimeError(f"missing Yosys metric {pattern}")
    return int(matches[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-ceiling-ppa"))
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--verilator", default="verilator")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    rtl = project / "rtl/candidates/a5_speculative_pregrant"
    sources = [rtl / "a5_transition_predictor.sv",
               rtl / "a5_last_successor_predictor.sv",
               rtl / "a5_speculative_pregrant_core.sv"]
    configs = [
        ("fallback", 0, 1, 4, 16, 2, 1, 0),
        ("markov_h4_t1_c2_g1", 1, 1, 4, 1, 2, 1, 16),
        ("markov_h4_t4_c2_g0", 1, 1, 4, 4, 2, 0, 49),
        ("markov_h4_t8_c2_g1", 1, 1, 4, 8, 2, 1, 93),
        ("markov_h4_t16_c1_g1", 1, 1, 4, 16, 1, 1, 165),
        ("markov_h4_t16_c2_g1", 1, 1, 4, 16, 2, 1, 181),
        ("last_successor_h4_t16", 1, 2, 4, 16, 2, 1, 149),
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PATH"] = str(Path(args.yosys).resolve().parent) + os.pathsep + environment["PATH"]
    rows: list[dict[str, object]] = []
    for name, enabled, style, history, entries, confidence, gated, bits in configs:
        params = (f"-chparam ENABLE_PREDICTOR {enabled} "
                  f"-chparam PREDICTOR_STYLE {style} "
                  f"-chparam PRED_HISTORY_BITS {history} "
                  f"-chparam PRED_TABLE_ENTRIES {entries} "
                  f"-chparam PRED_CONF_WIDTH {confidence} "
                  f"-chparam PRED_CONFIDENCE_GATE {gated} "
                  "-chparam ENABLE_METRICS 0")
        program = ("read_verilog -sv -D A5_YOSYS_PROXY " +
                   " ".join(str(path) for path in sources) +
                   "; hierarchy -top a5_speculative_pregrant_core " + params +
                   "; synth -flatten -top a5_speculative_pregrant_core" +
                   "; abc -g NAND; stat; ltp -noff")
        result = subprocess.run([args.yosys, "-Q", "-p", program], cwd=project,
                                env=environment, text=True, capture_output=True)
        report = result.stdout + result.stderr
        (args.output / f"{name}.yosys.log").write_text(report, encoding="utf-8")
        if result.returncode:
            sys.stdout.write(report)
            return result.returncode
        lint = subprocess.run([
            args.verilator, "--lint-only", "--timing", "--assert", "-Wall",
            "-Wno-fatal", "-Wno-PINCONNECTEMPTY", "-Wno-SYNCASYNCNET",
            "--top-module", "a5_speculative_pregrant_core", "-DA5_YOSYS_PROXY",
            f"-GENABLE_PREDICTOR={enabled}", f"-GPREDICTOR_STYLE={style}",
            f"-GPRED_HISTORY_BITS={history}", f"-GPRED_TABLE_ENTRIES={entries}",
            f"-GPRED_CONF_WIDTH={confidence}", f"-GPRED_CONFIDENCE_GATE={gated}",
            "-GENABLE_METRICS=0", *(str(path) for path in sources)
        ], cwd=project, text=True, capture_output=True)
        (args.output / f"{name}.verilator.log").write_text(
            lint.stdout + lint.stderr, encoding="utf-8")
        if lint.returncode:
            sys.stdout.write(lint.stdout + lint.stderr)
            return lint.returncode
        rows.append({
            "config": name, "predictor_state_bits": bits,
            "flip_flops": metric(r"\$_DFFE_PN0P_\s+(\d+)", report),
            "nand_cells": metric(r"\$_NAND_\s+(\d+)", report),
            "not_cells": metric(r"\$_NOT_\s+(\d+)", report),
            "total_cells": metric(r"Number of cells:\s+(\d+)", report),
            "topological_depth": metric(
                r"Longest topological path .*\(length=(\d+)\)", report),
            "verilator_lint": "PASS",
        })
        print(f"PASS {name}")
    with (args.output / "a5-ceiling-ppa.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"A5_CEILING_PPA_PROXY_PASS configs={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
