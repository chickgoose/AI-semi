#!/usr/bin/env python3
"""Run local Yosys structural and longest-topological-path proxies for A8."""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
from pathlib import Path


ARCHITECTURES = ("rr", "exact", "b1", "b2", "b4", "b8")
SOURCE_COUNTS = (16, 32, 64)


def architecture_script(root: Path, architecture: str, sources: int) -> str:
    rtl = root / "rtl/candidates/a8_age_calendar_wheel"
    if architecture == "rr":
        filename = rtl / "a8_rr_reference_arbiter.sv"
        top = "a8_rr_reference_arbiter"
        parameters = f"-chparam NUM_SOURCES {sources}"
    elif architecture == "exact":
        filename = rtl / "a8_exact_age_reference_arbiter.sv"
        top = "a8_exact_age_reference_arbiter"
        parameters = f"-chparam NUM_SOURCES {sources}"
    else:
        bucket_cycles = int(architecture[1:])
        epoch_count = 2 * sources // bucket_cycles
        filename = rtl / "a8_age_calendar_wheel_arbiter.sv"
        top = "a8_age_calendar_wheel_arbiter"
        parameters = (
            f"-chparam NUM_SOURCES {sources} "
            f"-chparam BUCKET_CYCLES {bucket_cycles} "
            f"-chparam EPOCH_COUNT {epoch_count}"
        )
    return (
        f"read_verilog -sv -nolatches {filename}; "
        f"hierarchy -top {top} {parameters}; "
        "proc; flatten; opt; stat; ltp -noff"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/a8-yosys-proxy"))
    parser.add_argument("--yosys", type=Path,
                        default=Path(os.environ.get("A8_YOSYS", "/tmp/a8-yosys/usr/bin/yosys")))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.yosys.is_file():
        parser.error(f"local yosys not found: {args.yosys}")

    environment = os.environ.copy()
    bundled_lib = "/tmp/a8-yosys/usr/lib/x86_64-linux-gnu"
    if args.yosys.as_posix().startswith("/tmp/a8-yosys/"):
        old_path = environment.get("LD_LIBRARY_PATH", "")
        environment["LD_LIBRARY_PATH"] = bundled_lib + (":" + old_path if old_path else "")

    rows: list[dict[str, object]] = []
    for sources in SOURCE_COUNTS:
        for architecture in ARCHITECTURES:
            command = [str(args.yosys), "-Q", "-p",
                       architecture_script(root, architecture, sources)]
            result = subprocess.run(command, cwd=root, env=environment, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    check=False)
            log_path = args.output_dir / f"{architecture}-n{sources}.log"
            log_path.write_text(result.stdout, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"Yosys failed for {architecture}/N={sources}; see {log_path}")
            cells = re.findall(r"Number of cells:\s+(\d+)", result.stdout)
            depth = re.findall(r"Longest topological path .* \(length=(\d+)\)", result.stdout)
            if len(cells) != 1 or len(depth) != 1:
                raise RuntimeError(f"could not parse Yosys proxy for {architecture}/N={sources}")
            rows.append({"architecture": architecture, "source_count": sources,
                         "generic_cells": int(cells[0]), "logic_depth": int(depth[0])})
            print(f"A8_YOSYS arch={architecture} n={sources} cells={cells[0]} depth={depth[0]}")

    output = args.output_dir / "yosys-summary.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "architecture", "source_count", "generic_cells", "logic_depth"))
        writer.writeheader()
        writer.writerows(rows)
    print(f"A8_YOSYS_PROXY_PASS rows={len(rows)} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
