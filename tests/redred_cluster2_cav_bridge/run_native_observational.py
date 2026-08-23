#!/usr/bin/env python3
"""Verify pinned Ganghee bytes, then run the observational native ledger TB."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


# Keep importing this runner from creating bridge-worktree bytecode artifacts.
sys.dont_write_bytecode = True


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.redred_cluster2_cav_bridge.native_ledger import (  # noqa: E402
    CLEAN_GIT_AUTHORITY,
    EXPECTED_CODE_FILES,
    FILE_BYTES_AUTHORITY,
    NativeLedgerError,
    TRACKED_CYCLEMASK_PATH,
    TRACKED_CYCLEMASK_RAW_SHA256,
    TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256,
    canonical_transport_outcome_jsonl,
    inspect_cyclemask_encoding,
    parse_native_ledger,
    verify_faer_checkout,
)


AUTHORITY_PATH = (
    PROJECT_ROOT
    / "benchmarks"
    / "redred_cluster2_cav_bridge"
    / "ganghee_cluster2_native_authority.json"
)
TB_PATH = (
    PROJECT_ROOT
    / "tests"
    / "redred_cluster2_cav_bridge"
    / "redred_cluster2_native_observational_tb.sv"
)
TOP = "redred_cluster2_native_observational_tb"
OBSERVATIONAL_TB_SHA256 = (
    "50750a75f9730e2ec6f62a7850362431d4855b7305f7277cf84d3175ab91f035"
)


class RunnerError(RuntimeError):
    pass


def _run(command, log_path: Path, output_root: Path) -> str:
    temporary_root = output_root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    simulator_environment = os.environ.copy()
    simulator_environment["TMPDIR"] = str(temporary_root)
    try:
        completed = subprocess.run(
            [str(part) for part in command],
            check=False,
            cwd=str(output_root),
            env=simulator_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as error:
        raise RunnerError("could not execute simulator command") from error
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RunnerError(
            "simulator command failed with status %d; see %s"
            % (completed.returncode, log_path)
        )
    return completed.stdout


def _git_worktree_status(root: Path, required: bool):
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
            check=False,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        if required:
            raise RunnerError("cannot inspect bridge worktree status") from error
        return None
    if completed.returncode != 0:
        if required:
            raise RunnerError("cannot inspect bridge worktree status")
        return None
    return completed.stdout


def _assert_worktrees_unchanged(bridge_before, faer_root: Path, faer_before):
    bridge_after = _git_worktree_status(PROJECT_ROOT, required=True)
    if bridge_after != bridge_before:
        raise RunnerError("bridge worktree status changed during simulator run")
    if faer_before is not None:
        faer_after = _git_worktree_status(faer_root, required=False)
        if faer_after is None or faer_after != faer_before:
            raise RunnerError("FAER worktree status changed during simulator run")


def _assert_post_run_state(options, bridge_before, faer_before):
    verify_faer_checkout(
        options.faer_root, AUTHORITY_PATH, options.cyclemask_relative,
        options.authority_mode,
    )
    _assert_worktrees_unchanged(
        bridge_before, options.faer_root, faer_before
    )


def _read_xrun_completion_log(tool_log: Path, output_root: Path) -> str:
    expected_path = output_root / "xrun.log"
    if tool_log != expected_path:
        raise RunnerError("xrun completion log path is not pinned")
    try:
        payload = tool_log.read_bytes()
    except OSError as error:
        raise RunnerError("xrun completion log is unavailable") from error
    if not payload or len(payload) > 64 * 1024 * 1024:
        raise RunnerError("xrun completion log byte size is invalid")
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise RunnerError("xrun completion log is not UTF-8") from error


def _select_simulator(requested: str):
    if requested in ("auto", "xrun"):
        xrun = shutil.which("xrun")
        if not xrun:
            known_xrun = Path("/tools/cadence/XCELIUMMAIN2309/tools/bin/64bit/xrun")
            if known_xrun.is_file():
                xrun = str(known_xrun)
        if xrun:
            return "xrun", Path(xrun)
        if requested == "xrun":
            return None
    if requested in ("auto", "verilator"):
        verilator = shutil.which("verilator")
        if verilator:
            return "verilator", Path(verilator)
        if requested == "verilator":
            return None
    if requested in ("auto", "iverilog"):
        iverilog = shutil.which("iverilog")
        vvp = shutil.which("vvp")
        if iverilog and vvp:
            return "iverilog", (Path(iverilog), Path(vvp))
    return None


def _stage_verified_files(verified, output_root: Path):
    staged = {}
    snapshot_root = output_root / "faer_snapshot"
    for role, (relative, expected_sha256) in EXPECTED_CODE_FILES.items():
        try:
            payload = verified[role].read_bytes()
        except OSError as error:
            raise RunnerError("verified Ganghee member became unreadable") from error
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise RunnerError("Ganghee member changed before staging: %s" % role)
        destination = snapshot_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha256:
            raise RunnerError("staged Ganghee member hash differs: %s" % role)
        staged[role] = destination
    trace_payload = verified["tracked_cyclemask"].read_bytes()
    trace_encoding = inspect_cyclemask_encoding(trace_payload)
    if (
        trace_encoding.raw_sha256
        != TRACKED_CYCLEMASK_RAW_SHA256[trace_encoding.line_endings]
        or trace_encoding.canonical_semantic_lf_sha256
        != TRACKED_CYCLEMASK_SEMANTIC_LF_SHA256
    ):
        raise RunnerError("tracked cyclemask changed before staging")
    trace_destination = snapshot_root / Path(TRACKED_CYCLEMASK_PATH)
    trace_destination.parent.mkdir(parents=True, exist_ok=True)
    trace_destination.write_bytes(trace_payload)
    staged_encoding = inspect_cyclemask_encoding(trace_destination.read_bytes())
    if staged_encoding != trace_encoding:
        raise RunnerError("staged cyclemask raw provenance differs")
    staged["tracked_cyclemask"] = trace_destination
    try:
        tb_payload = TB_PATH.read_bytes()
    except OSError as error:
        raise RunnerError("observational TB became unreadable") from error
    if hashlib.sha256(tb_payload).hexdigest() != OBSERVATIONAL_TB_SHA256:
        raise RunnerError("observational TB bytes differ from runner pin")
    tb_destination = output_root / "bridge_snapshot" / TB_PATH.name
    tb_destination.parent.mkdir(parents=True, exist_ok=True)
    tb_destination.write_bytes(tb_payload)
    staged_tb_payload = tb_destination.read_bytes()
    if (
        staged_tb_payload != tb_payload
        or hashlib.sha256(staged_tb_payload).hexdigest() != OBSERVATIONAL_TB_SHA256
    ):
        raise RunnerError("staged observational TB bytes differ")
    staged["observational_tb"] = tb_destination
    return staged, trace_encoding


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("faer_root", type=Path)
    parser.add_argument(
        "cyclemask_relative",
        help="normalized path relative to faer_root; only the pinned trace is accepted",
    )
    parser.add_argument(
        "--simulator", choices=("auto", "xrun", "verilator", "iverilog"), default="auto"
    )
    parser.add_argument(
        "--authority-mode",
        choices=(FILE_BYTES_AUTHORITY, CLEAN_GIT_AUTHORITY),
        default=FILE_BYTES_AUTHORITY,
    )
    options = parser.parse_args(arguments)

    try:
        # This complete authority check intentionally precedes simulator lookup
        # and every compilation command.
        verified = verify_faer_checkout(
            options.faer_root, AUTHORITY_PATH, options.cyclemask_relative,
            options.authority_mode,
        )
    except NativeLedgerError as error:
        print("NATIVE_OBSERVATIONAL_AUTHORITY_FAIL: %s" % error, file=sys.stderr)
        return 1

    selected = _select_simulator(options.simulator)
    if selected is None:
        print("NATIVE_OBSERVATIONAL_SKIP simulator_unavailable", file=sys.stderr)
        return 2

    try:
        bridge_status_before = _git_worktree_status(PROJECT_ROOT, required=True)
        faer_status_before = _git_worktree_status(options.faer_root, required=False)
    except RunnerError as error:
        print("NATIVE_OBSERVATIONAL_FAIL: %s" % error, file=sys.stderr)
        return 1

    output_root = Path(tempfile.mkdtemp(prefix="redred-cluster2-native-"))
    ledger_path = output_root / "native_ledger.psv"
    compile_log = output_root / "compile.log"
    run_log = output_root / "run.log"
    try:
        staged, trace_encoding = _stage_verified_files(verified, output_root)
    except RunnerError as error:
        print("NATIVE_OBSERVATIONAL_FAIL: %s" % error, file=sys.stderr)
        print("output_root=%s" % output_root, file=sys.stderr)
        return 1
    rtl_paths = [
        staged["arbiter2"],
        staged["arbiter4_tree"],
        staged["cluster2_steal_buf_rtl"],
    ]
    staged_tb_path = staged["observational_tb"]
    simulator_name, simulator = selected
    try:
        trace_path = staged["tracked_cyclemask"]
        plusargs = [
            "+CYCLEMASK_FILE=%s" % trace_path,
            "+LEDGER_FILE=%s" % ledger_path,
        ]
        if simulator_name == "xrun":
            xcelium_root = output_root / "xcelium.d"
            tool_log = output_root / "xrun.log"
            run_output = _run([
                simulator,
                "-64bit", "-sv", "-timescale", "1ns/1ps",
                "-access", "+r", "-top", TOP,
                "-xmlibdirname", xcelium_root,
                "-l", tool_log,
                *rtl_paths,
                staged_tb_path,
                *plusargs,
            ], run_log, output_root)
        elif simulator_name == "verilator":
            object_root = output_root / "obj"
            _run([
                simulator,
                "--binary", "--timing", "--assert", "-Wall", "-Wno-fatal",
                "-Wno-BLKSEQ", "--gate-stmts", "0",
                "--top-module", TOP,
                "--Mdir", object_root,
                *rtl_paths,
                staged_tb_path,
            ], compile_log, output_root)
            executable = object_root / ("V" + TOP)
            run_output = _run([executable, *plusargs], run_log, output_root)
        else:
            iverilog, vvp = simulator
            executable = output_root / "native_tb.vvp"
            _run([
                iverilog, "-g2012", "-s", TOP, "-o", executable,
                *rtl_paths,
                staged_tb_path,
            ], compile_log, output_root)
            run_output = _run([vvp, executable, *plusargs], run_log, output_root)
        rows = parse_native_ledger(trace_path.read_bytes(), ledger_path.read_bytes())
        delivered_count = sum(row["outcome"] == "DELIVERED" for row in rows)
        expected_pass = (
            "REDRED_CLUSTER2_NATIVE_LEDGER_PASS generated=%d delivered=%d overrun=%d"
            % (len(rows), delivered_count, len(rows) - delivered_count)
        )
        completion_output = run_output
        completion_path = run_log
        if simulator_name == "xrun" and expected_pass not in run_output.splitlines():
            completion_output = _read_xrun_completion_log(tool_log, output_root)
            completion_path = tool_log
        run_lines = completion_output.splitlines()
        failure_markers = ("$fatal", "native_ledger_fail", "*e,", "*f,")
        if run_lines.count(expected_pass) != 1 or any(
            token in line.lower()
            for line in run_lines
            for token in failure_markers
        ):
            raise RunnerError(
                "simulator completion marker differs; see %s" % completion_path
            )
        outcome_path = output_root / "transport_outcomes.jsonl"
        outcome_path.write_bytes(canonical_transport_outcome_jsonl(rows))
        _assert_post_run_state(options, bridge_status_before, faer_status_before)
    except (OSError, NativeLedgerError, RunnerError) as error:
        try:
            _assert_post_run_state(options, bridge_status_before, faer_status_before)
        except (NativeLedgerError, RunnerError) as status_error:
            error = status_error
        print("NATIVE_OBSERVATIONAL_FAIL: %s" % error, file=sys.stderr)
        print("output_root=%s" % output_root, file=sys.stderr)
        return 1

    print(
        "NATIVE_OBSERVATIONAL_PASS authority_mode=%s simulator=%s events=%d "
        "cyclemask_encoding=%s raw_sha256=%s semantic_lf_sha256=%s output_root=%s"
        % (
            options.authority_mode, simulator_name, len(rows),
            trace_encoding.line_endings, trace_encoding.raw_sha256,
            trace_encoding.canonical_semantic_lf_sha256, output_root,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
