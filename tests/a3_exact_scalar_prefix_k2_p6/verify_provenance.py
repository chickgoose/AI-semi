#!/usr/bin/env python3
"""Fail-closed verifier for the complete A3 exact-K2 to P6 source closure."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Mapping


SCHEMA = "a3_exact_scalar_prefix_k2_p6_integration_v2"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REQUIRED_ARTIFACTS = {
    "a3_owner_rtl":
        "rtl/candidates/a3_exact_scalar_prefix_k2/rtl/a3_exact_scalar_prefix_k2.sv",
    "a3_candidate_profile":
        "rtl/candidates/a3_exact_scalar_prefix_k2/candidate-profile.json",
    "a3_owner_filelist":
        "rtl/candidates/a3_exact_scalar_prefix_k2/files.f",
    "p6_pair_launch":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_launch.sv",
    "p6_pair_tx":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_tx.sv",
    "p6_pair_rx":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv",
    "p6_pair_observer":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_observer.sv",
    "p6_exact_pair_endpoint":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_exact_pair_endpoint.sv",
    "p6_atomic_bundle_frontend":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_frontend.sv",
    "p6_atomic_bundle_adapter":
        "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_atomic_bundle_adapter.sv",
    "a3_p6_integration_top":
        "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6_top.sv",
    "a3_p6_integration_tb":
        "tests/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6_tb.sv",
    "a3_p6_integration_filelist":
        "rtl/candidates/a3_exact_scalar_prefix_k2_p6/a3_exact_scalar_prefix_k2_p6.f",
}


class ProvenanceFailure(RuntimeError):
    """Raised when the pinned source closure is incomplete or changed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_provenance(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceFailure(f"cannot read provenance: {error}") from error
    if not isinstance(document, dict):
        raise ProvenanceFailure("provenance root must be an object")
    return document


def verify_document(
    document: Mapping[str, object],
    project_root: Path,
    overrides: Mapping[str, Path] | None = None,
) -> None:
    if document.get("schema") != SCHEMA:
        raise ProvenanceFailure(f"schema must be {SCHEMA}")

    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise ProvenanceFailure("artifacts must be an array")

    by_role: dict[str, Mapping[str, object]] = {}
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ProvenanceFailure(f"artifact {index} must be an object")
        role = artifact.get("role")
        if not isinstance(role, str) or role in by_role:
            raise ProvenanceFailure(f"artifact {index} has missing/duplicate role")
        by_role[role] = artifact

    actual_roles = set(by_role)
    required_roles = set(REQUIRED_ARTIFACTS)
    if actual_roles != required_roles:
        missing = sorted(required_roles - actual_roles)
        extra = sorted(actual_roles - required_roles)
        raise ProvenanceFailure(f"artifact inventory mismatch missing={missing} extra={extra}")

    contract = document.get("contract")
    if not isinstance(contract, dict) or contract.get("artifact_count") != len(REQUIRED_ARTIFACTS):
        raise ProvenanceFailure("contract artifact_count does not match required closure")
    if contract.get("p6_rtl_source_count") != 7:
        raise ProvenanceFailure("contract must pin exactly seven P6 RTL sources")

    override_paths = overrides or {}
    unknown_overrides = set(override_paths) - required_roles
    if unknown_overrides:
        raise ProvenanceFailure(f"unknown override roles: {sorted(unknown_overrides)}")

    for role, required_path in REQUIRED_ARTIFACTS.items():
        artifact = by_role[role]
        manifest_path = artifact.get("path")
        expected_sha = artifact.get("sha256")
        if manifest_path != required_path:
            raise ProvenanceFailure(
                f"role {role} path mismatch expected={required_path} actual={manifest_path}"
            )
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            raise ProvenanceFailure(f"role {role} has invalid sha256")
        source_path = override_paths.get(role, project_root / required_path)
        if not source_path.is_file():
            raise ProvenanceFailure(f"role {role} source missing: {source_path}")
        actual_sha = sha256_file(source_path)
        if actual_sha != expected_sha:
            raise ProvenanceFailure(
                f"role {role} hash mismatch expected={expected_sha} actual={actual_sha}"
            )


def expect_failure(label: str, operation) -> str:
    try:
        operation()
    except ProvenanceFailure as error:
        if "hash mismatch" not in str(error):
            raise ProvenanceFailure(
                f"{label} mutation failed for the wrong reason: {error}"
            ) from error
        return str(error)
    raise ProvenanceFailure(f"{label} mutation unexpectedly passed")


def run_mutation_self_test(document: dict[str, object], project_root: Path) -> None:
    source_role = "p6_pair_launch"
    source_path = project_root / REQUIRED_ARTIFACTS[source_role]
    with tempfile.TemporaryDirectory(prefix="a3-p6-provenance-") as temp_dir:
        mutated_source = Path(temp_dir) / source_path.name
        mutated_source.write_bytes(source_path.read_bytes() + b"\n// provenance mutation\n")
        source_error = expect_failure(
            "source",
            lambda: verify_document(
                document, project_root, overrides={source_role: mutated_source}
            ),
        )

        mutated_document = copy.deepcopy(document)
        artifacts = mutated_document["artifacts"]
        assert isinstance(artifacts, list)
        profile = next(
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and artifact.get("role") == "a3_candidate_profile"
        )
        expected_sha = profile["sha256"]
        assert isinstance(expected_sha, str)
        profile["sha256"] = ("0" if expected_sha[0] != "0" else "1") + expected_sha[1:]
        hash_error = expect_failure(
            "hash", lambda: verify_document(mutated_document, project_root)
        )

    if source_role not in source_error or "a3_candidate_profile" not in hash_error:
        raise ProvenanceFailure("mutation diagnostics did not identify the changed roles")
    print("A3_P6_PROVENANCE_MUTATION_PASS source_change=CAUGHT hash_change=CAUGHT")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    document = load_provenance(args.provenance)
    verify_document(document, project_root)
    print(f"A3_P6_PROVENANCE_PASS artifacts={len(REQUIRED_ARTIFACTS)} p6_sources=7")
    if args.self_test:
        run_mutation_self_test(document, project_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvenanceFailure as error:
        print(f"A3_P6_PROVENANCE_FAIL {error}")
        raise SystemExit(1) from error
