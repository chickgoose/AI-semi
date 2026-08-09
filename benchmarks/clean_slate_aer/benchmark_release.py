#!/usr/bin/env python3
"""Generate and validate detached address-only benchmark release manifests.

The release manifest is deliberately a sidecar.  It binds immutable Git
objects and never hashes itself, so there is no circular "commit the manifest
that names its own commit/hash" construction.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


SCHEMA = "aer-address-only-benchmark-release-v1"
GENERATOR_VERSION = "3.0"
TRACE_ABI = {
    "version": 4,
    "identity_mode": "address_only",
    "header_fields": [
        "version", "event_count", "stim_cycles", "source_count",
        "load_milli", "sink_mode", "sink_arg0", "sink_arg1", "seed",
    ],
    "event_fields": [
        "occurrence_cycle", "tb_only_event_id", "logical_source",
        "address", "deadline",
    ],
    "required_relation": "address == logical_source",
}
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
OBJECT_RE = re.compile(r"[0-9a-f]{40,64}\Z")
VERSION_RE = re.compile(
    rb"(?m)^\s*GENERATOR_VERSION\s*=\s*['\"]([^'\"]+)['\"]\s*$"
)
FORBIDDEN_COMPONENTS = {"result", "results", "log", "logs"}


class ReleaseError(ValueError):
    """A fail-closed release-manifest error."""


@dataclass(frozen=True)
class ReleaseInputs:
    generator: str
    preparer: str
    testbench: str
    runners: tuple[str, ...]
    full_manifest: str
    capacity_manifest: str
    golden: str
    analyzers: tuple[str, ...]
    test_evidence: tuple[tuple[str, str], ...]


def _git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace").strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}") from exc
    return result if binary else result.decode("utf-8", errors="strict").strip()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"{label} is not a UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ReleaseError(
            f"{label} keys differ: missing={sorted(expected-actual)} "
            f"extra={sorted(actual-expected)}"
        )


def _repo_path(text: str) -> str:
    if not isinstance(text, str) or not text:
        raise ReleaseError("artifact path must be a nonempty string")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ReleaseError(f"artifact path is not repository-relative: {text}")
    lowered = {part.lower() for part in path.parts}
    if lowered & FORBIDDEN_COMPONENTS or path.suffix.lower() == ".log":
        raise ReleaseError(f"results/log artifacts are forbidden: {text}")
    return path.as_posix()


def _clean(repo: Path) -> None:
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        first = str(status).splitlines()[0]
        raise ReleaseError(f"worktree is dirty; first entry: {first}")


def _binding(repo: Path, kind: str) -> dict[str, Any]:
    _clean(repo)
    commit = str(_git(repo, "rev-parse", "HEAD^{commit}"))
    tree = str(_git(repo, "rev-parse", f"{commit}^{{tree}}"))
    if kind == "commit":
        return {"kind": "commit", "commit": commit, "tree": tree}
    if kind == "tree":
        return {"kind": "tree", "commit": None, "tree": tree}
    raise ReleaseError(f"unsupported binding kind: {kind}")


def _object(binding: dict[str, Any]) -> str:
    return binding["commit"] if binding["kind"] == "commit" else binding["tree"]


def _blob(repo: Path, binding: dict[str, Any], path: str) -> bytes:
    path = _repo_path(path)
    value = _git(repo, "show", f"{_object(binding)}:{path}", binary=True)
    assert isinstance(value, bytes)
    return value


def _artifact(repo: Path, binding: dict[str, Any], path: str) -> dict[str, str]:
    path = _repo_path(path)
    return {"path": path, "sha256": _sha(_blob(repo, binding, path))}


def _generator_version(data: bytes) -> str:
    match = VERSION_RE.search(data)
    if not match:
        raise ReleaseError("generator does not declare GENERATOR_VERSION")
    return match.group(1).decode("ascii")


def _generator_is_address_only(data: bytes) -> bool:
    try:
        tree = ast.parse(data.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ReleaseError("generator is not valid UTF-8 Python") from exc
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        fields: dict[str, Any] = {}
        for key_node, value_node in zip(node.keys, node.values):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                try:
                    fields[key_node.value] = ast.literal_eval(value_node)
                except (ValueError, TypeError):
                    pass
        if (
            fields.get("event_identity_mode") == "address_only"
            and fields.get("dut_address_fields") == ["logical_source"]
            and fields.get("dut_payload_fields") == []
        ):
            return True
    return False


def _runs(document: dict[str, Any], label: str, count: int) -> list[dict[str, Any]]:
    runs = document.get("runs")
    if not isinstance(runs, list) or len(runs) != count:
        actual = len(runs) if isinstance(runs, list) else "non-list"
        raise ReleaseError(f"{label} run count: expected {count}, got {actual}")
    if not all(isinstance(run, dict) and isinstance(run.get("name"), str) for run in runs):
        raise ReleaseError(f"{label} contains an invalid run")
    names = [run["name"] for run in runs]
    if len(set(names)) != len(names):
        raise ReleaseError(f"{label} contains duplicate run names")
    return runs


def _verify_suite_documents(
    generator: bytes,
    full: dict[str, Any],
    capacity: dict[str, Any],
    golden: dict[str, Any],
    full_name: str,
) -> None:
    if _generator_version(generator) != GENERATOR_VERSION:
        raise ReleaseError(f"generator version must be {GENERATOR_VERSION}")
    if not _generator_is_address_only(generator):
        raise ReleaseError("generator does not emit the address-only identity contract")
    full_runs = _runs(full, "official full manifest", 48)
    capacity_runs = _runs(capacity, "official capacity manifest", 20)
    full_by_name = {run["name"]: run for run in full_runs}
    for run in capacity_runs:
        if full_by_name.get(run["name"]) != run:
            raise ReleaseError(
                f"capacity run is not byte-equivalent JSON to full run: {run['name']}"
            )
    if golden.get("generator_version") != GENERATOR_VERSION:
        raise ReleaseError("golden generator_version mismatch")
    if golden.get("suite") != PurePosixPath(full_name).name:
        raise ReleaseError("golden suite does not name the official full manifest")
    _runs(golden, "golden fixture", 48)


def _validate_evidence(entries: Any) -> None:
    if not isinstance(entries, list) or not entries:
        raise ReleaseError("at least one embedded test-evidence marker is required")
    names: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ReleaseError(f"test_evidence[{index}] must be an object")
        _exact_keys(entry, {"name", "status", "marker"}, f"test_evidence[{index}]")
        if not isinstance(entry["name"], str) or not entry["name"]:
            raise ReleaseError("test evidence name must be nonempty")
        if entry["name"] in names:
            raise ReleaseError(f"duplicate test evidence name: {entry['name']}")
        names.add(entry["name"])
        if entry["status"] != "PASS":
            raise ReleaseError(f"test evidence is not PASS: {entry['name']}")
        if not isinstance(entry["marker"], str) or not entry["marker"]:
            raise ReleaseError(f"test evidence marker is empty: {entry['name']}")


def _manifest_from_inputs(
    repo: Path, binding_kind: str, inputs: ReleaseInputs
) -> dict[str, Any]:
    binding = _binding(repo, binding_kind)
    paths = [
        inputs.generator, inputs.preparer, inputs.testbench,
        inputs.full_manifest, inputs.capacity_manifest, inputs.golden,
        *inputs.runners, *inputs.analyzers,
    ]
    normalized = [_repo_path(path) for path in paths]
    if len(set(normalized)) != len(normalized):
        raise ReleaseError("each bound artifact path must be unique")

    generator_blob = _blob(repo, binding, inputs.generator)
    full_blob = _blob(repo, binding, inputs.full_manifest)
    capacity_blob = _blob(repo, binding, inputs.capacity_manifest)
    golden_blob = _blob(repo, binding, inputs.golden)
    _verify_suite_documents(
        generator_blob,
        _json_bytes(full_blob, inputs.full_manifest),
        _json_bytes(capacity_blob, inputs.capacity_manifest),
        _json_bytes(golden_blob, inputs.golden),
        inputs.full_manifest,
    )
    evidence = [
        {"name": name, "status": "PASS", "marker": marker}
        for name, marker in inputs.test_evidence
    ]
    _validate_evidence(evidence)
    return {
        "schema": SCHEMA,
        "binding": binding,
        "generator": {
            **_artifact(repo, binding, inputs.generator),
            "version": GENERATOR_VERSION,
        },
        "preparer": _artifact(repo, binding, inputs.preparer),
        "testbench": _artifact(repo, binding, inputs.testbench),
        "runners": [_artifact(repo, binding, path) for path in inputs.runners],
        "official_manifests": {
            "full_n16": {
                **_artifact(repo, binding, inputs.full_manifest),
                "run_count": 48,
            },
            "capacity_n16": {
                **_artifact(repo, binding, inputs.capacity_manifest),
                "run_count": 20,
            },
        },
        "golden": {
            **_artifact(repo, binding, inputs.golden),
            "generator_version": GENERATOR_VERSION,
            "run_count": 48,
        },
        "trace_abi": TRACE_ABI,
        "analyzers": [_artifact(repo, binding, path) for path in inputs.analyzers],
        "test_evidence": evidence,
    }


def _verify_artifact(
    repo: Path, binding: dict[str, Any], value: Any, label: str,
    extra_keys: set[str] | None = None,
) -> str:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} must be an object")
    expected = {"path", "sha256"} | (extra_keys or set())
    _exact_keys(value, expected, label)
    path = _repo_path(value["path"])
    if not isinstance(value["sha256"], str) or not SHA_RE.fullmatch(value["sha256"]):
        raise ReleaseError(f"{label}.sha256 is invalid")
    actual = _sha(_blob(repo, binding, path))
    if actual != value["sha256"]:
        raise ReleaseError(f"{label} hash mismatch: {path}")
    return path


def validate_manifest(repo: Path, manifest: dict[str, Any]) -> None:
    repo = repo.resolve()
    _clean(repo)
    _exact_keys(
        manifest,
        {"schema", "binding", "generator", "preparer", "testbench", "runners",
         "official_manifests", "golden", "trace_abi", "analyzers", "test_evidence"},
        "release manifest",
    )
    if manifest["schema"] != SCHEMA:
        raise ReleaseError(f"unsupported schema: {manifest['schema']!r}")
    binding = manifest["binding"]
    if not isinstance(binding, dict):
        raise ReleaseError("binding must be an object")
    _exact_keys(binding, {"kind", "commit", "tree"}, "binding")
    if binding["kind"] not in {"commit", "tree"}:
        raise ReleaseError("binding.kind must be commit or tree")
    if not isinstance(binding["tree"], str) or not OBJECT_RE.fullmatch(binding["tree"]):
        raise ReleaseError("binding.tree is invalid")
    resolved_tree = str(_git(repo, "rev-parse", f"{binding['tree']}^{{tree}}"))
    if resolved_tree != binding["tree"]:
        raise ReleaseError("binding.tree does not identify a tree object")
    if binding["kind"] == "commit":
        if not isinstance(binding["commit"], str) or not OBJECT_RE.fullmatch(binding["commit"]):
            raise ReleaseError("binding.commit is invalid")
        commit = str(_git(repo, "rev-parse", f"{binding['commit']}^{{commit}}"))
        if commit != binding["commit"]:
            raise ReleaseError("binding.commit is not canonical")
        commit_tree = str(_git(repo, "rev-parse", f"{commit}^{{tree}}"))
        if commit_tree != binding["tree"]:
            raise ReleaseError("binding commit/tree mismatch")
    elif binding["commit"] is not None:
        raise ReleaseError("tree binding must set commit to null")

    paths: list[str] = []
    generator_path = _verify_artifact(
        repo, binding, manifest["generator"], "generator", {"version"}
    )
    if manifest["generator"]["version"] != GENERATOR_VERSION:
        raise ReleaseError("generator.version mismatch")
    paths.append(generator_path)
    paths.append(_verify_artifact(repo, binding, manifest["preparer"], "preparer"))
    paths.append(_verify_artifact(repo, binding, manifest["testbench"], "testbench"))

    for collection_name in ("runners", "analyzers"):
        collection = manifest[collection_name]
        if not isinstance(collection, list) or not collection:
            raise ReleaseError(f"{collection_name} must be a nonempty array")
        for index, artifact in enumerate(collection):
            paths.append(_verify_artifact(
                repo, binding, artifact, f"{collection_name}[{index}]"
            ))

    official = manifest["official_manifests"]
    if not isinstance(official, dict):
        raise ReleaseError("official_manifests must be an object")
    _exact_keys(official, {"full_n16", "capacity_n16"}, "official_manifests")
    full_path = _verify_artifact(
        repo, binding, official["full_n16"], "official full manifest", {"run_count"}
    )
    capacity_path = _verify_artifact(
        repo, binding, official["capacity_n16"], "official capacity manifest",
        {"run_count"},
    )
    if official["full_n16"]["run_count"] != 48:
        raise ReleaseError("official full manifest count must be 48")
    if official["capacity_n16"]["run_count"] != 20:
        raise ReleaseError("official capacity manifest count must be 20")
    paths.extend((full_path, capacity_path))

    golden_path = _verify_artifact(
        repo, binding, manifest["golden"], "golden",
        {"generator_version", "run_count"},
    )
    if manifest["golden"]["generator_version"] != GENERATOR_VERSION:
        raise ReleaseError("golden generator version mismatch")
    if manifest["golden"]["run_count"] != 48:
        raise ReleaseError("golden run count must be 48")
    paths.append(golden_path)
    if manifest["trace_abi"] != TRACE_ABI:
        raise ReleaseError("trace ABI is not the frozen address-only v4 contract")
    if len(paths) != len(set(paths)):
        raise ReleaseError("bound artifact paths must be unique")
    _validate_evidence(manifest["test_evidence"])

    generator_blob = _blob(repo, binding, generator_path)
    _verify_suite_documents(
        generator_blob,
        _json_bytes(_blob(repo, binding, full_path), full_path),
        _json_bytes(_blob(repo, binding, capacity_path), capacity_path),
        _json_bytes(_blob(repo, binding, golden_path), golden_path),
        full_path,
    )


def generate_manifest(
    repo: Path, output: Path, binding_kind: str, inputs: ReleaseInputs
) -> dict[str, Any]:
    repo = repo.resolve()
    output = output.resolve()
    try:
        output.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ReleaseError("release manifest must be a detached sidecar outside the repository")
    manifest = _manifest_from_inputs(repo, binding_kind, inputs)
    validate_manifest(repo, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(output)
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read release manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseError("release manifest must be a JSON object")
    return value


def _inputs(args: argparse.Namespace) -> ReleaseInputs:
    evidence: list[tuple[str, str]] = []
    for entry in args.test_evidence:
        if "=" not in entry:
            raise ReleaseError("--test-evidence must be NAME=PASS_MARKER")
        evidence.append(tuple(entry.split("=", 1)))
    return ReleaseInputs(
        generator=args.generator, preparer=args.preparer, testbench=args.testbench,
        runners=tuple(args.runner), full_manifest=args.full_manifest,
        capacity_manifest=args.capacity_manifest, golden=args.golden,
        analyzers=tuple(args.analyzer), test_evidence=tuple(evidence),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="write a detached release sidecar")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--binding", choices=("commit", "tree"), default="commit")
    generate.add_argument("--generator", required=True)
    generate.add_argument("--preparer", required=True)
    generate.add_argument("--testbench", required=True)
    generate.add_argument("--runner", action="append", required=True)
    generate.add_argument("--full-manifest", required=True)
    generate.add_argument("--capacity-manifest", required=True)
    generate.add_argument("--golden", required=True)
    generate.add_argument("--analyzer", action="append", required=True)
    generate.add_argument("--test-evidence", action="append", required=True,
                          metavar="NAME=PASS_MARKER")
    validate = sub.add_parser("validate", help="validate a detached sidecar")
    validate.add_argument("--repo", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "generate":
            manifest = generate_manifest(args.repo, args.output, args.binding, _inputs(args))
            print(
                f"BENCHMARK_RELEASE_GENERATED kind={manifest['binding']['kind']} "
                f"tree={manifest['binding']['tree']} output={args.output}"
            )
        else:
            manifest = load_manifest(args.manifest)
            validate_manifest(args.repo.resolve(), manifest)
            print(
                f"BENCHMARK_RELEASE_VALID kind={manifest['binding']['kind']} "
                f"tree={manifest['binding']['tree']}"
            )
    except ReleaseError as exc:
        print(f"BENCHMARK_RELEASE_REJECTED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
