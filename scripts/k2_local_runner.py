#!/usr/bin/env python3
"""Run immutable K2 compile/directed/reset stages and publish a verified receipt."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import k2_local_receipt as receipt


SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
ASSIGNMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
REQUIRED_STAGES = ("compile", "directed_trace", "reset_drain")
OPTIONAL_STAGES = ("full50", "capacity22")
TOKEN = re.compile(r"^@K2_(TOOL|OUTPUT):([A-Za-z0-9_.-]+)@$")
FIXED_ENVIRONMENT = {"PATH": os.defpath, "LANG": "C", "LC_ALL": "C", "TZ": "UTC"}


class RunnerError(ValueError):
    pass


def _write_new(path: Path, payload: bytes, mode: int = 0o400) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_snapshot(source: Path, destination: Path, label: str,
                   executable: bool = False) -> tuple[str, tuple[int, int, int, int]]:
    payload, info = receipt.stable_read(source, label)
    if not payload:
        raise RunnerError(f"{label} is empty: {source}")
    if executable and not info.st_mode & 0o111:
        raise RunnerError(f"{label} is not executable: {source}")
    _write_new(destination, payload, 0o500 if executable else 0o400)
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return receipt.sha256_bytes(payload), identity


def _assignment(value: str, label: str, *, default: str | None = None) -> tuple[str, str]:
    name, separator, assigned = value.partition("=")
    if not separator:
        if default is None:
            raise argparse.ArgumentTypeError(f"{label} must be NAME=VALUE")
        assigned = default
    if not ASSIGNMENT_NAME.fullmatch(name) or not assigned or "\x00" in assigned:
        raise argparse.ArgumentTypeError(f"invalid {label}: {value!r}")
    return name, assigned


def _mapping(rows: list[tuple[str, str]], label: str) -> dict[str, str]:
    result = {}
    for key, value in rows:
        if key in result:
            raise RunnerError(f"duplicate {label}: {key}")
        result[key] = value
    return dict(sorted(result.items()))


def _read_filelist(path: Path) -> tuple[bytes, list[tuple[str, Path]]]:
    payload, _ = receipt.stable_read(path, "candidate filelist")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("candidate filelist is not UTF-8") from exc
    rows, seen = [], set()
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if any(character.isspace() for character in line) or line.startswith(("-", "+")):
            raise RunnerError(
                f"filelist line {number} must be one literal source path; flags/includes are explicit"
            )
        source = Path(line)
        if not source.is_absolute():
            source = path.parent / source
        if source.is_symlink():
            raise RunnerError(f"filelist line {number} names a symlink: {line}")
        source = source.resolve(strict=False)
        logical = line
        if logical in seen:
            raise RunnerError(f"duplicate candidate source in filelist: {logical}")
        seen.add(logical)
        rows.append((logical, source))
    if not rows:
        raise RunnerError("candidate filelist has no sources")
    return payload, rows


def _load_plan(path: Path) -> tuple[dict[str, Any], bytes]:
    document, payload = receipt.read_json(path, "command plan")
    if set(document) != {"schema_version", "tools", "stages"} or document.get("schema_version") != 1:
        raise RunnerError("command plan schema mismatch")
    tools = document.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise RunnerError("command plan tools must be a non-empty object")
    for name, spec in tools.items():
        if (not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or
                not isinstance(spec, dict) or set(spec) != {"version_argv"} or
                not isinstance(spec["version_argv"], list) or
                any(not isinstance(arg, str) or "\x00" in arg for arg in spec["version_argv"])):
            raise RunnerError(f"invalid command plan tool: {name!r}")
    stages = document.get("stages")
    if not isinstance(stages, list):
        raise RunnerError("command plan stages must be an array")
    names = []
    output_names = set()
    output_paths: list[Path] = []
    compile_build_names: set[str] = set()
    referenced_tools: set[str] = set()
    for position, stage in enumerate(stages):
        if not isinstance(stage, dict) or set(stage) != {"name", "optional", "argv", "outputs"}:
            raise RunnerError(f"command plan stages[{position}] schema mismatch")
        name = stage.get("name")
        if name not in (*REQUIRED_STAGES, *OPTIONAL_STAGES) or name in names:
            raise RunnerError(f"invalid or duplicate stage name: {name!r}")
        names.append(name)
        if stage.get("optional") != (name in OPTIONAL_STAGES):
            raise RunnerError(f"stage {name} optional flag mismatch")
        argv = stage.get("argv")
        if not isinstance(argv, list) or not argv or any(
                not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise RunnerError(f"stage {name} argv must be a non-empty string array")
        for argument in argv:
            match = TOKEN.fullmatch(argument)
            if match and match.group(1) == "TOOL":
                referenced_tools.add(match.group(2))
        outputs = stage.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise RunnerError(f"stage {name} must declare outputs")
        roles = []
        for output in outputs:
            if (not isinstance(output, dict) or set(output) != {"name", "path", "role", "kind"} or
                    not isinstance(output.get("name"), str) or
                    not SAFE_NAME.fullmatch(output["name"]) or output["name"] in output_names or
                    output.get("kind") not in {"file", "tree"} or
                    not isinstance(output.get("role"), str) or not output["role"]):
                raise RunnerError(f"stage {name} has invalid output declaration")
            relative = Path(receipt.relative_name(output.get("path"), f"stage {name} output path"))
            for prior in output_paths:
                if relative == prior or relative in prior.parents or prior in relative.parents:
                    raise RunnerError(f"stage output paths overlap: {relative} and {prior}")
            output_paths.append(relative)
            output_names.add(output["name"])
            roles.append(output["role"])
            if name == "compile" and output["role"] == "build":
                compile_build_names.add(output["name"])
        if name == "compile" and "build" not in roles:
            raise RunnerError("compile stage must declare a build output")
        if name != "compile" and roles.count("suite_result") != 1:
            raise RunnerError(f"suite {name} must declare exactly one suite_result")
        if name == "compile":
            mandatory = {"@K2_TOP@", "@K2_FILELIST@", "@K2_DEFINES@", "@K2_PARAMS@"}
            if not mandatory <= set(argv):
                raise RunnerError(
                    f"compile stage must consume explicit candidate tokens: {sorted(mandatory-set(argv))}"
                )
        else:
            build_tokens = {f"@K2_OUTPUT:{output_name}@" for output_name in compile_build_names}
            if not set(argv) & build_tokens:
                raise RunnerError(f"suite {name} does not consume a compiled build output")
        missing_output_tokens = {f"@K2_OUTPUT:{output['name']}@" for output in outputs} - set(argv)
        if missing_output_tokens:
            raise RunnerError(
                f"stage {name} does not receive its declared output paths: {sorted(missing_output_tokens)}"
            )
    expected = list(REQUIRED_STAGES) + [name for name in OPTIONAL_STAGES if name in names]
    if names != expected:
        raise RunnerError(f"command plan order must be {expected}, got {names}")
    if referenced_tools != set(tools):
        raise RunnerError(
            f"command plan tools must all be used and attached; unused={sorted(set(tools)-referenced_tools)}, "
            f"unknown={sorted(referenced_tools-set(tools))}"
        )
    return document, payload


def _assert_unchanged(rows: list[tuple[Path, str, tuple[int, int, int, int], str]]) -> None:
    for path, expected_sha, expected_identity, label in rows:
        payload, info = receipt.stable_read(path, label)
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if identity != expected_identity or receipt.sha256_bytes(payload) != expected_sha:
            raise RunnerError(f"{label} changed during execution: {path}")


def _artifact(path: str, digest: str) -> dict[str, str]:
    return {"path": path, "sha256": digest}


def _tree_files(root: Path, relative: Path, marker_mtime: int, label: str,
                kind: str) -> list[dict[str, str]]:
    target = root / relative
    if not target.exists() or target.is_symlink():
        raise RunnerError(f"{label} was not freshly produced: {target}")
    if kind == "file" and not target.is_file():
        raise RunnerError(f"{label} must be a file: {target}")
    if kind == "tree" and not target.is_dir():
        raise RunnerError(f"{label} must be a directory tree: {target}")
    paths = [target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file())
    if not paths:
        raise RunnerError(f"{label} is empty: {target}")
    rows = []
    for path in paths:
        payload, info = receipt.stable_read(path, label)
        if not payload:
            raise RunnerError(f"{label} contains an empty file: {path}")
        if info.st_mtime_ns <= marker_mtime:
            raise RunnerError(f"{label} is stale (not newer than stage marker): {path}")
        rows.append(_artifact(str(path.relative_to(root)), receipt.sha256_bytes(payload)))
    return rows


def _expand_argv(argv: list[str], *, tools: dict[str, Path], outputs: dict[str, Path],
                 root: Path, top: str, filelist: Path, sources: list[Path],
                 defines: dict[str, str], parameters: dict[str, str]) -> list[str]:
    scalar = {"@K2_TOP@": top, "@K2_FILELIST@": str(filelist),
              "@K2_RUN_DIR@": str(root)}
    expanded = []
    for argument in argv:
        if argument == "@K2_SOURCES@":
            expanded.extend(map(str, sources)); continue
        if argument == "@K2_DEFINES@":
            expanded.extend(f"-D{name}={value}" for name, value in defines.items()); continue
        if argument == "@K2_PARAMS@":
            expanded.extend(f"-G{name}={value}" for name, value in parameters.items()); continue
        match = TOKEN.fullmatch(argument)
        if match:
            table = tools if match.group(1) == "TOOL" else outputs
            if match.group(2) not in table:
                raise RunnerError(f"unknown command token: {argument}")
            expanded.append(str(table[match.group(2)])); continue
        for token, value in scalar.items():
            argument = argument.replace(token, value)
        if "@K2_" in argument:
            raise RunnerError(f"unknown or embedded list command token: {argument}")
        expanded.append(argument)
    first = Path(expanded[0]).resolve(strict=False)
    allowed = {path.resolve() for path in tools.values()} | {
        path.resolve(strict=False) for path in outputs.values()
    }
    if first not in allowed:
        raise RunnerError("command executable must be an attached tool or a declared prior output")
    return expanded


def _run_command(argv: list[str], log: Path, env: dict[str, str], cwd: Path,
                 timeout_seconds: int) -> None:
    _write_new(log, b"", 0o600)
    with log.open("r+b") as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, close_fds=True,
                                   start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            stream.flush(); os.fsync(stream.fileno())
            log.chmod(0o400)
            raise RunnerError(
                f"command timed out after {timeout_seconds}s: {argv[0]} (log {log})"
            ) from exc
        stream.flush(); os.fsync(stream.fileno())
    log.chmod(0o400)
    if returncode:
        raise RunnerError(f"command exited {returncode}: {argv[0]} (log {log})")
    if not log.stat().st_size:
        raise RunnerError(f"command emitted an empty log: {log}")


def _validate_suite_result(path: Path, stage: str, command_sha: str,
                           input_sha: str, challenge: str) -> None:
    document, _ = receipt.read_json(path, f"{stage} suite result")
    expected = {"schema_version", "suite", "status", "stage_command_sha256",
                "stage_input_sha256", "execution_challenge", "checks"}
    if set(document) != expected or document.get("schema_version") != 1:
        raise RunnerError(f"{stage} suite result schema mismatch")
    if (document.get("suite") != stage or document.get("status") != "PASS" or
            document.get("stage_command_sha256") != command_sha or
            document.get("stage_input_sha256") != input_sha or
            document.get("execution_challenge") != challenge):
        raise RunnerError(f"{stage} suite result is fabricated, stale, or bound to another command")
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks:
        raise RunnerError(f"{stage} suite result is sentinel-only")
    for position, check in enumerate(checks):
        if (not isinstance(check, dict) or set(check) != {"name", "status", "evidence"} or
                not isinstance(check.get("name"), str) or not check["name"] or
                check.get("status") != "PASS" or not isinstance(check.get("evidence"), dict) or
                not check["evidence"]):
            raise RunnerError(f"{stage} check[{position}] is not substantive PASS evidence")


def _all_regular_files(root: Path) -> set[str]:
    result = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RunnerError(f"attempt contains a symlink: {path}")
        if path.is_file():
            result.add(str(path.relative_to(root)))
    return result


def run(args: argparse.Namespace) -> Path:
    if not SAFE_NAME.fullmatch(args.candidate):
        raise RunnerError("candidate name is not a safe identifier")
    if not SV_NAME.fullmatch(args.top):
        raise RunnerError("top is not a SystemVerilog identifier")
    defines = _mapping(args.define, "define")
    parameters = _mapping(args.param, "parameter")
    explicit_environment = _mapping(args.env, "environment variable")
    reserved_environment = set(FIXED_ENVIRONMENT) | {"TMPDIR"}
    reserved_environment.update(name for name in explicit_environment if name.startswith("K2_"))
    conflicts = set(explicit_environment) & reserved_environment
    if conflicts:
        raise RunnerError(f"environment attempts to override reserved keys: {sorted(conflicts)}")
    if parameters.get("RETIRE_LANES") != "2":
        raise RunnerError("explicit --param RETIRE_LANES=2 is mandatory")
    plan, plan_payload = _load_plan(args.command_plan)
    tools_input = _mapping(args.tool, "tool")
    if set(tools_input) != set(plan["tools"]):
        raise RunnerError("explicit tools must exactly match command plan tools")
    enabled = set(args.enable_suite)
    plan_names = {stage["name"] for stage in plan["stages"]}
    if not enabled <= plan_names:
        raise RunnerError(f"enabled suite is absent from command plan: {sorted(enabled-plan_names)}")
    stages = [stage for stage in plan["stages"]
              if not stage["optional"] or stage["name"] in enabled]

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink() or not output_root.is_dir():
        raise RunnerError("output root must be a real directory")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + secrets.token_hex(8)
    root = output_root / f".incomplete-{run_id}"
    final_root = output_root / f"attempt-{run_id}"
    root.mkdir(mode=0o700)
    args._attempt_root = root
    temporary = root / "tmp"
    temporary.mkdir(mode=0o700)
    base_environment = {**FIXED_ENVIRONMENT, "TMPDIR": str(temporary), **explicit_environment}

    immutable_originals: list[tuple[Path, str, tuple[int, int, int, int], str]] = []
    filelist_payload, source_rows = _read_filelist(args.filelist)
    filelist_sha, filelist_identity = _copy_snapshot(
        args.filelist, root / "candidate" / "original.filelist", "candidate filelist")
    if receipt.sha256_bytes(filelist_payload) != filelist_sha:
        raise RunnerError("candidate filelist changed between parse and snapshot")
    immutable_originals.append((args.filelist, filelist_sha, filelist_identity, "candidate filelist"))
    source_receipt, source_paths = [], []
    for position, (logical, source) in enumerate(source_rows):
        suffix = source.suffix if source.suffix and len(source.suffix) <= 16 else ".src"
        relative = Path("candidate") / "sources" / f"{position:04d}{suffix}"
        digest, identity = _copy_snapshot(source, root / relative, f"candidate source {logical}")
        immutable_originals.append((source, digest, identity, f"candidate source {logical}"))
        source_receipt.append({"logical_path": logical, "artifact": _artifact(str(relative), digest)})
        source_paths.append(root / relative)
    snapshot_filelist = root / "candidate" / "snapshot.filelist"
    snapshot_source_names = [str(path.relative_to(root)) for path in source_paths]
    _write_new(snapshot_filelist, ("\n".join(snapshot_source_names) + "\n").encode())
    snapshot_filelist_sha = receipt.sha256_bytes(snapshot_filelist.read_bytes())
    source_binding = [{"logical_path": row["logical_path"],
                       "sha256": row["artifact"]["sha256"]} for row in source_receipt]
    source_bundle_sha = receipt.sha256_bytes(receipt.canonical_bytes(source_binding))

    plan_path = root / "inputs" / "command-plan.json"
    _write_new(plan_path, plan_payload)
    plan_sha = receipt.sha256_bytes(plan_payload)
    orchestrator_receipt = []
    for name, original in (("runner", Path(__file__).resolve()),
                           ("verifier", Path(receipt.__file__).resolve())):
        attached = root / "orchestrator" / f"{name}.py"
        digest, identity = _copy_snapshot(original, attached, f"A1 K2 {name}")
        immutable_originals.append((original, digest, identity, f"A1 K2 {name}"))
        orchestrator_receipt.append({"name": name,
                                     "artifact": _artifact(str(attached.relative_to(root)), digest)})
    tool_paths: dict[str, Path] = {}
    tool_receipt = []
    for name in sorted(tools_input):
        original_input = Path(tools_input[name])
        if original_input.is_symlink():
            raise RunnerError(f"tool {name} must not be a symlink")
        original = original_input.resolve(strict=False)
        executable = root / "tools" / name / "executable"
        digest, identity = _copy_snapshot(original, executable, f"tool {name}", executable=True)
        immutable_originals.append((original, digest, identity, f"tool {name}"))
        tool_paths[name] = executable
        version_argv = [str(executable), *plan["tools"][name]["version_argv"]]
        version_command = root / "tools" / name / "version-command.json"
        version_document = {"schema_version": 1, "tool": name, "argv": version_argv,
                            "environment": base_environment}
        _write_new(version_command, receipt.canonical_bytes(version_document))
        version_output = root / "tools" / name / "version.txt"
        _run_command(version_argv, version_output, base_environment, root, args.timeout_seconds)
        tool_receipt.append({"name": name, "executable": _artifact(
            str(executable.relative_to(root)), digest), "version_output": _artifact(
            str(version_output.relative_to(root)), receipt.sha256_bytes(version_output.read_bytes())),
            "version_command": _artifact(str(version_command.relative_to(root)),
                                          receipt.sha256_bytes(version_command.read_bytes()))})
    _assert_unchanged(immutable_originals)

    candidate_record = {"name": args.candidate, "top": args.top, "retire_lanes": 2,
        "defines": defines, "parameters": parameters,
        "filelist": _artifact("candidate/original.filelist", filelist_sha),
        "snapshot_filelist": _artifact("candidate/snapshot.filelist", snapshot_filelist_sha),
        "source_bundle_sha256": source_bundle_sha, "sources": source_receipt}
    candidate_input_sha = receipt.sha256_bytes(receipt.canonical_bytes(candidate_record))
    output_paths: dict[str, Path] = {}
    output_records: list[dict[str, Any]] = []
    command_records = []
    for ordinal, stage in enumerate(stages):
        stage_name = stage["name"]
        for spec in stage["outputs"]:
            output_paths[spec["name"]] = root / spec["path"]
        argv = _expand_argv(stage["argv"], tools=tool_paths,
                            outputs={name: path for name, path in output_paths.items()
                                     if path.exists() or any(spec["name"] == name for spec in stage["outputs"])},
                            root=root, top=args.top, filelist=snapshot_filelist,
                            sources=source_paths, defines=defines, parameters=parameters)
        prior_binding = [{"name": row["name"], "sha256": row["sha256"]}
                         for row in output_records]
        input_document = {"candidate_sha256": candidate_input_sha, "plan_sha256": plan_sha,
                          "orchestrator": orchestrator_receipt,
                          "tools": [{"name": row["name"],
                                     "sha256": row["executable"]["sha256"]}
                                    for row in tool_receipt],
                          "prior_outputs": prior_binding}
        input_sha = receipt.sha256_bytes(receipt.canonical_bytes(input_document))
        challenge = secrets.token_hex(32)
        descriptor = {"schema_version": 1, "stage": stage_name, "ordinal": ordinal,
                      "argv": argv, "input_sha256": input_sha,
                      "execution_challenge": challenge,
                      "environment": base_environment,
                      "declared_outputs": stage["outputs"]}
        command_path = root / "commands" / f"{ordinal:02d}-{stage_name}.command.json"
        _write_new(command_path, receipt.canonical_bytes(descriptor))
        command_sha = receipt.sha256_bytes(command_path.read_bytes())
        marker = root / "commands" / f"{ordinal:02d}-{stage_name}.freshness"
        _write_new(marker, secrets.token_bytes(32))
        marker_mtime = marker.stat().st_mtime_ns
        environment = dict(base_environment)
        environment.update({"K2_RUN_ID": run_id, "K2_STAGE_NAME": stage_name,
            "K2_STAGE_COMMAND_SHA256": command_sha, "K2_STAGE_INPUT_SHA256": input_sha,
            "K2_STAGE_CHALLENGE": challenge, "K2_CANDIDATE_TOP": args.top,
            "K2_CANDIDATE_FILELIST": str(snapshot_filelist),
            "K2_CANDIDATE_DEFINES_JSON": json.dumps(defines, sort_keys=True),
            "K2_CANDIDATE_PARAMS_JSON": json.dumps(parameters, sort_keys=True)})
        log = root / "commands" / f"{ordinal:02d}-{stage_name}.log"
        _run_command(argv, log, environment, root, args.timeout_seconds)
        stage_outputs = []
        for spec in stage["outputs"]:
            relative = Path(spec["path"])
            rows = _tree_files(root, relative, marker_mtime,
                               f"{stage_name} output {spec['name']}", spec["kind"])
            record = {"name": spec["name"], "role": spec["role"], "kind": spec["kind"],
                      "files": rows, "sha256": receipt.sha256_bytes(receipt.canonical_bytes(rows))}
            if spec["kind"] == "file" and (len(rows) != 1 or rows[0]["path"] != str(relative)):
                raise RunnerError(f"declared file output is not exactly one file: {relative}")
            stage_outputs.append(record)
            output_records.append(record)
            if spec["role"] == "suite_result":
                _validate_suite_result(root / relative, stage_name, command_sha, input_sha, challenge)
        marker.unlink()
        _assert_unchanged(immutable_originals)
        command_records.append({"stage": stage_name, "ordinal": ordinal,
            "command": _artifact(str(command_path.relative_to(root)), command_sha),
            "log": _artifact(str(log.relative_to(root)), receipt.sha256_bytes(log.read_bytes())),
            "input_sha256": input_sha, "execution_challenge": challenge,
            "outputs": stage_outputs})

    # Detect post-run mutation of any output, source, tool, or command evidence.
    for record in output_records:
        current = []
        for artifact in record["files"]:
            payload, _ = receipt.stable_read(root / artifact["path"], "final stage output")
            current.append(_artifact(artifact["path"], receipt.sha256_bytes(payload)))
        if current != record["files"] or receipt.sha256_bytes(receipt.canonical_bytes(current)) != record["sha256"]:
            raise RunnerError(f"stage output changed after validation: {record['name']}")
    _assert_unchanged(immutable_originals)

    receipt_document = {"schema_version": receipt.SCHEMA_VERSION, "kind": receipt.RECEIPT_KIND,
        "status": "PASS", "run_id": run_id, "candidate": candidate_record,
        "plan": _artifact("inputs/command-plan.json", plan_sha),
        "orchestrator": orchestrator_receipt, "tools": tool_receipt,
        "commands": command_records,
        "bundle_manifest": {"path": "bundle.manifest.json", "sha256": "0" * 64}}
    referenced = set()
    def collect_artifact(row: dict[str, str]) -> None:
        referenced.add(row["path"])
    collect_artifact(candidate_record["filelist"]); collect_artifact(candidate_record["snapshot_filelist"])
    for row in candidate_record["sources"]: collect_artifact(row["artifact"])
    collect_artifact(receipt_document["plan"])
    for row in orchestrator_receipt: collect_artifact(row["artifact"])
    for row in tool_receipt:
        collect_artifact(row["executable"]); collect_artifact(row["version_output"]); collect_artifact(row["version_command"])
    for row in command_records:
        collect_artifact(row["command"]); collect_artifact(row["log"])
        for output in row["outputs"]:
            for artifact in output["files"]: collect_artifact(artifact)
    shutil.rmtree(temporary)
    actual = _all_regular_files(root)
    if actual != referenced:
        raise RunnerError(f"commands produced undeclared files; undeclared={sorted(actual-referenced)}, missing={sorted(referenced-actual)}")
    manifest_rows = []
    for relative in sorted(referenced):
        payload, _ = receipt.stable_read(root / relative, "bundle artifact")
        manifest_rows.append(_artifact(relative, receipt.sha256_bytes(payload)))
    manifest_path = root / "bundle.manifest.json"
    _write_new(manifest_path, receipt.canonical_bytes({"schema_version": 1, "files": manifest_rows}))
    receipt_document["bundle_manifest"]["sha256"] = receipt.sha256_bytes(manifest_path.read_bytes())
    receipt_path = root / "receipt.json"
    _write_new(receipt_path, receipt.canonical_bytes(receipt_document))
    try:
        receipt.verify_bundle(root)
    except (OSError, receipt.ReceiptError):
        receipt_path.unlink()
        raise
    os.rename(root, final_root)
    directory_fd = os.open(output_root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return final_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--filelist", required=True, type=Path)
    parser.add_argument("--define", action="append", default=[],
                        type=lambda value: _assignment(value, "define", default="1"))
    parser.add_argument("--param", action="append", default=[],
                        type=lambda value: _assignment(value, "parameter"))
    parser.add_argument("--tool", action="append", default=[], required=True,
                        type=lambda value: _assignment(value, "tool"))
    parser.add_argument("--env", action="append", default=[],
                        type=lambda value: _assignment(value, "environment variable"))
    parser.add_argument("--command-plan", required=True, type=Path)
    parser.add_argument("--enable-suite", action="append", default=[], choices=OPTIONAL_STAGES)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        path = run(args)
    except (OSError, RunnerError, receipt.ReceiptError) as exc:
        attempt = getattr(args, "_attempt_root", None)
        if attempt is not None and attempt.is_dir() and not (attempt / "receipt.json").exists():
            try:
                _write_new(attempt / "failure.json", receipt.canonical_bytes({
                    "schema_version": 1, "status": "FAIL", "error": str(exc)}))
            except OSError:
                pass
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = receipt.verify_bundle(path)
    print("K2_LOCAL_RUN_PASS " + json.dumps({"bundle": str(path), **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
