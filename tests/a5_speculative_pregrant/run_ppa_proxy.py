#!/usr/bin/env python3
"""Collect local lint, state, generic gate, and topological-depth proxies."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import re
import subprocess
import sys


def last_int(pattern: str, text: str) -> int:
    values = re.findall(pattern, text, flags=re.MULTILINE)
    if not values:
        raise RuntimeError(f"missing proxy pattern: {pattern}")
    return int(values[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/a5-ppa-proxy"))
    parser.add_argument("--yosys", default=os.environ.get("YOSYS", "yosys"))
    parser.add_argument("--verilator", default=os.environ.get("VERILATOR", "verilator"))
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[2]
    rtl = project / "rtl/candidates/a5_speculative_pregrant"
    sources = [rtl / "a5_transition_predictor.sv", rtl / "a5_speculative_pregrant_core.sv"]
    args.output.mkdir(parents=True, exist_ok=True)
    configs = [
        ("fallback", 0, 4, 16, 2),
        ("h1_t16_c2", 1, 1, 16, 2),
        ("h2_t16_c2", 1, 2, 16, 2),
        ("h4_t2_c2", 1, 4, 2, 2),
        ("h4_t4_c2", 1, 4, 4, 2),
        ("h4_t8_c2", 1, 4, 8, 2),
        ("h4_t16_c1", 1, 4, 16, 1),
        ("h4_t16_c2", 1, 4, 16, 2),
        ("h4_t16_c3", 1, 4, 16, 3),
    ]
    rows: list[dict[str, object]] = []
    environment = os.environ.copy()
    environment["PATH"] = str(Path(args.yosys).resolve().parent) + os.pathsep + environment["PATH"]

    for name, enabled, history_bits, table_entries, conf_bits in configs:
        parameter_commands = (
            f"-chparam ENABLE_PREDICTOR {enabled} "
            f"-chparam PRED_HISTORY_BITS {history_bits} "
            f"-chparam PRED_TABLE_ENTRIES {table_entries} "
            f"-chparam PRED_CONF_WIDTH {conf_bits} "
            "-chparam ENABLE_METRICS 0"
        )
        yosys_program = (
            "read_verilog -sv -D A5_YOSYS_PROXY "
            + " ".join(str(path) for path in sources)
            + "; hierarchy -top a5_speculative_pregrant_core "
            + parameter_commands
            + "; synth -flatten -top a5_speculative_pregrant_core"
            + "; abc -g NAND; stat; ltp -noff"
        )
        yosys_result = subprocess.run(
            [args.yosys, "-Q", "-p", yosys_program], cwd=project,
            env=environment, text=True, capture_output=True,
        )
        (args.output / f"{name}.yosys.log").write_text(
            yosys_result.stdout + yosys_result.stderr, encoding="utf-8"
        )
        if yosys_result.returncode:
            sys.stdout.write(yosys_result.stdout + yosys_result.stderr)
            return yosys_result.returncode
        text = yosys_result.stdout + yosys_result.stderr

        verilator_command = [
            args.verilator, "--lint-only", "--timing", "-Wall", "-Wno-fatal",
            "-Wno-PINCONNECTEMPTY", "--top-module", "a5_speculative_pregrant_core",
            "-DA5_YOSYS_PROXY", f"-GENABLE_PREDICTOR={enabled}",
            f"-GPRED_HISTORY_BITS={history_bits}",
            f"-GPRED_TABLE_ENTRIES={table_entries}",
            f"-GPRED_CONF_WIDTH={conf_bits}", "-GENABLE_METRICS=0",
            *(str(path) for path in sources),
        ]
        verilator_result = subprocess.run(
            verilator_command, cwd=project, text=True, capture_output=True
        )
        (args.output / f"{name}.verilator.log").write_text(
            verilator_result.stdout + verilator_result.stderr, encoding="utf-8"
        )
        if verilator_result.returncode:
            sys.stdout.write(verilator_result.stdout + verilator_result.stderr)
            return verilator_result.returncode

        predictor_bits = (
            table_entries * (1 + history_bits + 4 + conf_bits) + 5
            if enabled else 0
        )
        rows.append(
            {
                "config": name,
                "enabled": enabled,
                "history_bits": history_bits,
                "table_entries": table_entries,
                "confidence_bits": conf_bits,
                "theoretical_predictor_bits": predictor_bits,
                "theoretical_total_algorithm_bits":
                    (27 + predictor_bits) if enabled else 25,
                "yosys_flip_flops": last_int(r"\$_DFFE_PN0P_\s+(\d+)", text),
                "yosys_nand_cells": last_int(r"\$_NAND_\s+(\d+)", text),
                "yosys_not_cells": last_int(r"\$_NOT_\s+(\d+)", text),
                "yosys_total_cells": last_int(r"Number of cells:\s+(\d+)", text),
                "yosys_topological_depth": last_int(
                    r"Longest topological path .*\(length=(\d+)\)", text
                ),
                "verilator_lint": "PASS",
            }
        )
        print(f"PASS {name}")

    output_csv = args.output / "a5-ppa-proxy.csv"
    with output_csv.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"A5_PPA_PROXY_PASS configs={len(rows)} output={output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
