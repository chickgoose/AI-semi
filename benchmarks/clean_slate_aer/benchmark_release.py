#!/usr/bin/env python3
"""Generate and validate detached address-only benchmark release manifests.

The release manifest is deliberately a sidecar.  It binds immutable Git
objects and never hashes itself, so there is no circular "commit the manifest
that names its own commit/hash" construction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


SCHEMA = "aer-address-only-benchmark-release-v5"
TRUSTED_POLICY_PATH = "benchmarks/clean_slate_aer/a1_release_policy.json"
TRUSTED_POLICY_SHA256 = "72b5eb73887f0b1d5a11c5f2fcd97f859477b543155af6ec16871eae0983d6dc"
CURRENT_GENERATOR_VERSION = "4.0"
HISTORICAL_GENERATOR_VERSION = "3.0"
SUITE_POLICY = {
    "expected_full_count": 50,
    "expected_capacity_count": 22,
    "required_run_names": [
        "mixed_phase_always_ready_identity",
        "mixed_phase_always_ready_bit_reverse",
    ],
}
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
FORBIDDEN_COMPONENTS = {"result", "results", "log", "logs"}
REQUIRED_PPA_CONTRACT = {
    "baseline-n16": ("aer_dut", {"ADDR_WIDTH": 16, "NUM_SOURCES": 16}, []),
    "a23-ee430-n16": (
        "a23_ee430_dut", {"ADDR_WIDTH": 16, "NUM_SOURCES": 16}, [],
    ),
    "a7-prefix-k4-n16": (
        "a7_prefix_structural_top", {"AW": 16, "K": 4, "N": 16, "SW": 4}, [],
    ),
    "a7-replicated-k4-n16": (
        "a7_replicated_structural_top",
        {"AW": 16, "K": 4, "N": 16, "SW": 4}, [],
    ),
}


class ReleaseError(ValueError):
    """A fail-closed release-manifest error."""


@dataclass(frozen=True)
class ReleaseInputs:
    policy: str
    generator: str
    preparer: str
    testbench: str
    native_bindings: tuple[str, ...]
    ppa_registry: str
    runners: tuple[str, ...]
    full_manifest: str
    capacity_manifest: str
    golden: str
    analyzers: tuple[str, ...]
    test_receipts: tuple[str, ...]


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


def _tracked_native_bindings(repo: Path, binding: dict[str, Any]) -> list[str]:
    listing = str(_git(
        repo, "ls-tree", "-r", "--name-only", _object(binding), "--",
        "tb/clean/native",
    ))
    return sorted(
        path for path in listing.splitlines()
        if path.startswith("tb/clean/native/") and path.endswith("_binding.sv")
    )


def _load_trusted_policy(
    repo: Path, binding: dict[str, Any], path: str
) -> dict[str, Any]:
    if path != TRUSTED_POLICY_PATH:
        raise ReleaseError(f"policy path must be {TRUSTED_POLICY_PATH}")
    blob = _blob(repo, binding, path)
    if _sha(blob) != TRUSTED_POLICY_SHA256:
        raise ReleaseError("trusted A1 release policy hash mismatch")
    policy = _json_bytes(blob, path)
    _exact_keys(
        policy,
        {"schema", "generator_version", "trace_abi_version", "identity_mode",
         "required_relation", "full_count", "capacity_count", "artifacts",
         "test_receipts", "ppa_registry"},
        "trusted policy",
    )
    if policy["schema"] != "aer-a1-release-policy-v1":
        raise ReleaseError("unsupported trusted policy schema")
    if (
        policy["generator_version"] != CURRENT_GENERATOR_VERSION
        or policy["trace_abi_version"] != 4
        or policy["identity_mode"] != "address_only"
        or policy["required_relation"] != "address == logical_source"
        or policy["full_count"] != 50
        or policy["capacity_count"] != 22
    ):
        raise ReleaseError("trusted policy is not the canonical A1 v4/50/22 policy")
    return policy


def _verify_policy_artifact(
    repo: Path, binding: dict[str, Any], value: Any, label: str
) -> str:
    if not isinstance(value, dict):
        raise ReleaseError(f"{label} policy artifact must be an object")
    _exact_keys(value, {"path", "sha256"}, f"{label} policy artifact")
    path = _repo_path(value["path"])
    if not isinstance(value["sha256"], str) or not SHA_RE.fullmatch(value["sha256"]):
        raise ReleaseError(f"{label} policy SHA-256 is invalid")
    if _sha(_blob(repo, binding, path)) != value["sha256"]:
        raise ReleaseError(f"canonical policy hash mismatch: {label}: {path}")
    return path


def _filelist_sources(data: bytes, path: str) -> list[str]:
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError(f"PPA filelist is not UTF-8: {path}") from exc
    tokens: list[str] = []
    for line in source.splitlines():
        tokens.extend(line.split("#", 1)[0].split())
    if any(token == "-f" or token.startswith("-f") for token in tokens):
        raise ReleaseError(f"nested PPA filelists are forbidden in registry: {path}")
    sources = [token.strip("'\"") for token in tokens if not token.startswith(("-", "+"))]
    if not sources:
        raise ReleaseError(f"PPA filelist has no sources: {path}")
    return [_repo_path(source) for source in sources]


def _validate_ppa_registry(
    repo: Path,
    binding: dict[str, Any],
    registry_path: str,
    native_paths: Sequence[str],
) -> None:
    registry = _json_bytes(_blob(repo, binding, registry_path), registry_path)
    _exact_keys(registry, {"schema", "candidates"}, "PPA registry")
    if registry["schema"] != "aer-candidate-ppa-registry-v1":
        raise ReleaseError("unsupported PPA registry schema")
    candidates = registry["candidates"]
    if not isinstance(candidates, list):
        raise ReleaseError("PPA registry candidates must be an array")
    names = [candidate.get("name") for candidate in candidates if isinstance(candidate, dict)]
    if set(names) != set(REQUIRED_PPA_CONTRACT) or len(names) != len(REQUIRED_PPA_CONTRACT):
        raise ReleaseError("PPA registry candidate set is not exact")
    binding_names = {PurePosixPath(path).name for path in native_paths}
    for candidate in candidates:
        name = candidate.get("name")
        _exact_keys(
            candidate,
            {"name", "top", "parameters", "defines", "filelist", "tool_scripts",
             "sources"},
            f"PPA candidate {name}",
        )
        expected_top, expected_parameters, expected_defines = REQUIRED_PPA_CONTRACT[name]
        if (
            candidate["top"] != expected_top
            or candidate["parameters"] != expected_parameters
            or candidate["defines"] != expected_defines
        ):
            raise ReleaseError(f"PPA top/parameters/defines mismatch: {name}")
        filelist = _verify_policy_artifact(
            repo, binding, candidate["filelist"], f"PPA {name} filelist"
        )
        if filelist == "tb/files.f" or filelist.startswith("tb/clean/"):
            raise ReleaseError(f"verification filelist cannot be PPA source: {filelist}")
        source_paths = _filelist_sources(_blob(repo, binding, filelist), filelist)
        if any(PurePosixPath(path).name in binding_names for path in source_paths):
            raise ReleaseError(f"native binding is forbidden in PPA sources: {name}")
        sources = candidate["sources"]
        if not isinstance(sources, list) or not sources:
            raise ReleaseError(f"PPA source closure is empty: {name}")
        declared_sources = [
            _verify_policy_artifact(repo, binding, item, f"PPA {name} source")
            for item in sources
        ]
        if declared_sources != source_paths:
            raise ReleaseError(f"PPA source closure differs from filelist: {name}")
        scripts = candidate["tool_scripts"]
        if not isinstance(scripts, list) or not scripts:
            raise ReleaseError(f"PPA tool script closure is empty: {name}")
        for item in scripts:
            _verify_policy_artifact(repo, binding, item, f"PPA {name} tool script")


def _validate_native_boundary(
    repo: Path, binding: dict[str, Any], native_paths: Sequence[str]
) -> None:
    tracked = _tracked_native_bindings(repo, binding)
    if sorted(native_paths) != tracked:
        raise ReleaseError(
            "native_bindings must exactly enumerate tracked "
            f"tb/clean/native/*_binding.sv files: expected {tracked}"
        )


def _required_generator_version(release_kind: str) -> str:
    if release_kind == "current":
        return CURRENT_GENERATOR_VERSION
    if release_kind == "historical":
        return HISTORICAL_GENERATOR_VERSION
    raise ReleaseError(f"unsupported release kind: {release_kind!r}")


def _runs(document: dict[str, Any], label: str) -> list[dict[str, Any]]:
    runs = document.get("runs")
    if not isinstance(runs, list):
        raise ReleaseError(f"{label} runs must be an array")
    if not all(isinstance(run, dict) and isinstance(run.get("name"), str) for run in runs):
        raise ReleaseError(f"{label} contains an invalid run")
    names = [run["name"] for run in runs]
    if len(set(names)) != len(names):
        raise ReleaseError(f"{label} contains duplicate run names")
    return runs


def _verify_suite_documents(
    full: dict[str, Any],
    capacity: dict[str, Any],
    golden: dict[str, Any],
    full_name: str,
    required_version: str,
) -> tuple[int, int, int]:
    full_runs = _runs(full, "official full manifest")
    capacity_runs = _runs(capacity, "official capacity manifest")
    if len(full_runs) != SUITE_POLICY["expected_full_count"]:
        raise ReleaseError(
            "official full manifest count: expected "
            f"{SUITE_POLICY['expected_full_count']}, got {len(full_runs)}"
        )
    if len(capacity_runs) != SUITE_POLICY["expected_capacity_count"]:
        raise ReleaseError(
            "official capacity manifest count: expected "
            f"{SUITE_POLICY['expected_capacity_count']}, got {len(capacity_runs)}"
        )
    full_by_name = {run["name"]: run for run in full_runs}
    for run in capacity_runs:
        if full_by_name.get(run["name"]) != run:
            raise ReleaseError(
                f"capacity run is not byte-equivalent JSON to full run: {run['name']}"
            )
    for required_name in SUITE_POLICY["required_run_names"]:
        if required_name not in full_by_name:
            raise ReleaseError(f"official full manifest lacks required run: {required_name}")
        if not any(run["name"] == required_name for run in capacity_runs):
            raise ReleaseError(
                f"official capacity manifest lacks required run: {required_name}"
            )
    if golden.get("generator_version") != required_version:
        raise ReleaseError("golden generator_version mismatch")
    if golden.get("suite") != PurePosixPath(full_name).name:
        raise ReleaseError("golden suite does not name the official full manifest")
    golden_runs = _runs(golden, "golden fixture")
    if len(golden_runs) != len(full_runs):
        raise ReleaseError(
            f"golden run count differs from full manifest: "
            f"{len(golden_runs)} != {len(full_runs)}"
        )
    return len(full_runs), len(capacity_runs), len(golden_runs)


def _validate_executed_receipts(
    repo: Path,
    binding: dict[str, Any],
    entries: Any,
    policy: dict[str, Any],
) -> list[str]:
    expected = policy["test_receipts"]
    if not isinstance(entries, list) or entries != expected:
        raise ReleaseError("test receipts do not exactly match trusted policy")
    artifacts = policy["artifacts"]
    allowed_commands = {
        ("python3", artifacts["self_test"]["path"]),
        ("python3", artifacts["neutrality_self_test"]["path"]),
    }
    seen_commands: set[tuple[str, ...]] = set()
    paths: list[str] = []
    environment = os.environ.copy()
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "LC_ALL": "C"})
    for index, entry in enumerate(entries):
        path = _verify_policy_artifact(
            repo, binding, entry, f"test receipt {index}"
        )
        receipt = _json_bytes(_blob(repo, binding, path), path)
        _exact_keys(
            receipt,
            {"schema", "name", "command", "exit_code", "log_sha256",
             "required_markers"},
            f"test receipt {index}",
        )
        if receipt["schema"] != "aer-executed-test-receipt-v1":
            raise ReleaseError(f"unsupported test receipt schema: {path}")
        command_value = receipt["command"]
        if not (
            isinstance(command_value, list)
            and all(isinstance(item, str) and item for item in command_value)
        ):
            raise ReleaseError(f"test receipt command is invalid: {path}")
        command = tuple(command_value)
        if command not in allowed_commands or command in seen_commands:
            raise ReleaseError(f"test receipt command is not canonical: {path}")
        seen_commands.add(command)
        if receipt["exit_code"] != 0:
            raise ReleaseError(f"test receipt does not declare exit code zero: {path}")
        if not isinstance(receipt["log_sha256"], str) or not SHA_RE.fullmatch(
            receipt["log_sha256"]
        ):
            raise ReleaseError(f"test receipt log SHA-256 is invalid: {path}")
        markers = receipt["required_markers"]
        if not isinstance(markers, list) or not markers or not all(
            isinstance(marker, str) and marker for marker in markers
        ):
            raise ReleaseError(f"test receipt markers are invalid: {path}")
        try:
            result = subprocess.run(
                list(command), cwd=repo, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=120, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError(f"receipt command execution failed: {path}: {exc}") from exc
        log = result.stdout + result.stderr
        if result.returncode != receipt["exit_code"]:
            raise ReleaseError(f"receipt command exit code mismatch: {path}")
        if _sha(log) != receipt["log_sha256"]:
            raise ReleaseError(f"receipt command log hash mismatch: {path}")
        decoded = log.decode("utf-8", errors="replace")
        if any(marker not in decoded for marker in markers):
            raise ReleaseError(f"receipt command marker missing: {path}")
        paths.append(path)
    if seen_commands != allowed_commands:
        raise ReleaseError("trusted self-test receipt command set is incomplete")
    return paths


def _manifest_from_inputs(
    repo: Path, binding_kind: str, inputs: ReleaseInputs, release_kind: str
) -> dict[str, Any]:
    required_version = _required_generator_version(release_kind)
    if release_kind != "current":
        raise ReleaseError("trusted canonical policy authorizes current releases only")
    binding = _binding(repo, binding_kind)
    policy = _load_trusted_policy(repo, binding, inputs.policy)
    artifacts = policy["artifacts"]
    if not isinstance(artifacts, dict):
        raise ReleaseError("trusted policy artifacts must be an object")
    _exact_keys(
        artifacts,
        {"generator", "preparer", "testbench", "full_manifest",
         "capacity_manifest", "golden", "self_test", "neutrality_self_test"},
        "trusted policy artifacts",
    )
    selected = {
        "generator": inputs.generator,
        "preparer": inputs.preparer,
        "testbench": inputs.testbench,
        "full_manifest": inputs.full_manifest,
        "capacity_manifest": inputs.capacity_manifest,
        "golden": inputs.golden,
    }
    for name, path in selected.items():
        if path != artifacts[name]["path"]:
            raise ReleaseError(f"{name} path differs from canonical policy")
    for name, value in artifacts.items():
        _verify_policy_artifact(repo, binding, value, name)
    if inputs.ppa_registry != policy["ppa_registry"]["path"]:
        raise ReleaseError("PPA registry path differs from canonical policy")
    if list(inputs.test_receipts) != [item["path"] for item in policy["test_receipts"]]:
        raise ReleaseError("test receipt paths differ from canonical policy")
    paths = [
        inputs.policy, inputs.generator, inputs.preparer, inputs.testbench,
        inputs.full_manifest, inputs.capacity_manifest, inputs.golden,
        inputs.ppa_registry, *inputs.test_receipts, *inputs.native_bindings,
        *inputs.runners, *inputs.analyzers,
    ]
    normalized = [_repo_path(path) for path in paths]
    if len(set(normalized)) != len(normalized):
        raise ReleaseError("each bound artifact path must be unique")

    full_blob = _blob(repo, binding, inputs.full_manifest)
    capacity_blob = _blob(repo, binding, inputs.capacity_manifest)
    golden_blob = _blob(repo, binding, inputs.golden)
    counts = _verify_suite_documents(
        _json_bytes(full_blob, inputs.full_manifest),
        _json_bytes(capacity_blob, inputs.capacity_manifest),
        _json_bytes(golden_blob, inputs.golden),
        inputs.full_manifest,
        required_version,
    )
    return {
        "schema": SCHEMA,
        "release_kind": release_kind,
        "binding": binding,
        "policy": _artifact(repo, binding, inputs.policy),
        "suite_policy": {
            **SUITE_POLICY,
            "required_run_names": list(SUITE_POLICY["required_run_names"]),
        },
        "generator": {
            **_artifact(repo, binding, inputs.generator),
            "version": required_version,
        },
        "preparer": _artifact(repo, binding, inputs.preparer),
        "testbench": _artifact(repo, binding, inputs.testbench),
        "native_bindings": [
            _artifact(repo, binding, path) for path in inputs.native_bindings
        ],
        "ppa_registry": _artifact(repo, binding, inputs.ppa_registry),
        "runners": [_artifact(repo, binding, path) for path in inputs.runners],
        "official_manifests": {
            "full_n16": {
                **_artifact(repo, binding, inputs.full_manifest),
                "run_count": counts[0],
            },
            "capacity_n16": {
                **_artifact(repo, binding, inputs.capacity_manifest),
                "run_count": counts[1],
            },
        },
        "golden": {
            **_artifact(repo, binding, inputs.golden),
            "generator_version": required_version,
            "run_count": counts[2],
        },
        "trace_abi": {
            **TRACE_ABI,
            "header_fields": list(TRACE_ABI["header_fields"]),
            "event_fields": list(TRACE_ABI["event_fields"]),
        },
        "analyzers": [_artifact(repo, binding, path) for path in inputs.analyzers],
        "test_receipts": [
            _artifact(repo, binding, path) for path in inputs.test_receipts
        ],
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
        {"schema", "release_kind", "binding", "policy", "suite_policy", "generator",
         "preparer", "testbench", "native_bindings", "ppa_registry", "runners",
         "official_manifests", "golden", "trace_abi", "analyzers", "test_receipts"},
        "release manifest",
    )
    if manifest["schema"] != SCHEMA:
        raise ReleaseError(f"unsupported schema: {manifest['schema']!r}")
    required_version = _required_generator_version(manifest["release_kind"])
    if manifest["release_kind"] != "current":
        raise ReleaseError("trusted canonical policy authorizes current releases only")
    if manifest["suite_policy"] != SUITE_POLICY:
        raise ReleaseError("suite policy is not the official 50/22 mixed-phase policy")
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

    policy_path = _verify_artifact(repo, binding, manifest["policy"], "policy")
    policy = _load_trusted_policy(repo, binding, policy_path)
    if manifest["policy"]["sha256"] != TRUSTED_POLICY_SHA256:
        raise ReleaseError("manifest policy SHA-256 is not trusted")
    policy_artifacts = policy["artifacts"]
    paths: list[str] = [policy_path]
    generator_path = _verify_artifact(
        repo, binding, manifest["generator"], "generator", {"version"}
    )
    if manifest["generator"]["version"] != required_version:
        raise ReleaseError("generator.version mismatch")
    paths.append(generator_path)
    preparer_path = _verify_artifact(repo, binding, manifest["preparer"], "preparer")
    testbench_path = _verify_artifact(repo, binding, manifest["testbench"], "testbench")
    paths.extend((preparer_path, testbench_path))
    selected_artifacts = {
        "generator": manifest["generator"],
        "preparer": manifest["preparer"],
        "testbench": manifest["testbench"],
    }
    for name, value in selected_artifacts.items():
        if value["path"] != policy_artifacts[name]["path"] or value["sha256"] != policy_artifacts[name]["sha256"]:
            raise ReleaseError(f"manifest {name} differs from canonical policy")

    native_collection = manifest["native_bindings"]
    if not isinstance(native_collection, list) or not native_collection:
        raise ReleaseError("native_bindings must be a nonempty array")
    native_paths = [
        _verify_artifact(repo, binding, artifact, f"native_bindings[{index}]")
        for index, artifact in enumerate(native_collection)
    ]
    paths.extend(native_paths)
    _validate_native_boundary(repo, binding, native_paths)

    registry_path = _verify_artifact(
        repo, binding, manifest["ppa_registry"], "PPA registry"
    )
    if manifest["ppa_registry"] != policy["ppa_registry"]:
        raise ReleaseError("PPA registry differs from canonical policy")
    _validate_ppa_registry(repo, binding, registry_path, native_paths)
    paths.append(registry_path)

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
    paths.extend((full_path, capacity_path))
    if (
        official["full_n16"]["path"] != policy_artifacts["full_manifest"]["path"]
        or official["full_n16"]["sha256"] != policy_artifacts["full_manifest"]["sha256"]
        or official["capacity_n16"]["path"] != policy_artifacts["capacity_manifest"]["path"]
        or official["capacity_n16"]["sha256"] != policy_artifacts["capacity_manifest"]["sha256"]
    ):
        raise ReleaseError("official manifests differ from canonical policy")

    golden_path = _verify_artifact(
        repo, binding, manifest["golden"], "golden",
        {"generator_version", "run_count"},
    )
    if manifest["golden"]["generator_version"] != required_version:
        raise ReleaseError("golden generator version mismatch")
    if (
        manifest["golden"]["path"] != policy_artifacts["golden"]["path"]
        or manifest["golden"]["sha256"] != policy_artifacts["golden"]["sha256"]
    ):
        raise ReleaseError("golden differs from canonical policy")
    paths.append(golden_path)
    if manifest["trace_abi"] != TRACE_ABI:
        raise ReleaseError("trace ABI is not the frozen address-only v4 contract")
    if len(paths) != len(set(paths)):
        raise ReleaseError("bound artifact paths must be unique")
    receipt_paths = _validate_executed_receipts(
        repo, binding, manifest["test_receipts"], policy
    )
    paths.extend(receipt_paths)
    if len(paths) != len(set(paths)):
        raise ReleaseError("bound artifact paths must be unique")

    counts = _verify_suite_documents(
        _json_bytes(_blob(repo, binding, full_path), full_path),
        _json_bytes(_blob(repo, binding, capacity_path), capacity_path),
        _json_bytes(_blob(repo, binding, golden_path), golden_path),
        full_path,
        required_version,
    )
    if official["full_n16"]["run_count"] != counts[0]:
        raise ReleaseError("declared full run count differs from bound manifest")
    if official["capacity_n16"]["run_count"] != counts[1]:
        raise ReleaseError("declared capacity run count differs from bound manifest")
    if manifest["golden"]["run_count"] != counts[2]:
        raise ReleaseError("declared golden run count differs from bound fixture")


def generate_manifest(
    repo: Path, output: Path, binding_kind: str, inputs: ReleaseInputs,
    release_kind: str = "current",
) -> dict[str, Any]:
    repo = repo.resolve()
    output = output if output.is_absolute() else Path.cwd() / output
    if os.path.lexists(output):
        raise ReleaseError("release output already exists, including a dangling symlink")
    if not output.parent.exists() or not output.parent.is_dir():
        raise ReleaseError("release output parent must be an existing directory")
    if output.parent.is_symlink():
        raise ReleaseError("release output parent must not be a symlink")
    output = output.parent.resolve(strict=True) / output.name
    try:
        output.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ReleaseError("release manifest must be a detached sidecar outside the repository")
    manifest = _manifest_from_inputs(repo, binding_kind, inputs, release_kind)
    validate_manifest(repo, manifest)
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise ReleaseError("release output appeared before no-replace publish") from exc
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
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
    return ReleaseInputs(
        policy=args.policy,
        generator=args.generator, preparer=args.preparer, testbench=args.testbench,
        native_bindings=tuple(args.native_binding),
        ppa_registry=args.ppa_registry,
        runners=tuple(args.runner), full_manifest=args.full_manifest,
        capacity_manifest=args.capacity_manifest, golden=args.golden,
        analyzers=tuple(args.analyzer), test_receipts=tuple(args.test_receipt),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="write a detached release sidecar")
    generate.add_argument("--repo", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--binding", choices=("commit", "tree"), default="commit")
    generate.add_argument(
        "--release-kind", choices=("current", "historical"), default="current",
        help="the trusted A1 policy currently authorizes current releases only",
    )
    generate.add_argument("--policy", required=True)
    generate.add_argument("--generator", required=True)
    generate.add_argument("--preparer", required=True)
    generate.add_argument("--testbench", required=True)
    generate.add_argument("--native-binding", action="append", required=True)
    generate.add_argument("--ppa-registry", required=True)
    generate.add_argument("--runner", action="append", required=True)
    generate.add_argument("--full-manifest", required=True)
    generate.add_argument("--capacity-manifest", required=True)
    generate.add_argument("--golden", required=True)
    generate.add_argument("--analyzer", action="append", required=True)
    generate.add_argument("--test-receipt", action="append", required=True)
    validate = sub.add_parser("validate", help="validate a detached sidecar")
    validate.add_argument("--repo", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "generate":
            manifest = generate_manifest(
                args.repo, args.output, args.binding, _inputs(args), args.release_kind
            )
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
