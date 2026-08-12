#!/usr/bin/env python3
"""Materialize and execute only SHA-pinned owner artifacts with provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

from mutation_gate import VECTORS, load_vectors
from oracle import CONTRACTS, ContractViolation, run_trace


ROOT = Path(__file__).resolve().parent
DEFAULT_REGISTRY = ROOT / "owner_bindings.json"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
FIXED_ENV_KEYS = {"LANG", "LC_ALL", "PYTHONHASHSEED"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    if set(document) != {"schema_version", "bindings"} or document["schema_version"] != 2:
        raise ContractViolation("OWNER_REGISTRY_SCHEMA")
    if not isinstance(document["bindings"], list):
        raise ContractViolation("OWNER_BINDINGS_NOT_LIST")
    return document["bindings"]


def _validate_execution(binding: dict, source_paths: set[str]) -> dict:
    execution = binding["execution"]
    if not isinstance(execution, dict) or set(execution) != {
        "tool", "artifact", "argv", "env"
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
    joined = "\0".join(argv)
    for marker in ("{snapshot}", "{vectors}", "{result}", "{challenge}"):
        if marker not in joined:
            raise ContractViolation(f"OWNER_ARGV_MARKER_MISSING marker={marker}")
    env = execution["env"]
    if not isinstance(env, dict) or set(env) != FIXED_ENV_KEYS or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ContractViolation("OWNER_ENV_NOT_CLOSED")
    return execution


def materialize_binding(binding: dict, destination: Path) -> tuple[Path, str, dict]:
    required = {"name", "contract", "owner_repo", "owner_commit", "sources", "execution"}
    if set(binding) != required:
        raise ContractViolation("OWNER_BINDING_FIELDS")
    if binding["contract"] not in CONTRACTS:
        raise ContractViolation("OWNER_BINDING_CONTRACT")
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
    manifest_hash = _sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )
    artifact_hash = next(
        row["sha256"] for row in manifest if row["path"] == execution["artifact"]
    )
    provenance = {
        "owner_commit": commit,
        "artifact_sha256": artifact_hash,
        "snapshot_manifest_sha256": manifest_hash,
    }
    return snapshot, commit, provenance


def _external_expected(contract: str) -> dict[str, list[dict]]:
    expected = {}
    for name, (case_contract, trace) in load_vectors().items():
        if case_contract != contract:
            continue
        expected[name] = [
            {
                "grant_count": observation.grant_count,
                "addresses": list(observation.addresses),
                "committed": list(observation.committed),
                "held_after": list(observation.held_after),
            }
            for observation in run_trace(contract, trace)
        ]
    return expected


def execute_binding(
    binding: dict, snapshot: Path, result_path: Path, provenance: dict
) -> dict:
    execution = binding["execution"]
    challenge_path = snapshot / ".w8-binding-challenge.json"
    challenge = {**provenance, "nonce": secrets.token_hex(32)}
    challenge_bytes = json.dumps(challenge, sort_keys=True).encode()
    challenge_path.write_bytes(challenge_bytes)
    expected_provenance = {
        **provenance,
        "challenge_sha256": _sha256(challenge_bytes),
    }
    replacements = {
        "{snapshot}": str(snapshot),
        "{vectors}": str(VECTORS),
        "{result}": str(result_path),
        "{challenge}": str(challenge_path),
        "{contract}": binding["contract"],
    }
    argv = [execution["tool"]["path"], str(snapshot / execution["artifact"])]
    for item in execution["argv"]:
        expanded = item
        for marker, value in replacements.items():
            expanded = expanded.replace(marker, value)
        argv.append(expanded)
    completed = subprocess.run(
        argv,
        cwd=snapshot,
        env=dict(execution["env"]),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode:
        raise ContractViolation(
            f"OWNER_ARTIFACT_FAILED rc={completed.returncode} output={completed.stdout}"
        )
    if not result_path.is_file():
        raise ContractViolation("OWNER_RESULT_MISSING")
    return expected_provenance


def validate_owner_result(binding: dict, result_path: Path, provenance: dict) -> int:
    document = json.loads(result_path.read_text(encoding="utf-8"))
    required = {"schema_version", "contract", "vectors_sha256", "provenance", "cases"}
    if set(document) != required or document["schema_version"] != 2:
        raise ContractViolation("OWNER_RESULT_SCHEMA")
    if document["contract"] != binding["contract"]:
        raise ContractViolation("OWNER_RESULT_CONTRACT")
    if document["vectors_sha256"] != _sha256(VECTORS.read_bytes()):
        raise ContractViolation("OWNER_RESULT_VECTOR_SHA")
    if document["provenance"] != provenance:
        raise ContractViolation("OWNER_SNAPSHOT_PROOF_MISSING")
    expected = _external_expected(binding["contract"])
    if document["cases"] != expected:
        raise ContractViolation("OWNER_RESULT_DIVERGENCE")
    return len(expected)


def run_registry(registry: Path, work_root: Path) -> dict:
    rows, names = [], set()
    for binding in _load_registry(registry):
        name = binding.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ContractViolation("OWNER_BINDING_NAME")
        names.add(name)
        snapshot, commit, provenance = materialize_binding(binding, work_root)
        result_path = work_root / f"{name}.result.json"
        expected_provenance = execute_binding(binding, snapshot, result_path, provenance)
        cases = validate_owner_result(binding, result_path, expected_provenance)
        rows.append({
            "name": name,
            "contract": binding["contract"],
            "commit": commit,
            "sources": len(binding["sources"]),
            "cases": cases,
            "status": "PASS",
        })
    return {
        "schema_version": 2,
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
        print(f"W8_A8_K2_OWNER_BINDING_PASS bindings={report['count']}")
    else:
        print("W8_A8_K2_OWNER_BINDING_SKIP bindings=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
