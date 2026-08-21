"""Strict one-shot consumer of a frozen Stage-4 score-free campaign seal.

This module never creates decisions or score-free evidence.  It snapshots an
externally anchored seal, reconstructs the immutable scorer inputs, invokes
``score_window`` once per frozen leaf, aggregates each arm, and publishes one
atomically sealed result.  A failed run publishes no partial numeric output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference import reference as reference_module
from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    DecisionReceipt,
    DecisionRecord,
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
)
from benchmarks.redred_mc_wtb_stage4_integration.sealing import (
    SealingError,
    verify_score_free_seal,
)
from benchmarks.redred_mc_wtb_stage4_scoring import scoring as scoring_module
from benchmarks.redred_mc_wtb_stage4_scoring.scoring import (
    ArmAggregate,
    EventLoss,
    LatencySummary,
    RayEvent,
    ScoreBoundaryEvidence,
    ScoreFreeAccounting,
    ScoreInputManifest,
    ShadowRay,
    WindowMetrics,
    aggregate_arm,
    score_window,
    validate_complete_comparison,
)


class ScoreRunnerError(ValueError):
    """The official post-seal scoring ceremony failed closed."""


@dataclass(frozen=True)
class OfficialScoreResult:
    output_path: Path
    result: Mapping[str, Any]
    result_body_sha256: str
    output_sha256: str


_CAMPAIGN_FILE = "stage4-score-free-seal-manifest.json"
_CAMPAIGN_SCHEMA = "redred.mc_wtb.stage4_score_free_campaign_seal/v1"
_CAMPAIGN_CONTENT = "SCORE_FREE_OBSERVER_EVIDENCE_ONLY"
_RESULT_SCHEMA = "redred.mc_wtb.stage4_official_score_result/v1"
_WINDOW_SCHEMA = "redred.mc_wtb.stage4_score_free_window_seal/v1"
_AUTHORITATIVE_INPUT_SCHEMA = (
    "redred.mc_wtb.stage4_authoritative_window_cycle_inputs/v1"
)
_MANIFEST_SCHEMA = "redred.mc_wtb.stage4_score_input_manifest/v2"
_BOUNDARY_SCHEMA = "redred.mc_wtb.stage4_score_boundary_evidence/v1"
_RECEIPT_SCHEMA = "redred.mc_wtb.stage4_decision_receipt/v2"
_EXPECTED_WINDOW_COUNT = 24
_ARM_ORDER = (
    "zoh_freshness",
    "delayed_exact",
    "causal_cav",
    "oracle_resampled_groundtruth_1khz",
)
_LEAF_FILES = (
    "full-cycle-result.json",
    "cycle-receipts.json",
    "query-decision-records.json",
    "decision-receipt.json",
    "score-free-accounting.json",
    "score-free-accounting-evidence.json",
    "score-boundary-evidence.json",
    "score-input-manifest.json",
)
_DIAGNOSTIC_FILE = "delayed-unbounded-depth-diagnostic.json"
_BANK_CAPACITY_PER_POLARITY = 256
_BANK_MAX_AGE_NS = 2_000_000
_SHA256 = frozenset("0123456789abcdef")
_CONSERVATION = {
    "missing_events": 0,
    "duplicate_events": 0,
    "unexpected_events": 0,
    "reordered_events": 0,
    "exact_once": True,
    "ordered_retirement": True,
}
_CAMPAIGN_FIELDS = frozenset((
    "schema",
    "content_class",
    "assay_manifest_sha256",
    "assay_authority_sha256",
    "assay_closure_sha256",
    "comparison_contract_sha256",
    "registry_sha256",
    "window_count",
    "arm_count",
    "arm_window_count",
    "window_order",
    "arm_order",
    "windows",
    "files",
))
_WINDOW_FIELDS = frozenset((
    "schema",
    "window_id",
    "warmup_start_ns_inclusive",
    "query_start_ns_inclusive",
    "query_end_ns_exclusive",
    "selected_event_count",
    "query_event_count",
    "ordered_query_event_ids_sha256",
    "ray_events_path",
    "ray_events_sha256",
    "authoritative_cycle_inputs",
    "arms",
))
_AUTHORITATIVE_INPUT_FIELDS = frozenset((
    "schema",
    "window_id",
    "window_start_ns",
    "input_events_sha256",
    "input_poses_sha256",
    "input_event_ids_sha256",
    "input_count",
    "input_pose_count",
))
_ARM_BINDING_FIELDS = frozenset((
    "score_input_manifest_path",
    "score_input_manifest_sha256",
    "score_boundary_evidence_path",
    "score_boundary_evidence_sha256",
    "delayed_unbounded_depth_diagnostic",
))


@dataclass(frozen=True)
class _LeafInput:
    window_id: str
    arm: str
    receipt: DecisionReceipt
    records: Tuple[DecisionRecord, ...]
    rays: Tuple[RayEvent, ...]
    accounting: ScoreFreeAccounting
    manifest: ScoreInputManifest
    boundary: ScoreBoundaryEvidence
    expected_manifest_sha256: str
    expected_receipt_sha256: str
    expected_accounting_sha256: str


@dataclass(frozen=True)
class _Preflight:
    campaign: Mapping[str, Any]
    leaves: Tuple[_LeafInput, ...]
    scorer_sha256: str
    reference_sha256: str
    runner_sha256: str
    runtime: Mapping[str, Any]
    authoritative_window_bindings: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "_FileIdentity":
        return cls(
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha(value: Any, where: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise ScoreRunnerError("%s must be a lowercase SHA-256" % where)
    return value


def _require_int(value: Any, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ScoreRunnerError("%s must be an integer >= %d" % (where, minimum))
    return value


def _require_text(value: Any, where: str) -> str:
    if type(value) is not str or not value:
        raise ScoreRunnerError("%s must be a non-empty string" % where)
    return value


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ScoreRunnerError("%s must be an object" % where)
    return value


def _require_list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise ScoreRunnerError("%s must be an array" % where)
    return value


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], where: str) -> None:
    expected_set = frozenset(expected)
    actual = frozenset(value)
    if actual != expected_set:
        raise ScoreRunnerError(
            "%s fields differ; missing=%r extra=%r"
            % (where, sorted(expected_set - actual), sorted(actual - expected_set))
        )


def _object_without_duplicates(
    pairs: Iterable[Tuple[str, Any]]
) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise ScoreRunnerError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ScoreRunnerError("non-finite JSON constant: %s" % value)


def _decode_json(payload: bytes, where: str) -> Any:
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ScoreRunnerError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ScoreRunnerError("%s is not strict ASCII JSON" % where) from exc
    if canonical_json_bytes(value) != payload:
        raise ScoreRunnerError("%s is not canonical JSON" % where)
    return value


def _safe_relative(value: Any, where: str) -> str:
    text = _require_text(value, where)
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or any(
        part in ("", ".", "..") for part in path.parts
    ):
        raise ScoreRunnerError("%s is not a safe relative path" % where)
    if str(path) != text:
        raise ScoreRunnerError("%s is not normalized" % where)
    return text


def _open_root(path: Path) -> int:
    if path.is_symlink():
        raise ScoreRunnerError("seal root must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ScoreRunnerError("cannot open seal root as a real directory") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ScoreRunnerError("seal root is not a directory")
    return descriptor


def _inventory(descriptor: int, prefix: str = "") -> Tuple[set, set]:
    files = set()
    directories = set()
    try:
        entries = list(os.scandir(descriptor))
    except OSError as exc:
        raise ScoreRunnerError("cannot enumerate seal tree") from exc
    for entry in entries:
        relative = "%s/%s" % (prefix, entry.name) if prefix else entry.name
        try:
            entry_info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ScoreRunnerError(
                "cannot inspect sealed entry: %s" % relative
            ) from exc
        if stat.S_ISLNK(entry_info.st_mode):
            raise ScoreRunnerError("seal tree contains a symlink: %s" % relative)
        if stat.S_ISDIR(entry_info.st_mode):
            directories.add(relative)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            try:
                child = os.open(entry.name, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ScoreRunnerError(
                    "cannot open seal directory: %s" % relative
                ) from exc
            try:
                if _FileIdentity.from_stat(os.fstat(child)) != _FileIdentity.from_stat(
                    entry_info
                ):
                    raise ScoreRunnerError(
                        "seal directory changed while inventorying: %s" % relative
                    )
                child_files, child_directories = _inventory(child, relative)
            finally:
                os.close(child)
            files.update(child_files)
            directories.update(child_directories)
        elif stat.S_ISREG(entry_info.st_mode):
            if entry_info.st_nlink != 1:
                raise ScoreRunnerError(
                    "seal tree contains a hard-linked file: %s" % relative
                )
            files.add(relative)
        else:
            raise ScoreRunnerError(
                "seal tree contains a non-regular entry: %s" % relative
            )
    return files, directories


def _read_at(root_descriptor: int, relative: str) -> bytes:
    parts = PurePosixPath(_safe_relative(relative, "sealed path")).parts
    descriptor = os.dup(root_descriptor)
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        for part in parts[:-1]:
            next_descriptor = os.open(
                part, directory_flags, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        try:
            before = _FileIdentity.from_stat(
                os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
            )
        except OSError as exc:
            raise ScoreRunnerError(
                "cannot inspect sealed file: %s" % relative
            ) from exc
        if not stat.S_ISREG(before.mode) or before.links != 1:
            raise ScoreRunnerError(
                "sealed path is not a single-link regular file: %s" % relative
            )
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=descriptor)
        try:
            opened = _FileIdentity.from_stat(os.fstat(file_descriptor))
            if opened != before:
                raise ScoreRunnerError(
                    "sealed file changed while opening: %s" % relative
                )
            with os.fdopen(file_descriptor, "rb", closefd=False) as stream:
                payload = stream.read()
            after_fd = _FileIdentity.from_stat(os.fstat(file_descriptor))
            try:
                after_path = _FileIdentity.from_stat(
                    os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
                )
            except OSError as exc:
                raise ScoreRunnerError(
                    "sealed file changed after reading: %s" % relative
                ) from exc
            if before != after_fd or before != after_path or len(payload) != before.size:
                raise ScoreRunnerError(
                    "sealed file changed while being snapshotted: %s" % relative
                )
            return payload
        finally:
            os.close(file_descriptor)
    except OSError as exc:
        raise ScoreRunnerError("cannot read sealed file: %s" % relative) from exc
    finally:
        os.close(descriptor)


def _expected_directories(files: Iterable[str]) -> set:
    result = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            result.add(str(parent))
            parent = parent.parent
    return result


def _copy_snapshot(
    seal_dir: Path,
    expected_global_seal_sha256: str,
    snapshot: Path,
) -> Mapping[str, Any]:
    expected_root = _require_sha(
        expected_global_seal_sha256, "externally supplied global seal"
    )
    root_descriptor = _open_root(seal_dir)
    try:
        campaign_payload = _read_at(root_descriptor, _CAMPAIGN_FILE)
        if _sha256(campaign_payload) != expected_root:
            raise ScoreRunnerError("campaign manifest differs from external seal")
        campaign = _require_mapping(
            _decode_json(campaign_payload, _CAMPAIGN_FILE), "campaign manifest"
        )
        _exact_keys(campaign, _CAMPAIGN_FIELDS, "campaign manifest")
        indexed = _require_mapping(campaign["files"], "campaign file index")
        indexed_paths = set()
        for raw_relative in indexed:
            indexed_paths.add(_safe_relative(raw_relative, "campaign file index path"))
        expected_files = set(indexed_paths)
        expected_files.add(_CAMPAIGN_FILE)
        actual_files, actual_directories = _inventory(root_descriptor)
        if actual_files != expected_files:
            raise ScoreRunnerError(
                "seal inventory differs; missing=%r extra=%r"
                % (sorted(expected_files - actual_files), sorted(actual_files - expected_files))
            )
        expected_directories = _expected_directories(expected_files)
        if actual_directories != expected_directories:
            raise ScoreRunnerError(
                "seal directory inventory differs; missing=%r extra=%r"
                % (
                    sorted(expected_directories - actual_directories),
                    sorted(actual_directories - expected_directories),
                )
            )
        for relative in sorted(expected_files):
            payload = (
                campaign_payload
                if relative == _CAMPAIGN_FILE
                else _read_at(root_descriptor, relative)
            )
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        final_files, final_directories = _inventory(root_descriptor)
        if final_files != expected_files or final_directories != expected_directories:
            raise ScoreRunnerError("seal inventory changed while being snapshotted")
    finally:
        os.close(root_descriptor)
    try:
        verify_score_free_seal(
            snapshot, expected_seal_manifest_sha256=expected_root
        )
    except SealingError as exc:
        raise ScoreRunnerError("hardened seal verification failed: %s" % exc) from exc
    return campaign


def _read_snapshot_json(snapshot: Path, relative: str) -> Any:
    path = snapshot / _safe_relative(relative, "snapshot path")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ScoreRunnerError("cannot read snapshotted file: %s" % relative) from exc
    return _decode_json(payload, relative)


def _hash_module_file(module: Any, where: str) -> str:
    raw = getattr(module, "__file__", None)
    if type(raw) is not str or not raw:
        raise ScoreRunnerError("%s has no source-file identity" % where)
    path = Path(raw).resolve()
    if path.is_symlink() or not path.is_file():
        raise ScoreRunnerError("%s source is not a regular file" % where)
    return _sha256(path.read_bytes())


def _runtime_identity() -> Mapping[str, Any]:
    executable = Path(sys.executable).resolve()
    if executable.is_symlink() or not executable.is_file():
        raise ScoreRunnerError("Python executable identity is unavailable")
    body = {
        "implementation": platform.python_implementation().lower(),
        "version": list(sys.version_info[:3]),
        "byteorder": sys.byteorder,
        "executable_sha256": _sha256(executable.read_bytes()),
        "bank_capacity_per_polarity": _BANK_CAPACITY_PER_POLARITY,
        "bank_max_age_ns": _BANK_MAX_AGE_NS,
    }
    result = dict(body)
    result["identity_sha256"] = canonical_sha256(body)
    return result


def _parse_shadow(value: Any, where: str) -> ShadowRay:
    row = _require_mapping(value, where)
    fields = (
        "arm",
        "ray",
        "transform",
        "pose_ids",
        "pose_timestamps_ns",
        "pose_commit_cycles",
        "pose_sha256",
    )
    _exact_keys(row, fields, where)
    return ShadowRay(
        row["arm"],
        tuple(_require_list(row["ray"], "%s.ray" % where)),
        row["transform"],
        tuple(_require_list(row["pose_ids"], "%s.pose_ids" % where)),
        tuple(
            _require_list(
                row["pose_timestamps_ns"], "%s.pose_timestamps_ns" % where
            )
        ),
        tuple(
            _require_list(
                row["pose_commit_cycles"], "%s.pose_commit_cycles" % where
            )
        ),
        tuple(_require_list(row["pose_sha256"], "%s.pose_sha256" % where)),
    )


def _parse_rays(value: Any, where: str) -> Tuple[RayEvent, ...]:
    rows = _require_list(value, where)
    result = []  # type: List[RayEvent]
    fields = (
        "window_id",
        "event_id",
        "timestamp_ns",
        "polarity",
        "is_query",
        "sensor_ray",
        "world_shadow_rays",
    )
    for index, raw in enumerate(rows):
        location = "%s[%d]" % (where, index)
        row = _require_mapping(raw, location)
        _exact_keys(row, fields, location)
        shadows = tuple(
            _parse_shadow(item, "%s.world_shadow_rays[%d]" % (location, offset))
            for offset, item in enumerate(
                _require_list(
                    row["world_shadow_rays"], "%s.world_shadow_rays" % location
                )
            )
        )
        result.append(RayEvent(
            row["window_id"],
            row["event_id"],
            row["timestamp_ns"],
            row["polarity"],
            row["is_query"],
            tuple(_require_list(row["sensor_ray"], "%s.sensor_ray" % location)),
            shadows,
        ))
    return tuple(result)


def _parse_records(value: Any, where: str) -> Tuple[DecisionRecord, ...]:
    rows = _require_list(value, where)
    return tuple(
        DecisionRecord.from_mapping(_require_mapping(row, "%s[%d]" % (where, index)))
        for index, row in enumerate(rows)
    )


def _parse_receipt(value: Any, where: str) -> DecisionReceipt:
    row = _require_mapping(value, where)
    fields = (
        "schema",
        "comparison_contract_sha256",
        "registry_sha256",
        "dataset_pose_arrival_assumption",
        "window_id",
        "arm",
        "expected_events",
        "retired_records",
        "ordered_event_ids_sha256",
        "decision_records_sha256",
        "sink_mode",
        "conservation",
    )
    _exact_keys(row, fields, where)
    if row["schema"] != _RECEIPT_SCHEMA or row["conservation"] != _CONSERVATION:
        raise ScoreRunnerError("%s schema or conservation differs" % where)
    receipt = DecisionReceipt(
        row["comparison_contract_sha256"],
        row["registry_sha256"],
        row["dataset_pose_arrival_assumption"],
        row["window_id"],
        row["arm"],
        row["expected_events"],
        row["retired_records"],
        row["ordered_event_ids_sha256"],
        row["decision_records_sha256"],
        row["sink_mode"],
    )
    if receipt.to_mapping() != row:
        raise ScoreRunnerError("%s does not round-trip exactly" % where)
    return receipt


def _parse_accounting(value: Any, where: str) -> ScoreFreeAccounting:
    row = _require_mapping(value, where)
    fields = (
        "window_id",
        "arm",
        "baseline_retire_cycles",
        "attempted_correction_event_ids",
        "freshness_veto_event_ids",
        "invalid_pose_bypass_event_ids",
        "operational_waste_event_ids",
        "peak_buffer_entries",
        "minimum_zero_loss_buffer_entries",
        "buffer_bit_cycles",
        "pose_bandwidth_bits_per_second",
        "event_bandwidth_bits_per_second",
        "incremental_state_bits",
        "source_overrun_events",
        "accepted_event_loss",
        "causality_violations",
        "leakage_violations",
    )
    _exact_keys(row, fields, where)
    baseline = tuple(
        tuple(_require_list(item, "%s.baseline[%d]" % (where, index)))
        for index, item in enumerate(
            _require_list(row["baseline_retire_cycles"], "%s.baseline" % where)
        )
    )
    accounting = ScoreFreeAccounting(
        row["window_id"],
        row["arm"],
        baseline,
        tuple(row["attempted_correction_event_ids"]),
        tuple(row["freshness_veto_event_ids"]),
        tuple(row["invalid_pose_bypass_event_ids"]),
        tuple(row["operational_waste_event_ids"]),
        row["peak_buffer_entries"],
        row["minimum_zero_loss_buffer_entries"],
        row["buffer_bit_cycles"],
        row["pose_bandwidth_bits_per_second"],
        row["event_bandwidth_bits_per_second"],
        row["incremental_state_bits"],
        row["source_overrun_events"],
        row["accepted_event_loss"],
        row["causality_violations"],
        row["leakage_violations"],
    )
    if accounting.to_mapping() != row:
        raise ScoreRunnerError("%s does not round-trip exactly" % where)
    return accounting


def _parse_manifest(value: Any, where: str) -> ScoreInputManifest:
    row = _require_mapping(value, where)
    fields = (
        "schema",
        "window_id",
        "arm",
        "decision_receipt_sha256",
        "score_free_accounting_sha256",
        "ray_events_sha256",
        "assay_authoritative_input_manifest_sha256",
        "full_cycle_result_sha256",
        "cycle_receipts_sha256",
        "query_projection_sha256",
        "artifact_sha256",
    )
    _exact_keys(row, fields, where)
    if row["schema"] != _MANIFEST_SCHEMA:
        raise ScoreRunnerError("%s schema differs" % where)
    artifacts = _require_mapping(row["artifact_sha256"], "%s.artifacts" % where)
    manifest = ScoreInputManifest(
        row["window_id"],
        row["arm"],
        row["decision_receipt_sha256"],
        row["score_free_accounting_sha256"],
        row["ray_events_sha256"],
        row["assay_authoritative_input_manifest_sha256"],
        row["full_cycle_result_sha256"],
        row["cycle_receipts_sha256"],
        row["query_projection_sha256"],
        tuple(sorted(artifacts.items())),
    )
    if manifest.to_mapping() != row:
        raise ScoreRunnerError("%s does not round-trip exactly" % where)
    return manifest


def _parse_boundary(value: Any, where: str) -> ScoreBoundaryEvidence:
    row = _require_mapping(value, where)
    fields = (
        "schema",
        "assay_authoritative_input_manifest_sha256",
        "full_cycle_result_sha256",
        "cycle_receipts_sha256",
        "query_projection_sha256",
    )
    _exact_keys(row, fields, where)
    if row["schema"] != _BOUNDARY_SCHEMA:
        raise ScoreRunnerError("%s schema differs" % where)
    return ScoreBoundaryEvidence(
        row["assay_authoritative_input_manifest_sha256"],
        row["full_cycle_result_sha256"],
        row["cycle_receipts_sha256"],
        row["query_projection_sha256"],
    )


def _parse_authoritative_inputs(
    value: Any,
    *,
    window_id: str,
    window_start_ns: int,
    selected_count: int,
) -> Mapping[str, Any]:
    where = "window authoritative cycle inputs"
    row = _require_mapping(value, where)
    _exact_keys(row, _AUTHORITATIVE_INPUT_FIELDS, where)
    if (
        row["schema"] != _AUTHORITATIVE_INPUT_SCHEMA
        or row["window_id"] != window_id
        or _require_int(row["window_start_ns"], "%s.window_start_ns" % where)
        != window_start_ns
    ):
        raise ScoreRunnerError("authoritative cycle input identity differs")
    for field in (
        "input_events_sha256",
        "input_poses_sha256",
        "input_event_ids_sha256",
    ):
        _require_sha(row[field], "%s.%s" % (where, field))
    if _require_int(row["input_count"], "%s.input_count" % where) != selected_count:
        raise ScoreRunnerError("authoritative event count differs from window seal")
    _require_int(row["input_pose_count"], "%s.input_pose_count" % where)
    return dict(row)


def _file_sha(index: Mapping[str, Any], path: str, where: str) -> str:
    entry = _require_mapping(index.get(path), where)
    return _require_sha(entry.get("sha256"), "%s.sha256" % where)


def _preflight(snapshot: Path, campaign: Mapping[str, Any]) -> _Preflight:
    contract = load_comparison_contract()
    if campaign["schema"] != _CAMPAIGN_SCHEMA or campaign["content_class"] != _CAMPAIGN_CONTENT:
        raise ScoreRunnerError("campaign schema or content class differs")
    if (
        campaign["comparison_contract_sha256"] != contract.canonical_sha256
        or campaign["registry_sha256"] != contract.registry["sha256"]
    ):
        raise ScoreRunnerError("campaign contract or registry binding differs")
    if (
        _require_int(campaign["window_count"], "campaign.window_count")
        != _EXPECTED_WINDOW_COUNT
        or _require_int(campaign["arm_count"], "campaign.arm_count")
        != len(_ARM_ORDER)
        or _require_int(campaign["arm_window_count"], "campaign.arm_window_count")
        != _EXPECTED_WINDOW_COUNT * len(_ARM_ORDER)
        or tuple(campaign["arm_order"]) != _ARM_ORDER
    ):
        raise ScoreRunnerError("campaign is not the frozen 24-by-4 matrix")
    window_order = tuple(
        _require_text(value, "campaign.window_order")
        for value in _require_list(campaign["window_order"], "campaign.window_order")
    )
    if len(window_order) != _EXPECTED_WINDOW_COUNT or len(set(window_order)) != len(window_order):
        raise ScoreRunnerError("campaign window order is not 24 unique windows")
    frozen_windows = tuple(window_registry())
    if len(frozen_windows) != _EXPECTED_WINDOW_COUNT:
        raise ScoreRunnerError("frozen registry does not contain exactly 24 windows")
    frozen_window_order = tuple(
        _require_text(row.get("window_id"), "frozen window registry ID")
        for row in frozen_windows
    )
    if window_order != frozen_window_order:
        raise ScoreRunnerError("campaign window order differs from the frozen registry")
    pointers = _require_list(campaign["windows"], "campaign.windows")
    if len(pointers) != _EXPECTED_WINDOW_COUNT:
        raise ScoreRunnerError("campaign window pointers differ")
    index = _require_mapping(campaign["files"], "campaign.files")
    scorer_sha = _hash_module_file(scoring_module, "scoring module")
    reference_sha = _hash_module_file(reference_module, "reference module")
    runner_sha = _sha256(Path(__file__).resolve().read_bytes())
    leaves = []  # type: List[_LeafInput]
    authoritative_window_bindings = []  # type: List[Mapping[str, Any]]
    expected_indexed = {"assay-closure.json"}
    total_queries = 0
    for position, (window_id, raw_pointer, frozen) in enumerate(
        zip(window_order, pointers, frozen_windows)
    ):
        pointer = _require_mapping(raw_pointer, "campaign.windows[%d]" % position)
        _exact_keys(pointer, ("window_id", "path", "sha256"), "campaign window pointer")
        window_path = "windows/%s/window-seal.json" % window_id
        if (
            pointer["window_id"] != window_id
            or pointer["path"] != window_path
            or pointer["sha256"] != _file_sha(index, window_path, "window seal index")
        ):
            raise ScoreRunnerError("campaign window pointer differs from frozen order")
        expected_indexed.add(window_path)
        window = _require_mapping(
            _read_snapshot_json(snapshot, window_path), "window seal"
        )
        _exact_keys(window, _WINDOW_FIELDS, "window seal")
        if (
            window["schema"] != _WINDOW_SCHEMA
            or window["window_id"] != window_id
            or any(
                _require_int(window[field], "window.%s" % field)
                != _require_int(frozen[field], "frozen registry.%s" % field)
                for field in (
                    "warmup_start_ns_inclusive",
                    "query_start_ns_inclusive",
                    "query_end_ns_exclusive",
                )
            )
        ):
            raise ScoreRunnerError("window seal identity differs")
        query_count = _require_int(window["query_event_count"], "window query count", 1)
        selected_count = _require_int(
            window["selected_event_count"], "window selected count", query_count
        )
        if selected_count < query_count:
            raise ScoreRunnerError("window selected count is below query count")
        authoritative = _parse_authoritative_inputs(
            window["authoritative_cycle_inputs"],
            window_id=window_id,
            window_start_ns=frozen["warmup_start_ns_inclusive"],
            selected_count=selected_count,
        )
        authoritative_window_bindings.append(authoritative)
        total_queries += query_count
        ray_path = "windows/%s/ray-events.json" % window_id
        if (
            window["ray_events_path"] != ray_path
            or window["ray_events_sha256"] != _file_sha(index, ray_path, "ray index")
        ):
            raise ScoreRunnerError("window ray binding differs")
        expected_indexed.add(ray_path)
        rays = _parse_rays(_read_snapshot_json(snapshot, ray_path), ray_path)
        if len(rays) != selected_count or sum(event.is_query for event in rays) != query_count:
            raise ScoreRunnerError("window ray population differs")
        arms = _require_mapping(window["arms"], "window arms")
        if set(arms) != set(_ARM_ORDER):
            raise ScoreRunnerError("window does not contain exactly four arms")
        for arm in _ARM_ORDER:
            leaf_root = "windows/%s/arms/%s" % (window_id, arm)
            for name in _LEAF_FILES:
                expected_indexed.add("%s/%s" % (leaf_root, name))
            binding = _require_mapping(arms[arm], "window arm binding")
            _exact_keys(binding, _ARM_BINDING_FIELDS, "window arm binding")
            manifest_path = "%s/score-input-manifest.json" % leaf_root
            boundary_path = "%s/score-boundary-evidence.json" % leaf_root
            if (
                binding["score_input_manifest_path"] != manifest_path
                or binding["score_input_manifest_sha256"]
                != _file_sha(index, manifest_path, "leaf manifest index")
                or binding["score_boundary_evidence_path"] != boundary_path
                or binding["score_boundary_evidence_sha256"]
                != _file_sha(index, boundary_path, "leaf boundary index")
            ):
                raise ScoreRunnerError("window arm binding path or hash differs")
            diagnostic = binding["delayed_unbounded_depth_diagnostic"]
            if diagnostic is not None:
                diagnostic_mapping = _require_mapping(diagnostic, "diagnostic binding")
                diagnostic_path = _safe_relative(
                    diagnostic_mapping.get("path"), "diagnostic path"
                )
                if arm != "delayed_exact" or diagnostic_path != "%s/%s" % (
                    leaf_root,
                    _DIAGNOSTIC_FILE,
                ):
                    raise ScoreRunnerError("diagnostic is outside a delayed leaf")
                expected_indexed.add(diagnostic_path)
            records_path = "%s/query-decision-records.json" % leaf_root
            receipt_path = "%s/decision-receipt.json" % leaf_root
            accounting_path = "%s/score-free-accounting.json" % leaf_root
            records = _parse_records(
                _read_snapshot_json(snapshot, records_path), records_path
            )
            receipt = _parse_receipt(
                _read_snapshot_json(snapshot, receipt_path), receipt_path
            )
            accounting = _parse_accounting(
                _read_snapshot_json(snapshot, accounting_path), accounting_path
            )
            manifest = _parse_manifest(
                _read_snapshot_json(snapshot, manifest_path), manifest_path
            )
            boundary = _parse_boundary(
                _read_snapshot_json(snapshot, boundary_path), boundary_path
            )
            if len(records) != query_count:
                raise ScoreRunnerError("leaf query count differs from window seal")
            if any(
                identity != (window_id, arm)
                for identity in (
                    (receipt.window_id, receipt.arm),
                    (accounting.window_id, accounting.arm),
                    (manifest.window_id, manifest.arm),
                )
            ):
                raise ScoreRunnerError("leaf identity differs")
            artifacts = dict(manifest.artifact_sha256)
            expected_arm_parameters = canonical_sha256(contract.arms[arm])
            if (
                artifacts["protocol"] != contract.canonical_sha256
                or artifacts["registry"] != contract.registry["sha256"]
                or artifacts["arm_parameters"] != expected_arm_parameters
                or artifacts["scorer"] != scorer_sha
            ):
                raise ScoreRunnerError("leaf code, contract, registry, or arm binding differs")
            if manifest.ray_events_sha256 != _file_sha(index, ray_path, "ray index"):
                raise ScoreRunnerError("leaf does not bind shared ray bytes")
            leaves.append(_LeafInput(
                window_id,
                arm,
                receipt,
                records,
                rays,
                accounting,
                manifest,
                boundary,
                _file_sha(index, manifest_path, "leaf manifest index"),
                _file_sha(index, receipt_path, "leaf receipt index"),
                _file_sha(index, accounting_path, "leaf accounting index"),
            ))
    if total_queries != int(contract.registry["query_event_count"]):
        raise ScoreRunnerError("campaign query denominator differs from frozen registry")
    if len(leaves) != _EXPECTED_WINDOW_COUNT * len(_ARM_ORDER):
        raise ScoreRunnerError("preflight did not reconstruct exactly 96 leaves")
    if len({(leaf.window_id, leaf.arm) for leaf in leaves}) != len(leaves):
        raise ScoreRunnerError("preflight reconstructed a duplicate leaf")
    if set(index) != expected_indexed:
        raise ScoreRunnerError(
            "indexed seal content differs; missing=%r extra=%r"
            % (sorted(expected_indexed - set(index)), sorted(set(index) - expected_indexed))
        )
    if campaign["assay_closure_sha256"] != _file_sha(
        index, "assay-closure.json", "assay closure index"
    ):
        raise ScoreRunnerError("campaign assay closure binding differs")
    return _Preflight(
        campaign,
        tuple(leaves),
        scorer_sha,
        reference_sha,
        runner_sha,
        _runtime_identity(),
        tuple(authoritative_window_bindings),
    )


def _latency_mapping(value: LatencySummary) -> Mapping[str, Any]:
    return {
        "count": value.count,
        "mean_cycles": value.mean_cycles,
        "p50_cycles": value.p50_cycles,
        "p95_cycles": value.p95_cycles,
        "p99_cycles": value.p99_cycles,
        "max_cycles": value.max_cycles,
    }


def _event_loss_mapping(value: EventLoss) -> Mapping[str, Any]:
    return {
        "event_id": value.event_id,
        "sensor_loss": value.sensor_loss,
        "world_shadow_loss": value.world_shadow_loss,
        "policy_loss": value.policy_loss,
        "enabled": value.enabled,
        "quality_waste": value.quality_waste,
        "sensor_reference_event_id": value.sensor_reference_event_id,
        "world_reference_event_id": value.world_reference_event_id,
        "occurrence_latency_cycles": value.occurrence_latency_cycles,
        "added_latency_cycles": value.added_latency_cycles,
    }


def _window_metrics_mapping(value: WindowMetrics) -> Mapping[str, Any]:
    mapping = {
        "schema": "redred.mc_wtb.stage4_score_window_result/v1",
        "window_id": value.window_id,
        "arm": value.arm,
        "manifest_sha256": value.manifest_sha256,
        "receipt_sha256": value.receipt_sha256,
        "accounting_sha256": value.accounting_sha256,
        "event_losses": [_event_loss_mapping(row) for row in value.event_losses],
        "accepted_events": value.accepted_events,
        "enabled_events": value.enabled_events,
        "quality_waste_events": value.quality_waste_events,
        "freshness_veto_events": value.freshness_veto_events,
        "invalid_pose_bypass_events": value.invalid_pose_bypass_events,
        "attempted_corrections": value.attempted_corrections,
        "operational_waste_events": value.operational_waste_events,
        "sensor_loss_sum": value.sensor_loss_sum,
        "policy_loss_sum": value.policy_loss_sum,
        "all_event_effect": value.all_event_effect,
        "enabled_only_effect": value.enabled_only_effect,
        "positive_window": value.positive_window,
        "enable_rate": value.enable_rate,
        "freshness_veto_rate": value.freshness_veto_rate,
        "invalid_pose_bypass_rate": value.invalid_pose_bypass_rate,
        "operational_waste_rate": value.operational_waste_rate,
        "quality_waste_rate": value.quality_waste_rate,
        "occurrence_latency": _latency_mapping(value.occurrence_latency),
        "added_latency": _latency_mapping(value.added_latency),
        "peak_buffer_entries": value.peak_buffer_entries,
        "minimum_zero_loss_buffer_entries": value.minimum_zero_loss_buffer_entries,
        "buffer_bit_cycles": value.buffer_bit_cycles,
        "pose_bandwidth_bits_per_second": value.pose_bandwidth_bits_per_second,
        "event_bandwidth_bits_per_second": value.event_bandwidth_bits_per_second,
        "incremental_state_bits": value.incremental_state_bits,
        "source_overrun_events": value.source_overrun_events,
        "accepted_event_loss": value.accepted_event_loss,
        "causality_violations": value.causality_violations,
        "leakage_violations": value.leakage_violations,
    }
    body_sha = canonical_sha256(mapping)
    result = dict(mapping)
    result["result_sha256"] = body_sha
    return result


def _aggregate_mapping(
    value: ArmAggregate, leaf_results: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any]:
    return {
        "arm": value.arm,
        "window_result_sha256": [row["result_sha256"] for row in leaf_results],
        "accepted_events": value.accepted_events,
        "enabled_events": value.enabled_events,
        "attempted_corrections": value.attempted_corrections,
        "freshness_veto_events": value.freshness_veto_events,
        "invalid_pose_bypass_events": value.invalid_pose_bypass_events,
        "operational_waste_events": value.operational_waste_events,
        "quality_waste_events": value.quality_waste_events,
        "all_event_effect": value.all_event_effect,
        "enabled_only_effect": value.enabled_only_effect,
        "positive_windows": value.positive_windows,
        "enable_rate": value.enable_rate,
        "freshness_veto_rate": value.freshness_veto_rate,
        "invalid_pose_bypass_rate": value.invalid_pose_bypass_rate,
        "operational_waste_rate": value.operational_waste_rate,
        "quality_waste_rate": value.quality_waste_rate,
        "occurrence_latency": _latency_mapping(value.occurrence_latency),
        "added_latency": _latency_mapping(value.added_latency),
        "peak_buffer_entries": value.peak_buffer_entries,
        "minimum_zero_loss_buffer_entries": value.minimum_zero_loss_buffer_entries,
        "buffer_bit_cycles": value.buffer_bit_cycles,
        "pose_bandwidth_bits_per_second": value.pose_bandwidth_bits_per_second,
        "event_bandwidth_bits_per_second": value.event_bandwidth_bits_per_second,
        "incremental_state_bits": value.incremental_state_bits,
        "source_overrun_events": value.source_overrun_events,
        "accepted_event_loss": value.accepted_event_loss,
        "causality_violations": value.causality_violations,
        "leakage_violations": value.leakage_violations,
        "numeric_disposition": value.numeric_disposition,
        "final_disposition": value.final_disposition,
    }


def _assert_semantic_limits(aggregates: Mapping[str, ArmAggregate]) -> None:
    delayed = aggregates["delayed_exact"].final_disposition
    oracle = aggregates["oracle_resampled_groundtruth_1khz"].final_disposition
    if delayed not in ("STOP", "DIAGNOSTIC_UPPER_BOUND"):
        raise ScoreRunnerError("delayed arm exceeded its diagnostic label")
    if oracle not in ("STOP", "INTERFACE_VALUE_ONLY"):
        raise ScoreRunnerError("oracle arm exceeded its interface-only label")


def _execute_once(
    preflight: _Preflight,
    expected_global_seal_sha256: str,
) -> Mapping[str, Any]:
    contract = load_comparison_contract()
    expected_keys = tuple((leaf.window_id, leaf.arm) for leaf in preflight.leaves)
    remaining = set(expected_keys)
    called = set()
    metrics = []  # type: List[WindowMetrics]
    for leaf in preflight.leaves:
        key = (leaf.window_id, leaf.arm)
        if key not in remaining or key in called:
            raise ScoreRunnerError("score_window leaf ledger rejected %r" % (key,))
        remaining.remove(key)
        called.add(key)
        metric = score_window(
            contract,
            leaf.receipt,
            leaf.records,
            leaf.rays,
            leaf.accounting,
            leaf.manifest,
            leaf.boundary,
            expected_manifest_sha256=leaf.expected_manifest_sha256,
            expected_receipt_sha256=leaf.expected_receipt_sha256,
            expected_accounting_sha256=leaf.expected_accounting_sha256,
            bank_capacity_per_polarity=_BANK_CAPACITY_PER_POLARITY,
            bank_max_age_ns=_BANK_MAX_AGE_NS,
        )
        if not isinstance(metric, WindowMetrics) or (
            metric.window_id,
            metric.arm,
        ) != key:
            raise ScoreRunnerError("score_window returned the wrong leaf identity")
        metrics.append(metric)
    if remaining or called != set(expected_keys) or len(called) != 96:
        raise ScoreRunnerError("score_window did not execute exactly once for 96 leaves")
    by_arm = {}  # type: Dict[str, List[WindowMetrics]]
    for arm in _ARM_ORDER:
        by_arm[arm] = [metric for metric in metrics if metric.arm == arm]
    aggregates = {}  # type: Dict[str, ArmAggregate]
    for arm in _ARM_ORDER:
        aggregate = aggregate_arm(contract, tuple(by_arm[arm]))
        if aggregate.arm != arm:
            raise ScoreRunnerError("aggregate_arm returned the wrong arm")
        aggregates[arm] = aggregate
    comparison = validate_complete_comparison(
        tuple(aggregates[arm] for arm in _ARM_ORDER)
    )
    if set(item.arm for item in comparison) != set(_ARM_ORDER):
        raise ScoreRunnerError("complete comparison returned the wrong arm set")
    _assert_semantic_limits(aggregates)
    leaf_results = tuple(_window_metrics_mapping(metric) for metric in metrics)
    aggregate_results = {}
    for arm in _ARM_ORDER:
        arm_leaves = [row for row in leaf_results if row["arm"] == arm]
        aggregate_results[arm] = _aggregate_mapping(aggregates[arm], arm_leaves)
    leaf_bindings = [
        {
            "window_id": leaf.window_id,
            "arm": leaf.arm,
            "manifest_sha256": leaf.expected_manifest_sha256,
            "receipt_sha256": leaf.expected_receipt_sha256,
            "accounting_sha256": leaf.expected_accounting_sha256,
        }
        for leaf in preflight.leaves
    ]
    return {
        "schema": _RESULT_SCHEMA,
        "status": "COMPLETE",
        "input_bindings": {
            "global_seal_manifest_sha256": expected_global_seal_sha256,
            "assay_manifest_sha256": preflight.campaign["assay_manifest_sha256"],
            "assay_authority_sha256": preflight.campaign["assay_authority_sha256"],
            "assay_closure_sha256": preflight.campaign["assay_closure_sha256"],
            "comparison_contract_sha256": contract.canonical_sha256,
            "registry_sha256": contract.registry["sha256"],
            "authoritative_window_cycle_inputs_sha256": canonical_sha256(
                preflight.authoritative_window_bindings
            ),
            "scorer_py_sha256": preflight.scorer_sha256,
            "causal_reference_py_sha256": preflight.reference_sha256,
            "score_runner_py_sha256": preflight.runner_sha256,
            "runtime": preflight.runtime,
        },
        "execution": {
            "window_order": list(preflight.campaign["window_order"]),
            "arm_order": list(_ARM_ORDER),
            "leaf_bindings_sha256": canonical_sha256(leaf_bindings),
            "expected_leaf_count": 96,
            "score_window_call_count": len(called),
            "aggregate_arm_call_count": 4,
            "validate_complete_comparison_call_count": 1,
            "resume_supported": False,
        },
        "arm_semantic_limits": {
            "delayed_exact": {
                "label": "DIAGNOSTIC_UPPER_BOUND",
                "go_to_epoch_integration_allowed": False,
            },
            "oracle_resampled_groundtruth_1khz": {
                "label": "INTERFACE_VALUE_ONLY",
                "go_to_epoch_integration_allowed": False,
            },
        },
        "leaves": list(leaf_results),
        "aggregates": aggregate_results,
        "comparison_order": [item.arm for item in comparison],
    }


def _publish_result(output_path: Path, body: Mapping[str, Any]) -> OfficialScoreResult:
    output = Path(output_path)
    if os.path.lexists(str(output)):
        raise ScoreRunnerError("output already exists; resume/overwrite is forbidden")
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ScoreRunnerError("output parent must be an existing real directory")
    body_sha = canonical_sha256(body)
    result = dict(body)
    result["result_seal"] = {
        "algorithm": "SHA256_CANONICAL_JSON_EXCLUDING_RESULT_SEAL",
        "sha256": body_sha,
    }
    payload = canonical_json_bytes(result)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % output.name, dir=str(parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_name, str(output))
        except FileExistsError as exc:
            raise ScoreRunnerError(
                "output appeared during publication; overwrite is forbidden"
            ) from exc
        directory_descriptor = os.open(
            str(parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return OfficialScoreResult(output, result, body_sha, _sha256(payload))


def run_official_score(
    seal_dir: Path,
    *,
    expected_global_seal_sha256: str,
    output_path: Path,
) -> OfficialScoreResult:
    """Score one externally sealed 24-by-4 campaign exactly once.

    The seal is copied into a private snapshot and fully preflighted before
    scoring.  No partial result is written if any verification, leaf score,
    aggregate, or complete-comparison check fails.
    """

    output = Path(output_path)
    if os.path.lexists(str(output)):
        raise ScoreRunnerError("output already exists; resume/overwrite is forbidden")
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ScoreRunnerError("output parent must be an existing real directory")
    snapshot = Path(tempfile.mkdtemp(prefix=".mcwtb-score-snapshot.", dir=str(parent)))
    try:
        campaign = _copy_snapshot(
            Path(seal_dir), expected_global_seal_sha256, snapshot
        )
        preflight = _preflight(snapshot, campaign)
        body = _execute_once(preflight, expected_global_seal_sha256)
        return _publish_result(output, body)
    finally:
        shutil.rmtree(str(snapshot), ignore_errors=True)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score one externally sealed Stage-4 24-by-4 campaign"
    )
    parser.add_argument("--seal-dir", required=True, type=Path)
    parser.add_argument("--expected-global-seal-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_official_score(
            args.seal_dir,
            expected_global_seal_sha256=args.expected_global_seal_sha256,
            output_path=args.output,
        )
    except (ScoreRunnerError, ValueError, OSError) as exc:
        parser.error(str(exc))
    sys.stdout.write(result.output_sha256 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
