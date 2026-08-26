#!/usr/bin/env python3
"""Run exact Ganghee polarity-v1 RTL and verify its raw native observations."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.redred_cluster2_cav_bridge.polarity_native_ledger import (  # noqa: E402
    PolarityNativeLedgerError,
    parse_addrpol_trace,
    verify_polarity_native_ledger,
)


TB_PATH = (
    PROJECT_ROOT
    / "tests/redred_cluster2_cav_bridge"
    / "redred_cluster2_polarity_v1_native_observational_tb.sv"
)
TOP = "redred_cluster2_polarity_v1_native_observational_tb"
REPOSITORY_URL = "https://github.com/GangHeeJo/AI-SEMI"
PUBLIC_REF = "refs/remotes/origin/main"
PINNED_COMMIT = "44f8918c6e0085f7b75bb90fbe6c099abe1882cc"
TRACE_PATH = "common_traces_uzh/uzh_shapes_rotation_patch.addrpol.txt"
TRACE_SHA256 = "9f682af4eb11239f0743c2f95a82e4302836ac8a02e68278b8b69464beac55c4"
TRACE_LINE_COUNT = 3259
TRACE_EVENT_COUNT = 8503
TB_SHA256 = "595120fd996573e24e724b3d9a0e0984736975319421ab7f4b1997ab45fcb5a8"
RTL_SOURCES = (
    (
        "rtl/arbiter2.v",
        "25d2ffcfe9fbddda4925627e91d52249ee495a1ba91eb40c22b157993da9a684",
    ),
    (
        "rtl/arbiter4_tree.v",
        "108d3ddfd386c2e537ee4eb757dfcd0a6c1d3a50b22c41cbbacc34741bd86e31",
    ),
    (
        "rtl/aer_tx16_trad_rowcol_fovea_cluster2_steal_buf_polarity.v",
        "20d601a9ee1d4d78854dbfeb5ee60f1c8db712c07c20aff6364c51c142e5ad81",
    ),
)


class RunnerError(RuntimeError):
    pass


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_exact(path: Path, expected: str, label: str) -> bytes:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise RunnerError("cannot read %s" % label) from error
    if _sha256(payload) != expected:
        raise RunnerError("%s SHA-256 differs" % label)
    return payload


def _git(root: Path, arguments):
    try:
        completed = subprocess.run(
            ["git"] + list(arguments), cwd=str(root), check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RunnerError("cannot execute Git provenance check") from error
    if completed.returncode != 0:
        raise RunnerError("Git provenance check failed")
    return completed.stdout


def _normalize_repository_url(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def verify_source_checkout(root: Path, trace_relative: str):
    if not root.is_absolute() or root != root.resolve() or not root.is_dir():
        raise RunnerError("FAER root must be an absolute normalized directory")
    if trace_relative != TRACE_PATH:
        raise RunnerError("polarity trace relative path is not pinned")
    if _git(root, ["rev-parse", "HEAD"]).decode("ascii").strip() != PINNED_COMMIT:
        raise RunnerError("FAER HEAD differs from pinned public main")
    if _git(root, ["rev-parse", PUBLIC_REF]).decode("ascii").strip() != PINNED_COMMIT:
        raise RunnerError("FAER origin/main differs from pinned public main")
    origin = _git(root, ["remote", "get-url", "origin"]).decode("utf-8").strip()
    if _normalize_repository_url(origin) != REPOSITORY_URL:
        raise RunnerError("FAER origin URL differs from public authority")
    status = _git(root, ["status", "--porcelain", "--untracked-files=all", "-z"])
    if status:
        raise RunnerError("FAER checkout is not clean")

    verified = {}
    for relative, expected in RTL_SOURCES:
        path = root / relative
        if path.resolve() != path or not path.is_file():
            raise RunnerError("RTL source path is not a regular normalized file")
        _read_exact(path, expected, relative)
        verified[relative] = path
    trace = root / TRACE_PATH
    if trace.resolve() != trace or not trace.is_file():
        raise RunnerError("polarity trace path is not a regular normalized file")
    trace_payload = _read_exact(trace, TRACE_SHA256, TRACE_PATH)
    try:
        occurrences, line_endings = parse_addrpol_trace(trace_payload)
    except PolarityNativeLedgerError as error:
        raise RunnerError("pinned polarity trace is invalid") from error
    if line_endings != "LF" or len(trace_payload.splitlines()) != TRACE_LINE_COUNT:
        raise RunnerError("pinned polarity trace encoding or line count differs")
    if len(occurrences) != TRACE_EVENT_COUNT:
        raise RunnerError("pinned polarity trace event count differs")
    verified[TRACE_PATH] = trace
    return verified, status


def _select_simulator(requested: str):
    if requested in ("auto", "xrun"):
        executable = shutil.which("xrun")
        if not executable:
            known = Path("/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun")
            if known.is_file():
                executable = str(known)
        if executable:
            return "xrun", Path(executable)
        if requested == "xrun":
            return None
    if requested in ("auto", "verilator"):
        executable = shutil.which("verilator")
        if executable:
            return "verilator", Path(executable)
        if requested == "verilator":
            return None
    if requested in ("auto", "iverilog"):
        compiler = shutil.which("iverilog")
        runtime = shutil.which("vvp")
        if compiler and runtime:
            return "iverilog", (Path(compiler), Path(runtime))
    return None


def _run(command, log: Path, output_root: Path) -> str:
    environment = os.environ.copy()
    temporary = output_root / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment["TMPDIR"] = str(temporary)
    try:
        completed = subprocess.run(
            [str(part) for part in command], cwd=str(output_root), env=environment,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except OSError as error:
        raise RunnerError("cannot execute simulator") from error
    log.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise RunnerError("simulator command failed; see %s" % log)
    return completed.stdout


def _stage(verified, output_root: Path):
    staged = []
    snapshot = output_root / "source_snapshot"
    for relative, expected in RTL_SOURCES:
        payload = _read_exact(verified[relative], expected, relative)
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        _read_exact(destination, expected, "staged " + relative)
        staged.append(destination)
    trace_payload = _read_exact(verified[TRACE_PATH], TRACE_SHA256, TRACE_PATH)
    trace = snapshot / TRACE_PATH
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_bytes(trace_payload)
    _read_exact(trace, TRACE_SHA256, "staged polarity trace")
    tb_payload = _read_exact(TB_PATH, TB_SHA256, "polarity observational TB")
    tb = output_root / "bridge_snapshot" / TB_PATH.name
    tb.parent.mkdir(parents=True, exist_ok=True)
    tb.write_bytes(tb_payload)
    _read_exact(tb, TB_SHA256, "staged polarity observational TB")
    return tuple(staged), trace, tb


def _assert_source_unchanged(root: Path, original_status):
    if _git(root, ["status", "--porcelain", "--untracked-files=all", "-z"]) != original_status:
        raise RunnerError("FAER checkout status changed during run")
    for relative, expected in RTL_SOURCES:
        _read_exact(root / relative, expected, relative)
    _read_exact(root / TRACE_PATH, TRACE_SHA256, TRACE_PATH)


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("faer_root", type=Path)
    parser.add_argument("trace_relative")
    parser.add_argument(
        "--simulator", choices=("auto", "xrun", "verilator", "iverilog"),
        default="auto",
    )
    options = parser.parse_args(arguments)
    try:
        verified, source_status = verify_source_checkout(
            options.faer_root, options.trace_relative
        )
    except RunnerError as error:
        print("POLARITY_V1_NATIVE_AUTHORITY_FAIL: %s" % error, file=sys.stderr)
        return 1
    selected = _select_simulator(options.simulator)
    if selected is None:
        print("POLARITY_V1_NATIVE_SKIP simulator_unavailable", file=sys.stderr)
        return 2

    output_root = Path(tempfile.mkdtemp(prefix="redred-cluster2-polarity-v1-"))
    compile_log = output_root / "compile.log"
    run_log = output_root / "run.log"
    ledger = output_root / "polarity_v1_raw_native_ledger.psv"
    try:
        rtl, trace, tb = _stage(verified, output_root)
        plusargs = ["+ADDRPOL_FILE=%s" % trace, "+LEDGER_FILE=%s" % ledger]
        simulator_name, simulator = selected
        if simulator_name == "xrun":
            tool_log = output_root / "xrun.log"
            run_output = _run([
                simulator, "-64bit", "-sv", "-timescale", "1ns/1ps",
                "-top", TOP, "-xmlibdirname", output_root / "xcelium.d",
                "-l", tool_log, rtl[0], rtl[1], rtl[2], tb,
                plusargs[0], plusargs[1],
            ], run_log, output_root)
        elif simulator_name == "verilator":
            object_root = output_root / "obj"
            _run([
                simulator, "--binary", "--timing", "--assert", "-Wall",
                "-Wno-fatal", "--top-module", TOP, "--Mdir", object_root,
                rtl[0], rtl[1], rtl[2], tb,
            ], compile_log, output_root)
            run_output = _run([
                object_root / ("V" + TOP), plusargs[0], plusargs[1],
            ], run_log, output_root)
        else:
            iverilog, vvp = simulator
            executable = output_root / "polarity_v1_tb.vvp"
            _run([
                iverilog, "-g2012", "-s", TOP, "-o", executable,
                rtl[0], rtl[1], rtl[2], tb,
            ], compile_log, output_root)
            run_output = _run([
                vvp, executable, plusargs[0], plusargs[1],
            ], run_log, output_root)
        try:
            report = verify_polarity_native_ledger(
                trace.read_bytes(), ledger.read_bytes()
            )
        except PolarityNativeLedgerError as error:
            raise RunnerError("raw polarity ledger verification failed") from error
        if report.identity_order_independence_claimed:
            raise RunnerError("raw observation verifier made a forbidden identity claim")
        marker = (
            "REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%d delivered=%d "
            "overrun=%d phantom=0 duplicate=0 drain_empty=1"
            % (report.generated, report.delivered, report.overrun)
        )
        completion_output = run_output
        if simulator_name == "xrun" and marker not in run_output.splitlines():
            tool_payload = tool_log.read_bytes()
            if not tool_payload or len(tool_payload) > 64 * 1024 * 1024:
                raise RunnerError("xrun completion log size differs")
            completion_output = tool_payload.decode("utf-8", errors="strict")
        lines = completion_output.splitlines()
        if lines.count(marker) != 1 or any("fatal" in line.lower() for line in lines):
            raise RunnerError("simulator completion marker differs")
        _assert_source_unchanged(options.faer_root, source_status)
    except (OSError, RunnerError, UnicodeError) as error:
        try:
            _assert_source_unchanged(options.faer_root, source_status)
        except RunnerError as source_error:
            error = source_error
        print("POLARITY_V1_NATIVE_FAIL: %s" % error, file=sys.stderr)
        print("output_root=%s" % output_root, file=sys.stderr)
        return 1
    print(
        "POLARITY_V1_NATIVE_PASS commit=%s simulator=%s events=%d "
        "identity_order_independence_claimed=false output_root=%s"
        % (PINNED_COMMIT, simulator_name, report.generated, output_root)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
