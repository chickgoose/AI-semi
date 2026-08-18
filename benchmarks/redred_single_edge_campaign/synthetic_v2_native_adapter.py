#!/usr/bin/env python3
"""Fail-closed, lossless view adapter for the native synthetic-v2 evidence.

The adapter does not rewrite either producer document or the archive.  It first
executes the producer's verifier and helper bytes from their pinned Git commit.
Only after that verifier succeeds does it expose an ordered campaign view whose
values and artifact references retain the producer-native names and schemas.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType
from typing import Any


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]

VIEW_SCHEMA = "redred_single_edge_synthetic_native_view_v1"
VIEW_STATUS = "PASS_NATIVE_VERIFIED"
CAMPAIGN_VIEW_SCHEMA = "redred_single_edge_campaign_normalized_view_v1"
ADAPTER_ID = "synthetic_v2_native_adapter_v1"
VIEW_SCHEMA_PATH = "benchmarks/redred_single_edge_campaign/synthetic_v2_native_view.schema.json"
VIEW_SCHEMA_SHA256 = "a7d32c473534f3f762f9791905b7fd59ada01bf8941b3022b354270b22ffe078"

PUBLICATION_PATH = "tests/a23_single_edge_synthetic_v2/synthetic_v2_publication.json"
RESULT_PATH = "tests/a23_single_edge_synthetic_v2/synthetic_v2_result.json"
ARCHIVE_PATH = "tests/a23_single_edge_synthetic_v2/synthetic_v2_export.tar.gz"
NATIVE_VERIFIER_PATH = "tests/a23_single_edge_synthetic_v2/run_v2.py"
NATIVE_HELPER_PATH = "tests/a23_single_edge_synthetic_export/export_preserved.py"

PUBLICATION_COMMIT = "0d5a1da74fb5a45e567f6b05a555d6a5db698147"
PUBLICATION_TREE = "eb3dd70d94c8ce0b2453ab1e55a2f7d0bdb678f9"
PACKAGE_COMMIT = "a6c74eb25d54a3e6bfed7de796b2998f991865e2"
PACKAGE_TREE = "72faf5fd9c56ed00ab7e114dce668cb0f255aabd"
SOURCE_COMMIT = "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b"
SOURCE_TREE = "e6030c7990f602a7fc1c73ac529b008b8e2c4133"
INTEGRATION_COMMIT = "a0a4eb38632245db8ff5937ea5b6c6e3f3839246"
INTEGRATION_TREE = "d0fda8da2c10693b5d7093e0e2d505590722c1ea"

EXPECTED_ARTIFACTS = {
    PUBLICATION_PATH: {
        "sha256": "95ddce3980e20321592552bdb64cb2aa1187acbfa3e93e28d2b8697d09fb0931",
        "size_bytes": 1852,
        "git_blob_oid": "6e36e5352a257a8a61aabb84e84c6856a5453099",
    },
    RESULT_PATH: {
        "sha256": "7a4a8a3f0d8238b9c5f3c72c6ae1d2bf026030e7247eddfd62d9c4c2bbf70554",
        "size_bytes": 159005,
        "git_blob_oid": "699db7aef0a6689c1c799f2e8664abee2bf6d08e",
    },
    ARCHIVE_PATH: {
        "sha256": "b3a2a69525880a0510865127d8a30fb8b78c01b5664d885d29ec6f7d37979786",
        "size_bytes": 12279031,
        "git_blob_oid": "32dc7e72dddb905082b10275e8920963643ac02a",
    },
}
EXPECTED_PINNED_CODE_BLOBS = {
    NATIVE_VERIFIER_PATH: "3843a2996cc01f145f336f5ffc7c2a148fe4a726",
    NATIVE_HELPER_PATH: "ac55abb7b27effc6bbe231d672bffafd3e147b4b",
}

NATIVE_PUBLICATION_SCHEMA = "a23_single_edge_synthetic_v2_publication_v1"
NATIVE_RESULT_SCHEMA = "a23_single_edge_synthetic_v2_result_v1"
NATIVE_MANIFEST_SCHEMA = "a23_single_edge_synthetic_v2_export_manifest_v1"
NATIVE_STATUS = "PASS_HARDENED_SYNTHETIC_V2"
NATIVE_EVIDENCE_CLASS = "TEAM_DEFINED_SYNTHETIC_FULL50_ACTUAL_SINGLE_EDGE_RTL_V2"
ARCHIVE_PREFIX = "a23-single-edge-synthetic-v2"
MANIFEST_MEMBER = f"{ARCHIVE_PREFIX}/MANIFEST.json"
RESULT_MEMBER = f"{ARCHIVE_PREFIX}/result/synthetic_v2_result.json"
OWNER_ORDER = ("a2", "a3")
MUTATION_ORDER = ("drop", "duplicate", "reorder", "reset_escape")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_OID = re.compile(r"[0-9a-f]{40}\Z")
MAX_STABLE_FILE_BYTES = 32 * 1024 * 1024


class SyntheticNativeAdapterError(RuntimeError):
    """The native tuple, its provenance, or its normalized mapping is invalid."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semantic_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def pretty(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            strict_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyntheticNativeAdapterError(f"{label} must be an object")
    if set(value) != keys:
        raise SyntheticNativeAdapterError(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def load_json_bytes(data: bytes, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SyntheticNativeAdapterError(
                    f"{label} contains duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise SyntheticNativeAdapterError(
            f"{label} contains non-standard JSON constant: {value}"
        )

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SyntheticNativeAdapterError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise SyntheticNativeAdapterError(f"{label} must be a JSON object")
    return value


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


def stable_file(path: Path, label: str) -> tuple[tuple[int, ...], bytes]:
    """Read one bounded, unlinked regular file without following its final name."""
    try:
        before = path.lstat()
    except OSError as error:
        raise SyntheticNativeAdapterError(f"cannot inspect {label}") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SyntheticNativeAdapterError(f"{label} is not a regular non-symlink file")
    if before.st_nlink != 1:
        raise SyntheticNativeAdapterError(f"{label} is hardlinked")
    if before.st_size > MAX_STABLE_FILE_BYTES:
        raise SyntheticNativeAdapterError(f"{label} exceeds its bounded size")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SyntheticNativeAdapterError(f"cannot safely open {label}") from error
    try:
        opened = os.fstat(descriptor)
        if _stat_identity(opened) != _stat_identity(before):
            raise SyntheticNativeAdapterError(f"{label} changed before read")
        blocks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > MAX_STABLE_FILE_BYTES:
                raise SyntheticNativeAdapterError(f"{label} exceeds its bounded size")
            blocks.append(block)
        after = os.fstat(descriptor)
        if _stat_identity(after) != _stat_identity(opened):
            raise SyntheticNativeAdapterError(f"{label} changed while read")
        data = b"".join(blocks)
        if len(data) != opened.st_size:
            raise SyntheticNativeAdapterError(f"{label} size changed while read")
        return _stat_identity(opened), data
    finally:
        os.close(descriptor)


def _reject_symlink_components(root: Path, relative: str, label: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise SyntheticNativeAdapterError(f"{label} path escapes repository")
    cursor = root
    if cursor.is_symlink():
        raise SyntheticNativeAdapterError("repository root is symlinked")
    for part in Path(relative).parts:
        cursor /= part
        if cursor.is_symlink():
            raise SyntheticNativeAdapterError(f"{label} traverses a symlink")
    return cursor


def git_output(root: Path, arguments: list[str], *, binary: bool = False) -> bytes | str:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    process = subprocess.run(
        ["git", "--no-replace-objects", *arguments], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=not binary,
        env=environment, check=False,
    )
    if process.returncode:
        error = process.stderr if not binary else process.stderr.decode(errors="replace")
        raise SyntheticNativeAdapterError(
            f"Git command failed: {' '.join(arguments)}: {error.strip()}"
        )
    return process.stdout


def git_bytes(root: Path, commit: str, relative: str) -> bytes:
    return git_output(root, ["show", f"{commit}:{relative}"], binary=True)  # type: ignore[return-value]


def git_oid(root: Path, revision: str) -> str:
    value = str(git_output(root, ["rev-parse", revision])).strip()
    if GIT_OID.fullmatch(value) is None:
        raise SyntheticNativeAdapterError(f"Git identity is malformed: {revision}")
    return value


def require_ancestor(root: Path, ancestor: str, descendant: str, label: str) -> None:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    process = subprocess.run(
        ["git", "--no-replace-objects", "merge-base", "--is-ancestor",
         ancestor, descendant], cwd=root, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, check=False,
    )
    if process.returncode != 0:
        raise SyntheticNativeAdapterError(f"{label} is not reachable")


def verify_git_provenance(
    root: Path, snapshots: dict[str, bytes],
) -> tuple[bytes, bytes, list[dict[str, Any]]]:
    top = Path(str(git_output(root, ["rev-parse", "--show-toplevel"])).strip()).resolve()
    if top != root.resolve():
        raise SyntheticNativeAdapterError("repository root differs from Git worktree")
    if str(git_output(root, ["rev-parse", "--show-object-format"])).strip() != "sha1":
        raise SyntheticNativeAdapterError("Git object format differs from pinned SHA-1")
    alternates = Path(str(git_output(
        root, ["rev-parse", "--git-path", "objects/info/alternates"],
    )).strip())
    if not alternates.is_absolute():
        alternates = root / alternates
    if alternates.exists() or alternates.is_symlink():
        raise SyntheticNativeAdapterError("Git alternate object store is forbidden")
    expected_trees = {
        PUBLICATION_COMMIT: PUBLICATION_TREE,
        PACKAGE_COMMIT: PACKAGE_TREE,
        SOURCE_COMMIT: SOURCE_TREE,
        INTEGRATION_COMMIT: INTEGRATION_TREE,
    }
    for commit, tree in expected_trees.items():
        if git_oid(root, f"{commit}^{{commit}}") != commit:
            raise SyntheticNativeAdapterError(f"pinned commit does not resolve exactly: {commit}")
        if git_oid(root, f"{commit}^{{tree}}") != tree:
            raise SyntheticNativeAdapterError(f"pinned tree differs: {commit}")
    require_ancestor(root, PACKAGE_COMMIT, PUBLICATION_COMMIT, "producer package commit")
    require_ancestor(root, PUBLICATION_COMMIT, "HEAD", "publication commit")
    publication_inventory = []
    for relative, expected in EXPECTED_ARTIFACTS.items():
        data = snapshots[relative]
        if digest(data) != expected["sha256"] or len(data) != expected["size_bytes"]:
            raise SyntheticNativeAdapterError(f"published native bytes differ: {relative}")
        if git_bytes(root, PUBLICATION_COMMIT, relative) != data:
            raise SyntheticNativeAdapterError(f"published native Git bytes differ: {relative}")
        blob = git_oid(root, f"{PUBLICATION_COMMIT}:{relative}")
        if blob != expected["git_blob_oid"]:
            raise SyntheticNativeAdapterError(f"published native Git blob differs: {relative}")
        publication_inventory.append({
            "path": relative, "sha256": expected["sha256"],
            "size_bytes": expected["size_bytes"], "git_blob_oid": blob,
        })
    verifier = git_bytes(root, PACKAGE_COMMIT, NATIVE_VERIFIER_PATH)
    helper = git_bytes(root, PACKAGE_COMMIT, NATIVE_HELPER_PATH)
    for relative, data in ((NATIVE_VERIFIER_PATH, verifier), (NATIVE_HELPER_PATH, helper)):
        if git_oid(root, f"{PACKAGE_COMMIT}:{relative}") != EXPECTED_PINNED_CODE_BLOBS[relative]:
            raise SyntheticNativeAdapterError(f"pinned native verifier blob differs: {relative}")
        current_path = _reject_symlink_components(root, relative, "native verifier input")
        _, current = stable_file(current_path, f"native verifier input {relative}")
        if current != data:
            raise SyntheticNativeAdapterError(f"working native verifier bytes differ: {relative}")
    return verifier, helper, publication_inventory


def load_pinned_native(verifier: bytes, helper: bytes) -> ModuleType:
    """Load only the exact verifier/helper bytes from the producer Git object."""
    helper_name = "export_preserved"
    previous_helper = sys.modules.get(helper_name)
    path_snapshot = list(sys.path)
    try:
        helper_module = ModuleType(helper_name)
        helper_module.__file__ = str(PROJECT / NATIVE_HELPER_PATH)
        exec(compile(helper, helper_module.__file__, "exec"), helper_module.__dict__)
        sys.modules[helper_name] = helper_module
        native = ModuleType("redred_synthetic_v2_pinned_native_verifier")
        native.__file__ = str(PROJECT / NATIVE_VERIFIER_PATH)
        exec(compile(verifier, native.__file__, "exec"), native.__dict__)
        return native
    except Exception as error:
        raise SyntheticNativeAdapterError("cannot load pinned native verifier") from error
    finally:
        sys.path[:] = path_snapshot
        if previous_helper is None:
            sys.modules.pop(helper_name, None)
        else:
            sys.modules[helper_name] = previous_helper


def _inventory(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = manifest.get("inventory")
    if not isinstance(rows, list):
        raise SyntheticNativeAdapterError("native manifest inventory is not a list")
    result: dict[str, dict[str, Any]] = {}
    for position, value in enumerate(rows):
        row = exact(value, {"path", "role", "size_bytes", "sha256"},
                    f"native manifest inventory[{position}]")
        path = row["path"]
        if not isinstance(path, str) or path in result:
            raise SyntheticNativeAdapterError("native manifest inventory path differs")
        result[path] = row
    return result


def artifact_ref(
    inventory: dict[str, dict[str, Any]], relative: str, expected_sha: str | None = None,
) -> dict[str, Any]:
    if relative not in inventory:
        raise SyntheticNativeAdapterError(f"normalized artifact is absent: {relative}")
    row = inventory[relative]
    if expected_sha is not None and row["sha256"] != expected_sha:
        raise SyntheticNativeAdapterError(f"normalized artifact hash differs: {relative}")
    return {
        "member": f"{ARCHIVE_PREFIX}/{relative}",
        "native_relative_path": relative,
        "role": row["role"],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
    }


def _sequence_index(
    rows: Any, traffic_order: list[str], label: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    expected_pairs = [(owner, trace) for owner in OWNER_ORDER for trace in traffic_order]
    if not isinstance(rows, list) or len(rows) != len(expected_pairs):
        raise SyntheticNativeAdapterError(f"{label} roster length differs")
    observed: list[tuple[str, str]] = []
    result = {}
    expected_keys = {
        "owner", "trace", "event_row_count", "accepted_ordinal_count",
        "retired_ordinal_count", "event_row_sequence_sha256", "accept_order_sha256",
        "retire_order_sha256", "ordinal_csv_sha256",
        "ordinal_simulation_log_sha256",
    }
    for position, value in enumerate(rows):
        row = exact(value, expected_keys, f"{label}[{position}]")
        pair = (row["owner"], row["trace"])
        observed.append(pair)
        result[pair] = row
    if observed != expected_pairs or len(result) != len(expected_pairs):
        raise SyntheticNativeAdapterError(f"{label} owner/trace order differs")
    return result


def _git_inventory(
    root: Path, commit: str, paths: list[str], hashes: dict[str, str], label: str,
) -> list[dict[str, Any]]:
    result = []
    for relative in paths:
        data = git_bytes(root, commit, relative)
        if digest(data) != hashes[relative]:
            raise SyntheticNativeAdapterError(f"{label} Git bytes differ: {relative}")
        result.append({
            "path": relative, "sha256": hashes[relative],
            "size_bytes": len(data),
            "git_blob_oid": git_oid(root, f"{commit}:{relative}"),
        })
    return result


def build_normalized(
    root: Path, publication: dict[str, Any], v2_result: dict[str, Any],
    manifest: dict[str, Any], payload: dict[str, bytes], native_report: dict[str, Any],
    publication_inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    fixed_publication = {
        "schema": NATIVE_PUBLICATION_SCHEMA, "status": NATIVE_STATUS,
        "package_commit": PACKAGE_COMMIT, "package_tree": PACKAGE_TREE,
        "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
        "integration_commit": INTEGRATION_COMMIT, "integration_tree": INTEGRATION_TREE,
        "export_sha256": EXPECTED_ARTIFACTS[ARCHIVE_PATH]["sha256"],
        "export_size_bytes": EXPECTED_ARTIFACTS[ARCHIVE_PATH]["size_bytes"],
        "v2_result_sha256": EXPECTED_ARTIFACTS[RESULT_PATH]["sha256"],
        "v2_result_size_bytes": EXPECTED_ARTIFACTS[RESULT_PATH]["size_bytes"],
        "export_inventory_entry_count": 1520,
        "physical_status": "HOLD",
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    }
    for key, expected in fixed_publication.items():
        if not strict_equal(publication.get(key), expected):
            raise SyntheticNativeAdapterError(f"native publication field differs: {key}")
    if v2_result.get("schema") != NATIVE_RESULT_SCHEMA \
            or v2_result.get("status") != NATIVE_STATUS \
            or v2_result.get("evidence_class") != NATIVE_EVIDENCE_CLASS:
        raise SyntheticNativeAdapterError("native v2 result identity differs")
    if manifest.get("schema") != NATIVE_MANIFEST_SCHEMA \
            or manifest.get("status") != NATIVE_STATUS \
            or manifest.get("archive_prefix") != ARCHIVE_PREFIX:
        raise SyntheticNativeAdapterError("native export manifest identity differs")
    if not strict_equal(v2_result.get("identities"), {
        "package_commit": PACKAGE_COMMIT, "package_tree": PACKAGE_TREE,
        "package_input_identity_sha256": publication["package_input_identity_sha256"],
        "source_commit": SOURCE_COMMIT, "source_tree": SOURCE_TREE,
        "integration_commit": INTEGRATION_COMMIT, "integration_tree": INTEGRATION_TREE,
        "tool_identity_sha256": publication["tool_identity_sha256"],
        "trace_identity_sha256": publication["trace_identity_sha256"],
        "pins_sha256": publication["pins_sha256"],
    }):
        raise SyntheticNativeAdapterError("native result/publication provenance differs")

    primary_bytes = payload.get("primary/result.json")
    reproduction_bytes = payload.get("reproduction/result.json")
    if primary_bytes is None or reproduction_bytes is None:
        raise SyntheticNativeAdapterError("native primary/reproduction result is absent")
    primary = load_json_bytes(primary_bytes, "native primary result")
    reproduction = load_json_bytes(reproduction_bytes, "native reproduction result")
    dataset = exact(v2_result.get("dataset"), {
        "id", "source_class", "organizer_official", "trace_count",
        "shared_prepared_trace_count", "per_campaign_actual_full50_executions",
        "combined_actual_full50_executions", "trace_identities",
    }, "native dataset")
    if not strict_equal({key: dataset[key] for key in (
        "id", "source_class", "organizer_official", "trace_count",
        "shared_prepared_trace_count", "per_campaign_actual_full50_executions",
        "combined_actual_full50_executions",
    )}, {
        "id": "full50", "source_class": "TEAM_DEFINED_SYNTHETIC",
        "organizer_official": False, "trace_count": 50,
        "shared_prepared_trace_count": 50,
        "per_campaign_actual_full50_executions": 100,
        "combined_actual_full50_executions": 200,
    }):
        raise SyntheticNativeAdapterError("native dataset classification/counters differ")
    trace_identities = dataset["trace_identities"]
    if not isinstance(trace_identities, list) or len(trace_identities) != 50:
        raise SyntheticNativeAdapterError("native trace identity roster differs")
    traffic_order = [row.get("name") for row in trace_identities if isinstance(row, dict)]
    if len(traffic_order) != 50 or len(set(traffic_order)) != 50 \
            or any(not isinstance(name, str) or not name for name in traffic_order):
        raise SyntheticNativeAdapterError("native traffic run order differs")
    trace_by_name = {row["name"]: row for row in trace_identities}

    primary_sequences = _sequence_index(
        v2_result["sequence_evidence"]["primary_full50_runs"],
        traffic_order, "native primary ordinal evidence",
    )
    reproduction_sequences = _sequence_index(
        v2_result["semantic_reproduction"]["reproduction_full50_runs"],
        traffic_order, "native reproduction ordinal evidence",
    )
    manifest_inventory = _inventory(manifest)
    primary_owners = exact(primary.get("owners"), set(OWNER_ORDER), "primary owners")
    reproduction_owners = exact(
        reproduction.get("owners"), set(OWNER_ORDER), "reproduction owners",
    )
    normalized_owners = []
    for owner in OWNER_ORDER:
        primary_owner = primary_owners[owner]
        reproduction_owner = reproduction_owners[owner]
        primary_runs = primary_owner.get("full50", {}).get("runs")
        reproduction_runs = reproduction_owner.get("full50", {}).get("runs")
        if not isinstance(primary_runs, dict) or not isinstance(reproduction_runs, dict) \
                or set(primary_runs) != set(traffic_order) \
                or set(reproduction_runs) != set(traffic_order):
            raise SyntheticNativeAdapterError(f"native {owner} traffic roster differs")
        runs = []
        for name in traffic_order:
            primary_metrics = primary_runs[name]
            reproduction_metrics = reproduction_runs[name]
            if not strict_equal(primary_metrics, reproduction_metrics):
                raise SyntheticNativeAdapterError(
                    f"native {owner}/{name} reproduction metrics differ"
                )
            primary_sequence = primary_sequences[(owner, name)]
            reproduction_sequence = reproduction_sequences[(owner, name)]
            left = {key: value for key, value in primary_sequence.items()
                    if key != "ordinal_simulation_log_sha256"}
            right = {key: value for key, value in reproduction_sequence.items()
                     if key != "ordinal_simulation_log_sha256"}
            if not strict_equal(left, right):
                raise SyntheticNativeAdapterError(
                    f"native {owner}/{name} reproduced ordinal semantics differ"
                )
            if (primary_sequence["event_row_count"] != primary_metrics["generated"] or
                    primary_sequence["accepted_ordinal_count"] != primary_metrics["accepted"] or
                    primary_sequence["retired_ordinal_count"] != primary_metrics["retired"]):
                raise SyntheticNativeAdapterError(
                    f"native {owner}/{name} ordinal counters differ from metrics"
                )
            trace = trace_by_name[name]
            if (primary_metrics["trace_sha256"] != trace["trace_sha256"] or
                    primary_metrics["prepared_trace_sha256"] !=
                    trace["prepared_trace_sha256"]):
                raise SyntheticNativeAdapterError(
                    f"native {owner}/{name} trace identity differs"
                )

            def campaign_run(campaign: str, metrics: dict[str, Any],
                             sequence: dict[str, Any]) -> dict[str, Any]:
                artifact_base = f"{campaign}/work/artifacts/{owner}/none/{name}"
                ordinal_base = f"{campaign}/work/ordinal/{owner}/{name}"
                generator_base = f"{campaign}/work/generator-v4/{name}"
                return {
                    "metrics": copy.deepcopy(metrics),
                    "trace": artifact_ref(
                        manifest_inventory, f"{generator_base}.events.jsonl",
                        metrics["trace_sha256"],
                    ),
                    "prepared_trace": artifact_ref(
                        manifest_inventory, f"{campaign}/work/prepared/{name}.trace",
                        metrics["prepared_trace_sha256"],
                    ),
                    "events": artifact_ref(
                        manifest_inventory, f"{artifact_base}/events.csv",
                        metrics["events_sha256"],
                    ),
                    "summary": artifact_ref(
                        manifest_inventory, f"{artifact_base}/summary.csv",
                        metrics["summary_sha256"],
                    ),
                    "simulation_log": artifact_ref(
                        manifest_inventory, f"{artifact_base}/simulation.log",
                    ),
                    "ordinal_csv": artifact_ref(
                        manifest_inventory, f"{ordinal_base}/ordinals.csv",
                        sequence["ordinal_csv_sha256"],
                    ),
                    "ordinal_simulation_log": artifact_ref(
                        manifest_inventory, f"{ordinal_base}/simulation.log",
                        sequence["ordinal_simulation_log_sha256"],
                    ),
                    "ordinal_evidence": copy.deepcopy(sequence),
                }

            runs.append({
                "trace": name,
                "trace_identity": copy.deepcopy(trace),
                "primary": campaign_run("primary", primary_metrics, primary_sequence),
                "reproduction": campaign_run(
                    "reproduction", reproduction_metrics, reproduction_sequence,
                ),
            })
        normalized_owners.append({
            "owner": owner,
            "primary_aggregate": copy.deepcopy(primary_owner["full50"]["aggregate"]),
            "reproduction_aggregate": copy.deepcopy(
                reproduction_owner["full50"]["aggregate"]
            ),
            "primary_reset": copy.deepcopy(primary_owner["reset"]),
            "reproduction_reset": copy.deepcopy(reproduction_owner["reset"]),
            "primary_activation": copy.deepcopy(primary_owner["mutation_activation"]),
            "reproduction_activation": copy.deepcopy(
                reproduction_owner["mutation_activation"]
            ),
            "runs": runs,
        })

    expected_mutations = [
        (owner, mutation) for owner in OWNER_ORDER for mutation in MUTATION_ORDER
    ]
    for label, mutations in (
        ("primary", primary.get("mutations")),
        ("reproduction", reproduction.get("mutations")),
    ):
        if not isinstance(mutations, list) or [
            (row.get("owner"), row.get("mutation"))
            for row in mutations if isinstance(row, dict)
        ] != expected_mutations:
            raise SyntheticNativeAdapterError(f"native {label} mutation order differs")

    verified_files = primary["provenance"]["verified_files"]
    rtl_paths = primary["provenance"]["actual_rtl_git"]["verified_rtl_paths"]
    source_inventory = _git_inventory(
        root, SOURCE_COMMIT, rtl_paths, verified_files, "source RTL",
    )
    integration_inventory = _git_inventory(
        root, INTEGRATION_COMMIT, rtl_paths, verified_files, "integration RTL",
    )
    package_inputs = []
    repository_prefix = "inputs/repository/"
    for row in manifest["inventory"]:
        relative = row["path"]
        if not relative.startswith(repository_prefix):
            continue
        git_path = relative.removeprefix(repository_prefix)
        data = git_bytes(root, PACKAGE_COMMIT, git_path)
        if digest(data) != row["sha256"]:
            raise SyntheticNativeAdapterError(
                f"native archived package Git bytes differ: {git_path}"
            )
        package_inputs.append({
            "path": git_path, "archive_member": f"{ARCHIVE_PREFIX}/{relative}",
            "native_role": row["role"], "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "git_blob_oid": git_oid(root, f"{PACKAGE_COMMIT}:{git_path}"),
        })

    normalized = {
        "native_identity": {
            "publication_schema": publication["schema"],
            "publication_status": publication["status"],
            "result_schema": v2_result["schema"],
            "result_status": v2_result["status"],
            "evidence_class": v2_result["evidence_class"],
            "manifest_schema": manifest["schema"],
            "archive_prefix": manifest["archive_prefix"],
        },
        "dataset": copy.deepcopy(dataset),
        "execution_accounting": copy.deepcopy(v2_result["execution_accounting"]),
        "provenance": {
            "git_object_format": "sha1",
            "git_replace_objects": "DISABLED",
            "git_alternate_object_stores": "FORBIDDEN",
            "publication_commit": PUBLICATION_COMMIT,
            "publication_tree": PUBLICATION_TREE,
            "publication_inventory": publication_inventory,
            "package": copy.deepcopy(v2_result["identities"]),
            "package_input_inventory": package_inputs,
            "source_inventory": source_inventory,
            "integration_inventory": integration_inventory,
        },
        "owner_order": list(OWNER_ORDER),
        "traffic_run_order": traffic_order,
        "owners": normalized_owners,
        "primary_mutations": copy.deepcopy(primary["mutations"]),
        "reproduction_mutations": copy.deepcopy(reproduction["mutations"]),
        "semantic_reproduction": copy.deepcopy(v2_result["semantic_reproduction"]),
        "sequence_definition": {
            key: copy.deepcopy(value) for key, value in v2_result["sequence_evidence"].items()
            if key != "primary_full50_runs"
        },
        "qualification": copy.deepcopy(v2_result["qualification"]),
    }
    return {
        "schema": VIEW_SCHEMA,
        "status": VIEW_STATUS,
        "campaign_input_slot": "synthetic_v2",
        "adapter_mode": "LOSSLESS_REFERENCE_VIEW_NO_RELABEL_NO_REPACK",
        "native_verification": {
            "verifier_execution": "PINNED_GIT_BYTES",
            "verifier_commit": PACKAGE_COMMIT,
            "verifier_git_blob_oid": EXPECTED_PINNED_CODE_BLOBS[NATIVE_VERIFIER_PATH],
            "helper_git_blob_oid": EXPECTED_PINNED_CODE_BLOBS[NATIVE_HELPER_PATH],
            "view_schema_path": VIEW_SCHEMA_PATH,
            "view_schema_sha256": VIEW_SCHEMA_SHA256,
            "result": copy.deepcopy(native_report),
        },
        "native_artifacts": {
            "publication_commit": PUBLICATION_COMMIT,
            "publication_tree": PUBLICATION_TREE,
            "publication": copy.deepcopy(publication_inventory[0]),
            "result": copy.deepcopy(publication_inventory[1]),
            "archive": {
                **copy.deepcopy(publication_inventory[2]),
                "archive_prefix": ARCHIVE_PREFIX,
                "manifest_member": MANIFEST_MEMBER,
                "manifest_schema": manifest["schema"],
                "manifest_sha256": publication["export_manifest_sha256"],
                "inventory_entry_count": manifest["inventory_entry_count"],
                "inventory_size_bytes": manifest["inventory_size_bytes"],
                "result_member": RESULT_MEMBER,
            },
        },
        "native_documents": {
            "publication": copy.deepcopy(publication),
            "v2_result": copy.deepcopy(v2_result),
            "primary_result": copy.deepcopy(primary),
            "reproduction_result": copy.deepcopy(reproduction),
            "export_manifest": copy.deepcopy(manifest),
        },
        "normalized": normalized,
        "normalized_sha256": digest(semantic_bytes(normalized)),
        "claim_boundary": {
            "native_schema_preserved": True,
            "native_paths_preserved": True,
            "archive_repacked": False,
            "traffic_relabeled": False,
            "official_contest_claimed": False,
            "physical_claimed": False,
            "power_claimed": False,
            "selection_claimed": False,
            "release_claimed": False,
            "canonical_campaign_status":
                "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
        },
    }


def validate_view(view: dict[str, Any]) -> None:
    exact(view, {
        "schema", "status", "campaign_input_slot", "adapter_mode",
        "native_verification", "native_artifacts", "native_documents",
        "normalized", "normalized_sha256", "claim_boundary",
    }, "synthetic native view")
    fixed = {
        "schema": VIEW_SCHEMA, "status": VIEW_STATUS,
        "campaign_input_slot": "synthetic_v2",
        "adapter_mode": "LOSSLESS_REFERENCE_VIEW_NO_RELABEL_NO_REPACK",
    }
    for key, expected in fixed.items():
        if view[key] != expected:
            raise SyntheticNativeAdapterError(f"synthetic native view {key} differs")
    if not isinstance(view["normalized_sha256"], str) \
            or SHA256.fullmatch(view["normalized_sha256"]) is None \
            or view["normalized_sha256"] != digest(semantic_bytes(view["normalized"])):
        raise SyntheticNativeAdapterError("synthetic native view digest differs")
    expected_boundary = {
        "native_schema_preserved": True, "native_paths_preserved": True,
        "archive_repacked": False, "traffic_relabeled": False,
        "official_contest_claimed": False, "physical_claimed": False,
        "power_claimed": False, "selection_claimed": False,
        "release_claimed": False,
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    }
    if not strict_equal(view["claim_boundary"], expected_boundary):
        raise SyntheticNativeAdapterError("synthetic native claim boundary differs")


def campaign_normalized_view(native_view: dict[str, Any]) -> dict[str, Any]:
    """Project the verified native view into the aggregate gate's strict shape.

    This projection deliberately holds the policy and candidate gates.  Native
    tuple verification establishes source evidence integrity, not a canonical
    campaign decision, candidate selection, or release authority.
    """
    validate_view(native_view)
    if native_view["status"] != VIEW_STATUS \
            or native_view["claim_boundary"]["canonical_campaign_status"] != \
            "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT":
        raise SyntheticNativeAdapterError("native view cannot enter campaign normalization")
    _, adapter_bytes = stable_file(Path(__file__), "synthetic native adapter")
    return {
        "schema": CAMPAIGN_VIEW_SCHEMA,
        "slot": "synthetic_v2",
        "verification": {
            "status": "PASS",
            "separately_verified": True,
            "adapter_id": ADAPTER_ID,
            "adapter_sha256": digest(adapter_bytes),
            "source_result_sha256": EXPECTED_ARTIFACTS[RESULT_PATH]["sha256"],
            "source_publication_sha256": EXPECTED_ARTIFACTS[PUBLICATION_PATH]["sha256"],
        },
        "classification": {
            "evidence_status": "PASS",
            "source_class": "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": True,
            "official_contest_traffic": False,
            "p6_evidence_used": False,
        },
        "campaign_units": {
            "family_id": "team-full50-family",
            "unit_kind": "SYNTHETIC_TRACE_CAMPAIGN",
            "independent_sample_count": 50,
            "retiming_labels": [],
            "retimings_are_independent_samples": False,
            "pooling_with_other_slots_permitted": False,
        },
        "shared_gates": {
            "native_tuple_integrity": "PASS",
            "canonical_campaign_policy": "HOLD",
        },
        "candidates": {
            "A2": {
                "role": "PRIMARY",
                "semantic_class": "AGGREGATE_WEIGHTED_PERFORMANCE",
                "gate_status": "HOLD",
                "failure_scope": "UNRESOLVED",
                "reason_codes": ["CANONICAL_POLICY_NOT_ATTESTED_BY_NATIVE_VERIFICATION"],
            },
            "A3": {
                "role": "SEMANTIC_FALLBACK",
                "semantic_class": "EXACT_SCALAR_PREFIX",
                "gate_status": "HOLD",
                "failure_scope": "UNRESOLVED",
                "reason_codes": ["CANONICAL_POLICY_NOT_ATTESTED_BY_NATIVE_VERIFICATION"],
            },
        },
        "claims": {
            "official": False, "physical": False, "power": False, "release": False,
        },
    }


def evaluate(root: Path = PROJECT) -> dict[str, Any]:
    root = root.resolve(strict=True)
    schema_path = _reject_symlink_components(root, VIEW_SCHEMA_PATH, "view schema")
    _, schema_data = stable_file(schema_path, "view schema")
    if digest(schema_data) != VIEW_SCHEMA_SHA256:
        raise SyntheticNativeAdapterError("view schema bytes differ")
    paths = {
        relative: _reject_symlink_components(root, relative, "native artifact")
        for relative in EXPECTED_ARTIFACTS
    }
    initial: dict[str, tuple[tuple[int, ...], bytes]] = {
        relative: stable_file(path, f"native artifact {relative}")
        for relative, path in paths.items()
    }
    snapshots = {relative: value[1] for relative, value in initial.items()}
    publication = load_json_bytes(snapshots[PUBLICATION_PATH], "native publication")
    verifier_bytes, helper_bytes, publication_inventory = verify_git_provenance(
        root, snapshots,
    )
    native = load_pinned_native(verifier_bytes, helper_bytes)
    try:
        native_report = native.validate_reopened(
            paths[ARCHIVE_PATH], paths[RESULT_PATH], paths[PUBLICATION_PATH],
        )
        manifest, payload = native.read_archive(paths[ARCHIVE_PATH])
        v2_result = native.load_json_bytes(snapshots[RESULT_PATH], "native v2 result")
    except Exception as error:
        raise SyntheticNativeAdapterError(f"native verification failed: {error}") from error
    for relative, path in paths.items():
        final = stable_file(path, f"native artifact {relative}")
        if final != initial[relative]:
            raise SyntheticNativeAdapterError(
                f"native artifact changed during verification: {relative}"
            )
    if payload.get("result/synthetic_v2_result.json") != snapshots[RESULT_PATH]:
        raise SyntheticNativeAdapterError("native embedded result differs after verification")
    view = build_normalized(
        root, publication, v2_result, manifest, payload,
        native_report, publication_inventory,
    )
    validate_view(view)
    return view


def write_new(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise SyntheticNativeAdapterError("output already exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise SyntheticNativeAdapterError("output parent is not a real directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--campaign-view", action="store_true",
        help="emit the strict aggregate-gate view instead of the full native view",
    )
    args = parser.parse_args()
    try:
        view = evaluate()
        data = pretty(campaign_normalized_view(view) if args.campaign_view else view)
        if args.output is not None:
            write_new(args.output, data)
        sys.stdout.buffer.write(data)
        return 0
    except (SyntheticNativeAdapterError, OSError, subprocess.SubprocessError,
            ValueError, KeyError, TypeError) as error:
        print(f"REDRED_SYNTHETIC_NATIVE_ADAPTER_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
