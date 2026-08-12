#!/usr/bin/env python3
"""Verify a self-contained, fail-closed A1 K2 local-run receipt bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
RECEIPT_KIND = "a1-k2-local-receipt"
SHA256_LENGTH = 64
PLAN_TOKEN = re.compile(r"^@K2_(TOOL|OUTPUT):([A-Za-z0-9_.-]+)@$")


class ReceiptError(ValueError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("utf-8")


def stable_read(path: Path, label: str) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReceiptError(f"{label} is not a regular non-symlink file: {path}")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            payload = stream.read()
            after_read = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise ReceiptError(f"cannot read {label} {path}: {exc}") from exc
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if not (identity(before) == identity(opened) == identity(after_read) == identity(after)):
        raise ReceiptError(f"{label} changed while being read: {path}")
    if before.st_nlink != 1:
        raise ReceiptError(f"{label} must not be hard linked: {path}")
    return payload, before


def checked_sha(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != SHA256_LENGTH or
            any(character not in "0123456789abcdef" for character in value)):
        raise ReceiptError(f"{label} is not a lowercase SHA256 digest")
    return value


def relative_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise ReceiptError(f"{label} must be a normalized contained relative path")
    return value


def contained(root: Path, value: Any, label: str) -> Path:
    relative = Path(relative_name(value, label))
    path = root / relative
    component = root
    for part in relative.parts:
        component /= part
        try:
            if component.is_symlink():
                raise ReceiptError(f"{label} traverses a symlink: {relative}")
        except OSError as exc:
            raise ReceiptError(f"cannot inspect {label}: {exc}") from exc
    return path


def read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    payload, _ = stable_read(path, label)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptError(f"{label} must be a JSON object")
    return value, payload


def _artifact_ref(row: Any, label: str, manifest: dict[str, str]) -> str:
    if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
        raise ReceiptError(f"{label} must contain exactly path and sha256")
    path = relative_name(row["path"], f"{label}.path")
    digest = checked_sha(row["sha256"], f"{label}.sha256")
    if manifest.get(path) != digest:
        raise ReceiptError(f"{label} is unattached or disagrees with bundle manifest: {path}")
    return path


def _check_result(root: Path, row: dict[str, Any], command_sha: str,
                  input_sha: str, manifest: dict[str, str], referenced: set[str]) -> None:
    result_rows = [item for item in row["outputs"] if item.get("role") == "suite_result"]
    if len(result_rows) != 1:
        raise ReceiptError(f"suite {row['stage']} must have exactly one suite_result")
    result_row = result_rows[0]
    if result_row.get("kind") != "file" or set(result_row) != {
            "name", "role", "kind", "files", "sha256"}:
        raise ReceiptError(f"suite {row['stage']} result descriptor is malformed")
    if len(result_row["files"]) != 1:
        raise ReceiptError(f"suite {row['stage']} result must be one attached file")
    path = _artifact_ref(result_row["files"][0], "suite result", manifest)
    referenced.add(path)
    document, _ = read_json(contained(root, path, "suite result path"), "suite result")
    expected = {"schema_version", "suite", "status", "stage_command_sha256",
                "stage_input_sha256", "execution_challenge", "checks"}
    if set(document) != expected or document.get("schema_version") != 1:
        raise ReceiptError(f"suite {row['stage']} result schema mismatch")
    if document.get("suite") != row["stage"] or document.get("status") != "PASS":
        raise ReceiptError(f"suite {row['stage']} did not report structured PASS")
    if document.get("stage_command_sha256") != command_sha:
        raise ReceiptError(f"suite {row['stage']} command binding mismatch")
    if document.get("stage_input_sha256") != input_sha:
        raise ReceiptError(f"suite {row['stage']} input binding mismatch")
    if document.get("execution_challenge") != row.get("execution_challenge"):
        raise ReceiptError(f"suite {row['stage']} freshness challenge mismatch")
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ReceiptError(f"suite {row['stage']} is sentinel-only: no checks")
    for position, check in enumerate(checks):
        if (not isinstance(check, dict) or set(check) != {"name", "status", "evidence"} or
                not isinstance(check.get("name"), str) or not check["name"] or
                check.get("status") != "PASS" or not isinstance(check.get("evidence"), dict) or
                not check["evidence"]):
            raise ReceiptError(f"suite {row['stage']} check[{position}] is not substantive PASS evidence")


def verify_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise ReceiptError(f"bundle root is not a real directory: {root}")
    receipt, receipt_payload = read_json(root / "receipt.json", "receipt")
    required = {"schema_version", "kind", "status", "run_id", "candidate", "plan",
                "orchestrator", "tools", "commands", "bundle_manifest"}
    if set(receipt) != required or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ReceiptError("receipt schema mismatch")
    if receipt.get("kind") != RECEIPT_KIND or receipt.get("status") != "PASS":
        raise ReceiptError("receipt is not an A1 K2 PASS receipt")

    manifest_ref = receipt.get("bundle_manifest")
    if not isinstance(manifest_ref, dict) or set(manifest_ref) != {"path", "sha256"}:
        raise ReceiptError("bundle_manifest reference schema mismatch")
    manifest_path = contained(root, manifest_ref["path"], "bundle manifest path")
    manifest_doc, manifest_payload = read_json(manifest_path, "bundle manifest")
    if sha256_bytes(manifest_payload) != checked_sha(manifest_ref["sha256"], "bundle manifest hash"):
        raise ReceiptError("bundle manifest SHA256 mismatch")
    if set(manifest_doc) != {"schema_version", "files"} or manifest_doc.get("schema_version") != 1:
        raise ReceiptError("bundle manifest schema mismatch")
    rows = manifest_doc.get("files")
    if not isinstance(rows, list) or not rows:
        raise ReceiptError("bundle manifest has no files")
    manifest: dict[str, str] = {}
    inodes: set[tuple[int, int]] = set()
    for position, item in enumerate(rows):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ReceiptError(f"bundle manifest files[{position}] schema mismatch")
        path = relative_name(item["path"], f"bundle manifest files[{position}].path")
        if path in manifest or path in {"receipt.json", manifest_ref["path"]}:
            raise ReceiptError(f"duplicate or recursive manifest path: {path}")
        digest = checked_sha(item["sha256"], f"bundle manifest files[{position}].sha256")
        payload, info = stable_read(contained(root, path, "manifest file"), "manifest file")
        if (info.st_dev, info.st_ino) in inodes:
            raise ReceiptError(f"bundle files reuse an inode: {path}")
        inodes.add((info.st_dev, info.st_ino))
        if sha256_bytes(payload) != digest:
            raise ReceiptError(f"bundle file SHA256 mismatch: {path}")
        manifest[path] = digest

    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReceiptError(f"bundle contains symlink: {path.relative_to(root)}")
        if path.is_file():
            actual.add(str(path.relative_to(root)))
    expected_actual = set(manifest) | {"receipt.json", manifest_ref["path"]}
    if actual != expected_actual:
        raise ReceiptError(
            f"partial or unmanifested bundle; missing={sorted(expected_actual-actual)}, "
            f"extra={sorted(actual-expected_actual)}"
        )

    referenced: set[str] = set()
    candidate = receipt.get("candidate")
    expected_candidate = {"name", "top", "retire_lanes", "defines", "parameters",
                          "filelist", "snapshot_filelist", "source_bundle_sha256", "sources"}
    if not isinstance(candidate, dict) or set(candidate) != expected_candidate:
        raise ReceiptError("candidate receipt schema mismatch")
    if (not isinstance(candidate.get("name"), str) or not candidate["name"] or
            not isinstance(candidate.get("top"), str) or not candidate["top"] or
            not isinstance(candidate.get("defines"), dict) or
            not isinstance(candidate.get("parameters"), dict) or
            any(not isinstance(key, str) or not isinstance(value, str)
                for mapping in (candidate["defines"], candidate["parameters"])
                for key, value in mapping.items())):
        raise ReceiptError("candidate identity/define/parameter schema mismatch")
    if candidate.get("retire_lanes") != 2 or candidate.get("parameters", {}).get("RETIRE_LANES") != "2":
        raise ReceiptError("receipt is not bound to RETIRE_LANES=2")
    for label in ("filelist", "snapshot_filelist"):
        referenced.add(_artifact_ref(candidate[label], f"candidate {label}", manifest))
    sources = candidate.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ReceiptError("candidate has no attached sources")
    canonical_sources = []
    seen_logical = set()
    for position, source in enumerate(sources):
        if not isinstance(source, dict) or set(source) != {"logical_path", "artifact"}:
            raise ReceiptError(f"candidate sources[{position}] schema mismatch")
        logical = source.get("logical_path")
        if not isinstance(logical, str) or not logical or logical in seen_logical:
            raise ReceiptError("candidate logical source paths must be nonempty and unique")
        seen_logical.add(logical)
        path = _artifact_ref(source["artifact"], f"candidate sources[{position}]", manifest)
        referenced.add(path)
        canonical_sources.append({"logical_path": logical, "sha256": source["artifact"]["sha256"]})
    if sha256_bytes(canonical_bytes(canonical_sources)) != checked_sha(
            candidate.get("source_bundle_sha256"), "source bundle hash"):
        raise ReceiptError("source bundle hash does not bind the ordered attached sources")
    snapshot_payload, _ = stable_read(
        contained(root, candidate["snapshot_filelist"]["path"], "snapshot filelist path"),
        "snapshot filelist")
    try:
        snapshot_lines = snapshot_payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReceiptError("snapshot filelist is not UTF-8") from exc
    expected_snapshot = [source["artifact"]["path"] for source in sources]
    if snapshot_lines != expected_snapshot:
        raise ReceiptError("snapshot filelist does not exactly bind ordered source artifacts")

    plan_path = _artifact_ref(receipt["plan"], "plan", manifest)
    referenced.add(plan_path)
    plan_doc, _ = read_json(contained(root, plan_path, "plan path"), "command plan")
    if (set(plan_doc) != {"schema_version", "tools", "stages"} or
            plan_doc.get("schema_version") != 1 or not isinstance(plan_doc.get("stages"), list)):
        raise ReceiptError("attached command plan schema mismatch")
    orchestrator = receipt.get("orchestrator")
    if (not isinstance(orchestrator, list) or
            [row.get("name") if isinstance(row, dict) else None for row in orchestrator]
            != ["runner", "verifier"]):
        raise ReceiptError("orchestrator closure must attach runner and verifier")
    for position, row in enumerate(orchestrator):
        if not isinstance(row, dict) or set(row) != {"name", "artifact"}:
            raise ReceiptError(f"orchestrator[{position}] schema mismatch")
        referenced.add(_artifact_ref(row["artifact"], f"orchestrator {row['name']}", manifest))
    tools = receipt.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ReceiptError("receipt has no attached tools")
    tool_names = set()
    tool_bindings = []
    execution_environment: dict[str, str] | None = None
    tool_artifacts: dict[str, str] = {}
    plan_tools = plan_doc.get("tools")
    if not isinstance(plan_tools, dict):
        raise ReceiptError("attached plan tools must be an object")
    for position, tool in enumerate(tools):
        if not isinstance(tool, dict) or set(tool) != {
                "name", "executable", "version_output", "version_command"}:
            raise ReceiptError(f"tools[{position}] schema mismatch")
        name = tool.get("name")
        if not isinstance(name, str) or not name or name in tool_names:
            raise ReceiptError("tool names must be nonempty and unique")
        tool_names.add(name)
        tool_artifacts[name] = tool["executable"]["path"]
        for label in ("executable", "version_output", "version_command"):
            referenced.add(_artifact_ref(tool[label], f"tool {name} {label}", manifest))
        version_doc, _ = read_json(
            contained(root, tool["version_command"]["path"], "tool version command path"),
            "tool version command")
        expected_version_doc = {"schema_version", "tool", "argv", "environment"}
        if (set(version_doc) != expected_version_doc or version_doc.get("schema_version") != 1 or
                version_doc.get("tool") != name or
                not isinstance(version_doc.get("argv"), list) or not version_doc["argv"]):
            raise ReceiptError(f"tool {name} version command schema mismatch")
        environment = version_doc.get("environment")
        if (not isinstance(environment, dict) or
                any(not isinstance(key, str) or not isinstance(value, str)
                    for key, value in environment.items())):
            raise ReceiptError(f"tool {name} version environment schema mismatch")
        if execution_environment is None:
            execution_environment = environment
        elif environment != execution_environment:
            raise ReceiptError("tool version commands do not share one exact environment")
        version_payload, _ = stable_read(
            contained(root, tool["version_output"]["path"], "tool version output path"),
            "tool version output")
        if not version_payload:
            raise ReceiptError(f"tool {name} version output is empty")
        tool_bindings.append({"name": name, "sha256": tool["executable"]["sha256"]})
    if tool_names != set(plan_tools):
        raise ReceiptError("attached tools do not exactly match command plan tools")
    if execution_environment is None:
        raise ReceiptError("execution environment is unavailable")
    temporary_name = execution_environment.get("TMPDIR")
    if not isinstance(temporary_name, str) or Path(temporary_name).name != "tmp":
        raise ReceiptError("recorded execution TMPDIR is malformed")
    recorded_root = Path(temporary_name).parent
    if recorded_root.name != f".incomplete-{receipt['run_id']}":
        raise ReceiptError("recorded execution root does not bind the receipt run_id")
    for tool in tools:
        version_doc, _ = read_json(
            contained(root, tool["version_command"]["path"], "tool version command path"),
            "tool version command")
        plan_tool = plan_tools.get(tool["name"])
        if (not isinstance(plan_tool, dict) or set(plan_tool) != {"version_argv"} or
                not isinstance(plan_tool.get("version_argv"), list) or
                any(not isinstance(argument, str) for argument in plan_tool["version_argv"]) or
                version_doc["argv"] != [str(recorded_root / tool["executable"]["path"]),
                                        *plan_tool["version_argv"]]):
            raise ReceiptError(f"tool {tool['name']} version argv differs from attached plan")

    commands = receipt.get("commands")
    if not isinstance(commands, list) or len(commands) < 3:
        raise ReceiptError("receipt must contain compile, directed_trace, and reset_drain commands")
    names = [row.get("stage") if isinstance(row, dict) else None for row in commands]
    required_prefix = ["compile", "directed_trace", "reset_drain"]
    if names[:3] != required_prefix or names[3:] not in ([], ["full50"], ["capacity22"],
                                                         ["full50", "capacity22"]):
        raise ReceiptError(f"suite order is not fail-closed: {names}")
    plan_stages = plan_doc["stages"]
    if not all(isinstance(stage, dict) for stage in plan_stages):
        raise ReceiptError("attached command plan contains a malformed stage")
    plan_by_name = {stage.get("name"): stage for stage in plan_stages}
    if len(plan_by_name) != len(plan_stages) or any(name not in plan_by_name for name in names):
        raise ReceiptError("receipt stages do not map uniquely to the attached plan")
    all_output_paths = {}
    for stage in plan_stages:
        outputs = stage.get("outputs")
        if not isinstance(outputs, list):
            raise ReceiptError("attached plan stage outputs must be an array")
        for output in outputs:
            if (not isinstance(output, dict) or set(output) != {"name", "path", "role", "kind"} or
                    not isinstance(output.get("name"), str) or output["name"] in all_output_paths):
                raise ReceiptError("attached plan contains malformed or duplicate outputs")
            relative_name(output.get("path"), "plan output path")
            all_output_paths[output["name"]] = str(recorded_root / output["path"])

    def expand_plan_argv(raw: Any) -> list[str]:
        if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
            raise ReceiptError("attached plan argv is malformed")
        scalar = {"@K2_TOP@": candidate["top"],
                  "@K2_FILELIST@": str(recorded_root / candidate["snapshot_filelist"]["path"]),
                  "@K2_RUN_DIR@": str(recorded_root)}
        expanded = []
        for argument in raw:
            if argument == "@K2_SOURCES@":
                expanded.extend(str(recorded_root / source["artifact"]["path"])
                                for source in sources)
                continue
            if argument == "@K2_DEFINES@":
                expanded.extend(f"-D{name}={value}" for name, value in candidate["defines"].items())
                continue
            if argument == "@K2_PARAMS@":
                expanded.extend(f"-G{name}={value}" for name, value in candidate["parameters"].items())
                continue
            match = PLAN_TOKEN.fullmatch(argument)
            if match:
                table = tool_artifacts if match.group(1) == "TOOL" else all_output_paths
                key = match.group(2)
                if key not in table:
                    raise ReceiptError(f"attached plan has unknown token: {argument}")
                value = table[key]
                if match.group(1) == "TOOL":
                    value = str(recorded_root / value)
                expanded.append(value)
                continue
            for token, value in scalar.items():
                argument = argument.replace(token, value)
            if "@K2_" in argument:
                raise ReceiptError(f"attached plan has unknown token: {argument}")
            expanded.append(argument)
        return expanded
    previous_outputs: list[dict[str, str]] = []
    candidate_sha = sha256_bytes(canonical_bytes(candidate))
    for position, command in enumerate(commands):
        expected_command = {"stage", "ordinal", "command", "log", "input_sha256",
                            "execution_challenge", "outputs"}
        if not isinstance(command, dict) or set(command) != expected_command:
            raise ReceiptError(f"commands[{position}] schema mismatch")
        if command.get("ordinal") != position:
            raise ReceiptError("command ordinals are not contiguous")
        command_path = _artifact_ref(command["command"], "command descriptor", manifest)
        log_path = _artifact_ref(command["log"], "command log", manifest)
        referenced.update((command_path, log_path))
        descriptor, descriptor_payload = read_json(contained(root, command_path, "command path"),
                                                   "command descriptor")
        expected_descriptor = {"schema_version", "stage", "ordinal", "argv", "input_sha256",
                               "execution_challenge", "environment", "declared_outputs"}
        if (set(descriptor) != expected_descriptor or descriptor.get("schema_version") != 1 or
                not isinstance(descriptor.get("argv"), list) or not descriptor["argv"] or
                any(not isinstance(argument, str) or not argument for argument in descriptor["argv"])):
            raise ReceiptError("command descriptor schema mismatch")
        if descriptor.get("environment") != execution_environment:
            raise ReceiptError("command environment differs from attached tool environment")
        command_sha = sha256_bytes(descriptor_payload)
        if command_sha != command["command"]["sha256"]:
            raise ReceiptError("command descriptor hash mismatch")
        if descriptor.get("stage") != command["stage"] or descriptor.get("ordinal") != position:
            raise ReceiptError("command descriptor identity mismatch")
        if descriptor.get("input_sha256") != command["input_sha256"]:
            raise ReceiptError("command input hash mismatch")
        if descriptor.get("execution_challenge") != command["execution_challenge"]:
            raise ReceiptError("command challenge mismatch")
        if descriptor.get("declared_outputs") != plan_by_name[command["stage"]].get("outputs"):
            raise ReceiptError("command declared outputs differ from attached plan")
        if descriptor.get("argv") != expand_plan_argv(plan_by_name[command["stage"]].get("argv")):
            raise ReceiptError("command argv is not the exact attached-plan expansion")
        expected_input = sha256_bytes(canonical_bytes({
            "candidate_sha256": candidate_sha,
            "plan_sha256": receipt["plan"]["sha256"],
            "orchestrator": orchestrator,
            "tools": tool_bindings,
            "prior_outputs": previous_outputs,
        }))
        if command["input_sha256"] != expected_input:
            raise ReceiptError(f"command {command['stage']} has an unattached input hash")
        log_payload, _ = stable_read(contained(root, log_path, "command log path"), "command log")
        if not log_payload:
            raise ReceiptError(f"command {command['stage']} log is empty")
        outputs = command.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ReceiptError(f"command {command['stage']} has no attached outputs")
        for output in outputs:
            if (not isinstance(output, dict) or set(output) !=
                    {"name", "role", "kind", "files", "sha256"} or
                    output.get("kind") not in {"file", "tree"} or
                    not isinstance(output.get("files"), list) or not output["files"]):
                raise ReceiptError(f"command {command['stage']} output schema mismatch")
            file_rows = []
            for artifact in output["files"]:
                artifact_path = _artifact_ref(artifact, "command output", manifest)
                referenced.add(artifact_path)
                file_rows.append(artifact)
            if sha256_bytes(canonical_bytes(file_rows)) != output["sha256"]:
                raise ReceiptError(f"command {command['stage']} output tree hash mismatch")
            plan_output = next((item for item in plan_by_name[command["stage"]]["outputs"]
                                if item.get("name") == output["name"]), None)
            if (plan_output is None or output["role"] != plan_output.get("role") or
                    output["kind"] != plan_output.get("kind")):
                raise ReceiptError("receipt output does not match attached plan declaration")
            planned_path = plan_output["path"]
            paths = [item["path"] for item in file_rows]
            if output["kind"] == "file" and paths != [planned_path]:
                raise ReceiptError("file output path differs from attached plan")
            if output["kind"] == "tree" and any(
                    not path.startswith(planned_path.rstrip("/") + "/") for path in paths):
                raise ReceiptError("tree output escapes its attached plan path")
            previous_outputs.append({"name": output["name"], "sha256": output["sha256"]})
        if command["stage"] != "compile":
            _check_result(root, command, command_sha, command["input_sha256"], manifest, referenced)

    if referenced != set(manifest):
        raise ReceiptError(
            f"bundle contains unattached hashes/files: {sorted(set(manifest)-referenced)}"
        )
    return {"run_id": receipt["run_id"], "candidate": candidate["name"],
            "receipt_sha256": sha256_bytes(receipt_payload), "stages": names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-receipt-sha256")
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.bundle)
        if (args.expected_receipt_sha256 is not None and
                result["receipt_sha256"] != checked_sha(
                    args.expected_receipt_sha256, "expected receipt hash")):
            raise ReceiptError("receipt does not match detached expected SHA256")
    except (OSError, ReceiptError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("K2_LOCAL_RECEIPT_PASS " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
