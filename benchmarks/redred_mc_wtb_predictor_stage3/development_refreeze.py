"""Fail-closed authority for development reuse of consumed NEW108 evidence.

This module verifies receipts only.  It never imports the campaign runner,
opens a dataset, executes a candidate, computes a score, or writes an
artifact.  A DEVELOPMENT_REFREEZE authority is deliberately separate from a
campaign epoch: it preserves the legacy v4 attempts as a HOLD, records that
legacy outcome visibility is unknown, and authorizes at most descriptive
measurement on the permanently consumed development cohort.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256

from . import candidate_authority


LEGACY_MIGRATION_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.legacy_migration_hold/v1"
)
PROPOSAL_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.development_refreeze_proposal/v1"
)
DIRECTION_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.development_refreeze_direction/v1"
)
AUTHORITY_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.development_refreeze_authority/v1"
)
V4_ATTEMPT_SCHEMA = "redred.mc_wtb_predictor_stage3.campaign108_attempt/v4"
V4_CAMPAIGN_AUTHORITY_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.campaign_authority/v1"
)
V4_CANDIDATE_AUTHORITY_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.candidate_authority/v2"
)

CANDIDATE_ORDER = ("RG3", "DSPB", "PLL")
CANDIDATE_IDS = tuple(
    candidate_authority.candidate_native_id(name) for name in CANDIDATE_ORDER
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REFREEZE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,126}[A-Za-z0-9]\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

_ROOT_FIELDS = frozenset(("candidate", "path"))
_ARTIFACT_FIELDS = frozenset((
    "candidate", "candidate_id", "artifact_role", "path", "size_bytes",
    "sha256", "semantic_sha256",
))
_LEGACY_FIELDS = frozenset((
    "schema", "status", "source_campaign_schema", "append_only",
    "legacy_attempts_preserved", "failure_classification",
    "outcomes_unseen", "labels_constructed_before_candidate_invocation",
    "epoch2_authorized", "cohort_role",
    "development_model_measurement_authorized",
    "development_refreeze_required", "claim_boundary", "preserved_roots",
    "artifacts", "inventory_sha256", "receipt_sha256",
))
_CLAIM_FIELDS = frozenset((
    "descriptive_only", "unbiased_claim_allowed",
    "generalization_claim_allowed", "promotion_allowed", "rtl_ppa_allowed",
))
_PROPOSAL_FIELDS = frozenset((
    "schema", "status", "authority_domain", "refreeze_id",
    "legacy_migration_receipt_sha256", "legacy_artifact_inventory_sha256",
    "candidate_ids", "bindings", "measurement_policy", "proposal_sha256",
))
_BINDING_FIELDS = frozenset((
    "source_split_plan_sha256", "ordered_query_ids_sha256",
    "selector_labels_sidecar_sha256", "stage3_adapter_sha256",
    "neutral_input_builder_sha256", "candidate_authority_aggregate_sha256",
    "candidate_config_aggregate_sha256", "evaluator_sha256",
    "screen_result_schema_sha256",
))
_POLICY_FIELDS = frozenset((
    "cohort_role", "legacy_outcomes_unseen",
    "labels_constructed_before_candidate_invocation", "descriptive_only",
    "development_model_measurement_allowed", "development_comparison_allowed",
    "unbiased_claim_allowed", "generalization_claim_allowed",
    "promotion_allowed", "rtl_ppa_allowed", "epoch2_authorized",
    "holdout_reconstituted", "single_attempt_per_candidate_config",
    "retry_allowed", "within_run_tuning_allowed",
    "future_variants_require_new_refreeze",
))
_DIRECTION_FIELDS = frozenset((
    "schema", "status", "action", "authorization_provenance",
    "proposal_sha256", "refreeze_id", "authorized_candidate_ids",
    "acknowledged_policy", "direction_sha256",
))
_AUTHORITY_FIELDS = frozenset((
    "schema", "status", "authority_domain", "refreeze_id",
    "legacy_migration_receipt_sha256", "proposal_sha256", "direction_sha256",
    "candidate_ids", "bindings", "measurement_policy", "authority_sha256",
))
_V4_ATTEMPT_FIELDS = frozenset((
    "schema", "candidate_id", "authority_name", "attempt_index",
    "campaign_authority_sha256", "candidate_authority_sha256",
    "authority_config_sha256", "caller_config_sha256",
    "caller_config_semantic_sha256", "cncp_sha256", "cncp_semantic_sha256",
    "campaign_runner_sha256", "adapter_execution_count",
    "verification_replay_count", "verification_replay_is_tuning",
    "retry_allowed", "tuning_allowed", "attempt_sha256",
))
_V4_CAMPAIGN_FIELDS = frozenset((
    "schema", "candidate_order", "candidates", "aggregate_sha256",
))
_V4_MANIFEST_FIELDS = frozenset((
    "schema", "candidate", "native_candidate_id", "config_encoding",
    "config_bytes_hex", "config_sha256", "executable_encoding",
    "executable_bytes_hex", "executable_sha256", "dependencies",
    "dependency_aggregate_sha256", "manifest_sha256",
))
_DEPENDENCY_FIELDS = frozenset(("role", "path", "sha256"))

_ARTIFACT_ROLES = ("ATTEMPT_V4", "CAMPAIGN_AUTHORITY_V4")


class DevelopmentRefreezeError(ValueError):
    """A legacy artifact, policy boundary, path, or receipt seal failed."""


def _exact_mapping(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DevelopmentRefreezeError("%s field schema differs" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise DevelopmentRefreezeError("%s is not canonical SHA-256" % where)
    return value


def _identifier(value: object, where: str) -> str:
    if type(value) is not str or _REFREEZE_ID.fullmatch(value) is None:
        raise DevelopmentRefreezeError("%s is not canonical" % where)
    return value


def _relative_path(value: object, where: str) -> PurePosixPath:
    if type(value) is not str or _SAFE_PATH.fullmatch(value) is None:
        raise DevelopmentRefreezeError("%s is not a safe relative path" % where)
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise DevelopmentRefreezeError("%s is not a canonical relative path" % where)
    return path


def _canonical_hex(value: object, where: str) -> bytes:
    if type(value) is not str or len(value) % 2 != 0:
        raise DevelopmentRefreezeError("%s is not canonical hex" % where)
    try:
        payload = bytes.fromhex(value)
    except ValueError as exc:
        raise DevelopmentRefreezeError("%s is not canonical hex" % where) from exc
    if payload.hex() != value:
        raise DevelopmentRefreezeError("%s is not canonical hex" % where)
    return payload


def _direct_seal(value: Mapping[str, object], field: str, where: str) -> str:
    supplied = _sha256(value.get(field), "%s seal" % where)
    unsigned = dict(value)
    unsigned.pop(field, None)
    if canonical_sha256(unsigned) != supplied:
        raise DevelopmentRefreezeError("%s seal differs" % where)
    return supplied


def _no_duplicate_pairs(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DevelopmentRefreezeError("JSON object has duplicate key %r" % key)
        result[key] = value
    return result


def _read_json_bytes(payload: bytes, where: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_no_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError("non-finite JSON number %s" % token)
            ),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise DevelopmentRefreezeError("%s is not strict UTF-8 JSON" % where) from exc
    if not isinstance(value, Mapping):
        raise DevelopmentRefreezeError("%s is not a JSON object" % where)
    return value


def _canonical_anchor(value: Path) -> Path:
    anchor = Path(value)
    if not anchor.is_absolute() or anchor.is_symlink() or not anchor.is_dir():
        raise DevelopmentRefreezeError("artifact anchor is not a real absolute directory")
    try:
        resolved = anchor.resolve(strict=True)
    except OSError as exc:
        raise DevelopmentRefreezeError("artifact anchor cannot be resolved") from exc
    if anchor != resolved:
        raise DevelopmentRefreezeError("artifact anchor is not a canonical path")
    return anchor


def _safe_node(anchor: Path, relative: PurePosixPath, directory: bool) -> Path:
    if anchor.is_symlink() or not anchor.is_dir():
        raise DevelopmentRefreezeError("artifact anchor is not a real directory")
    current = anchor
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            node = current.lstat()
        except OSError as exc:
            raise DevelopmentRefreezeError("preserved path is missing") from exc
        if stat.S_ISLNK(node.st_mode):
            raise DevelopmentRefreezeError("preserved path contains a symlink")
        final = index == len(relative.parts) - 1
        if not final and not stat.S_ISDIR(node.st_mode):
            raise DevelopmentRefreezeError("preserved path parent is not a directory")
    mode = current.lstat().st_mode
    if directory and not stat.S_ISDIR(mode):
        raise DevelopmentRefreezeError("preserved root is not a directory")
    if not directory and not stat.S_ISREG(mode):
        raise DevelopmentRefreezeError("preserved artifact is not a regular file")
    return current


def _read_unchanged_file(path: Path) -> Tuple[bytes, os.stat_result]:
    before = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise DevelopmentRefreezeError("cannot safely open preserved artifact") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(opened.st_mode)
        ):
            raise DevelopmentRefreezeError("preserved artifact identity changed")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_ARTIFACT_BYTES:
                raise DevelopmentRefreezeError("preserved artifact is unreasonably large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise DevelopmentRefreezeError("preserved artifact changed while reading")
    return b"".join(chunks), after


def _verify_claim_boundary(value: object) -> None:
    boundary = _exact_mapping(value, _CLAIM_FIELDS, "legacy claim boundary")
    expected = {
        "descriptive_only": True,
        "unbiased_claim_allowed": False,
        "generalization_claim_allowed": False,
        "promotion_allowed": False,
        "rtl_ppa_allowed": False,
    }
    if any(
        type(boundary.get(field)) is not type(expected_value)
        or boundary.get(field) != expected_value
        for field, expected_value in expected.items()
    ):
        raise DevelopmentRefreezeError("legacy claim boundary is unsafe")


def _verify_measurement_policy(value: object) -> Mapping[str, object]:
    policy = _exact_mapping(value, _POLICY_FIELDS, "measurement policy")
    expected = {
        "cohort_role": "DEVELOPMENT_CONSUMED",
        "legacy_outcomes_unseen": "UNKNOWN",
        "labels_constructed_before_candidate_invocation": True,
        "descriptive_only": True,
        "development_model_measurement_allowed": True,
        "development_comparison_allowed": False,
        "unbiased_claim_allowed": False,
        "generalization_claim_allowed": False,
        "promotion_allowed": False,
        "rtl_ppa_allowed": False,
        "epoch2_authorized": False,
        "holdout_reconstituted": False,
        "single_attempt_per_candidate_config": True,
        "retry_allowed": False,
        "within_run_tuning_allowed": False,
        "future_variants_require_new_refreeze": True,
    }
    if any(
        type(policy.get(field)) is not type(expected_value)
        or policy.get(field) != expected_value
        for field, expected_value in expected.items()
    ):
        raise DevelopmentRefreezeError("measurement policy is unsafe")
    return policy


def _verify_bindings(value: object) -> Mapping[str, object]:
    bindings = _exact_mapping(value, _BINDING_FIELDS, "refreeze bindings")
    for field in sorted(_BINDING_FIELDS):
        _sha256(bindings[field], "refreeze binding %s" % field)
    return bindings


def _verify_candidate_ids(value: object, where: str) -> None:
    if type(value) is not list or tuple(value) != CANDIDATE_IDS:
        raise DevelopmentRefreezeError("%s differ from frozen candidate order" % where)


def _verify_v4_candidate_manifest(value: object, candidate: str) -> Mapping[str, object]:
    manifest = _exact_mapping(value, _V4_MANIFEST_FIELDS, "v4 candidate authority")
    candidate_index = CANDIDATE_ORDER.index(candidate)
    if (
        manifest["schema"] != V4_CANDIDATE_AUTHORITY_SCHEMA
        or manifest["candidate"] != candidate
        or manifest["native_candidate_id"] != CANDIDATE_IDS[candidate_index]
        or manifest["config_encoding"] != "adapter-export-bytes-hex/v1"
        or manifest["executable_encoding"] != "canonical-json-ascii-hex/v1"
    ):
        raise DevelopmentRefreezeError("v4 candidate identity differs")
    config = _canonical_hex(manifest["config_bytes_hex"], "v4 config")
    executable = _canonical_hex(
        manifest["executable_bytes_hex"], "v4 executable artifact"
    )
    if hashlib.sha256(config).hexdigest() != _sha256(
        manifest["config_sha256"], "v4 config digest"
    ):
        raise DevelopmentRefreezeError("v4 config bytes differ from digest")
    if hashlib.sha256(executable).hexdigest() != _sha256(
        manifest["executable_sha256"], "v4 executable digest"
    ):
        raise DevelopmentRefreezeError("v4 executable bytes differ from digest")
    dependencies = manifest["dependencies"]
    if type(dependencies) is not list or not dependencies:
        raise DevelopmentRefreezeError("v4 dependencies are missing")
    paths = []
    for dependency in dependencies:
        row = _exact_mapping(dependency, _DEPENDENCY_FIELDS, "v4 dependency")
        if type(row["role"]) is not str or not row["role"]:
            raise DevelopmentRefreezeError("v4 dependency role differs")
        path = str(_relative_path(row["path"], "v4 dependency path"))
        _sha256(row["sha256"], "v4 dependency digest")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise DevelopmentRefreezeError("v4 dependency path is duplicated")
    if canonical_sha256(dependencies) != _sha256(
        manifest["dependency_aggregate_sha256"], "v4 dependency aggregate"
    ):
        raise DevelopmentRefreezeError("v4 dependency aggregate differs")
    _direct_seal(manifest, "manifest_sha256", "v4 candidate authority")
    return manifest


def _verify_v4_campaign_authority(value: object) -> Mapping[str, object]:
    authority = _exact_mapping(value, _V4_CAMPAIGN_FIELDS, "v4 campaign authority")
    if (
        authority["schema"] != V4_CAMPAIGN_AUTHORITY_SCHEMA
        or authority["candidate_order"] != list(CANDIDATE_ORDER)
        or type(authority["candidates"]) is not list
        or len(authority["candidates"]) != len(CANDIDATE_ORDER)
    ):
        raise DevelopmentRefreezeError("v4 campaign authority identity differs")
    for candidate, manifest in zip(CANDIDATE_ORDER, authority["candidates"]):
        _verify_v4_candidate_manifest(manifest, candidate)
    _direct_seal(authority, "aggregate_sha256", "v4 campaign authority")
    return authority


def _verify_v4_attempt(
    value: object,
    candidate: str,
    authority: Mapping[str, object],
) -> Mapping[str, object]:
    attempt = _exact_mapping(value, _V4_ATTEMPT_FIELDS, "v4 attempt")
    index = CANDIDATE_ORDER.index(candidate)
    manifest = authority["candidates"][index]
    if (
        attempt["schema"] != V4_ATTEMPT_SCHEMA
        or attempt["candidate_id"] != CANDIDATE_IDS[index]
        or attempt["authority_name"] != candidate
        or type(attempt["attempt_index"]) is not int
        or attempt["attempt_index"] != 1
        or type(attempt["adapter_execution_count"]) is not int
        or attempt["adapter_execution_count"] != 2
        or type(attempt["verification_replay_count"]) is not int
        or attempt["verification_replay_count"] != 1
        or attempt["verification_replay_is_tuning"] is not False
        or attempt["retry_allowed"] is not False
        or attempt["tuning_allowed"] is not False
        or attempt["campaign_authority_sha256"] != authority["aggregate_sha256"]
        or attempt["candidate_authority_sha256"] != manifest["manifest_sha256"]
        or attempt["authority_config_sha256"] != manifest["config_sha256"]
        or attempt["caller_config_sha256"] != manifest["config_sha256"]
    ):
        raise DevelopmentRefreezeError("v4 attempt policy or authority differs")
    for field in (
        "caller_config_semantic_sha256", "cncp_sha256", "cncp_semantic_sha256",
        "campaign_runner_sha256",
    ):
        _sha256(attempt[field], "v4 attempt %s" % field)
    _direct_seal(attempt, "attempt_sha256", "v4 attempt")
    return attempt


def _artifact_key(candidate: str, role: str) -> Tuple[int, int]:
    return CANDIDATE_ORDER.index(candidate), _ARTIFACT_ROLES.index(role)


def verify_legacy_migration_hold_receipt(
    receipt: object,
    artifact_anchor: Path,
) -> str:
    """Verify the HOLD receipt and every byte in all three preserved v4 roots."""

    anchor = _canonical_anchor(Path(artifact_anchor))
    hold = _exact_mapping(receipt, _LEGACY_FIELDS, "legacy migration HOLD receipt")
    if (
        hold["schema"] != LEGACY_MIGRATION_SCHEMA
        or hold["status"] != "LEGACY_MIGRATION_HOLD"
        or hold["source_campaign_schema"] != V4_ATTEMPT_SCHEMA
        or hold["append_only"] is not True
        or hold["legacy_attempts_preserved"] is not True
        or hold["failure_classification"]
        != "COMMON_PRE_SCORE_INFRASTRUCTURE_SUPPORTED_NOT_EXECUTION_PROVEN"
        or hold["outcomes_unseen"] != "UNKNOWN"
        or hold["labels_constructed_before_candidate_invocation"] is not True
        or hold["epoch2_authorized"] is not False
        or hold["cohort_role"] != "DEVELOPMENT_CONSUMED"
        or hold["development_model_measurement_authorized"] is not False
        or hold["development_refreeze_required"] is not True
    ):
        raise DevelopmentRefreezeError("legacy migration HOLD boundary differs")
    _verify_claim_boundary(hold["claim_boundary"])

    roots = hold["preserved_roots"]
    if type(roots) is not list or len(roots) != len(CANDIDATE_ORDER):
        raise DevelopmentRefreezeError("preserved root inventory differs")
    root_paths: Dict[str, PurePosixPath] = {}
    for expected, value in zip(CANDIDATE_ORDER, roots):
        row = _exact_mapping(value, _ROOT_FIELDS, "preserved root")
        if row["candidate"] != expected:
            raise DevelopmentRefreezeError("preserved root order differs")
        path = _relative_path(row["path"], "preserved root path")
        if path in root_paths.values():
            raise DevelopmentRefreezeError("preserved root path is duplicated")
        _safe_node(anchor, path, True)
        root_paths[expected] = path

    artifacts = hold["artifacts"]
    if type(artifacts) is not list or len(artifacts) != 6:
        raise DevelopmentRefreezeError("legacy artifact inventory differs")
    expected_order = [
        (candidate, role)
        for candidate in CANDIDATE_ORDER for role in _ARTIFACT_ROLES
    ]
    seen_paths = set()
    seen_inodes = set()
    parsed: Dict[Tuple[str, str], Mapping[str, object]] = {}
    raw_authority_digests = []
    for expected, value in zip(expected_order, artifacts):
        row = _exact_mapping(value, _ARTIFACT_FIELDS, "legacy artifact")
        candidate, role = expected
        if row["candidate"] != candidate or row["artifact_role"] != role:
            raise DevelopmentRefreezeError("legacy artifact order differs")
        index = CANDIDATE_ORDER.index(candidate)
        if row["candidate_id"] != CANDIDATE_IDS[index]:
            raise DevelopmentRefreezeError("legacy artifact candidate ID differs")
        relative = _relative_path(row["path"], "legacy artifact path")
        root = root_paths[candidate]
        filename = "%s.%s.json" % (
            candidate.lower(),
            "attempt" if role == "ATTEMPT_V4" else "campaign-authority",
        )
        if relative != root / filename:
            raise DevelopmentRefreezeError("legacy artifact path or parent differs")
        if relative in seen_paths:
            raise DevelopmentRefreezeError("legacy artifact path is duplicated")
        seen_paths.add(relative)
        path = _safe_node(anchor, relative, False)
        payload, identity = _read_unchanged_file(path)
        inode = (identity.st_dev, identity.st_ino)
        if inode in seen_inodes:
            raise DevelopmentRefreezeError("legacy artifact is a hard-link alias")
        seen_inodes.add(inode)
        if type(row["size_bytes"]) is not int or row["size_bytes"] < 1:
            raise DevelopmentRefreezeError("legacy artifact size differs")
        digest = hashlib.sha256(payload).hexdigest()
        if row["size_bytes"] != len(payload) or row["sha256"] != digest:
            raise DevelopmentRefreezeError("legacy artifact bytes differ")
        _sha256(row["sha256"], "legacy artifact digest")
        value_json = _read_json_bytes(payload, "legacy artifact")
        if role == "CAMPAIGN_AUTHORITY_V4":
            verified = _verify_v4_campaign_authority(value_json)
            semantic = verified["aggregate_sha256"]
            raw_authority_digests.append(digest)
        else:
            verified = value_json
            semantic = verified.get("attempt_sha256")
        if row["semantic_sha256"] != semantic:
            raise DevelopmentRefreezeError("legacy artifact semantic seal differs")
        _sha256(row["semantic_sha256"], "legacy artifact semantic digest")
        parsed[(candidate, role)] = verified

    if len(set(raw_authority_digests)) != 1:
        raise DevelopmentRefreezeError("v4 campaign authority bytes differ by candidate")
    authority = parsed[("RG3", "CAMPAIGN_AUTHORITY_V4")]
    runner_digests = set()
    for candidate in CANDIDATE_ORDER:
        candidate_authority_value = parsed[(candidate, "CAMPAIGN_AUTHORITY_V4")]
        if candidate_authority_value != authority:
            raise DevelopmentRefreezeError("v4 campaign authorities differ")
        attempt = _verify_v4_attempt(
            parsed[(candidate, "ATTEMPT_V4")], candidate, authority
        )
        runner_digests.add(attempt["campaign_runner_sha256"])
    if len(runner_digests) != 1:
        raise DevelopmentRefreezeError("v4 attempts do not bind one runner")

    for candidate, relative_root in root_paths.items():
        root_path = _safe_node(anchor, relative_root, True)
        expected_names = {
            "%s.attempt.json" % candidate.lower(),
            "%s.campaign-authority.json" % candidate.lower(),
        }
        actual_names = set()
        for child in root_path.iterdir():
            if child.is_symlink() or not child.is_file():
                raise DevelopmentRefreezeError("preserved root has non-file content")
            actual_names.add(child.name)
        if actual_names != expected_names:
            raise DevelopmentRefreezeError("preserved root inventory is not exact")

    if canonical_sha256(artifacts) != _sha256(
        hold["inventory_sha256"], "legacy inventory aggregate"
    ):
        raise DevelopmentRefreezeError("legacy inventory aggregate differs")
    return _direct_seal(hold, "receipt_sha256", "legacy migration HOLD receipt")


def verify_development_refreeze_proposal(
    proposal: object,
    legacy_hold_receipt: object,
    artifact_anchor: Path,
) -> str:
    """Verify a pre-direction proposal without turning the legacy HOLD into epoch2."""

    legacy_digest = verify_legacy_migration_hold_receipt(
        legacy_hold_receipt, artifact_anchor
    )
    value = _exact_mapping(proposal, _PROPOSAL_FIELDS, "refreeze proposal")
    if (
        value["schema"] != PROPOSAL_SCHEMA
        or value["status"] != "DEVELOPMENT_REFREEZE_PROPOSED"
        or value["authority_domain"] != "CONSUMED_NEW108_DEVELOPMENT"
        or value["legacy_migration_receipt_sha256"] != legacy_digest
        or value["legacy_artifact_inventory_sha256"]
        != legacy_hold_receipt["inventory_sha256"]
    ):
        raise DevelopmentRefreezeError("refreeze proposal lineage differs")
    _identifier(value["refreeze_id"], "refreeze ID")
    _verify_candidate_ids(value["candidate_ids"], "proposal candidate IDs")
    _verify_bindings(value["bindings"])
    _verify_measurement_policy(value["measurement_policy"])
    return _direct_seal(value, "proposal_sha256", "refreeze proposal")


def verify_development_refreeze_direction(
    direction: object,
    proposal: object,
    legacy_hold_receipt: object,
    artifact_anchor: Path,
) -> str:
    """Verify explicit, content-addressed continue direction for the proposal."""

    proposal_digest = verify_development_refreeze_proposal(
        proposal, legacy_hold_receipt, artifact_anchor
    )
    value = _exact_mapping(direction, _DIRECTION_FIELDS, "refreeze direction")
    if (
        value["schema"] != DIRECTION_SCHEMA
        or value["status"] != "USER_CONTINUE_DIRECTION_RECORDED"
        or value["action"] != "CONTINUE_CONSUMED_NEW108_DEVELOPMENT"
        or value["authorization_provenance"]
        != "USER_SUPPLIED_UNAUTHENTICATED"
        or value["proposal_sha256"] != proposal_digest
        or value["refreeze_id"] != proposal["refreeze_id"]
        or value["acknowledged_policy"] != proposal["measurement_policy"]
    ):
        raise DevelopmentRefreezeError("refreeze continue direction differs")
    _verify_candidate_ids(
        value["authorized_candidate_ids"], "authorized candidate IDs"
    )
    _verify_measurement_policy(value["acknowledged_policy"])
    return _direct_seal(value, "direction_sha256", "refreeze direction")


def verify_development_refreeze_authority(
    authority: object,
    proposal: object,
    direction: object,
    legacy_hold_receipt: object,
    artifact_anchor: Path,
) -> str:
    """Verify the final descriptive-only authority chain; execute nothing."""

    proposal_digest = verify_development_refreeze_proposal(
        proposal, legacy_hold_receipt, artifact_anchor
    )
    direction_digest = verify_development_refreeze_direction(
        direction, proposal, legacy_hold_receipt, artifact_anchor
    )
    legacy_digest = legacy_hold_receipt["receipt_sha256"]
    value = _exact_mapping(authority, _AUTHORITY_FIELDS, "refreeze authority")
    if (
        value["schema"] != AUTHORITY_SCHEMA
        or value["status"] != "DEVELOPMENT_REFREEZE_AUTHORIZED"
        or value["authority_domain"] != "CONSUMED_NEW108_DEVELOPMENT"
        or value["refreeze_id"] != proposal["refreeze_id"]
        or value["legacy_migration_receipt_sha256"] != legacy_digest
        or value["proposal_sha256"] != proposal_digest
        or value["direction_sha256"] != direction_digest
        or value["bindings"] != proposal["bindings"]
        or value["measurement_policy"] != proposal["measurement_policy"]
    ):
        raise DevelopmentRefreezeError("refreeze authority lineage differs")
    _verify_candidate_ids(value["candidate_ids"], "authority candidate IDs")
    _verify_bindings(value["bindings"])
    _verify_measurement_policy(value["measurement_policy"])
    return _direct_seal(value, "authority_sha256", "refreeze authority")


__all__ = [
    "AUTHORITY_SCHEMA",
    "CANDIDATE_IDS",
    "CANDIDATE_ORDER",
    "DIRECTION_SCHEMA",
    "DevelopmentRefreezeError",
    "LEGACY_MIGRATION_SCHEMA",
    "PROPOSAL_SCHEMA",
    "verify_development_refreeze_authority",
    "verify_development_refreeze_direction",
    "verify_development_refreeze_proposal",
    "verify_legacy_migration_hold_receipt",
]
