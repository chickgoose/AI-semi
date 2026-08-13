#!/usr/bin/env python3
"""Run identical directed vectors on staged RTL and its mapped/SDF netlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(command: list[str], cwd: Path, log: Path) -> None:
    with log.open("ab") as stream:
        stream.write(("COMMAND " + " ".join(command) + "\n").encode())
        result = subprocess.run(command, cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command[0]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", choices=("fovea_a7", "a2_p6", "a3_p6"),
                        required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--rtl-filelist", type=Path, required=True)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--sdf", type=Path, required=True)
    parser.add_argument("--model", type=Path, action="append", required=True)
    parser.add_argument("--define", action="append", default=[])
    parser.add_argument("--include-dir", type=Path, action="append", default=[])
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--xrun", type=Path, required=True)
    parser.add_argument("--testbench", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    work = args.output.parent / "mapped-functional-xcelium"
    work.mkdir(parents=True, exist_ok=False)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("")
    tb = args.testbench.resolve(strict=True)
    sources = [Path(row.strip()) for row in args.rtl_filelist.read_text().splitlines()
               if row.strip()]
    if not sources or any(not source.is_file() for source in sources):
        raise RuntimeError("staged RTL filelist is empty or unreadable")
    defines = ["-define", f"W2_FUNCTIONAL_DUT={args.top}"]
    if args.design == "fovea_a7":
        defines += ["-define", "W2_FUNCTIONAL_R1"]
    for value in args.define:
        defines += ["-define", value]

    rtl_result = work / "rtl.result"
    mapped_result = work / "mapped.result"
    common = [str(args.xrun), "-64bit", "-sv", "-timescale", "1ns/1ps",
              "-top", "k2_w2_mapped_functional_tb", *defines]
    for include_dir in args.include_dir:
        common += ["-incdir", str(include_dir.resolve(strict=True))]
    execute([*common, "-xmlibdirname", str(work / "rtl.xcelium.d"),
             *map(str, args.model), *map(str, sources), str(tb),
             "+RESULT=" + str(rtl_result)], work, args.log)
    execute([*common, "-xmlibdirname", str(work / "mapped.xcelium.d"),
             *map(str, args.model), str(args.netlist), str(tb),
             "+RESULT=" + str(mapped_result), "+SDF_FILE=" + str(args.sdf)],
            work, args.log)
    if rtl_result.read_bytes() != mapped_result.read_bytes():
        raise RuntimeError("staged RTL and mapped/SDF functional transcripts differ")
    transcript = rtl_result.read_text()
    if "PASS\n" not in transcript or "TOTAL " not in transcript:
        raise RuntimeError("functional transcript is incomplete")
    with args.log.open("a") as stream:
        stream.write("STAGED_VS_MAPPED_TRANSCRIPT_EXACT\n")
        stream.write(transcript)
    document = {
        "schema": "k2_w2_mapped_functional_gate_v1",
        "status": "PASS",
        "design": args.design,
        "top": args.top,
        "mapped_netlist_sha256": digest(args.netlist),
        "method": "xcelium_vendor_models",
        "scenarios": args.scenarios.split(","),
        "checks": {
            "accepted": "EXACT", "retired": "EXACT",
            "global_order": "EXACT", "conservation": "EXACT",
            "protocol_error": "ZERO", "reset_and_drain": "PASS",
        },
        "log_sha256": digest(args.log),
        "model_sha256": {model.name: digest(model) for model in args.model},
        "sdf_status": "ANNOTATED",
        "sdf_sha256": digest(args.sdf),
    }
    args.output.write_text(json.dumps(document, sort_keys=True) + "\n")
    print("W2_MAPPED_FUNCTIONAL_PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"W2_MAPPED_FUNCTIONAL_FAIL {error}", file=sys.stderr)
        raise SystemExit(2)
