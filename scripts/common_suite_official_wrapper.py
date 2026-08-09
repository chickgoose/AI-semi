#!/usr/bin/env python3
"""Run one official common suite inside a receipt-qualified unique attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import common_suite_attempt as attempt_tool
import common_suite_execution_sidecar as sidecar_tool
import common_suite_official as official
import common_suite_receipt as receipt

RESERVED_ENV = {
    "AER_COMMON_MULTILANE_TRACE_DIR", "AER_A7_TRACE_DIR", "AER_CLEAN_OUT",
    "AER_RECEIPT_ATTEMPT_ROOT", "AER_RECEIPT_TRACE_DIR", "AER_RECEIPT_OUTPUT_DIR",
    "AER_RECEIPT_SUITE", "AER_RECEIPT_CANDIDATE", "TMPDIR",
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WrapperError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assignment(value: str, label: str) -> tuple[str, str]:
    key, separator, assigned = value.partition("=")
    if not separator or not key or not assigned:
        raise argparse.ArgumentTypeError(f"{label} must be NAME=VALUE")
    return key, assigned


def _mapping(rows: list[tuple[str, str]], label: str) -> dict[str, str]:
    result = {}
    for key, value in rows:
        if key in result:
            raise WrapperError(f"duplicate {label}: {key}")
        result[key] = value
    return result


def _run(command: list[str], log: Path, *, env: dict[str, str] | None = None) -> None:
    if log.exists():
        raise WrapperError(f"refusing to overwrite log: {log}")
    with log.open("xb") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env,
                                   close_fds=True, check=False)
        stream.flush(); os.fsync(stream.fileno())
    if completed.returncode:
        raise WrapperError(f"command failed with exit {completed.returncode}: {command[0]} (log {log})")


def _command(program: Path, *arguments: str) -> list[str]:
    prefix = [sys.executable, str(program)] if program.suffix == ".py" else [str(program)]
    return [*prefix, *arguments]


def _mkdir_new(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise WrapperError(f"refusing to reuse existing path: {path}") from exc


def generate_only(suite: str, manifest: Path, generator: Path, output: Path) -> dict[str, Any]:
    if suite not in official.SUITES:
        raise WrapperError(f"unknown official suite: {suite}")
    output.parent.mkdir(parents=True, exist_ok=True)
    _mkdir_new(output)
    log = output / "generator.log"
    _run(_command(generator, "--manifest", str(manifest), "--output-dir", str(output)), log)
    return receipt.validate_official_generation(output / "generation-index.json", manifest, suite)


def _contained_glob(root: Path, pattern: str, run_name: str, label: str) -> Path:
    if "{run}" not in pattern:
        raise WrapperError(f"{label} pattern must contain the {{run}} placeholder")
    rendered = pattern.replace("{run}", run_name)
    relative = Path(rendered)
    if relative.is_absolute() or ".." in relative.parts or "{run}" in rendered:
        raise WrapperError(f"{label} pattern must be contained and include only the {{run}} placeholder")
    matches = list(root.glob(rendered))
    if len(matches) != 1:
        raise WrapperError(f"{label} for {run_name} matched {len(matches)} paths: {rendered}")
    path = matches[0]
    try:
        info = path.lstat()
    except OSError as exc:
        raise WrapperError(f"cannot stat {label} for {run_name}: {exc}") from exc
    if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents or info.st_nlink != 1:
        raise WrapperError(f"{label} for {run_name} is not a private regular file")
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise WrapperError(f"artifact escapes attempt root: {path}") from exc


def _analyze(workload: str, analyzer: Path, trace: Path, run_manifest: Path,
             result: Path, summary: Path | None, output: Path, log: Path) -> None:
    if workload == "mixed_phase_always_ready":
        if summary is None:
            raise WrapperError("mixed-phase run requires a summary artifact")
        command = _command(analyzer, "--run-manifest", str(run_manifest), "--events", str(result),
                           "--summary", str(summary), "--require-qualified", "--output", str(output))
    else:
        command = _command(analyzer, "--trace", str(trace), "--run-manifest", str(run_manifest),
                           "--events", str(result), "--output", str(output))
    _run(command, log)
    if not output.is_file() or not output.stat().st_size:
        raise WrapperError(f"analyzer did not produce output: {output}")


def _write_empty_marker(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.flush(); os.fsync(stream.fileno())


def run_suite(args: argparse.Namespace) -> tuple[Path, Path]:
    candidate_doc = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
    candidate = candidate_doc.get("candidate") if isinstance(candidate_doc, dict) else None
    if not isinstance(candidate, str) or not SAFE_NAME.fullmatch(candidate):
        raise WrapperError("candidate manifest has no safe candidate identity")
    analyzers = {key: Path(value) for key, value in _mapping(args.analyzer, "analyzer").items()}
    required_analyzers = {"pairwise_contention", "mixed_phase_always_ready", "phase_transition"}
    if args.suite == "full50":
        required_analyzers.add("timing_pair")
    if set(analyzers) != required_analyzers:
        raise WrapperError(f"analyzer identities must be exactly {sorted(required_analyzers)}")
    runner_env = _mapping(args.runner_env, "runner environment")
    if set(runner_env) & RESERVED_ENV:
        raise WrapperError(f"runner environment attempts to override reserved keys: {sorted(set(runner_env) & RESERVED_ENV)}")
    dependencies: dict[str, list[Path]] = {}
    for key, value in args.tool_dependency:
        dependencies.setdefault(key, []).append(Path(value))

    plan = {
        "schema_version": 1, "suite": args.suite, "candidate": candidate,
        "official_manifest": {"name": args.official_manifest.name,
                              "sha256": _sha(args.official_manifest)},
        "runner_args": list(args.runner_arg), "runner_env": runner_env,
        "result_pattern": args.result_pattern, "summary_pattern": args.summary_pattern,
        "analyzers": sorted(analyzers), "simulator_identity": args.simulator_name,
    }
    with tempfile.TemporaryDirectory(prefix="aer-receipt-plan-") as plan_directory:
        plan_path = Path(plan_directory) / "execution-plan.json"
        descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(plan, stream, indent=2, sort_keys=True); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        dependencies.setdefault("runner", []).extend([Path(__file__).resolve(), plan_path])
        tools = {"runner": args.runner, "generator": args.generator, **analyzers}
        for identity, program in tools.items():
            if program.suffix == ".py":
                dependencies.setdefault(identity, []).append(Path(sys.executable))
        attempt_root = attempt_tool.create(
            args.output_root, args.suite, candidate, args.candidate_manifest, tools,
            tool_dependencies=dependencies, simulator_name=args.simulator_name,
            simulator_executable=args.simulator_executable,
            simulator_version=args.simulator_version)

    traces = attempt_root / "traces"; runner_output = attempt_root / "runner-output"
    temporary = attempt_root / "tmp"; logs = attempt_root / "logs"
    for directory in (traces, runner_output, temporary, logs):
        _mkdir_new(directory)
    _run(_command(args.generator, "--manifest", str(args.official_manifest), "--output-dir", str(traces)),
         logs / "generator.log")
    generation = receipt.validate_official_generation(traces / "generation-index.json",
                                                      args.official_manifest, args.suite)
    markers = {}
    for row in generation["runs"]:
        run_root = attempt_root / "runs" / row["name"]
        _mkdir_new(run_root)
        marker = run_root / "freshness.marker"; _write_empty_marker(marker); markers[row["name"]] = marker

    environment = os.environ.copy(); environment.update(runner_env)
    environment.update({
        "AER_COMMON_MULTILANE_TRACE_DIR": str(traces), "AER_A7_TRACE_DIR": str(traces),
        "AER_CLEAN_OUT": str(runner_output), "AER_RECEIPT_ATTEMPT_ROOT": str(attempt_root),
        "AER_RECEIPT_TRACE_DIR": str(traces), "AER_RECEIPT_OUTPUT_DIR": str(runner_output),
        "AER_RECEIPT_SUITE": args.suite, "AER_RECEIPT_CANDIDATE": candidate,
        "TMPDIR": str(temporary),
    })
    _run(_command(args.runner, *args.runner_arg), logs / "runner.log", env=environment)
    receipt.validate_official_generation(traces / "generation-index.json", args.official_manifest, args.suite)

    artifact_rows = []
    for generated in generation["runs"]:
        name, workload = generated["name"], generated["workload"]
        marker = markers[name]
        result = _contained_glob(attempt_root, args.result_pattern, name, "result")
        if result.stat().st_mtime_ns <= marker.stat().st_mtime_ns:
            raise WrapperError(f"result for {name} is not newer than its marker")
        summary = None
        if workload == "mixed_phase_always_ready":
            summary = _contained_glob(attempt_root, args.summary_pattern, name, "summary")
            if summary.stat().st_mtime_ns <= marker.stat().st_mtime_ns:
                raise WrapperError(f"summary for {name} is not newer than its marker")
        run_root = marker.parent
        artifact_row = {"name": name, "freshness_marker": _relative(attempt_root, marker),
                        "result": {"path": _relative(attempt_root, result), "sha256": _sha(result)}}
        analyzer_output = None
        if workload in receipt.ANALYZER_WORKLOADS:
            analyzer_output = run_root / "analysis.json"
            _analyze(workload, analyzers[workload], generated["trace"], generated["run_manifest"],
                     result, summary, analyzer_output, logs / f"analyzer-{name}.log")
            artifact_row["analyzer"] = {"path": _relative(attempt_root, analyzer_output),
                                        "sha256": _sha(analyzer_output)}
        sidecar_path = run_root / "execution.sidecar.json"
        sidecar_payload = (json.dumps(sidecar_tool.build(
            attempt_root, generated["run_manifest"], generated["trace"], result, analyzer_output),
            indent=2, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(sidecar_path, sidecar_payload)
        artifact_row["execution_sidecar"] = {"path": _relative(attempt_root, sidecar_path),
                                               "sha256": _sha(sidecar_path)}
        artifact_rows.append(artifact_row)

    attempt_path = attempt_root / "attempt.json"
    artifact_document = {"schema_version": receipt.SCHEMA_VERSION, "suite": args.suite,
                         "candidate": candidate, "attempt": {"path": "attempt.json", "sha256": _sha(attempt_path)},
                         "runs": artifact_rows}
    artifacts_path = attempt_root / "artifacts.json"
    receipt.publish_new_atomic(artifacts_path,
        (json.dumps(artifact_document, indent=2, sort_keys=True) + "\n").encode())
    result = receipt.validate(traces / "generation-index.json", args.official_manifest, args.suite,
                              artifacts_path, attempt_root)
    receipt_path = attempt_root / "common-suite.receipt.json"
    receipt.publish_new_atomic(receipt_path, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    return attempt_root, receipt_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate-only")
    generate.add_argument("--suite", choices=sorted(official.SUITES), required=True)
    generate.add_argument("--official-manifest", type=Path, required=True)
    generate.add_argument("--generator", type=Path, required=True)
    generate.add_argument("--output-dir", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--suite", choices=sorted(official.SUITES), required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--candidate-manifest", type=Path, required=True)
    run.add_argument("--official-manifest", type=Path, required=True)
    run.add_argument("--generator", type=Path, required=True)
    run.add_argument("--runner", type=Path, required=True)
    run.add_argument("--runner-arg", action="append", default=[])
    run.add_argument("--runner-env", action="append", type=lambda value: _assignment(value, "runner env"), default=[])
    run.add_argument("--result-pattern", required=True)
    run.add_argument("--summary-pattern", required=True)
    run.add_argument("--analyzer", action="append", type=lambda value: _assignment(value, "analyzer"), required=True)
    run.add_argument("--tool-dependency", action="append",
                     type=lambda value: _assignment(value, "tool dependency"), default=[])
    run.add_argument("--simulator-name", required=True)
    run.add_argument("--simulator-executable", type=Path, required=True)
    run.add_argument("--simulator-version", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "generate-only":
            result = generate_only(args.suite, args.official_manifest, args.generator, args.output_dir)
            print(f"PASS suite={args.suite} runs={len(result['names'])} output={args.output_dir.resolve()}")
        else:
            attempt_root, receipt_path = run_suite(args)
            print(f"PASS attempt={attempt_root.resolve()} receipt={receipt_path.resolve()}")
    except (OSError, json.JSONDecodeError, ValueError, receipt.ReceiptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
