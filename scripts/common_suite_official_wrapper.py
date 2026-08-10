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
    "AER_RECEIPT_CANDIDATE_MANIFEST", "AER_RECEIPT_CANDIDATE_BUNDLE",
    "AER_RECEIPT_SIMULATOR", "AER_RECEIPT_CANDIDATE_MANIFEST_SHA256",
    "AER_RECEIPT_CANDIDATE_BUNDLE_SHA256", "AER_RECEIPT_SIMULATOR_IDENTITY",
    "AER_RECEIPT_SIMULATOR_SHA256", "AER_RECEIPT_SIMULATOR_VERSION_SHA256",
    "AER_RECEIPT_COMPILE_MANIFEST", "AER_RECEIPT_COMPILE_LOG",
    "AER_RECEIPT_CANDIDATE_FILELIST", "AER_SIMULATOR",
    "AER_GANGHEE_FILELIST", "AER_GANGHEE_CLUSTER2_FILELIST",
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
PYTHON_EXECUTABLE = Path(sys.executable).resolve()


class WrapperError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_sha(path: Path, label: str) -> str:
    payload, _ = receipt._read_bytes_stable(path, label)
    if not payload:
        raise WrapperError(f"{label} is empty: {path}")
    return hashlib.sha256(payload).hexdigest()


def _capture_execution_sources(tool_sources: dict[str, list[Path]],
                               simulator_sources: dict[str, Path]) -> tuple[dict[str, list[str]], dict[str, str]]:
    return ({name: [_stable_sha(path, f"actual tool {name} source") for path in paths]
             for name, paths in tool_sources.items()},
            {name: _stable_sha(path, f"actual simulator {name}")
             for name, path in simulator_sources.items()})


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
    prefix = [str(PYTHON_EXECUTABLE), str(program)] if program.suffix == ".py" else [str(program)]
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


def _write_candidate_filelist(attempt_root: Path, candidate_spec: dict[str, Any],
                              output: Path) -> None:
    lines = []
    for row in candidate_spec["bundle_files"]:
        source = attempt_root / row["path"]
        if not source.is_file() or source.is_symlink():
            raise WrapperError(f"candidate snapshot source is unavailable: {source}")
        lines.append(str(source.resolve()))
    payload = ("\n".join(lines) + "\n").encode()
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())


def _common_candidate_environment(runner: Path, runner_args: list[str],
                                  filelist: Path) -> dict[str, str]:
    """Wire only native common-runner modes that already accept a file list."""
    if runner.name != "run_common_multilane_candidate.sh":
        return {}
    binding = runner_args[0] if runner_args else ""
    if binding == "ganghee" and len(runner_args) == 1:
        return {"AER_GANGHEE_FILELIST": str(filelist)}
    if binding == "ganghee-cluster2" and len(runner_args) == 1:
        return {"AER_GANGHEE_CLUSTER2_FILELIST": str(filelist)}
    if binding in {"clean", "drec-prefix"}:
        raise WrapperError(
            f"common runner binding {binding!r} has no immutable candidate-filelist "
            "input; refusing mutable project-tree execution"
        )
    raise WrapperError("common runner arguments do not identify a supported native binding")


def _run_suite(args: argparse.Namespace, plan_directory: Path) -> tuple[Path, Path]:
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
    plan_path = plan_directory / "execution-plan.json"
    descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(plan, stream, indent=2, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    dependencies.setdefault("runner", []).extend([Path(__file__).resolve(), plan_path])
    tools = {"runner": args.runner, "generator": args.generator, **analyzers}
    for identity, program in tools.items():
        if program.suffix == ".py":
            dependencies.setdefault(identity, []).append(PYTHON_EXECUTABLE)
    tool_sources = {identity: [program, *dependencies.get(identity, [])]
                    for identity, program in tools.items()}
    simulator_sources = {"executable": args.simulator_executable, "version": args.simulator_version}
    attempt_root = attempt_tool.create(
        args.output_root, args.suite, candidate, args.candidate_manifest, tools,
        tool_dependencies=dependencies, simulator_name=args.simulator_name,
        simulator_executable=args.simulator_executable,
        simulator_version=args.simulator_version)
    attempt_doc = json.loads((attempt_root / "attempt.json").read_text())
    pre_tools, pre_simulator = _capture_execution_sources(tool_sources, simulator_sources)

    traces = attempt_root / "traces"; runner_output = attempt_root / "runner-output"
    temporary = attempt_root / "tmp"; logs = attempt_root / "logs"
    runner_evidence = attempt_root / "runner-evidence"
    for directory in (traces, runner_output, temporary, logs, runner_evidence):
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

    candidate_spec = attempt_doc["candidate_manifest"]
    simulator_spec = attempt_doc["simulator"]
    candidate_snapshot = attempt_root / candidate_spec["path"]
    candidate_bundle = candidate_snapshot.parent
    simulator_snapshot = attempt_root / simulator_spec["executable"]["path"]
    compile_manifest_path = runner_evidence / "compile.manifest.json"
    compile_log_path = runner_evidence / "compile.log"
    candidate_filelist_path = runner_evidence / "candidate.snapshot.f"
    _write_candidate_filelist(attempt_root, candidate_spec, candidate_filelist_path)
    environment = os.environ.copy(); environment.update(runner_env)
    environment.update({
        "AER_COMMON_MULTILANE_TRACE_DIR": str(traces), "AER_A7_TRACE_DIR": str(traces),
        "AER_CLEAN_OUT": str(runner_output), "AER_RECEIPT_ATTEMPT_ROOT": str(attempt_root),
        "AER_RECEIPT_TRACE_DIR": str(traces), "AER_RECEIPT_OUTPUT_DIR": str(runner_output),
        "AER_RECEIPT_SUITE": args.suite, "AER_RECEIPT_CANDIDATE": candidate,
        "AER_RECEIPT_CANDIDATE_MANIFEST": str(candidate_snapshot),
        "AER_RECEIPT_CANDIDATE_BUNDLE": str(candidate_bundle),
        "AER_RECEIPT_CANDIDATE_FILELIST": str(candidate_filelist_path),
        "AER_RECEIPT_SIMULATOR": str(simulator_snapshot),
        "AER_RECEIPT_CANDIDATE_MANIFEST_SHA256": candidate_spec["sha256"],
        "AER_RECEIPT_CANDIDATE_BUNDLE_SHA256": candidate_doc["bundle_sha256"],
        "AER_RECEIPT_SIMULATOR_IDENTITY": simulator_spec["identity"],
        "AER_RECEIPT_SIMULATOR_SHA256": simulator_spec["executable"]["sha256"],
        "AER_RECEIPT_SIMULATOR_VERSION_SHA256": simulator_spec["version"]["sha256"],
        "AER_RECEIPT_COMPILE_MANIFEST": str(compile_manifest_path),
        "AER_RECEIPT_COMPILE_LOG": str(compile_log_path),
        "AER_SIMULATOR": simulator_spec["identity"],
        "PATH": str(simulator_snapshot.parent) + os.pathsep + environment.get("PATH", ""),
        "TMPDIR": str(temporary),
    })
    environment.update(_common_candidate_environment(args.runner, list(args.runner_arg),
                                                     candidate_filelist_path))
    _run(_command(args.runner, *args.runner_arg), logs / "runner.log", env=environment)
    receipt.validate_official_generation(traces / "generation-index.json", args.official_manifest, args.suite)

    prepared_rows = []
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
        analyzer_output = None
        if workload in receipt.ANALYZER_WORKLOADS:
            analyzer_output = run_root / "analysis.json"
            _analyze(workload, analyzers[workload], generated["trace"], generated["run_manifest"],
                     result, summary, analyzer_output, logs / f"analyzer-{name}.log")
        prepared_rows.append((generated, marker, result, summary, analyzer_output))

    if not compile_manifest_path.is_file() or not compile_log_path.is_file() or not compile_log_path.stat().st_size:
        raise WrapperError("runner did not emit mandatory compile manifest and non-empty compile log")
    simulator_identity = {"identity": simulator_spec["identity"],
        "executable_sha256": simulator_spec["executable"]["sha256"],
        "version_sha256": simulator_spec["version"]["sha256"]}
    expected_compile = {"schema_version": 1,
        "candidate_manifest_sha256": candidate_spec["sha256"],
        "candidate_bundle_sha256": candidate_doc["bundle_sha256"], "filelist": candidate_doc["filelist"],
        "top": candidate_doc["top"], "parameters": candidate_doc["parameters"],
        "defines": candidate_doc["defines"], "includes": candidate_doc["includes"],
        "source_count": candidate_doc["source_count"], "retire_lanes": candidate_doc["retire_lanes"],
        "simulator": simulator_identity}
    if json.loads(compile_manifest_path.read_text()) != expected_compile:
        raise WrapperError("runner compile manifest does not match exact candidate/simulator contract")

    post_tools, post_simulator = _capture_execution_sources(tool_sources, simulator_sources)
    execution_tools = {}
    for identity, sources in tool_sources.items():
        raw = attempt_doc["tools"][identity]; snapshots = [raw["entrypoint"], *raw["dependencies"]]
        if len(sources) != len(snapshots):
            raise WrapperError(f"tool {identity} execution source cardinality changed")
        rows = []
        for position, snapshot in enumerate(snapshots):
            expected = snapshot["sha256"]
            if pre_tools[identity][position] != expected or post_tools[identity][position] != expected:
                raise WrapperError(f"actual tool {identity} source changed or differs from snapshot")
            rows.append({"logical_name": snapshot["logical_name"], "sha256": expected,
                         "pre_sha256": pre_tools[identity][position],
                         "post_sha256": post_tools[identity][position]})
        execution_tools[identity] = {"bundle_sha256": raw["bundle_sha256"], "files": rows}
    for key, expected in (("executable", simulator_spec["executable"]["sha256"]),
                          ("version", simulator_spec["version"]["sha256"])):
        if pre_simulator[key] != expected or post_simulator[key] != expected:
            raise WrapperError(f"actual simulator {key} changed or differs from snapshot")
    execution_document = {"schema_version": receipt.EXECUTION_IDENTITY_SCHEMA_VERSION,
        "status": "pre_post_match", "candidate_manifest_sha256": candidate_spec["sha256"],
        "tools": execution_tools, "simulator": {**simulator_identity,
            "executable_pre_sha256": pre_simulator["executable"],
            "executable_post_sha256": post_simulator["executable"],
            "version_pre_sha256": pre_simulator["version"],
            "version_post_sha256": post_simulator["version"]},
        "compile_manifest_sha256": _sha(compile_manifest_path), "compile_log_sha256": _sha(compile_log_path)}
    execution_identity_path = attempt_root / "execution.identity.json"
    receipt.publish_new_atomic(execution_identity_path,
        (json.dumps(execution_document, indent=2, sort_keys=True) + "\n").encode())

    artifact_rows = []
    for generated, marker, result, summary, analyzer_output in prepared_rows:
        name = generated["name"]
        artifact_row = {"name": name, "freshness_marker": _relative(attempt_root, marker),
                        "result": {"path": _relative(attempt_root, result), "sha256": _sha(result)}}
        if analyzer_output is not None:
            artifact_row["analyzer"] = {"path": _relative(attempt_root, analyzer_output),
                                        "sha256": _sha(analyzer_output)}
        if summary is not None:
            artifact_row["summary"] = {"path": _relative(attempt_root, summary), "sha256": _sha(summary)}
        run_root = marker.parent
        sidecar_path = run_root / "execution.sidecar.json"
        sidecar_payload = (json.dumps(sidecar_tool.build(
            attempt_root, generated["run_manifest"], generated["trace"], result, analyzer_output,
            summary, execution_identity_path),
            indent=2, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(sidecar_path, sidecar_payload)
        artifact_row["execution_sidecar"] = {"path": _relative(attempt_root, sidecar_path),
                                               "sha256": _sha(sidecar_path)}
        artifact_rows.append(artifact_row)

    attempt_path = attempt_root / "attempt.json"
    artifact_document = {"schema_version": receipt.SCHEMA_VERSION, "suite": args.suite,
                         "candidate": candidate, "attempt": {"path": "attempt.json", "sha256": _sha(attempt_path)},
                         "execution_identity": {"path": _relative(attempt_root, execution_identity_path),
                                                "sha256": _sha(execution_identity_path)},
                         "compile_manifest": {"path": _relative(attempt_root, compile_manifest_path),
                                              "sha256": _sha(compile_manifest_path)},
                         "compile_log": {"path": _relative(attempt_root, compile_log_path),
                                         "sha256": _sha(compile_log_path)},
                         "runs": artifact_rows}
    artifacts_path = attempt_root / "artifacts.json"
    receipt.publish_new_atomic(artifacts_path,
        (json.dumps(artifact_document, indent=2, sort_keys=True) + "\n").encode())
    result = receipt.validate(traces / "generation-index.json", args.official_manifest, args.suite,
                              artifacts_path, attempt_root)
    receipt_path = attempt_root / "common-suite.receipt.json"
    receipt.publish_new_atomic(receipt_path, (json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    return attempt_root, receipt_path


def run_suite(args: argparse.Namespace) -> tuple[Path, Path]:
    with tempfile.TemporaryDirectory(prefix="aer-receipt-plan-") as plan_directory:
        return _run_suite(args, Path(plan_directory))


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
