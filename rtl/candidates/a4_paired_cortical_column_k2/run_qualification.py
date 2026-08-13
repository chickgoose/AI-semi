#!/usr/bin/env python3
"""Fail-closed complete local qualification for the isolated PCC-K2 candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RTL = HERE / "a4_paired_cortical_column_k2.sv"
ADAPTER = HERE / "a4_pcck2_ordered_link_adapter.sv"
TESTS = HERE / "tests"


class QualificationError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_tool(requested: str, version_arg: str) -> tuple[str, str]:
    found = shutil.which(requested)
    if found is None:
        raise QualificationError(f"required tool absent: {requested}")
    result = subprocess.run(
        [found, version_arg], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False
    )
    if result.returncode:
        raise QualificationError(f"required tool cannot execute: {found}")
    return found, result.stdout.strip()


def run(arguments: list[str], log: pathlib.Path) -> str:
    result = subprocess.run(
        arguments, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False
    )
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise QualificationError(f"command failed ({result.returncode}); see {log}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--common-root", required=True, type=pathlib.Path)
    parser.add_argument("--verilator", required=True)
    parser.add_argument("--yosys", required=True)
    parser.add_argument("--work-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.work_dir.exists() or args.output.exists():
        raise SystemExit("qualification refuses existing work/output paths")
    verilator, verilator_version = resolve_tool(args.verilator, "--version")
    # structural.py performs the Yosys shared-library-aware execution check.
    if shutil.which(args.yosys) is None:
        raise QualificationError(f"required tool absent: {args.yosys}")
    args.work_dir.mkdir(parents=True)

    lint = run([
        verilator, "--lint-only", "--timing", "-Wall",
        "--top-module", "a4_paired_cortical_column_k2", str(RTL),
    ], args.work_dir / "scheduler-lint.log")
    adapter_lint = run([
        verilator, "--lint-only", "--timing", "-Wall",
        "--top-module", "a4_pcck2_ordered_link_adapter", str(RTL), str(ADAPTER),
    ], args.work_dir / "adapter-lint.log")
    if "%Warning" in lint + adapter_lint:
        raise QualificationError("warning-free lint gate failed")
    run([
        sys.executable, "-B", "-m", "unittest", "discover", "-s", str(TESTS),
        "-p", "test_*.py", "-v",
    ], args.work_dir / "model-tests.log")

    directed = args.work_dir / "directed"
    run([
        sys.executable, "-B", str(TESTS / "generate_vectors.py"),
        "--output", str(directed),
    ], args.work_dir / "directed-generate.log")
    lockstep_obj = args.work_dir / "obj-lockstep"
    build = run([
        verilator, "--binary", "--timing", "--assert", "-Wall",
        "--top-module", "a4_pcck2_lockstep_tb", "--Mdir", str(lockstep_obj),
        "-o", "sim", str(RTL), str(TESTS / "lockstep_tb.sv"),
    ], args.work_dir / "lockstep-build.log")
    if "%Warning" in build:
        raise QualificationError("lockstep build warnings")
    directed_cases = (
        "all_rows", "sparse", "hotspot", "mirror", "reset", "reset_live", "stall"
    )
    for case_name in directed_cases:
        output = run([
            str(lockstep_obj / "sim"), f"+CASE={case_name}",
            f"+VECTORS={directed / (case_name + '.vectors')}",
        ], args.work_dir / f"directed-{case_name}.log")
        if f"A4_PCCK2_LOCKSTEP_PASS case={case_name}" not in output:
            raise QualificationError(f"missing directed marker: {case_name}")

    link_obj = args.work_dir / "obj-link"
    link_build = run([
        verilator, "--binary", "--timing", "--assert", "-Wall",
        "--top-module", "a4_pcck2_ordered_link_tb", "--Mdir", str(link_obj),
        "-o", "sim", str(RTL), str(ADAPTER), str(TESTS / "ordered_link_tb.sv"),
    ], args.work_dir / "link-build.log")
    if "%Warning" in link_build:
        raise QualificationError("ordered-link build warnings")
    link_run = run([str(link_obj / "sim")], args.work_dir / "link-run.log")
    if "A4_PCCK2_ORDERED_LINK_PASS" not in link_run:
        raise QualificationError("ordered-link marker missing")

    run([
        sys.executable, "-B", str(TESTS / "run_mutations.py"),
        "--verilator", verilator, "--vectors", str(directed),
        "--work-dir", str(args.work_dir / "mutations"),
    ], args.work_dir / "mutations.log")

    replay_report = args.work_dir / "replay.json"
    replay_vectors = args.work_dir / "replay-vectors"
    run([
        sys.executable, "-B", str(HERE / "replay_v4.py"),
        "--common-root", str(args.common_root),
        "--generated-root", str(args.work_dir / "generated-v4"),
        "--vectors-root", str(replay_vectors), "--output", str(replay_report),
    ], args.work_dir / "replay-generate.log")
    replay = json.loads(replay_report.read_text())
    rtl_replay_count = 0
    for suite in ("full50", "capacity22"):
        for record in replay["suites"][suite]["runs"]:
            name = record["name"]
            vector = replay_vectors / suite / f"{name}.vectors"
            output = run([
                str(lockstep_obj / "sim"), f"+CASE={suite}--{name}",
                f"+VECTORS={vector}",
            ], args.work_dir / f"replay-{suite}--{name}.log")
            if "A4_PCCK2_LOCKSTEP_PASS" not in output:
                raise QualificationError(f"frozen RTL replay failed: {suite}/{name}")
            rtl_replay_count += 1

    structural_report = args.work_dir / "structural.json"
    run([
        sys.executable, "-B", str(HERE / "structural.py"),
        "--verilator", verilator, "--yosys", args.yosys,
        "--work-dir", str(args.work_dir / "structural"),
        "--output", str(structural_report),
    ], args.work_dir / "structural-run.log")
    contract_report = args.work_dir / "contract-crosscheck.json"
    run([
        sys.executable, "-B", str(HERE / "contract_crosscheck.py"),
        "--output", str(contract_report),
    ], args.work_dir / "contract-crosscheck.log")

    material_paths = sorted(
        path for path in HERE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and "results" not in path.parts
    )
    document = {
        "schema": "a4_pcck2_qualification_v1",
        "decision": "LOCAL_PASS",
        "semantic_grade": "AGGREGATE_ONLY",
        "common_qualification": "HOLD",
        "ppa_qualification": "PROXY_ONLY_HOLD_FOR_LIBERTY_AND_PLACE_ROUTE",
        "tests": {
            "model_unit_tests": 6, "directed_rtl_cases": len(directed_cases),
            "mutation_falsifiers_killed": 5, "ordered_link_cases": 4,
            "frozen_v4_full50": 50, "frozen_v4_capacity22_subset": 22,
            "frozen_v4_rtl_executions": rtl_replay_count,
        },
        "persistent_committed_row_grants_120": [10, 50, 50, 10],
        "structural": json.loads(structural_report.read_text()),
        "contract_crosscheck": json.loads(contract_report.read_text()),
        "provenance": {
            "verilator_version": verilator_version,
            "generator_sha256": replay["provenance"]["generator_sha256"],
            "common_head": replay["provenance"]["common_head"],
            "material_source_sha256": {
                str(path.relative_to(HERE)): sha256(path) for path in material_paths
            },
        },
        "limits": [
            "Persistent aggregate [1,5,5,1] is proven; scalar-prefix equivalence is false.",
            "A5 transport adapter does not upgrade the aggregate-only semantic grade.",
            "A8 exact paired calendar and signed-debt equivalence are not claimed.",
            "Yosys results are generic proxies, not physical PPA closure.",
            "capacity22 is a subset replay, not 22 additional independent traces.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        "A4_PCCK2_QUALIFICATION_PASS directed=7 mutations=5 "
        "full50=50 capacity22=22 semantic=AGGREGATE_ONLY"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
