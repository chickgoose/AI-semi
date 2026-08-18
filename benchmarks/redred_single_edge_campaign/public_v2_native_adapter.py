#!/usr/bin/env python3
"""Fail-closed campaign adapter for the public-projected-v2 native schema.

The adapter validates and reads the producer's original bytes.  It never
extracts to disk, relabels the evidence, or creates a replacement archive.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
from types import ModuleType
from typing import Any


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
PRODUCER = PROJECT / "tests/a23_public_projected_v2/run.py"
PUBLICATION = PROJECT / "tests/a23_public_projected_v2/public_projected_v2_publication.json"
ARCHIVE = PROJECT / "tests/a23_public_projected_v2/public_projected_v2_export.tar.gz"
RESULT = PROJECT / "tests/a23_public_projected_v2/public_projected_v2_result.json"
REPRODUCTION = PROJECT / "tests/a23_public_projected_v2/public_projected_v2_reproduction_result.json"
SCHEMA = PACKAGE / "public_v2_native_adapter.schema.json"

PRODUCER_SHA256 = "bc0de21a47ec5465ae8a96e4e9d2d10764fe2f99452ee8a846dc1f360a7bc756"
PUBLICATION_SHA256 = "7cd68ab241ba1f824ed1e7b85bbdca85bf15b832743ba108877640b520ec4c23"
ARCHIVE_SHA256 = "d14721e8b4fef9d616af2bee92f4db80754707d545c60ef2b2616b9f14f6ccf2"
RESULT_SHA256 = "815c752f4852790d4db5c3c935cc2edc5821fee9a36f2e83c35d3ec8b73c5c12"
REPRODUCTION_SHA256 = "24da3a81fa34a5b3b4fd847fe9d49f937cdb45bf6dc64343adcc903476269482"
MANIFEST_SHA256 = "b24b521d5d31ae4fe39f09df230dd7e399b14e1695fe8aa23e803a8736f72cca"
SEMANTIC_SHA256 = "7491680311effb422b868fddd783d0ec6eb9f3aece54907260e52198fc26d157"
SEMANTIC_DEFINITION_SHA256 = "2d9f0585d127e9f18429ace8ac6d70218b2ac2377092d453d4e04cfa53f161c2"
PAYLOAD_COMMIT = "d4c247503af5bb322b92604bb342bc0ebf9e045e"
REVIEWED_PUBLICATION_COMMIT = "999e9401178cc5210bc863629cfa21d3a0241575"
EXECUTION_COMMIT = "429dbcb80cb14b04d185353d7a2f92c36238701b"
EXECUTION_SOURCE_COMMIT = "9092247aa07459bc67da66495dc643003957f42a"
INTEGRATION_COMMIT = "775ec4ba081c611cac88d4d75b05a1da3c68a543"
PUBLICATION_SIZE_BYTES = 1921
ARCHIVE_SIZE_BYTES = 261507
RESULT_SIZE_BYTES = 35288
REPRODUCTION_SIZE_BYTES = 35288
OWNERS = ("a2", "a3")
SCENARIOS = ("1x", "64x", "256x")
HEX40 = re.compile(r"^[0-9a-f]{40}$")

REPORT_KEYS = {
    "schema", "status", "adapter_mode", "source_class", "evidence_class",
    "canonical_redred_traffic", "official_redred_traffic", "p6_evidence_used",
    "release_status", "selection_status", "native_schemas", "raw_artifacts",
    "closed_inventory", "identity_accounting", "ordinal_validation",
    "semantic_validation", "git_provenance", "owners", "claim_boundary",
}
NORMALIZED_VIEW_KEYS = {
    "schema", "slot", "verification", "classification", "campaign_units",
    "shared_gates", "candidates", "claims",
}
EXPECTED_NATIVE_SCHEMAS = {
    "publication": "a23_public_projected_v2_publication_v2",
    "archive_manifest": "a23_public_projected_v2_export_manifest_v2",
    "closed_inventory": "a23_public_projected_v2_closed_inventory_v2",
    "result": "a23_public_projected_v2_result_v2",
    "ordinal": "a23_accept_retire_sequence_ordinals_v2",
}
EXPECTED_RAW_ARTIFACTS = {
    "publication": {"sha256": PUBLICATION_SHA256, "size_bytes": PUBLICATION_SIZE_BYTES},
    "archive": {"sha256": ARCHIVE_SHA256, "size_bytes": ARCHIVE_SIZE_BYTES},
    "manifest": {"sha256": MANIFEST_SHA256, "member": "MANIFEST.json"},
    "result": {"sha256": RESULT_SHA256, "size_bytes": RESULT_SIZE_BYTES},
    "reproduction": {
        "sha256": REPRODUCTION_SHA256, "size_bytes": REPRODUCTION_SIZE_BYTES,
    },
}
EXPECTED_CLOSED_INVENTORY = {
    "schema": "a23_public_projected_v2_closed_inventory_v2",
    "entry_count_excluding_manifest": 80,
    "archive_member_count_including_manifest": 81,
    "extra_entries_allowed": False,
    "ordered": True,
}
EXPECTED_IDENTITY_ACCOUNTING = {
    "pooled_3300_unique_events": False,
    "scenario_retimings": ["1x", "64x", "256x"],
    "unique_projected_window_events": 1100,
}
EXPECTED_ORDINAL_VALIDATION = {
    "schema": "a23_accept_retire_sequence_ordinals_v2",
    "accept_and_retire_exact_contiguous": True,
    "same_cycle_order_reconstructable": True,
    "accepted_counts": {
        "a2": {"1x": 1019, "64x": 1019, "256x": 906},
        "a3": {"1x": 1019, "64x": 1013, "256x": 817},
    },
}
EXPECTED_SEMANTIC_VALIDATION = {
    "definition_sha256": SEMANTIC_DEFINITION_SHA256,
    "primary_sha256": SEMANTIC_SHA256,
    "reproduction_sha256": SEMANTIC_SHA256,
    "matched": True,
}
EXPECTED_GIT_PROVENANCE = {
    "integration_commit": INTEGRATION_COMMIT,
    "execution_source_commit": EXECUTION_SOURCE_COMMIT,
    "execution_commit": EXECUTION_COMMIT,
    "payload_commit": PAYLOAD_COMMIT,
    "reviewed_publication_commit": REVIEWED_PUBLICATION_COMMIT,
    "payload_commit_meaning": "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS",
    "self_referential_commit_claim": False,
}
EXPECTED_OWNER_METRICS = {
    "a2": {
        "1x": {
            "generated": 1100, "source_overrun": 81, "accepted": 1019,
            "retired": 1019, "fixed_window_retired": 1018,
            "fixed_window_cycles": 153693, "fixed_window_events_per_cycle": 0.006623594,
        },
        "64x": {
            "generated": 1100, "source_overrun": 81, "accepted": 1019,
            "retired": 1019, "fixed_window_retired": 1016,
            "fixed_window_cycles": 2402, "fixed_window_events_per_cycle": 0.422980849,
        },
        "256x": {
            "generated": 1100, "source_overrun": 194, "accepted": 906,
            "retired": 906, "fixed_window_retired": 899,
            "fixed_window_cycles": 601, "fixed_window_events_per_cycle": 1.495840266,
        },
    },
    "a3": {
        "1x": {
            "generated": 1100, "source_overrun": 81, "accepted": 1019,
            "retired": 1019, "fixed_window_retired": 1018,
            "fixed_window_cycles": 153693, "fixed_window_events_per_cycle": 0.006623594,
        },
        "64x": {
            "generated": 1100, "source_overrun": 87, "accepted": 1013,
            "retired": 1013, "fixed_window_retired": 1010,
            "fixed_window_cycles": 2402, "fixed_window_events_per_cycle": 0.420482931,
        },
        "256x": {
            "generated": 1100, "source_overrun": 283, "accepted": 817,
            "retired": 817, "fixed_window_retired": 811,
            "fixed_window_cycles": 601, "fixed_window_events_per_cycle": 1.349417637,
        },
    },
}
EXPECTED_CLAIM_BOUNDARY = {
    "canonical_campaign_promoted": False,
    "official_contest_evidence_claimed": False,
    "synthetic_public_pooling": "FORBIDDEN",
    "archive_extracted_or_repacked": False,
    "producer_schema_relabelled": False,
    "system_release": "HOLD",
}


class PublicV2NativeAdapterError(RuntimeError):
    """The original producer tuple cannot be consumed without reinterpretation."""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_upstream(path: Path = PRODUCER) -> ModuleType:
    payload = path.read_bytes()
    if sha256(payload) != PRODUCER_SHA256:
        raise PublicV2NativeAdapterError("upstream public-v2 validator bytes differ")
    specification = importlib.util.spec_from_file_location(
        "redred_public_projected_v2_upstream", path,
    )
    if specification is None or specification.loader is None:
        raise PublicV2NativeAdapterError("cannot load upstream public-v2 validator")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PublicV2NativeAdapterError(f"{label} must be an object")
    if set(value) != keys:
        raise PublicV2NativeAdapterError(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            strict_equal(left_item, right_item)
            for left_item, right_item in zip(left, right)
        )
    return left == right


def require_exact_bytes(payload: bytes, size: int, digest: str, label: str) -> None:
    if len(payload) != size or sha256(payload) != digest:
        raise PublicV2NativeAdapterError(f"exact published {label} bytes differ")


def git(root: Path, arguments: list[str], label: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise PublicV2NativeAdapterError(f"Git verification failed: {label}")
    return process.stdout


def git_blob(root: Path, commit: str, relative: str) -> bytes:
    if HEX40.fullmatch(commit) is None:
        raise PublicV2NativeAdapterError("Git commit is not a full object ID")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise PublicV2NativeAdapterError("Git path is unsafe")
    return git(root, ["show", f"{commit}:{relative}"], f"{commit}:{relative}")


def require_ancestor(root: Path, ancestor: str, descendant: str) -> None:
    process = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if process.returncode:
        raise PublicV2NativeAdapterError(
            f"Git reachability differs: {ancestor} is not an ancestor of {descendant}"
        )


def archive_members(payload: bytes) -> dict[str, bytes]:
    """Read already-validated native archive members without filesystem extraction."""
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as bundle:
            result = {}
            for member in bundle.getmembers():
                stream = bundle.extractfile(member)
                if stream is None:
                    raise PublicV2NativeAdapterError(
                        f"cannot read native archive member: {member.name}"
                    )
                result[member.name] = stream.read()
            return result
    except tarfile.TarError as error:
        raise PublicV2NativeAdapterError(f"cannot read native archive: {error}") from error


def validate_ordinals(
    upstream: ModuleType, members: dict[str, bytes], result: dict[str, Any],
) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = {}
    for owner in OWNERS:
        counts[owner] = {}
        for scenario in SCENARIOS:
            name = f"run/sequences/{owner}/{scenario}.jsonl"
            payload = members[name]
            if not payload.endswith(b"\n") or b"\r" in payload:
                raise PublicV2NativeAdapterError(f"native ordinal encoding differs: {owner}/{scenario}")
            rows = [
                upstream.load_json_bytes(line, f"{owner}/{scenario} ordinal {index}")
                for index, line in enumerate(payload.splitlines())
            ]
            accepted = result["owners"][owner]["scenarios"][scenario]["accepted"]
            if len(rows) != accepted:
                raise PublicV2NativeAdapterError(f"native ordinal count differs: {owner}/{scenario}")
            expected = list(range(accepted))
            if [row.get("accept_sequence_ordinal") for row in rows] != expected \
                    or [row.get("retire_sequence_ordinal") for row in rows] != expected:
                raise PublicV2NativeAdapterError(
                    f"native ordinals are not exact and contiguous: {owner}/{scenario}"
                )
            if len({row.get("tb_event_id") for row in rows}) != accepted:
                raise PublicV2NativeAdapterError(
                    f"native ordinal event identities are not unique: {owner}/{scenario}"
                )
            expected_hash = result["owners"][owner]["scenarios"][scenario][
                "sequence_artifact_sha256"
            ]
            if sha256(payload) != expected_hash:
                raise PublicV2NativeAdapterError(
                    f"native ordinal artifact hash differs: {owner}/{scenario}"
                )
            sequence = result["owners"][owner]["scenarios"][scenario]["sequence"]
            if sequence != {
                "schema": "a23_accept_retire_sequence_ordinals_v2",
                "accepted_count": accepted, "retired_count": accepted,
                "same_cycle_order_reconstructable": True,
                "accept_order_sha256": sequence["accept_order_sha256"],
                "retire_order_sha256": sequence["retire_order_sha256"],
            } or sequence["accept_order_sha256"] != sequence["retire_order_sha256"]:
                raise PublicV2NativeAdapterError(
                    f"native ordinal result binding differs: {owner}/{scenario}"
                )
            counts[owner][scenario] = accepted
    return counts


def verify_git_provenance(
    root: Path, publication_payload: bytes, archive_payload: bytes,
    result_payload: bytes, reproduction_payload: bytes,
) -> None:
    head = git(root, ["rev-parse", "HEAD"], "HEAD").decode("ascii").strip()
    for ancestor, descendant in (
        (INTEGRATION_COMMIT, EXECUTION_COMMIT),
        (EXECUTION_SOURCE_COMMIT, EXECUTION_COMMIT),
        (EXECUTION_COMMIT, PAYLOAD_COMMIT),
        (PAYLOAD_COMMIT, head),
        (REVIEWED_PUBLICATION_COMMIT, head),
    ):
        require_ancestor(root, ancestor, descendant)
    paths = {
        "tests/a23_public_projected_v2/public_projected_v2_result.json": result_payload,
        "tests/a23_public_projected_v2/public_projected_v2_reproduction_result.json": reproduction_payload,
        "tests/a23_public_projected_v2/public_projected_v2_export.tar.gz": archive_payload,
    }
    for relative, payload in paths.items():
        if git_blob(root, PAYLOAD_COMMIT, relative) != payload:
            raise PublicV2NativeAdapterError(f"payload commit blob differs: {relative}")
    for relative, payload in {
        **paths,
        "tests/a23_public_projected_v2/public_projected_v2_publication.json": publication_payload,
    }.items():
        if git_blob(root, REVIEWED_PUBLICATION_COMMIT, relative) != payload:
            raise PublicV2NativeAdapterError(f"reviewed publication blob differs: {relative}")


def validate_report(report: dict[str, Any]) -> None:
    exact(report, REPORT_KEYS, "normalized adapter report")
    expected_scalars = {
        "schema": "redred_public_projected_v2_native_adapter_v1",
        "status": "PASS",
        "adapter_mode": "READ_ONLY_NATIVE_SCHEMA_NO_RELABEL_NO_REPACK",
        "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "p6_evidence_used": False,
        "release_status": "HOLD",
        "selection_status": "HOLD",
    }
    for key, expected in expected_scalars.items():
        if not strict_equal(report[key], expected):
            raise PublicV2NativeAdapterError(f"normalized report {key} differs")
    expected_nested = {
        "native_schemas": EXPECTED_NATIVE_SCHEMAS,
        "raw_artifacts": EXPECTED_RAW_ARTIFACTS,
        "closed_inventory": EXPECTED_CLOSED_INVENTORY,
        "identity_accounting": EXPECTED_IDENTITY_ACCOUNTING,
        "ordinal_validation": EXPECTED_ORDINAL_VALIDATION,
        "semantic_validation": EXPECTED_SEMANTIC_VALIDATION,
        "git_provenance": EXPECTED_GIT_PROVENANCE,
        "owners": EXPECTED_OWNER_METRICS,
        "claim_boundary": EXPECTED_CLAIM_BOUNDARY,
    }
    for key, expected in expected_nested.items():
        if not strict_equal(report[key], expected):
            raise PublicV2NativeAdapterError(
                f"normalized report {key} schema/constants differ"
            )


def exclusive_write(path: Path, payload: bytes) -> None:
    """Create one new regular output through an exclusive no-follow descriptor."""
    if ".." in path.parts:
        raise PublicV2NativeAdapterError("output path aliases through '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise PublicV2NativeAdapterError("output path traverses a symlink")
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute, flags, 0o644)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise PublicV2NativeAdapterError("output is not one new regular file")
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise PublicV2NativeAdapterError("output write made no progress")
            remaining = remaining[written:]
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = absolute.lstat()
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size")
    if absolute.is_symlink() or any(
        getattr(after, field) != getattr(current, field) for field in identity_fields
    ) or current.st_size != len(payload):
        raise PublicV2NativeAdapterError("output changed during creation")


def normalized_view(report: dict[str, Any]) -> dict[str, Any]:
    """Expose the strict public_v2 view consumed by the aggregate campaign gate."""
    validate_report(report)
    view = {
        "schema": "redred_single_edge_campaign_normalized_view_v1",
        "slot": "public_v2",
        "verification": {
            "status": "PASS",
            "separately_verified": True,
            "adapter_id": "public-projected-v2-native-v1",
            "adapter_sha256": sha256(Path(__file__).read_bytes()),
            "source_result_sha256": RESULT_SHA256,
            "source_publication_sha256": PUBLICATION_SHA256,
        },
        "classification": {
            "evidence_status": "PUBLIC_PROJECTED_EXTENSION",
            "source_class": "PUBLIC_PROJECTED_EXTENSION",
            "canonical_redred_traffic": False,
            "official_contest_traffic": False,
            "p6_evidence_used": False,
        },
        "campaign_units": {
            "family_id": "uzh-shapes-rotation-public-projected-v2",
            "unit_kind": "PUBLIC_DATASET_RETIMING_FAMILY",
            "independent_sample_count": 1,
            "retiming_labels": list(SCENARIOS),
            "retimings_are_independent_samples": False,
            "pooling_with_other_slots_permitted": False,
        },
        "shared_gates": {
            "actual_rtl_correctness": "PASS",
            "closed_native_inventory": "PASS",
            "git_provenance": "PASS",
            "semantic_reproduction": "PASS",
        },
        "candidates": {
            "A2": {
                "role": "PRIMARY",
                "semantic_class": "AGGREGATE_WEIGHTED_PERFORMANCE",
                "gate_status": "PASS", "failure_scope": "NONE", "reason_codes": [],
            },
            "A3": {
                "role": "SEMANTIC_FALLBACK",
                "semantic_class": "EXACT_SCALAR_PREFIX",
                "gate_status": "PASS", "failure_scope": "NONE", "reason_codes": [],
            },
        },
        "claims": {"official": False, "physical": False, "power": False, "release": False},
    }
    exact(view, NORMALIZED_VIEW_KEYS, "public_v2 normalized view")
    return view


def validate_tuple(
    publication_path: Path = PUBLICATION, archive_path: Path = ARCHIVE,
    root: Path = PROJECT,
) -> dict[str, Any]:
    upstream = load_upstream(root / "tests/a23_public_projected_v2/run.py")
    publication_payload = upstream.stable_read(publication_path, "public-v2 publication")
    archive_payload = upstream.stable_read(archive_path, "public-v2 archive")
    result_payload = upstream.stable_read(
        root / "tests/a23_public_projected_v2/public_projected_v2_result.json",
        "public-v2 result",
    )
    reproduction_payload = upstream.stable_read(
        root / "tests/a23_public_projected_v2/public_projected_v2_reproduction_result.json",
        "public-v2 reproduction result",
    )
    for payload, size, expected, label in (
        (publication_payload, PUBLICATION_SIZE_BYTES, PUBLICATION_SHA256, "publication"),
        (archive_payload, ARCHIVE_SIZE_BYTES, ARCHIVE_SHA256, "archive"),
        (result_payload, RESULT_SIZE_BYTES, RESULT_SHA256, "result"),
        (reproduction_payload, REPRODUCTION_SIZE_BYTES, REPRODUCTION_SHA256, "reproduction"),
    ):
        require_exact_bytes(payload, size, expected, label)

    publication = upstream.load_json_bytes(publication_payload, "public-v2 publication")
    upstream.validate_publication(publication)
    manifest = upstream.validate_published_archive(archive_payload, publication)
    members = archive_members(archive_payload)
    if tuple(members) != ("MANIFEST.json", *upstream.expected_export_names()):
        raise PublicV2NativeAdapterError("native archive inventory/order differs")
    if members["result/public_projected_v2_result.json"] != result_payload:
        raise PublicV2NativeAdapterError("native archive result is not byte-identical")
    if sha256(members["MANIFEST.json"]) != MANIFEST_SHA256:
        raise PublicV2NativeAdapterError("native manifest raw bytes differ")

    result = upstream.load_json_bytes(result_payload, "public-v2 result")
    reproduction = upstream.load_json_bytes(reproduction_payload, "public-v2 reproduction")
    upstream.validate_result(result)
    upstream.validate_result(reproduction)
    primary_semantic = upstream.semantic_sha256(result)
    reproduction_semantic = upstream.semantic_sha256(reproduction)
    if primary_semantic != SEMANTIC_SHA256 or reproduction_semantic != SEMANTIC_SHA256:
        raise PublicV2NativeAdapterError("native semantic reproduction differs")
    ordinal_counts = validate_ordinals(upstream, members, result)
    verify_git_provenance(
        root, publication_payload, archive_payload, result_payload, reproduction_payload,
    )

    report = {
        "schema": "redred_public_projected_v2_native_adapter_v1",
        "status": "PASS",
        "adapter_mode": "READ_ONLY_NATIVE_SCHEMA_NO_RELABEL_NO_REPACK",
        "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "evidence_class": "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL",
        "canonical_redred_traffic": False,
        "official_redred_traffic": False,
        "p6_evidence_used": False,
        "release_status": "HOLD",
        "selection_status": "HOLD",
        "native_schemas": {
            "publication": publication["schema"],
            "archive_manifest": manifest["schema"],
            "closed_inventory": manifest["inventory"]["schema"],
            "result": result["schema"],
            "ordinal": "a23_accept_retire_sequence_ordinals_v2",
        },
        "raw_artifacts": {
            "publication": {"sha256": PUBLICATION_SHA256, "size_bytes": PUBLICATION_SIZE_BYTES},
            "archive": {"sha256": ARCHIVE_SHA256, "size_bytes": ARCHIVE_SIZE_BYTES},
            "manifest": {"sha256": MANIFEST_SHA256, "member": "MANIFEST.json"},
            "result": {"sha256": RESULT_SHA256, "size_bytes": RESULT_SIZE_BYTES},
            "reproduction": {
                "sha256": REPRODUCTION_SHA256, "size_bytes": REPRODUCTION_SIZE_BYTES,
            },
        },
        "closed_inventory": {
            "schema": "a23_public_projected_v2_closed_inventory_v2",
            "entry_count_excluding_manifest": 80,
            "archive_member_count_including_manifest": 81,
            "extra_entries_allowed": False,
            "ordered": True,
        },
        "identity_accounting": publication["identity_accounting"],
        "ordinal_validation": {
            "schema": "a23_accept_retire_sequence_ordinals_v2",
            "accept_and_retire_exact_contiguous": True,
            "same_cycle_order_reconstructable": True,
            "accepted_counts": ordinal_counts,
        },
        "semantic_validation": {
            "definition_sha256": publication["semantic_reproduction"]["definition_sha256"],
            "primary_sha256": primary_semantic,
            "reproduction_sha256": reproduction_semantic,
            "matched": True,
        },
        "git_provenance": {
            "integration_commit": INTEGRATION_COMMIT,
            "execution_source_commit": EXECUTION_SOURCE_COMMIT,
            "execution_commit": EXECUTION_COMMIT,
            "payload_commit": PAYLOAD_COMMIT,
            "reviewed_publication_commit": REVIEWED_PUBLICATION_COMMIT,
            "payload_commit_meaning": "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS",
            "self_referential_commit_claim": False,
        },
        "owners": {
            owner: {
                scenario: {
                    key: result["owners"][owner]["scenarios"][scenario][key]
                    for key in (
                        "generated", "source_overrun", "accepted", "retired",
                        "fixed_window_retired", "fixed_window_cycles",
                        "fixed_window_events_per_cycle",
                    )
                } for scenario in SCENARIOS
            } for owner in OWNERS
        },
        "claim_boundary": {
            "canonical_campaign_promoted": False,
            "official_contest_evidence_claimed": False,
            "synthetic_public_pooling": "FORBIDDEN",
            "archive_extracted_or_repacked": False,
            "producer_schema_relabelled": False,
            "system_release": "HOLD",
        },
    }
    validate_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--repo-root", type=Path, default=PROJECT)
    parser.add_argument("--publication", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--normalized-view-only", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.repo_root.is_symlink() or ".." in arguments.repo_root.parts:
            raise PublicV2NativeAdapterError("repository root is aliased or symlinked")
        root = arguments.repo_root.resolve(strict=True)
        report = validate_tuple(
            arguments.publication or root / PUBLICATION.relative_to(PROJECT),
            arguments.archive or root / ARCHIVE.relative_to(PROJECT),
            root,
        )
        document = normalized_view(report) if arguments.normalized_view_only else report
        payload = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("ascii")
        if arguments.output:
            exclusive_write(arguments.output, payload)
        sys.stdout.buffer.write(payload)
        return 0
    except (PublicV2NativeAdapterError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"REDRED_PUBLIC_V2_NATIVE_ADAPTER_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
