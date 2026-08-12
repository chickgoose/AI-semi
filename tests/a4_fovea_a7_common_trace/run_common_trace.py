#!/usr/bin/env python3
"""Run pinned owner Fovea+A7 RTL on exact generator-v4 prepared traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


OWNER_COMMIT = "b5201254bceb39b3563370567355efe17a3b5e16"
GENERATOR_SHA = "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50"
PREPARER_SHA = "245078d3e1f6ed496a0de328f1568cb0a8302397ce9f5544021415f0afad2826"
MANIFEST_SHA = {
    "full50": "9fe40060e7e3fb37d41f2b0308cbcd21d50aa7e70ac052b9a59af3df69f2bba9",
    "capacity22": "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62",
}
OWNER_FILES = {
    "rtl/candidates/a7_weighted_fovea_ddr/a7_weighted_fovea_ddr.sv":
        "b125dc3cfc51f5c898d41f9b82660c346aafc9c7613433cee622514eb3456ec7",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv":
        "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv":
        "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv":
        "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv":
        "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv":
        "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv":
        "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
}
FOVEA_FILES = {
    "arbiter2.v": "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    "arbiter4_tree.v": "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    "aer_tx16_trad_rowcol_fovea.v": "353ffa6e2530400688561e3cb54f1f40ac0aa2de423b765254fbe06f6a5f806e",
}


class RunError(RuntimeError):
    pass


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RunError(f"not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, log: Path | None = None) -> None:
    if log is None:
        result = subprocess.run(command, check=False)
    else:
        with log.open("xb") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RunError(f"command failed ({result.returncode}): {command[0]}")


def extract_owner(repo: Path, destination: Path) -> list[Path]:
    extracted = []
    for logical, expected in OWNER_FILES.items():
        result = subprocess.run(["git", "-C", str(repo), "show", f"{OWNER_COMMIT}:{logical}"],
                                capture_output=True, check=False)
        if result.returncode or hashlib.sha256(result.stdout).hexdigest() != expected:
            raise RunError(f"pinned owner object mismatch: {logical}")
        path = destination / Path(logical).name
        path.write_bytes(result.stdout)
        extracted.append(path)
    return extracted


def discover_verilator(explicit: Path | None) -> Path:
    raw = explicit or (Path(os.environ["AER_VERILATOR"]) if os.environ.get("AER_VERILATOR") else None)
    raw = raw or (Path(os.environ["VERILATOR"]) if os.environ.get("VERILATOR") else None)
    raw = raw or (Path(shutil.which("verilator")) if shutil.which("verilator") else None)
    raw = raw or Path("/tmp/a7-sim-bin/verilator")
    if not raw.is_file() or not os.access(raw, os.X_OK):
        raise RunError("Verilator unavailable")
    return raw.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("smoke", "full50", "capacity22"), default="smoke")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--a1-root", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--fovea-dir", type=Path,
                        default=Path("/home/chickgoose/projects/a5/tests/a5_fovea_a7_structural/fixtures"))
    parser.add_argument("--verilator", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise RunError(f"refusing to overwrite output: {args.output}")
        args.output.mkdir(parents=True, mode=0o700)
        generator = args.a1_root / "benchmarks/clean_slate_aer/generate_trace.py"
        preparer = args.a1_root / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
        suite = "full50" if args.suite == "smoke" else args.suite
        manifest_name = "manifest.neutrality-n16.json" if suite == "full50" else "manifest.multilane-n16.json"
        manifest = args.a1_root / "benchmarks/clean_slate_aer" / manifest_name
        if digest(generator) != GENERATOR_SHA or digest(manifest) != MANIFEST_SHA[suite]:
            raise RunError("generator-v4/official manifest provenance mismatch")
        if digest(preparer) != PREPARER_SHA:
            raise RunError("prepared-trace tool provenance mismatch")
        verilator = discover_verilator(args.verilator)
        for name, expected in FOVEA_FILES.items():
            if digest(args.fovea_dir / name) != expected:
                raise RunError(f"canonical fovea provenance mismatch: {name}")

        trace_root = args.output / "traces"
        run([sys.executable, str(generator), "--manifest", str(manifest),
             "--output-dir", str(trace_root)], log=args.output / "generator.log")
        index = json.loads((trace_root / "generation-index.json").read_text())
        rows = index["runs"]
        if index.get("generator_version") != "4.0" or len(rows) != (50 if suite == "full50" else 22):
            raise RunError("official generation index mismatch")
        if args.suite == "smoke":
            rows = [row for row in rows if row["run"]["name"] == "core_simultaneous_identity"]
            if len(rows) != 1:
                raise RunError("smoke trace missing or duplicated")

        owner_root = args.output / "owner"
        owner_root.mkdir()
        sources = extract_owner(args.repo, owner_root)
        sources += [args.fovea_dir / name for name in FOVEA_FILES]
        tb = Path(__file__).with_name("a4_fovea_a7_common_trace_tb.sv")
        build = args.output / "obj"
        compile_command = [str(verilator), "--binary", "--timing", "-Wall",
                           "-Wno-BLKSEQ", "-Wno-SYNCASYNCNET", "-Wno-UNUSEDSIGNAL",
                           "-Wno-UNOPTFLAT", "--top-module", "a4_fovea_a7_common_trace_tb",
                           "--Mdir", str(build), "-o", "a4_common_trace",
                           "-DA7_WEIGHTED_FOVEA_MODULE=aer_tx16_trad_rowcol_fovea",
                           *map(str, sources), str(tb)]
        run(compile_command, log=args.output / "compile.log")
        binary = build / "a4_common_trace"
        reports = []
        for row in rows:
            name = row["run"]["name"]
            run_root = args.output / "runs" / name
            run_root.mkdir(parents=True)
            prepared = run_root / f"{name}.svtrace"
            run([sys.executable, str(preparer), "--trace", str(trace_root / row["trace_file"]),
                 "--run-manifest", str(trace_root / f"{name}.manifest.json"),
                 "--output", str(prepared), "--addr-width", "4"], log=run_root / "prepare.log")
            events = run_root / "trace.events.csv"
            summary = run_root / "trace.csv"
            log = run_root / "run.log"
            run([str(binary), f"+TRACE_FILE={prepared}", f"+TRACE_NAME={name}",
                 f"+EVENTS_OUT={events}", f"+SUMMARY_OUT={summary}"], log=log)
            text = log.read_text()
            if f"A4_FOVEA_A7_COMMON_TRACE_PASS name={name}" not in text:
                raise RunError(f"missing PASS marker: {name}")
            reports.append({"name": name, "trace_sha256": row["trace_sha256"],
                            "events_sha256": digest(events), "summary_sha256": digest(summary)})
        receipt = {"schema": "a4_fovea_a7_common_trace_v1", "status": "LOCAL_RTL",
                   "suite": args.suite, "owner_commit": OWNER_COMMIT,
                   "generator_sha256": digest(generator), "manifest_sha256": digest(manifest),
                   "preparer_sha256": digest(preparer), "verilator": str(verilator),
                   "verilator_sha256": digest(verilator), "runs": reports,
                   "queue_entries": 0, "consumer_latency_cycles": 2}
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(f"PASS suite={args.suite} runs={len(reports)} output={args.output}")
        return 0
    except (OSError, ValueError, RunError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
