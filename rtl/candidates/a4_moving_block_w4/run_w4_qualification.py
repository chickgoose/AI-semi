#!/usr/bin/env python3
"""Reproduce W4 exact lockstep, frozen-suite replay, and generic mapping."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
W3 = ROOT / "rtl/candidates/a4_moving_block_tree"
STRUCTURAL_TOOLS = ROOT / "tests/a4"
sys.path.insert(0, str(W3))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(STRUCTURAL_TOOLS))

from analyze_p99 import analyze_generated_suite  # noqa: E402
from replay_generator_v4 import write_rtl_vectors  # noqa: E402
from run_structural_gate import analyze_netlist  # noqa: E402


TOPS = {
    "frozen_850fbcf_normalized": "a4_w4_frozen_normalized",
    "shared_clearance": "a4_w4_shared_clearance",
    "shared_clearance_local_enable": "a4_w4_shared_clearance_local_enable",
}


class QualificationError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(
    arguments: list[str],
    cwd: pathlib.Path,
    log: pathlib.Path,
    environment: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise QualificationError(
            f"command failed ({result.returncode}): {' '.join(arguments)}; see {log}"
        )
    return result.stdout


def resolve_tool(
    requested: str,
    version_argument: str,
    environment: dict[str, str] | None = None,
) -> tuple[pathlib.Path, str]:
    found = shutil.which(requested)
    if found is None:
        raise QualificationError(f"cannot resolve required tool: {requested}")
    path = pathlib.Path(found).resolve()
    result = subprocess.run(
        [str(path), version_argument],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise QualificationError(f"cannot execute required tool: {path}")
    return path, (result.stdout + result.stderr).strip()


def yosys_environment(yosys: pathlib.Path) -> dict[str, str]:
    root = yosys.parents[2]
    environment = os.environ.copy()
    library = root / "usr/lib/x86_64-linux-gnu"
    environment["LD_LIBRARY_PATH"] = str(library) + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["YOSYS_DATDIR"] = str(root / "usr/share/yosys")
    return environment


def delta_percent(candidate: int, baseline: int) -> float:
    return round((candidate / baseline - 1.0) * 100.0, 6)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-root", type=pathlib.Path, required=True)
    parser.add_argument("--verilator", default=os.environ.get("AER_VERILATOR", "verilator"))
    parser.add_argument("--yosys", default=os.environ.get("AER_YOSYS", "yosys"))
    parser.add_argument("--work-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.work_dir.exists() or args.output.exists():
        raise SystemExit("work/output collision; W4 refuses overwrite")
    args.work_dir.mkdir(parents=True)

    verilator, verilator_version = resolve_tool(args.verilator, "--version")
    yosys_requested = shutil.which(args.yosys)
    if yosys_requested is None:
        raise QualificationError(f"cannot resolve required tool: {args.yosys}")
    yosys_path = pathlib.Path(yosys_requested).resolve()
    yenv = yosys_environment(yosys_path)
    yosys, yosys_version = resolve_tool(args.yosys, "-V", yenv)
    yenv = yosys_environment(yosys)
    rtl = HERE / "a4_moving_block_w4.sv"
    frozen_rtl = W3 / "a4_moving_block_tree.sv"
    tb = HERE / "tests/a4_w4_exact_lockstep_tb.sv"

    # Generate the exact frozen suites through the already hash-locked W3
    # replay.  Its output and generated paths are unique under this W4 run.
    generated = args.work_dir / "generated"
    model_report = args.work_dir / "w3-model-report.json"
    run(
        [
            sys.executable,
            "-B",
            str(W3 / "replay_generator_v4.py"),
            "--common-root",
            str(args.common_root.resolve()),
            "--suite",
            "all",
            "--generated-root",
            str(generated),
            "--output",
            str(model_report),
        ],
        ROOT,
        args.work_dir / "generator-v4-replay.log",
    )
    model = json.loads(model_report.read_text())

    lint = {}
    for design, top in TOPS.items():
        output = run(
            [
                str(verilator),
                "--lint-only",
                "--timing",
                "-Wall",
                "-Wno-fatal",
                "-Wno-DECLFILENAME",
                "--top-module",
                top,
                str(rtl),
            ],
            ROOT,
            args.work_dir / f"{design}.lint.log",
        )
        warnings = output.count("%Warning")
        if warnings:
            raise QualificationError(f"Verilator warnings for {design}: {warnings}")
        lint[design] = {"warnings": warnings, "status": "PASS"}

    object_dir = args.work_dir / "obj-lockstep"
    build_output = run(
        [
            str(verilator),
            "--binary",
            "--timing",
            "--assert",
            "-Wall",
            "-Wno-fatal",
            "-Wno-DECLFILENAME",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-BLKSEQ",
            "--top-module",
            "a4_w4_exact_lockstep_tb",
            "--Mdir",
            str(object_dir),
            "-o",
            "sim",
            str(frozen_rtl),
            str(rtl),
            str(tb),
        ],
        ROOT,
        args.work_dir / "lockstep-build.log",
    )
    if "%Warning" in build_output:
        raise QualificationError("warnings in exact lockstep build")

    lockstep = {}
    vector_root = args.work_dir / "vectors"
    vector_root.mkdir()
    for suite in ("full50", "capacity22"):
        generated_suite = generated / suite
        index = json.loads((generated_suite / "generation-index.json").read_text())
        passed = []
        for metadata in index["runs"]:
            name = metadata["run"]["name"]
            vector = vector_root / f"{suite}--{name}.vectors.txt"
            cycles = write_rtl_vectors(
                generated_suite / metadata["trace_file"], metadata, vector
            )
            output = run(
                [str(object_dir / "sim"), f"+VECTORS={vector}"],
                ROOT,
                args.work_dir / f"{suite}--{name}.lockstep.log",
            )
            marker = f"A4_W4_EXACT_LOCKSTEP_PASS cycles={cycles}"
            if marker not in output:
                raise QualificationError(f"missing lockstep marker for {suite}/{name}")
            passed.append(
                {
                    "name": name,
                    "trace_sha256": metadata["trace_sha256"],
                    "cycles": cycles,
                }
            )
        expected = 50 if suite == "full50" else 22
        if len(passed) != expected:
            raise QualificationError(f"{suite}: expected {expected} traces")
        lockstep[suite] = {"count": len(passed), "runs": passed}

    mapping = []
    for sources in (16, 64):
        for design, top in TOPS.items():
            netlist = args.work_dir / f"{design}-n{sources}.json"
            script = "; ".join(
                [
                    f"read_verilog -sv -DSYNTHESIS {rtl}",
                    f"hierarchy -check -top {top} -chparam NUM_SOURCES {sources} -chparam ADDR_WIDTH 32",
                    "proc",
                    "flatten",
                    "opt",
                    "memory",
                    "opt",
                    "techmap",
                    "opt",
                    "abc -g simple",
                    "clean",
                    "check",
                    "stat",
                    f"write_json {netlist}",
                ]
            )
            run(
                [str(yosys), "-Q", "-q", "-p", script],
                ROOT,
                args.work_dir / f"{design}-n{sources}.yosys.log",
                yenv,
            )
            metrics = analyze_netlist(netlist, top)
            mapping.append({"design": design, "sources": sources, **metrics})

    comparisons = []
    for sources in (16, 64):
        baseline = next(
            row
            for row in mapping
            if row["sources"] == sources
            and row["design"] == "frozen_850fbcf_normalized"
        )
        for design in ("shared_clearance", "shared_clearance_local_enable"):
            candidate = next(
                row
                for row in mapping
                if row["sources"] == sources and row["design"] == design
            )
            comparisons.append(
                {
                    "design": design,
                    "sources": sources,
                    "cells_delta_percent": delta_percent(
                        int(candidate["mapped_cells"]), int(baseline["mapped_cells"])
                    ),
                    "comb_delta_percent": delta_percent(
                        int(candidate["mapped_comb_cells"]),
                        int(baseline["mapped_comb_cells"]),
                    ),
                    "depth_delta": int(candidate["logic_depth"])
                    - int(baseline["logic_depth"]),
                    "max_fanout_delta": int(candidate["fanout_proxy_max"])
                    - int(baseline["fanout_proxy_max"]),
                    "state_delta": int(candidate["mapped_state_bits"])
                    - int(baseline["mapped_state_bits"]),
                }
            )

    tail = {
        suite: analyze_generated_suite(generated / suite)
        for suite in ("full50", "capacity22")
    }
    document = {
        "schema_version": 1,
        "baseline_commit": "850fbcfa4ad168b1250223610780f11378f6c391",
        "qualification": "LOCAL_ONLY",
        "common_qualification": "HOLD",
        "ppa_qualification": "HOLD",
        "provenance": {
            "candidate_head_before_run": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "w4_rtl_sha256": sha256(rtl),
            "frozen_rtl_sha256": sha256(frozen_rtl),
            "common_head": model["provenance"]["common_head"],
            "generator_sha256": model["provenance"]["generator_sha256"],
            "full50_manifest_sha256": model["suites"]["full50"]["manifest_sha256"],
            "capacity22_manifest_sha256": model["suites"]["capacity22"]["manifest_sha256"],
            "verilator_version": verilator_version,
            "yosys_version": yosys_version,
            "yosys_passes": "proc; flatten; opt; memory; opt; techmap; opt; abc -g simple; clean; check; stat",
        },
        "lint": lint,
        "lockstep": lockstep,
        "model_metrics": {
            suite: {
                "fixed": model["suites"][suite]["fixed"],
                "moving": model["suites"][suite]["moving"],
            }
            for suite in ("full50", "capacity22")
        },
        "mapping": mapping,
        "comparisons": comparisons,
        "p99_cause": tail,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        "A4_W4_QUALIFICATION_PASS full50=50 capacity22=22 "
        "designs=3 sizes=2"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
