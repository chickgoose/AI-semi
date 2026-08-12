#!/usr/bin/env python3
"""Compile once and run a fail-closed local Xcelium K=2 campaign."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import common_suite_official as official


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TOP_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$:]*$")
ASSIGNMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$:\[\]]*$")
DEFINE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
ERROR_PATTERNS = (
    re.compile(r"(?im)^\s*(?:xrun|xmvlog|xmelab|xmsim|ncsim):\s*\*[EF],"),
    re.compile(r"(?im)^\s*\*[EF],"),
    re.compile(r"(?im)\bUVM_(?:ERROR|FATAL)\b"),
    re.compile(r"(?im)\bAER_CLEAN_TEST_FAIL\b"),
    re.compile(r"(?im)^\s*(?:ERROR|FATAL)\s*[: ]"),
    re.compile(r"(?im)\b(?:segmentation fault|core dumped)\b"),
)
SUITE_SPECS = {
    name: {
        "manifest_name": row["manifest_name"],
        "manifest_sha256": row["manifest_sha256"],
        "names": tuple(row["names"]),
        "trace_sha256": {run: official.TRACE_SHA256[run] for run in row["names"]},
    }
    for name, row in official.SUITES.items()
}


class CampaignError(RuntimeError):
    """A fail-closed campaign validation or execution failure."""


@dataclass(frozen=True)
class InputRecord:
    role: str
    path: Path
    sha256: str


class InputTracker:
    def __init__(self) -> None:
        self.records: list[InputRecord] = []
        self._baseline: dict[Path, str] = {}
        self._allow_empty: dict[Path, bool] = {}

    def add(self, role: str, path: Path, *, allow_empty: bool = False) -> Path:
        resolved = path.resolve()
        digest = sha256_file(resolved, role, allow_empty=allow_empty)
        previous = self._baseline.get(resolved)
        if previous is not None and previous != digest:
            raise CampaignError(f"input changed during preflight: {resolved}")
        self._baseline[resolved] = digest
        self._allow_empty[resolved] = self._allow_empty.get(resolved, False) or allow_empty
        if not any(row.role == role and row.path == resolved for row in self.records):
            self.records.append(InputRecord(role, resolved, digest))
        return resolved

    def verify_unchanged(self) -> None:
        for path, expected in self._baseline.items():
            if sha256_file(path, "post-run tracked file",
                           allow_empty=self._allow_empty[path]) != expected:
                raise CampaignError(f"tracked file changed during campaign: {path}")

    def receipt_rows(self) -> list[dict[str, str]]:
        return [
            {"role": row.role, "path": str(row.path), "sha256": row.sha256}
            for row in self.records
        ]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_stable(path: Path, label: str, *, allow_empty: bool = False) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CampaignError(f"cannot stat {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise CampaignError(f"{label} is not a regular non-symlink file: {path}")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            payload = stream.read()
            after_read = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise CampaignError(f"cannot read {label}: {path}: {exc}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identities = {
        identity_before,
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns),
        (after_read.st_dev, after_read.st_ino, after_read.st_size, after_read.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    }
    if len(identities) != 1:
        raise CampaignError(f"{label} changed while being read: {path}")
    if not payload and not allow_empty:
        raise CampaignError(f"{label} is empty: {path}")
    return payload


def sha256_file(path: Path, label: str, *, allow_empty: bool = False) -> str:
    return sha256_bytes(read_stable(path, label, allow_empty=allow_empty))


def write_json_exclusive(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def safe_relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise CampaignError(f"artifact escapes attempt root: {path}") from exc


def private_file(root: Path, path: Path, label: str, *, nonempty: bool = True,
                 newer_than: int | None = None,
                 claimed: dict[tuple[int, int], Path] | None = None) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CampaignError(f"missing {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CampaignError(f"{label} is not a private regular file: {path}")
    if info.st_nlink != 1:
        raise CampaignError(f"{label} has link count {info.st_nlink}: {path}")
    safe_relative(root, path)
    if nonempty and info.st_size == 0:
        raise CampaignError(f"{label} is empty: {path}")
    if newer_than is not None and info.st_mtime_ns < newer_than:
        raise CampaignError(f"{label} predates its freshness marker: {path}")
    inode = (info.st_dev, info.st_ino)
    if claimed is not None:
        if inode in claimed:
            raise CampaignError(f"{label} reuses inode from {claimed[inode]}: {path}")
        claimed[inode] = path
    payload = read_stable(path, label, allow_empty=not nonempty)
    after = path.lstat()
    if ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise CampaignError(f"{label} changed during artifact validation: {path}")
    return {
        "path": safe_relative(root, path),
        "sha256": sha256_bytes(payload),
        "bytes": after.st_size,
    }


def resolve_input(project_root: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else project_root / candidate


def expand_filelist(project_root: Path, filelist: Path, tracker: InputTracker,
                    role: str, stack: tuple[Path, ...] = ()) -> list[Path]:
    resolved = tracker.add(f"{role}-filelist", filelist)
    if resolved in stack:
        raise CampaignError(f"recursive {role} filelist: {resolved}")
    sources: list[Path] = []
    text = read_stable(resolved, f"{role} filelist").decode("utf-8")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            tokens = shlex.split(raw_line, comments=True, posix=True)
        except ValueError as exc:
            raise CampaignError(f"{resolved}:{line_number}: invalid quoting: {exc}") from exc
        if not tokens:
            continue
        if tokens[0] in {"-f", "-F"} and len(tokens) == 2:
            nested = resolve_input(project_root, tokens[1])
            sources.extend(expand_filelist(project_root, nested, tracker, role,
                                           (*stack, resolved)))
            continue
        if len(tokens) != 1 or tokens[0].startswith(("-", "+")):
            raise CampaignError(
                f"{resolved}:{line_number}: only source paths and '-f PATH' are allowed; "
                "pass defines and parameters explicitly"
            )
        source = tracker.add(f"{role}-source", resolve_input(project_root, tokens[0]))
        sources.append(source)
    if not sources:
        raise CampaignError(f"{role} filelist resolves to no sources: {resolved}")
    duplicates = sorted({str(path) for path in sources if sources.count(path) > 1})
    if duplicates:
        raise CampaignError(f"{role} filelist repeats sources: {duplicates}")
    return sources


def write_expanded_filelist(path: Path, sources: Iterable[Path]) -> None:
    payload = "".join(f"{shlex.quote(str(source))}\n" for source in sources).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def parse_assignment(raw: str, label: str) -> tuple[str, str]:
    name, separator, value = raw.partition("=")
    if not separator or not ASSIGNMENT_NAME.fullmatch(name) or not value or any(
            character.isspace() for character in value):
        raise argparse.ArgumentTypeError(f"{label} must be NAME=VALUE without whitespace")
    return name, value


def parse_define(raw: str) -> tuple[str, str | None]:
    name, separator, value = raw.partition("=")
    if not DEFINE_NAME.fullmatch(name) or (separator and (not value or any(
            character.isspace() for character in value))):
        raise argparse.ArgumentTypeError("define must be NAME or NAME=VALUE without whitespace")
    return name, value if separator else None


def unique_mapping(rows: list[tuple[str, Any]], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in rows:
        if name in result:
            raise CampaignError(f"duplicate {label}: {name}")
        result[name] = value
    return result


def tool_command(program: Path, *arguments: str) -> list[str]:
    prefix = [sys.executable, str(program)] if program.suffix == ".py" else [str(program)]
    return [*prefix, *arguments]


def execute(command: list[str], console_log: Path, cwd: Path) -> None:
    console_log.parent.mkdir(parents=True, exist_ok=True)
    with console_log.open("xb") as stream:
        completed = subprocess.run(command, cwd=cwd, stdout=stream,
                                   stderr=subprocess.STDOUT, close_fds=True, check=False)
        stream.flush()
        os.fsync(stream.fileno())
    if completed.returncode:
        raise CampaignError(
            f"command failed with exit {completed.returncode}: {command[0]} "
            f"(console {console_log})"
        )


def scan_errors(paths: Iterable[Path], label: str) -> None:
    for path in paths:
        text = read_stable(path, label, allow_empty=True).decode("utf-8", errors="replace")
        for pattern in ERROR_PATTERNS:
            match = pattern.search(text)
            if match:
                excerpt = match.group(0).strip().replace("\n", " ")
                raise CampaignError(f"{label} contains fatal/error diagnostic {excerpt!r}: {path}")


def validate_pass(log: Path, run_name: str, *, reset: bool) -> None:
    text = read_stable(log, "run log").decode("utf-8", errors="replace")
    clean_markers = re.findall(r"(?m)^AER_CLEAN_TEST_PASS\s+(\S+)\s*$", text)
    if clean_markers != [run_name]:
        raise CampaignError(
            f"{run_name}: expected exactly one matching AER_CLEAN_TEST_PASS, got {clean_markers}"
        )
    reset_markers = re.findall(r"(?m)^AER_RESET_DRAIN_PASS\b.*$", text)
    if reset and len(reset_markers) != 1:
        raise CampaignError(f"{run_name}: expected exactly one AER_RESET_DRAIN_PASS")
    if not reset and reset_markers:
        raise CampaignError(f"{run_name}: unexpected reset PASS marker")


def validate_generation(root: Path, suite: str, manifest: Path) -> list[dict[str, Any]]:
    spec = SUITE_SPECS[suite]
    if manifest.name != spec["manifest_name"]:
        raise CampaignError(
            f"{suite}: official manifest name must be {spec['manifest_name']}, got {manifest.name}"
        )
    if sha256_file(manifest, f"{suite} manifest") != spec["manifest_sha256"]:
        raise CampaignError(f"{suite}: official manifest SHA mismatch")
    index_path = root / "generation-index.json"
    try:
        index = json.loads(read_stable(index_path, f"{suite} generation index"))
    except json.JSONDecodeError as exc:
        raise CampaignError(f"{suite}: invalid generation index: {exc}") from exc
    if set(index) != {"schema_version", "generator_version", "input_manifest", "runs"}:
        raise CampaignError(f"{suite}: generation-index schema mismatch")
    rows = index["runs"]
    if (index["schema_version"] != 1 or index["generator_version"] != "4.0" or
            index["input_manifest"] != manifest.name or not isinstance(rows, list)):
        raise CampaignError(f"{suite}: generation-index identity mismatch")
    names = [row.get("run", {}).get("name") if isinstance(row, dict) else None for row in rows]
    if names != list(spec["names"]) or len(names) != len(set(names)):
        raise CampaignError(f"{suite}: official run order/cardinality mismatch")
    expected_paths = {index_path.name}
    for row in rows:
        name = row["run"]["name"]
        trace_name = f"{name}.events.jsonl"
        metadata_name = f"{name}.manifest.json"
        expected_paths.update({trace_name, metadata_name})
        if (row.get("trace_file") != trace_name or
                row.get("trace_sha256") != spec["trace_sha256"][name] or
                row.get("event_identity_mode") != "address_only" or
                row.get("dut_address_fields") != ["logical_source"] or
                row.get("dut_payload_fields") != []):
            raise CampaignError(f"{suite}/{name}: official trace metadata mismatch")
        trace = root / trace_name
        metadata = root / metadata_name
        if sha256_file(trace, f"{suite}/{name} trace") != row["trace_sha256"]:
            raise CampaignError(f"{suite}/{name}: generated trace SHA mismatch")
        try:
            stored_metadata = json.loads(read_stable(metadata, f"{suite}/{name} metadata"))
        except json.JSONDecodeError as exc:
            raise CampaignError(f"{suite}/{name}: invalid metadata JSON") from exc
        if stored_metadata != row:
            raise CampaignError(f"{suite}/{name}: metadata/index mismatch")
    actual_paths = {path.name for path in root.iterdir()}
    if actual_paths != expected_paths:
        raise CampaignError(
            f"{suite}: generated file set mismatch missing={sorted(expected_paths-actual_paths)} "
            f"extra={sorted(actual_paths-expected_paths)}"
        )
    return rows


def allocate_attempt(output_root: Path, candidate: str) -> Path:
    if output_root.exists() and (output_root.is_symlink() or not output_root.is_dir()):
        raise CampaignError(f"output root is not a real directory: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    candidate_root = output_root / candidate
    if candidate_root.exists() and (candidate_root.is_symlink() or not candidate_root.is_dir()):
        raise CampaignError(f"candidate output root is not a real directory: {candidate_root}")
    candidate_root.mkdir(mode=0o700, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for _ in range(32):
        attempt = candidate_root / f"{timestamp}-p{os.getpid()}-{secrets.token_hex(6)}"
        try:
            attempt.mkdir(mode=0o700)
            return attempt
        except FileExistsError:
            continue
    raise CampaignError("could not allocate a unique attempt path")


def artifact_row(attempt: Path, path: Path, label: str, *, nonempty: bool = True) -> dict[str, Any]:
    return private_file(attempt, path, label, nonempty=nonempty)


def prepare_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--candidate-filelist", required=True, type=Path)
    parser.add_argument("--tb-filelist", required=True, type=Path)
    define_group = parser.add_mutually_exclusive_group(required=True)
    define_group.add_argument("--define", action="append", type=parse_define, default=[])
    define_group.add_argument("--no-defines", action="store_true")
    parser.add_argument("--param", action="append",
                        type=lambda value: parse_assignment(value, "parameter"), required=True)
    parser.add_argument("--suite", action="append", choices=sorted(SUITE_SPECS), required=True)
    parser.add_argument("--xrun", required=True, type=Path)
    parser.add_argument("--generator", required=True, type=Path)
    parser.add_argument("--preparer", required=True, type=Path)
    parser.add_argument("--full50-manifest", type=Path)
    parser.add_argument("--capacity22-manifest", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--addr-width", type=int, default=16)
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def run_campaign(args: argparse.Namespace) -> Path:
    if not SAFE_NAME.fullmatch(args.candidate):
        raise CampaignError("candidate is not a safe path identity")
    if not TOP_NAME.fullmatch(args.top):
        raise CampaignError("top is not a valid explicit elaboration top")
    if len(args.suite) != len(set(args.suite)):
        raise CampaignError("suite selections must be unique")
    if args.addr_width <= 0:
        raise CampaignError("addr-width must be positive")
    parameters = unique_mapping(args.param, "parameter")
    defines = unique_mapping(args.define, "define") if args.define else {}
    retire = [value for name, value in parameters.items() if name.split(".")[-1] == "RETIRE_LANES"]
    sources = [value for name, value in parameters.items() if name.split(".")[-1] == "NUM_SOURCES"]
    if retire != ["2"]:
        raise CampaignError("explicit parameters must contain exactly one RETIRE_LANES=2")
    if sources != ["16"]:
        raise CampaignError("official K2 suites require exactly one explicit NUM_SOURCES=16")

    project_root = args.project_root.resolve()
    if not project_root.is_dir() or project_root.is_symlink():
        raise CampaignError(f"project root is not a real directory: {project_root}")
    tracker = InputTracker()
    xrun = tracker.add("xrun", resolve_input(project_root, str(args.xrun)))
    if xrun.suffix != ".py" and not os.access(xrun, os.X_OK):
        raise CampaignError(f"xrun is not executable: {xrun}")
    generator = tracker.add("generator", resolve_input(project_root, str(args.generator)))
    preparer = tracker.add("preparer", resolve_input(project_root, str(args.preparer)))
    tracker.add("orchestrator", Path(__file__))
    candidate_sources = expand_filelist(
        project_root, resolve_input(project_root, str(args.candidate_filelist)),
        tracker, "candidate")
    tb_sources = expand_filelist(
        project_root, resolve_input(project_root, str(args.tb_filelist)), tracker, "tb")
    manifests: dict[str, Path] = {}
    for suite in args.suite:
        raw_manifest = getattr(args, f"{suite}_manifest")
        if raw_manifest is None:
            raise CampaignError(f"--{suite}-manifest is required when --suite {suite} is selected")
        manifests[suite] = tracker.add(
            f"{suite}-manifest", resolve_input(project_root, str(raw_manifest)))

    if args.output_root.is_symlink():
        raise CampaignError(f"output root must not be a symlink: {args.output_root}")
    attempt = allocate_attempt(args.output_root.resolve(), args.candidate)
    try:
        evidence = InputTracker()
        for directory in ("inputs", "traces", "prepared", "build", "work", "logs", "runs"):
            (attempt / directory).mkdir(mode=0o700)
        candidate_expanded = attempt / "inputs/candidate.expanded.f"
        tb_expanded = attempt / "inputs/tb.expanded.f"
        write_expanded_filelist(candidate_expanded, candidate_sources)
        write_expanded_filelist(tb_expanded, tb_sources)

        generated: dict[str, list[dict[str, Any]]] = {}
        generation_receipts: dict[str, Any] = {}
        prepared: dict[tuple[str, str], Path] = {}
        preparation_receipts: list[dict[str, Any]] = []
        for suite in args.suite:
            trace_root = attempt / "traces" / suite
            trace_root.mkdir(mode=0o700)
            generation_console = attempt / "logs" / f"generate.{suite}.log"
            execute(tool_command(generator, "--manifest", str(manifests[suite]),
                                 "--output-dir", str(trace_root)), generation_console,
                    attempt / "work")
            scan_errors([generation_console], f"{suite} generator log")
            generated[suite] = validate_generation(trace_root, suite, manifests[suite])
            generation_receipts[suite] = {
                "manifest": {
                    "path": str(manifests[suite]),
                    "sha256": sha256_file(manifests[suite], f"{suite} manifest"),
                },
                "generation_index": artifact_row(
                    attempt, trace_root / "generation-index.json", f"{suite} generation index"),
                "log": artifact_row(attempt, generation_console, f"{suite} generator log"),
                "run_count": len(generated[suite]),
            }
            evidence.add(f"{suite}-generation-log", generation_console)
            evidence.add(f"{suite}-generation-index", trace_root / "generation-index.json")
            prepare_root = attempt / "prepared" / suite
            prepare_root.mkdir(mode=0o700)
            prepare_log_root = attempt / "logs" / "prepare" / suite
            prepare_log_root.mkdir(parents=True, mode=0o700)
            for row in generated[suite]:
                name = row["run"]["name"]
                output = prepare_root / f"{name}.svtrace"
                log = prepare_log_root / f"{name}.log"
                execute(tool_command(
                    preparer, "--trace", str(trace_root / row["trace_file"]),
                    "--run-manifest", str(trace_root / f"{name}.manifest.json"),
                    "--output", str(output), "--addr-width", str(args.addr_width)),
                    log, attempt / "work")
                scan_errors([log], f"{suite}/{name} preparer log")
                marker_text = read_stable(log, "preparer log").decode("utf-8", errors="replace")
                if len(re.findall(r"(?m)^TRACE_PREPARED\b", marker_text)) != 1:
                    raise CampaignError(f"{suite}/{name}: missing or duplicate TRACE_PREPARED")
                prepared[(suite, name)] = output
                preparation_receipts.append({
                    "suite": suite, "name": name,
                    "trace": artifact_row(attempt, trace_root / row["trace_file"], "trace"),
                    "metadata": artifact_row(
                        attempt, trace_root / f"{name}.manifest.json", "trace metadata"),
                    "prepared": artifact_row(attempt, output, "prepared trace"),
                    "log": artifact_row(attempt, log, "preparer log"),
                })
                evidence.add(f"{suite}-trace", trace_root / row["trace_file"])
                evidence.add(f"{suite}-trace-metadata",
                             trace_root / f"{name}.manifest.json")
                evidence.add(f"{suite}-prepared-trace", output)
                evidence.add(f"{suite}-preparer-log", log)

        version_log = attempt / "logs/xrun.version.log"
        execute(tool_command(xrun, "-version"), version_log, attempt / "work")
        scan_errors([version_log], "xrun version log")
        if not read_stable(version_log, "xrun version log"):
            raise CampaignError("xrun version output is empty")
        evidence.add("xrun-version-log", version_log)

        snapshot = f"a1_k2_{args.candidate.replace('-', '_')}_{attempt.name[-12:]}"
        compile_log = attempt / "logs/compile.log"
        compile_console = attempt / "logs/compile.console.log"
        compile_command = tool_command(
            xrun, "-64bit", "-sv", "-timescale", "1ns/1ps", "-top", args.top,
            "-snapshot", snapshot, "-elaborate", "-xmlibdirname",
            str(attempt / "build/xcelium.d"))
        for name, value in parameters.items():
            compile_command.extend(["-defparam", f"{name}={value}"])
        for name, value in defines.items():
            compile_command.extend(["-define", name if value is None else f"{name}={value}"])
        compile_command.extend([
            "-f", str(candidate_expanded), "-f", str(tb_expanded), "-l", str(compile_log)])
        execute(compile_command, compile_console, attempt / "work")
        private_file(attempt, compile_log, "compile log")
        scan_errors([compile_log, compile_console], "compile log")
        evidence.add("compile-log", compile_log)
        evidence.add("compile-console-log", compile_console, allow_empty=True)

        run_plan: list[dict[str, Any]] = [{
            "kind": "reset", "suite": None, "name": "basic_reset_drain", "prepared": None,
        }]
        for suite in args.suite:
            run_plan.extend({
                "kind": "trace", "suite": suite, "name": row["run"]["name"],
                "prepared": prepared[(suite, row["run"]["name"])],
            } for row in generated[suite])
        plan = {
            "schema_version": 1,
            "candidate": args.candidate,
            "top": args.top,
            "defines": defines,
            "parameters": parameters,
            "retire_lanes": 2,
            "suites": list(args.suite),
            "compile_command": compile_command,
            "run_order": [
                {"kind": row["kind"], "suite": row["suite"], "name": row["name"]}
                for row in run_plan
            ],
        }
        plan_path = attempt / "execution-plan.json"
        write_json_exclusive(plan_path, plan)

        claimed_results: dict[tuple[int, int], Path] = {}
        run_receipts: list[dict[str, Any]] = []
        for position, row in enumerate(run_plan):
            suite_part = row["suite"] or "reset"
            run_dir = attempt / "runs" / f"{position:03d}-{suite_part}-{row['name']}"
            run_dir.mkdir(mode=0o700)
            marker = run_dir / "freshness.marker"
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            with os.fdopen(descriptor, "wb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            marker_time = marker.lstat().st_mtime_ns
            metrics = run_dir / "metrics.csv"
            events = run_dir / "events.csv"
            run_log = run_dir / "run.log"
            console_log = run_dir / "run.console.log"
            run_command = tool_command(
                xrun, "-64bit", "-R", "-snapshot", snapshot,
                "-xmlibdirname", str(attempt / "build/xcelium.d"),
                f"+CLEAN_TEST={row['name']}", f"+CANDIDATE={args.candidate}",
                f"+METRICS={metrics}", f"+EVENT_METRICS={events}")
            if row["kind"] == "trace":
                run_command.extend([
                    f"+TRACE_FILE={row['prepared']}", f"+TRACE_NAME={row['name']}"])
            run_command.extend(["-l", str(run_log)])
            execute(run_command, console_log, attempt / "work")
            private_file(attempt, run_log, f"{row['name']} run log", newer_than=marker_time)
            scan_errors([run_log, console_log], f"{row['name']} run log")
            validate_pass(run_log, row["name"], reset=row["kind"] == "reset")
            result_rows = {
                "metrics": private_file(
                    attempt, metrics, f"{row['name']} metrics", newer_than=marker_time,
                    claimed=claimed_results),
                "events": private_file(
                    attempt, events, f"{row['name']} events", newer_than=marker_time,
                    claimed=claimed_results),
            }
            run_receipts.append({
                "position": position, "kind": row["kind"], "suite": row["suite"],
                "name": row["name"], "command": run_command,
                "log": artifact_row(attempt, run_log, "run log"),
                "console_log": artifact_row(
                    attempt, console_log, "run console log", nonempty=False),
                "results": result_rows,
            })
            evidence.add(f"{row['name']}-run-log", run_log)
            evidence.add(f"{row['name']}-run-console-log", console_log, allow_empty=True)
            evidence.add(f"{row['name']}-metrics", metrics)
            evidence.add(f"{row['name']}-events", events)

        tracker.verify_unchanged()
        evidence.verify_unchanged()
        receipt = {
            "schema_version": 1,
            "campaign": "a1-local-xcelium-k2",
            "status": "PASS",
            "attempt_id": attempt.name,
            "candidate": args.candidate,
            "configuration": {
                "top": args.top,
                "candidate_filelist": artifact_row(
                    attempt, candidate_expanded, "expanded candidate filelist"),
                "tb_filelist": artifact_row(attempt, tb_expanded, "expanded TB filelist"),
                "defines": defines,
                "parameters": parameters,
                "retire_lanes": 2,
                "num_sources": 16,
            },
            "inputs": tracker.receipt_rows(),
            "sealed_evidence": evidence.receipt_rows(),
            "toolchain": {
                "xrun": str(xrun),
                "xrun_sha256": sha256_file(xrun, "xrun"),
                "version_log": artifact_row(attempt, version_log, "xrun version log"),
            },
            "generation": generation_receipts,
            "preparation": preparation_receipts,
            "compile": {
                "invocation_count": 1,
                "snapshot": snapshot,
                "command": compile_command,
                "log": artifact_row(attempt, compile_log, "compile log"),
                "console_log": artifact_row(
                    attempt, compile_console, "compile console log", nonempty=False),
            },
            "execution_plan": artifact_row(attempt, plan_path, "execution plan"),
            "runs": run_receipts,
            "reset_run_count": sum(row["kind"] == "reset" for row in run_receipts),
            "trace_run_count": sum(row["kind"] == "trace" for row in run_receipts),
        }
        receipt_path = attempt / "campaign.receipt.json"
        write_json_exclusive(receipt_path, receipt)
        receipt_sha = sha256_file(receipt_path, "campaign receipt")
        sha_path = attempt / "campaign.receipt.sha256"
        descriptor = os.open(sha_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(f"{receipt_sha}  {receipt_path.name}\n")
            stream.flush()
            os.fsync(stream.fileno())
        return receipt_path
    except Exception as exc:
        failure_path = attempt / "campaign.failure.json"
        if not failure_path.exists():
            try:
                write_json_exclusive(failure_path, {
                    "schema_version": 1, "status": "FAIL", "error": str(exc),
                    "attempt_id": attempt.name,
                })
            except OSError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    try:
        args = prepare_args(argv)
        receipt = run_campaign(args)
    except (CampaignError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"A1_K2_XRUN_CAMPAIGN_PASS receipt={receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
