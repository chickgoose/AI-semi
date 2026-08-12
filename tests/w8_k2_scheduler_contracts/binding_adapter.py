#!/usr/bin/env python3
"""Execute only immutable owner artifacts from exact materialized commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from oracle import CONTRACTS, ContractViolation


ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "owner_bindings.json"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
FIXED_ENV_KEYS = {"LANG", "LC_ALL", "PYTHONHASHSEED"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: object) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
    )
    if completed.returncode:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ContractViolation(f"OWNER_GIT_FAILED stderr={stderr.strip()}")
    return completed.stdout


def _safe_relative(text: str) -> PurePosixPath:
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise ContractViolation(f"OWNER_SOURCE_PATH_UNSAFE path={text}")
    return path


def _load_registry(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schema_version", "bindings"} or document["schema_version"] != 3:
        raise ContractViolation("OWNER_REGISTRY_SCHEMA")
    if not isinstance(document["bindings"], list):
        raise ContractViolation("OWNER_BINDINGS_NOT_LIST")
    return document["bindings"]


def _validate_execution(binding: dict, source_paths: set[str]) -> dict:
    execution = binding["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "tool", "artifact", "argv", "env", "required_output"
    }:
        raise ContractViolation("OWNER_EXECUTION_SCHEMA")
    tool = execution["tool"]
    if not isinstance(tool, dict) or set(tool) != {"path", "sha256"}:
        raise ContractViolation("OWNER_TOOL_SCHEMA")
    tool_path = Path(tool["path"])
    if not tool_path.is_absolute() or not tool_path.is_file():
        raise ContractViolation("OWNER_TOOL_NOT_ABSOLUTE_FILE")
    if HEX64.fullmatch(str(tool["sha256"])) is None:
        raise ContractViolation("OWNER_TOOL_SHA_FORMAT")
    if _sha256(tool_path.read_bytes()) != tool["sha256"]:
        raise ContractViolation("OWNER_TOOL_SHA_MISMATCH")
    artifact = str(_safe_relative(execution["artifact"]))
    if artifact not in source_paths:
        raise ContractViolation("OWNER_ARTIFACT_NOT_MATERIALIZED")
    argv = execution["argv"]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ContractViolation("OWNER_ARGV_SCHEMA")
    if any("{" in item or "}" in item for item in argv):
        raise ContractViolation("OWNER_ARGV_PLACEHOLDER_FORBIDDEN")
    env = execution["env"]
    if not isinstance(env, dict) or set(env) != FIXED_ENV_KEYS or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ContractViolation("OWNER_ENV_NOT_CLOSED")
    required_output = execution["required_output"]
    if not isinstance(required_output, list) or not required_output or not all(
        isinstance(item, str) and item for item in required_output
    ):
        raise ContractViolation("OWNER_REQUIRED_OUTPUT_SCHEMA")
    return execution


def materialize_binding(binding: dict, destination: Path) -> tuple[Path, dict]:
    required = {
        "name", "contract", "evidence_scope", "owner_repo", "owner_commit",
        "sources", "execution"
    }
    if set(binding) != required:
        raise ContractViolation("OWNER_BINDING_FIELDS")
    if binding["contract"] not in CONTRACTS:
        raise ContractViolation("OWNER_BINDING_CONTRACT")
    if binding["evidence_scope"] not in {"owner_model", "owner_selftest", "owner_rtl"}:
        raise ContractViolation("OWNER_EVIDENCE_SCOPE")
    commit = binding["owner_commit"]
    if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise ContractViolation(f"OWNER_COMMIT_NOT_FULL_SHA commit={commit}")
    repo = Path(binding["owner_repo"]).resolve()
    resolved = str(_git(repo, "rev-parse", f"{commit}^{{commit}}")).strip()
    if resolved != commit:
        raise ContractViolation("OWNER_COMMIT_NOT_EXACT")
    snapshot = destination / binding["name"]
    snapshot.mkdir(parents=True, exist_ok=False)
    sources = binding["sources"]
    if not isinstance(sources, list) or not sources:
        raise ContractViolation("OWNER_SOURCES_EMPTY")
    source_paths: set[str] = set()
    manifest: list[dict] = []
    for record in sources:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ContractViolation("OWNER_SOURCE_FIELDS")
        relative = _safe_relative(record["path"])
        normalized = str(relative)
        if normalized in source_paths:
            raise ContractViolation(f"OWNER_SOURCE_DUPLICATE path={normalized}")
        source_paths.add(normalized)
        expected_hash = record["sha256"]
        if not isinstance(expected_hash, str) or HEX64.fullmatch(expected_hash) is None:
            raise ContractViolation("OWNER_SOURCE_SHA_FORMAT")
        data = _git(repo, "show", f"{commit}:{normalized}", binary=True)
        assert isinstance(data, bytes)
        if _sha256(data) != expected_hash:
            raise ContractViolation(f"OWNER_SOURCE_SHA_MISMATCH path={normalized}")
        target = snapshot.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest.append({"path": normalized, "sha256": expected_hash})
    execution = _validate_execution(binding, source_paths)
    artifact_hash = next(
        row["sha256"] for row in manifest if row["path"] == execution["artifact"]
    )
    provenance = {
        "owner_commit": commit,
        "artifact": execution["artifact"],
        "artifact_sha256": artifact_hash,
        "tool_path": execution["tool"]["path"],
        "tool_sha256": execution["tool"]["sha256"],
        "argv_sha256": _canonical_hash(execution["argv"]),
        "env_sha256": _canonical_hash(execution["env"]),
        "snapshot_manifest_sha256": _canonical_hash(manifest),
    }
    return snapshot, provenance


def execute_binding(binding: dict, snapshot: Path, provenance: dict) -> dict:
    """The process shape cannot name an external adapter or arbitrary command."""

    execution = binding["execution"]
    artifact = snapshot.joinpath(*PurePosixPath(execution["artifact"]).parts)
    if not artifact.is_file() or _sha256(artifact.read_bytes()) != provenance["artifact_sha256"]:
        raise ContractViolation("OWNER_ARTIFACT_CHANGED_BEFORE_EXEC")
    argv = [execution["tool"]["path"], str(artifact), *execution["argv"]]
    completed = subprocess.run(
        argv,
        cwd=artifact.parent,
        env=dict(execution["env"]),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if _sha256(artifact.read_bytes()) != provenance["artifact_sha256"]:
        raise ContractViolation("OWNER_ARTIFACT_CHANGED_AFTER_EXEC")
    if completed.returncode:
        raise ContractViolation(
            f"OWNER_ARTIFACT_FAILED rc={completed.returncode} output={completed.stdout}"
        )
    for marker in execution["required_output"]:
        if marker not in completed.stdout:
            raise ContractViolation(f"OWNER_REQUIRED_OUTPUT_MISSING marker={marker!r}")
    return {
        **provenance,
        "process_argv": argv,
        "output_sha256": _sha256(completed.stdout.encode()),
        "required_output": list(execution["required_output"]),
    }


def run_registry(registry: Path, work_root: Path) -> dict:
    rows, names = [], set()
    for binding in _load_registry(registry):
        name = binding.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ContractViolation("OWNER_BINDING_NAME")
        names.add(name)
        snapshot, provenance = materialize_binding(binding, work_root)
        execution = execute_binding(binding, snapshot, provenance)
        rows.append({
            "name": name,
            "contract": binding["contract"],
            "evidence_scope": binding["evidence_scope"],
            "commit": provenance["owner_commit"],
            "sources": len(binding["sources"]),
            "execution": execution,
            "status": "PASS",
        })
    return {
        "schema_version": 3,
        "decision": "PASS" if rows else "SKIP_NO_OWNER_BINDINGS",
        "bindings": rows,
        "count": len(rows),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.work_root is not None:
        args.work_root.mkdir(parents=True, exist_ok=False)
        report = run_registry(args.registry, args.work_root)
    else:
        with tempfile.TemporaryDirectory(prefix="w8-k2-owner-") as temporary:
            report = run_registry(args.registry, Path(temporary))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["count"]:
        scopes = ",".join(row["evidence_scope"] for row in report["bindings"])
        print(f"W8_A8_K2_OWNER_BINDING_PASS bindings={report['count']} scopes={scopes}")
    else:
        print("W8_A8_K2_OWNER_BINDING_SKIP bindings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
