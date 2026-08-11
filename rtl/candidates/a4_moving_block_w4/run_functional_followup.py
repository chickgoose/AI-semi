#!/usr/bin/env python3
"""Run stalled/reset N16 and bounded N64 exact equivalence qualification."""

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
TESTS = HERE / "tests"
sys.path.insert(0, str(W3 / "tests"))
sys.path.insert(0, str(TESTS))

from generate_lockstep import generate as generate_w3  # noqa: E402
from generate_stall_reset_vectors import generate as generate_custom  # noqa: E402


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(arguments: list[str], log: pathlib.Path) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(arguments)}; see {log}"
        )
    return result.stdout


def audit_vector(path: pathlib.Path, sources: int, name: str) -> dict[str, int | str]:
    queues: list[list[int]] = [[] for _ in range(sources)]
    accepted = 0
    retired = 0
    reset_discarded = 0
    reset_cycles = 0
    max_outstanding = 0
    stall_run = 0
    max_stall = 0
    last_valid_mask = 1
    last_retire_valid = 1
    cycles = 0
    with path.open(encoding="ascii") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) != 3 + sources + 4:
                raise AssertionError(f"{name}: malformed vector cycle {cycles}")
            rst_n = int(fields[0])
            valid_mask = int(fields[1], 16)
            sink_ready = int(fields[2])
            payload = [int(value, 16) for value in fields[3 : 3 + sources]]
            cursor = 3 + sources
            ready_mask = int(fields[cursor], 16)
            retire_valid = int(fields[cursor + 1])
            retire_source = int(fields[cursor + 2], 16)
            retire_event = int(fields[cursor + 3], 16)
            if not rst_n:
                reset_cycles += 1
                reset_discarded += sum(len(queue) for queue in queues)
                queues = [[] for _ in range(sources)]
            else:
                if retire_valid and sink_ready:
                    if retire_source >= sources or not queues[retire_source]:
                        raise AssertionError(f"{name}: phantom/duplicate retire")
                    if queues[retire_source].pop(0) != retire_event:
                        raise AssertionError(f"{name}: source reorder")
                    retired += 1
                for source in range(sources):
                    if (ready_mask & valid_mask) & (1 << source):
                        queues[source].append(payload[source])
                        accepted += 1
            if rst_n and retire_valid and not sink_ready:
                stall_run += 1
                max_stall = max(max_stall, stall_run)
            else:
                stall_run = 0
            max_outstanding = max(max_outstanding, sum(len(queue) for queue in queues))
            last_valid_mask = valid_mask
            last_retire_valid = retire_valid
            cycles += 1
    if any(queues) or accepted != retired + reset_discarded:
        raise AssertionError(f"{name}: conservation/drain failure")
    if last_valid_mask or last_retire_valid:
        raise AssertionError(f"{name}: missing final idle drain cycle")
    return {
        "name": name,
        "sources": sources,
        "cycles": cycles,
        "accepted": accepted,
        "retired": retired,
        "reset_discarded": reset_discarded,
        "reset_cycles": reset_cycles,
        "max_outstanding": max_outstanding,
        "max_continuous_valid_root_stall": max_stall,
        "conservation": "PASS",
        "source_order": "PASS",
        "drain": "PASS",
    }


def build_simulator(
    verilator: pathlib.Path, sources: int, work: pathlib.Path
) -> pathlib.Path:
    object_dir = work / f"obj-n{sources}"
    output = run(
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
            "a4_w4_stall_reset_lockstep_tb",
            f"-GNUM_SOURCES={sources}",
            "--Mdir",
            str(object_dir),
            "-o",
            "sim",
            str(W3 / "a4_moving_block_tree.sv"),
            str(HERE / "a4_moving_block_w4.sv"),
            str(TESTS / "a4_w4_stall_reset_lockstep_tb.sv"),
        ],
        work / f"build-n{sources}.log",
    )
    if "%Warning" in output:
        raise RuntimeError(f"N{sources} build produced Verilator warnings")
    return object_dir / "sim"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verilator", default=os.environ.get("AER_VERILATOR", "verilator")
    )
    parser.add_argument("--work-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.work_dir.exists() or args.output.exists():
        raise SystemExit("work/output collision; functional follow-up refuses overwrite")
    resolved = shutil.which(args.verilator)
    if resolved is None:
        raise SystemExit(f"cannot resolve Verilator: {args.verilator}")
    verilator = pathlib.Path(resolved).resolve()
    version = subprocess.run(
        [str(verilator), "--version"], text=True, capture_output=True, check=False
    )
    if version.returncode or not version.stdout.startswith("Verilator "):
        raise SystemExit(f"invalid Verilator executable: {verilator}")
    args.work_dir.mkdir(parents=True)
    vectors = args.work_dir / "vectors"
    vectors.mkdir()

    w3_vector = vectors / "w3_frozen_760.vectors.txt"
    generate_w3(w3_vector, cycles=760, max_advance=2)
    records = [audit_vector(w3_vector, 16, "w3_frozen_760")]
    custom = []
    for name in (
        "long_root_stall",
        "no_reset_shock",
        "random_ready_midstream_reset",
        "bounded_n64",
    ):
        path = vectors / f"{name}.vectors.txt"
        generated = generate_custom(name, path)
        audited = audit_vector(path, int(generated["sources"]), name)
        for key in (
            "cycles",
            "accepted",
            "retired",
            "reset_discarded",
            "max_outstanding",
            "max_continuous_valid_root_stall",
        ):
            if generated[key] != audited[key]:
                raise AssertionError(f"{name}: generator/auditor mismatch for {key}")
        custom.append(audited)
    records.extend(custom)

    simulators = {
        sources: build_simulator(verilator, sources, args.work_dir)
        for sources in (16, 64)
    }
    for record in records:
        name = str(record["name"])
        path = vectors / (
            "w3_frozen_760.vectors.txt"
            if name == "w3_frozen_760"
            else f"{name}.vectors.txt"
        )
        output = run(
            [str(simulators[int(record["sources"])]), f"+VECTORS={path}"],
            args.work_dir / f"{name}.run.log",
        )
        marker = (
            f"A4_W4_STALL_RESET_PASS n={record['sources']} "
            f"cycles={record['cycles']}"
        )
        if marker not in output:
            raise AssertionError(f"{name}: missing PASS marker")
        record["exact_all_variants_lockstep"] = "PASS"
        record["vector_sha256"] = sha256(path)

    document = {
        "schema_version": 1,
        "baseline_commit": "850fbcfa4ad168b1250223610780f11378f6c391",
        "structural_metrics": "PRESERVED_FROM_DD06BC5_8918829",
        "promotion": "HOLD_PENDING_COMMON_AND_PHYSICAL_PPA",
        "provenance": {
            "head_before_run": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "verilator": version.stdout.strip(),
            "frozen_rtl_sha256": sha256(W3 / "a4_moving_block_tree.sv"),
            "w4_rtl_sha256": sha256(HERE / "a4_moving_block_w4.sv"),
            "followup_runner_sha256": sha256(pathlib.Path(__file__).resolve()),
            "w3_vector_generator_sha256": sha256(W3 / "tests/generate_lockstep.py"),
            "followup_vector_generator_sha256": sha256(
                TESTS / "generate_stall_reset_vectors.py"
            ),
            "followup_tb_sha256": sha256(TESTS / "a4_w4_stall_reset_lockstep_tb.sv"),
        },
        "cases": records,
        "summary": {
            "n16_cases": sum(record["sources"] == 16 for record in records),
            "n64_cases": sum(record["sources"] == 64 for record in records),
            "total_cycles": sum(int(record["cycles"]) for record in records),
            "all_signal_lockstep": "PASS",
            "all_conservation_order_drain": "PASS",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        "A4_W4_FUNCTIONAL_FOLLOWUP_PASS n16_cases=4 n64_cases=1 "
        f"cycles={document['summary']['total_cycles']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
