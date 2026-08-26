#!/usr/bin/env python3
"""Run the exact public Ganghee polarity-v1 RTL against its pinned addrpol trace."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
TB_SHA256 = "eaeb199bb9b9037c03e09eef8173fdd9971e73c63bc79c130549d9117f62e4ec"
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
LEDGER_SCHEMA = "redred.cluster2_cav_bridge.polarity_v1_native_ledger/v1"
TRACE_ROW = re.compile(rb"(0|[1-9][0-9]*) ([0-9a-f]{4}) ([0-9a-f]{4})\n")
UINT = re.compile(r"0|[1-9][0-9]*")


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
    occurrences = parse_trace(trace_payload)
    if len(trace_payload.splitlines()) != TRACE_LINE_COUNT:
        raise RunnerError("pinned polarity trace line count differs")
    if len(occurrences) != TRACE_EVENT_COUNT:
        raise RunnerError("pinned polarity trace event count differs")
    verified[TRACE_PATH] = trace
    return verified, status


def parse_trace(payload: bytes):
    if not payload or not payload.endswith(b"\n"):
        raise RunnerError("polarity trace must be nonempty LF-terminated bytes")
    occurrences = []
    previous_cycle = None
    event_id = 0
    offset = 0
    for match in TRACE_ROW.finditer(payload):
        if match.start() != offset:
            raise RunnerError("polarity trace contains malformed bytes")
        offset = match.end()
        cycle = int(match.group(1))
        arrival = int(match.group(2), 16)
        polarity = int(match.group(3), 16)
        if arrival == 0 or polarity & ~arrival:
            raise RunnerError("polarity trace bitmap semantics differ")
        if previous_cycle is not None and cycle <= previous_cycle:
            raise RunnerError("polarity trace cycles are not strictly increasing")
        previous_cycle = cycle
        for source in range(16):
            if arrival & (1 << source):
                occurrences.append((event_id, source, cycle, (polarity >> source) & 1))
                event_id += 1
    if offset != len(payload):
        raise RunnerError("polarity trace contains malformed trailing bytes")
    return occurrences


def _uint(token: str, label: str) -> int:
    if not UINT.fullmatch(token):
        raise RunnerError("ledger %s is not a canonical unsigned integer" % label)
    return int(token)


def validate_ledger(trace_payload: bytes, ledger_payload: bytes):
    try:
        text = ledger_payload.decode("ascii")
    except UnicodeError as error:
        raise RunnerError("polarity ledger is not ASCII") from error
    if not text.endswith("\n"):
        raise RunnerError("polarity ledger is not LF terminated")
    lines = text.splitlines()
    if len(lines) < 3 or lines[0] != "SCHEMA|" + LEDGER_SCHEMA:
        raise RunnerError("polarity ledger schema differs")
    occurrences = parse_trace(trace_payload)
    expected = {row[0]: row for row in occurrences}
    observed = {}
    retirement_slots = set()
    lane_rows = {}
    last_delivered_by_source = {}
    delivered = 0
    overruns = 0
    legal_pairs = {(0, 3), (1, 0), (1, 2), (1, 3), (2, 0), (2, 3)}

    for line in lines[1:-1]:
        fields = line.split("|")
        if len(fields) != 10 or fields[0] != "EVENT":
            raise RunnerError("polarity ledger event row shape differs")
        event_id = _uint(fields[1], "event_id")
        source = _uint(fields[2], "source")
        occurrence_cycle = _uint(fields[3], "occurrence_cycle")
        polarity = _uint(fields[9], "polarity")
        if polarity not in (0, 1) or event_id not in expected or event_id in observed:
            raise RunnerError("polarity ledger event identity differs")
        if expected[event_id] != (event_id, source, occurrence_cycle, polarity):
            raise RunnerError("polarity ledger source occurrence or polarity differs")
        outcome = fields[4]
        if outcome == "OVERRUN":
            if fields[5:9] != ["-", "-", "-", "-"]:
                raise RunnerError("overrun carries native retirement fields")
            overruns += 1
        elif outcome == "DELIVERED":
            retire_cycle = _uint(fields[5], "retire_cycle")
            lane = _uint(fields[6], "lane")
            row = _uint(fields[7], "row")
            column = _uint(fields[8], "column")
            if retire_cycle < occurrence_cycle or lane not in (0, 1):
                raise RunnerError("delivered timing or lane differs")
            if row > 3 or column > 3 or source != row * 4 + column:
                raise RunnerError("delivered native coordinate differs")
            allowed = ({0, 1, 2}, {0, 2, 3})[lane]
            if row not in allowed:
                raise RunnerError("delivered lane-row combination differs")
            slot = (retire_cycle, lane, column)
            if slot in retirement_slots:
                raise RunnerError("duplicate native retirement slot")
            retirement_slots.add(slot)
            lane_key = (retire_cycle, lane)
            if lane_key in lane_rows and lane_rows[lane_key] != row:
                raise RunnerError("one lane bitmap contains multiple rows")
            lane_rows[lane_key] = row
            if source in last_delivered_by_source and event_id <= last_delivered_by_source[source]:
                raise RunnerError("per-source FIFO retirement order differs")
            last_delivered_by_source[source] = event_id
            delivered += 1
        else:
            raise RunnerError("polarity ledger outcome differs")
        observed[event_id] = outcome

    for cycle in {key[0] for key in lane_rows}:
        row0 = lane_rows.get((cycle, 0))
        row1 = lane_rows.get((cycle, 1))
        if row0 is not None and row1 is not None and (row0, row1) not in legal_pairs:
            raise RunnerError("two-lane row pair differs")
        if row0 is not None and row1 is None and row0 not in (1, 2):
            raise RunnerError("lane0-only row differs")
        if row0 is None and row1 is not None and row1 not in (0, 3):
            raise RunnerError("lane1-only row differs")
    if set(observed) != set(expected):
        raise RunnerError("polarity ledger does not partition trace events")

    summary = lines[-1].split("|")
    if len(summary) != 5 or summary[0] != "SUMMARY":
        raise RunnerError("polarity ledger summary shape differs")
    counts = tuple(_uint(value, "summary") for value in summary[1:])
    wanted = (len(expected), delivered, overruns, delivered)
    if counts != wanted or len(expected) != delivered + overruns:
        raise RunnerError("polarity ledger conservation or check count differs")
    return wanted


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
    ledger = output_root / "polarity_v1_native_ledger.psv"
    try:
        rtl, trace, tb = _stage(verified, output_root)
        plusargs = ["+ADDRPOL_FILE=%s" % trace, "+LEDGER_FILE=%s" % ledger]
        simulator_name, simulator = selected
        if simulator_name == "xrun":
            tool_log = output_root / "xrun.log"
            run_output = _run([
                simulator, "-64bit", "-sv", "-timescale", "1ns/1ps",
                "-top", TOP, "-xmlibdirname", output_root / "xcelium.d",
                "-l", tool_log,
                rtl[0], rtl[1], rtl[2], tb, plusargs[0], plusargs[1],
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
        counts = validate_ledger(trace.read_bytes(), ledger.read_bytes())
        marker = (
            "REDRED_CLUSTER2_POLARITY_V1_NATIVE_PASS generated=%d delivered=%d "
            "overrun=%d polarity_checked=%d" % counts
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
    except (OSError, RunnerError) as error:
        try:
            _assert_source_unchanged(options.faer_root, source_status)
        except RunnerError as source_error:
            error = source_error
        print("POLARITY_V1_NATIVE_FAIL: %s" % error, file=sys.stderr)
        print("output_root=%s" % output_root, file=sys.stderr)
        return 1
    print(
        "POLARITY_V1_NATIVE_PASS commit=%s simulator=%s events=%d output_root=%s"
        % (PINNED_COMMIT, simulator_name, counts[0], output_root)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
