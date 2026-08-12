#!/usr/bin/env python3
"""Immutable-blob Xcelium qualification driver for scalar Ganghee Fovea."""

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


HERE = Path(__file__).resolve().parent
DEFAULT_CONTRACT = HERE / "contract.json"
EXPECTED_CONTRACT_SHA256 = "5f383671cc330cace2143242b013fdf663a93d1d72b5b75a29d049bcbbae8587"


class W7Error(RuntimeError):
    pass


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_contract(path: Path) -> dict:
    try:
        payload = path.read_bytes()
        document = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise W7Error(f"cannot read contract: {exc}") from exc
    if sha(payload) != EXPECTED_CONTRACT_SHA256:
        raise W7Error("contract SHA256 mismatch")
    if document.get("schema_version") != 1:
        raise W7Error("contract schema mismatch")
    return document


def git_bytes(repo: Path, commit: str, relative: str) -> bytes:
    command = ["git", "-C", str(repo), "show", f"{commit}:{relative}"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise W7Error(f"immutable Git blob unavailable {relative}: {detail}")
    return result.stdout


def verify(repo: Path, contract: dict) -> dict[str, bytes]:
    if not repo.is_absolute() or not (repo / ".git").exists():
        raise W7Error("--a1-repo must be an absolute Git worktree path")
    commit = contract["common"]["repo_commit"]
    protected = {**contract["common"]["paths"], **contract["fovea"]["files"]}
    blobs = {}
    for relative, expected in protected.items():
        payload = git_bytes(repo, commit, relative)
        actual = sha(payload)
        if actual != expected:
            raise W7Error(f"blob SHA256 mismatch {relative}: {actual}")
        blobs[relative] = payload
    if contract["fovea"]["top"] != "aer_tx16_trad_rowcol_fovea":
        raise W7Error("unexpected scalar Fovea top")
    if contract["fovea"]["weight"] != 5:
        raise W7Error("unexpected scalar Fovea WEIGHT")
    return blobs


def list_tree(repo: Path, commit: str, prefixes: list[str]) -> list[str]:
    command = ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", commit, "--", *prefixes]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise W7Error(f"cannot enumerate immutable snapshot: {result.stderr.strip()}")
    paths = [line for line in result.stdout.splitlines() if line]
    if not paths:
        raise W7Error("immutable snapshot enumeration was empty")
    return paths


def extract_snapshot(repo: Path, contract: dict, destination: Path) -> None:
    commit = contract["common"]["repo_commit"]
    prefixes = ["tb", "benchmarks/clean_slate_aer"]
    paths = set(list_tree(repo, commit, prefixes))
    paths.update(contract["fovea"]["files"])
    for relative in sorted(paths):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise W7Error(f"unsafe snapshot path: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git_bytes(repo, commit, relative))


def run_logged(command: list[str], cwd: Path, log: Path) -> None:
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise W7Error(f"command failed rc={result.returncode}; log={log}")


def validate_generation(directory: Path, expected_names: tuple[str, ...],
                        expected_hashes: dict[str, str]) -> list[dict]:
    try:
        index = json.loads((directory / "generation-index.json").read_text())
        rows = index["runs"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise W7Error(f"invalid generation index in {directory}: {exc}") from exc
    if index.get("generator_version") != "4.0" or len(rows) != len(expected_names):
        raise W7Error(f"generator version/count mismatch in {directory}")
    names = set()
    for row in rows:
        name = row.get("run", {}).get("name")
        trace = directory / str(row.get("trace_file", ""))
        manifest = directory / f"{name}.manifest.json"
        if not name or name in names or not trace.is_file() or not manifest.is_file():
            raise W7Error(f"missing/duplicate generated run {name!r}")
        names.add(name)
        if row.get("event_identity_mode") != "address_only" or row.get("dut_payload_fields") != []:
            raise W7Error(f"non-address-only generated run {name}")
        if sha(trace.read_bytes()) != row.get("trace_sha256"):
            raise W7Error(f"generated trace SHA mismatch {name}")
        if row.get("trace_sha256") != expected_hashes.get(name):
            raise W7Error(f"trace differs from frozen official SHA {name}")
    if tuple(row["run"]["name"] for row in rows) != expected_names:
        raise W7Error(f"generated names/order differ from frozen suite in {directory}")
    return rows


def require_pass(log: Path, test_name: str, metrics: Path, event_metrics: Path) -> None:
    text = log.read_text(errors="replace")
    sentinel = f"AER_CLEAN_TEST_PASS {test_name}"
    if sentinel not in text:
        raise W7Error(f"missing exact PASS sentinel for {test_name}: {log}")
    if "AER_CLEAN_TEST_FAIL" in text or "GANGHEE_NATIVE_BINDING duplicate/phantom" in text:
        raise W7Error(f"failure diagnostic present for {test_name}: {log}")
    for artifact in (metrics, event_metrics):
        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise W7Error(f"missing result artifact for {test_name}: {artifact}")


def execute(args: argparse.Namespace, contract: dict) -> Path:
    repo = args.a1_repo.resolve()
    verify(repo, contract)
    xrun = Path(args.xrun).resolve() if args.xrun else None
    if xrun is None:
        found = shutil.which("xrun")
        xrun = Path(found).resolve() if found else None
    if xrun is None or not xrun.is_file() or not os.access(xrun, os.X_OK):
        raise FileNotFoundError("Xcelium xrun executable is unavailable")

    if args.out:
        output = args.out.resolve()
        if output.exists():
            raise W7Error(f"refusing existing output root: {output}")
        output.mkdir(parents=True)
    else:
        output = Path(tempfile.mkdtemp(prefix="w7-a2-fovea-xcelium.", dir="/tmp"))
    snapshot = output / "snapshot"
    snapshot.mkdir()
    extract_snapshot(repo, contract, snapshot)
    official_namespace: dict = {}
    official_source = git_bytes(repo, contract["common"]["repo_commit"],
                                "scripts/common_suite_official.py")
    exec(compile(official_source, "common_suite_official.py", "exec"), official_namespace)
    official_hashes = official_namespace["TRACE_SHA256"]
    official_names = {"full50": official_namespace["FULL50"],
                      "capacity22": official_namespace["CAPACITY22"]}

    version_log = output / "xrun-version.log"
    run_logged([str(xrun), "-version"], snapshot, version_log)
    traces_root = output / "traces"
    logs_root = output / "logs"
    results_root = output / "results"
    for directory in (traces_root, logs_root, results_root):
        directory.mkdir()

    suites = list(contract["suites"]) if args.suite == "all" else [args.suite]
    generated = {}
    for suite in suites:
        spec = contract["suites"][suite]
        directory = traces_root / suite
        directory.mkdir()
        command = [sys.executable, str(snapshot / "benchmarks/clean_slate_aer/generate_trace.py"),
                   "--manifest", str(snapshot / spec["manifest"]), "--output-dir", str(directory)]
        run_logged(command, snapshot, logs_root / f"generate-{suite}.log")
        if len(official_names[suite]) != spec["count"]:
            raise W7Error(f"contract/official suite count mismatch {suite}")
        generated[suite] = validate_generation(directory, official_names[suite], official_hashes)

    xcelium_dir = output / "xcelium.d"
    fixture_root = "tests/a5_fovea_a7_structural/fixtures/"
    fovea_files = [snapshot / (fixture_root + name) for name in
                   ("arbiter2.v", "arbiter4_tree.v", "aer_tx16_trad_rowcol_fovea.v")]
    native_filelist = output / "scalar-fovea.f"
    native_filelist.write_text("\n".join(map(str, fovea_files)) + "\n")
    compile_command = [str(xrun), "-64bit", "-sv", "-timescale", "1ns/1ps",
        "-top", "aer_clean_tb", "-snapshot", "w7_scalar_fovea_n16", "-elaborate",
        "-xmlibdirname", str(xcelium_dir), "-define", "AER_CLEAN_GANGHEE_NATIVE",
        "-define", "AER_GANGHEE_NATIVE_MODULE=aer_tx16_trad_rowcol_fovea",
        "-defparam", "aer_clean_tb.NUM_SOURCES=16", "-defparam", "aer_clean_tb.ADDR_WIDTH=16",
        "-defparam", "aer_clean_tb.RETIRE_LANES=1", "-defparam", "aer_clean_tb.FIFO_DEPTH=0",
        "-f", str(snapshot / "tb/clean/files.f"),
        str(snapshot / "tb/clean/native/aer_ganghee_native_binding.sv"),
        "-f", str(native_filelist)]
    run_logged(compile_command, snapshot, logs_root / "elaborate.log")

    run_records = []

    def run_test(selector: str, expected_name: str, result_dir: Path, extra: list[str]) -> None:
        result_dir.mkdir(parents=True, exist_ok=True)
        metrics = result_dir / "metrics.csv"
        event_metrics = result_dir / "events.csv"
        log = logs_root / f"run-{result_dir.parent.name}-{result_dir.name}.log"
        command = [str(xrun), "-64bit", "-R", "-snapshot", "w7_scalar_fovea_n16",
            "-xmlibdirname", str(xcelium_dir), f"+CLEAN_TEST={selector}",
            "+CANDIDATE=ganghee-scalar-fovea-w5", f"+METRICS={metrics}",
            f"+EVENT_METRICS={event_metrics}", "+SEED=1", *extra]
        run_logged(command, snapshot, log)
        require_pass(log, expected_name, metrics, event_metrics)
        run_records.append({"name": expected_name,
                            "metrics_sha256": sha(metrics.read_bytes()),
                            "event_metrics_sha256": sha(event_metrics.read_bytes()),
                            "log_sha256": sha(log.read_bytes())})

    run_test("basic_reset_drain", "basic_reset_drain",
             results_root / "reset" / "basic_reset_drain", [])
    prepare = snapshot / "benchmarks/clean_slate_aer/prepare_sv_trace.py"
    for suite, rows in generated.items():
        for row in rows:
            name = row["run"]["name"]
            trace_dir = traces_root / suite
            prepared = trace_dir / f"{name}.svtrace"
            run_logged([sys.executable, str(prepare), "--trace", str(trace_dir / row["trace_file"]),
                        "--run-manifest", str(trace_dir / f"{name}.manifest.json"),
                        "--output", str(prepared), "--addr-width", "16"], snapshot,
                       logs_root / f"prepare-{suite}-{name}.log")
            run_test("trace", name, results_root / suite / name,
                     [f"+TRACE_FILE={prepared}", f"+TRACE_NAME={name}"])

    receipt = {
        "schema_version": 1, "decision": "PASS", "common_commit": contract["common"]["repo_commit"],
        "fovea_commit": contract["fovea"]["canonical_commit"], "top": contract["fovea"]["top"],
        "weight": contract["fovea"]["weight"], "suites": {key: len(value) for key, value in generated.items()},
        "reset": "PASS", "xrun_version_sha256": sha(version_log.read_bytes()),
        "runs": run_records,
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "run"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--a1-repo", type=Path, default=Path("/home/chickgoose/projects/a1"))
    parser.add_argument("--suite", choices=("all", "full50", "capacity22"), default="all")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--xrun")
    args = parser.parse_args()
    try:
        contract = read_contract(args.contract)
        blobs = verify(args.a1_repo.resolve(), contract)
        if args.action == "verify":
            print(f"W7_PROVENANCE_PASS blobs={len(blobs)} common={contract['common']['repo_commit']} "
                  f"fovea={contract['fovea']['canonical_commit']} top={contract['fovea']['top']}")
            return 0
        output = execute(args, contract)
        print(f"W7_XCELIUM_PASS output={output}")
        return 0
    except FileNotFoundError as exc:
        print(f"W7_TOOL_MISSING {exc}", file=sys.stderr)
        return 3
    except W7Error as exc:
        print(f"W7_CONTRACT_FAIL {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
