"""Canonical, score-free observer and campaign sealer for Stage-4 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import Arm
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    run_delayed_unbounded_diagnostic,
)

from . import adapter as integration_adapter


class SealingError(ValueError):
    """The official score-free seal could not be closed."""


@dataclass(frozen=True)
class SealResult:
    output_dir: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str


_ASSAY_MANIFEST = "stage4_input_manifest.json"
_ASSAY_STREAMS = (
    "stage4_events.jsonl",
    "stage4_occurrence_batches.jsonl",
    "stage4_occurrence_pose_snapshots.jsonl",
    "stage4_dataset_pose_packets.jsonl",
    "oracle_resampled_groundtruth_1khz.jsonl",
    "stage4_oracle_window_schedule.jsonl",
)
_ARM_ORDER = tuple(arm.value for arm in Arm)
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
_DELAYED_DIAGNOSTIC_FILE = "delayed-unbounded-depth-diagnostic.json"
_DELAYED_DIAGNOSTIC_KIND = "delayed_unbounded_depth_diagnostic"
_REPLAY_REQUIRED = "UNBOUNDED_REPLAY_REQUIRED_FOR_MINIMUM_ZERO_LOSS_DEPTH"
_DIAGNOSTIC_SCHEMA = (
    "redred.mc_wtb.stage4_delayed_unbounded_depth_diagnostic/v2"
)
_DIAGNOSTIC_EVENT_FIELDS = frozenset((
    "event_id",
    "timestamp_ns",
    "transform_guard_valid",
    "causal_pose_index",
))
_DIAGNOSTIC_POSE_FIELDS = frozenset((
    "pose_id",
    "timestamp_ns",
    "commit_cycle",
    "source",
    "pose_sha256",
    "value_valid",
    "arithmetic_valid",
))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_sha(value: Any, where: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SealingError("%s must be a lowercase SHA-256" % where)
    return value


def _require_int(value: Any, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SealingError("%s must be an integer >= %d" % (where, minimum))
    return value


def _require_bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise SealingError("%s must be bool" % where)
    return value


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SealingError("%s must be an object" % where)
    return value


def _require_array(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise SealingError("%s must be an array" % where)
    return value


def _object_without_duplicates(
    pairs: Iterable[Tuple[str, Any]]
) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise SealingError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _decode_json(payload: bytes, where: str) -> Any:
    try:
        value = json.loads(
            payload.decode("ascii"), object_pairs_hook=_object_without_duplicates
        )
    except SealingError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise SealingError("%s is not strict ASCII JSON" % where) from exc
    if canonical_json_bytes(value) != payload:
        raise SealingError("%s is not canonical JSON" % where)
    return value


def _read_json(path: Path) -> Tuple[Any, bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SealingError("cannot read %s" % path.name) from exc
    return _decode_json(payload, str(path)), payload


def _read_jsonl(path: Path) -> Tuple[Tuple[Mapping[str, Any], ...], bytes]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SealingError("cannot read %s" % path.name) from exc
    if payload and not payload.endswith(b"\n"):
        raise SealingError("%s lacks its final newline" % path.name)
    records = []  # type: List[Mapping[str, Any]]
    for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
        value = _decode_json(line, "%s:%d" % (path.name, line_number))
        if not isinstance(value, Mapping):
            raise SealingError("%s contains a non-object record" % path.name)
        records.append(value)
    return tuple(records), payload


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _to_mapping(value: Any, where: str) -> Mapping[str, Any]:
    try:
        mapping = value.to_mapping()
    except AttributeError as exc:
        raise SealingError("%s lacks a canonical mapping" % where) from exc
    if not isinstance(mapping, Mapping):
        raise SealingError("%s mapping is not an object" % where)
    return mapping


def _observe_file(
    root: Path,
    relative: str,
    *,
    kind: str,
    record_count: int,
) -> Mapping[str, Any]:
    path = root / relative
    value, payload = _read_json(path)
    if kind == "array":
        if not isinstance(value, list) or len(value) != record_count:
            raise SealingError("%s array count differs" % relative)
    elif kind == "object":
        if not isinstance(value, Mapping) or record_count != 1:
            raise SealingError("%s object shape differs" % relative)
    elif kind == _DELAYED_DIAGNOSTIC_KIND:
        if not isinstance(value, Mapping) or record_count != 1:
            raise SealingError("%s diagnostic shape differs" % relative)
        diagnostic = _validate_delayed_diagnostic_mapping(value, relative)
    else:
        raise SealingError("unknown sealed file kind")
    observed = {
        "sha256": _sha256(payload),
        "size_bytes": len(payload),
        "kind": kind,
        "record_count": record_count,
    }
    if kind == _DELAYED_DIAGNOSTIC_KIND:
        observed.update(diagnostic)
    return observed


def _validate_delayed_diagnostic_mapping(
    value: Mapping[str, Any], where: str
) -> Mapping[str, Any]:
    """Reconstruct and replay a diagnostic from independently read bytes."""

    if value.get("schema") != _DIAGNOSTIC_SCHEMA:
        raise SealingError("%s has the wrong diagnostic schema" % where)
    if value.get("arm") != Arm.DELAYED_EXACT.value:
        raise SealingError("%s is not delayed_exact evidence" % where)
    if value.get("arm_semantic_label") != "DIAGNOSTIC_UPPER_BOUND":
        raise SealingError("%s has the wrong arm semantic label" % where)
    if _require_bool(value.get("synthetic_test_mode"), "diagnostic mode"):
        raise SealingError("official diagnostic used synthetic test mode")

    supplied_evidence_sha256 = _require_sha(
        value.get("evidence_sha256"), "diagnostic evidence hash"
    )
    body = dict(value)
    del body["evidence_sha256"]
    if canonical_sha256(body) != supplied_evidence_sha256:
        raise SealingError("%s evidence hash differs" % where)

    event_values = _require_array(value.get("input_events"), "diagnostic events")
    pose_values = _require_array(value.get("input_poses"), "diagnostic poses")
    events = []  # type: List[Event]
    poses = []  # type: List[PosePacket]
    try:
        for index, raw in enumerate(event_values):
            row = _require_mapping(raw, "diagnostic event[%d]" % index)
            if frozenset(row) != _DIAGNOSTIC_EVENT_FIELDS:
                raise SealingError("diagnostic event field set differs")
            pose_index = row["causal_pose_index"]
            if pose_index is not None:
                pose_index = _require_int(
                    pose_index, "diagnostic event causal_pose_index"
                )
            events.append(Event(
                _require_int(row["event_id"], "diagnostic event_id"),
                _require_int(row["timestamp_ns"], "diagnostic timestamp"),
                _require_bool(
                    row["transform_guard_valid"],
                    "diagnostic transform_guard_valid",
                ),
                pose_index,
            ))
        for index, raw in enumerate(pose_values):
            row = _require_mapping(raw, "diagnostic pose[%d]" % index)
            if frozenset(row) != _DIAGNOSTIC_POSE_FIELDS:
                raise SealingError("diagnostic pose field set differs")
            source_value = row["source"]
            if type(source_value) is not str:
                raise SealingError("diagnostic pose source must be text")
            poses.append(PosePacket(
                _require_int(row["pose_id"], "diagnostic pose_id"),
                _require_int(row["timestamp_ns"], "diagnostic pose timestamp"),
                _require_int(
                    row["commit_cycle"], "diagnostic pose commit_cycle", -(1 << 63)
                ),
                PoseSource(source_value),
                _require_sha(row["pose_sha256"], "diagnostic pose hash"),
                _require_bool(row["value_valid"], "diagnostic pose value_valid"),
                _require_bool(
                    row["arithmetic_valid"],
                    "diagnostic pose arithmetic_valid",
                ),
            ))
        replay = run_delayed_unbounded_diagnostic(
            window_id=str(value.get("window_id")),
            window_start_ns=_require_int(
                value.get("window_start_ns"), "diagnostic window_start_ns"
            ),
            events=tuple(events),
            poses=tuple(poses),
            synthetic_test_mode=False,
        )
    except (CycleModelError, ValueError) as exc:
        raise SealingError("%s cannot be independently replayed" % where) from exc
    if replay.to_mapping() != dict(value):
        raise SealingError("%s differs from independent replay" % where)
    return {
        "evidence_sha256": supplied_evidence_sha256,
        "config_identity_sha256": _require_sha(
            value.get("config_identity_sha256"), "diagnostic config hash"
        ),
        "input_events_sha256": _require_sha(
            value.get("input_events_sha256"), "diagnostic input event hash"
        ),
        "input_poses_sha256": _require_sha(
            value.get("input_poses_sha256"), "diagnostic input pose hash"
        ),
        "decision_records_sha256": _require_sha(
            value.get("decision_records_sha256"), "diagnostic decision hash"
        ),
        "cycle_receipts_sha256": _require_sha(
            value.get("cycle_receipts_sha256"), "diagnostic receipt hash"
        ),
        "peak_fifo_depth": _require_int(
            value.get("peak_fifo_depth"), "diagnostic peak FIFO depth"
        ),
        "window_id": value.get("window_id"),
        "window_start_ns": value.get("window_start_ns"),
    }


def _packet_hashes(
    records: Sequence[Mapping[str, Any]], where: str
) -> Tuple[str, ...]:
    hashes = []  # type: List[str]
    for index, record in enumerate(records):
        body = dict(record)
        supplied = _require_sha(
            body.pop("packet_sha256", None), "%s[%d]" % (where, index)
        )
        if canonical_sha256(body) != supplied:
            raise SealingError("%s packet hash differs" % where)
        hashes.append(supplied)
    return tuple(hashes)


def _snapshot_hashes(
    records: Sequence[Mapping[str, Any]]
) -> Tuple[str, ...]:
    hashes = []  # type: List[str]
    for index, record in enumerate(records):
        body = dict(record)
        supplied = _require_sha(
            body.pop("pose_snapshot_sha256", None), "snapshot[%d]" % index
        )
        if canonical_sha256(body) != supplied:
            raise SealingError("snapshot hash differs")
        hashes.append(supplied)
    return tuple(hashes)


def _observe_assay(
    assay_dir: Path, expected_manifest_sha256: str
) -> Tuple[Mapping[str, Any], Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    expected = _require_sha(expected_manifest_sha256, "expected assay manifest")
    manifest_value, manifest_payload = _read_json(assay_dir / _ASSAY_MANIFEST)
    if not isinstance(manifest_value, Mapping):
        raise SealingError("assay manifest must be an object")
    manifest = manifest_value
    manifest_sha256 = _sha256(manifest_payload)
    if manifest_sha256 != expected:
        raise SealingError("assay manifest differs from the caller-supplied root")
    if (
        manifest.get("schema") != "redred.mc_wtb.stage4_score_free_inputs/v2"
        or manifest.get("provenance_scope")
        != "OFFICIAL_HASH_PINNED_DEVELOPMENT_INPUT"
        or manifest.get("fixture_label") is not None
    ):
        raise SealingError("assay manifest is not an official score-free input")

    contract = load_comparison_contract()
    registry = manifest.get("registry")
    if not isinstance(registry, Mapping) or registry != {
        "window_count": contract.registry["window_count"],
        "sha256": contract.registry["sha256"],
        "query_event_count": contract.registry["query_event_count"],
        "forbidden_interval_ns": contract.registry["forbidden_interval_ns"],
        "forbidden_interval_selected_records": 0,
    }:
        raise SealingError("official assay registry binding differs")
    if manifest.get("comparison_contract_sha256") != contract.canonical_sha256:
        raise SealingError("official assay contract binding differs")

    frozen_windows = tuple(window_registry())
    windows = manifest.get("windows")
    if not isinstance(windows, list) or len(windows) != len(frozen_windows):
        raise SealingError("official assay must contain exactly 24 windows")
    for summary, frozen in zip(windows, frozen_windows):
        if not isinstance(summary, Mapping):
            raise SealingError("assay window summary is not an object")
        for field in (
            "window_id",
            "warmup_start_ns_inclusive",
            "query_start_ns_inclusive",
            "query_end_ns_exclusive",
        ):
            if summary.get(field) != frozen[field]:
                raise SealingError("assay window registry projection differs")
    if sum(int(row.get("query_event_count", -1)) for row in windows) != int(
        contract.registry["query_event_count"]
    ):
        raise SealingError("assay window query counts do not conserve")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(_ASSAY_STREAMS):
        raise SealingError("assay artifact set differs")
    records = {}  # type: Dict[str, Tuple[Mapping[str, Any], ...]]
    stream_seals = {}  # type: Dict[str, Mapping[str, Any]]
    for name in _ASSAY_STREAMS:
        rows, payload = _read_jsonl(assay_dir / name)
        artifact = artifacts.get(name)
        if not isinstance(artifact, Mapping) or artifact != {
            "path": name,
            "sha256": _sha256(payload),
            "record_count": len(rows),
            "size_bytes": len(payload),
        }:
            raise SealingError("assay stream authority differs: %s" % name)
        records[name] = rows
        stream_seals[name] = dict(artifact)

    authority = manifest.get("authoritative_input_binding")
    if not isinstance(authority, Mapping):
        raise SealingError("assay authority is absent")
    authority_body = dict(authority)
    authority_sha256 = _require_sha(
        authority_body.pop("binding_sha256", None), "assay authority"
    )
    if canonical_sha256(authority_body) != authority_sha256:
        raise SealingError("assay authority canonical hash differs")

    calibration = authority.get("calibration_model")
    if not isinstance(calibration, Mapping):
        raise SealingError("calibration authority is absent")
    calibration_body = dict(calibration)
    calibration_sha256 = _require_sha(
        calibration_body.pop("authority_sha256", None), "calibration authority"
    )
    if canonical_sha256(calibration_body) != calibration_sha256:
        raise SealingError("calibration authority canonical hash differs")
    raw_sources = authority.get("raw_source_streams")
    source = manifest.get("source")
    if (
        not isinstance(raw_sources, Mapping)
        or not isinstance(source, Mapping)
        or calibration_body.get("source_sha256")
        != raw_sources.get("calib.txt_sha256")
        or calibration_body.get("source_sha256") != source.get("calibration_sha256")
    ):
        raise SealingError("calibration source binding differs")

    events = records["stage4_events.jsonl"]
    if sum(row.get("is_query") is True for row in events) != int(
        contract.registry["query_event_count"]
    ):
        raise SealingError("assay query event count differs")
    ray_projection = {
        "calibration_authority_sha256": calibration_sha256,
        "events": [
            {
                field: row.get(field)
                for field in (
                    "window_id",
                    "event_id",
                    "payload_hex",
                    "x",
                    "y",
                    "sensor_ray",
                )
            }
            for row in events
        ],
    }
    dataset_hashes = _packet_hashes(
        records["stage4_dataset_pose_packets.jsonl"], "dataset packet"
    )
    oracle_hashes = _packet_hashes(
        records["oracle_resampled_groundtruth_1khz.jsonl"], "oracle packet"
    )
    snapshot_hashes = _snapshot_hashes(
        records["stage4_occurrence_pose_snapshots.jsonl"]
    )
    schedule_hashes = tuple(
        _require_sha(row.get("packet_sha256"), "oracle schedule packet")
        for row in records["stage4_oracle_window_schedule.jsonl"]
    )
    closure = {
        "schema": "redred.mc_wtb.stage4_score_free_assay_closure/v1",
        "assay_manifest_sha256": manifest_sha256,
        "assay_authority_sha256": authority_sha256,
        "comparison_contract_sha256": contract.canonical_sha256,
        "registry_sha256": contract.registry["sha256"],
        "streams": stream_seals,
        "calibration": {
            "calib_txt_sha256": calibration_body["source_sha256"],
            "authority_sha256": calibration_sha256,
            "model_sha256": canonical_sha256(calibration_body.get("model")),
            "sensor_ray_generator_rule": calibration_body.get(
                "sensor_ray_generator_rule"
            ),
            "payload_bound_ray_projection_sha256": canonical_sha256(
                ray_projection
            ),
        },
        "packet_and_snapshot_roots": {
            "dataset_packet_sha256": canonical_sha256(list(dataset_hashes)),
            "oracle_packet_sha256": canonical_sha256(list(oracle_hashes)),
            "oracle_schedule_packet_sha256": canonical_sha256(
                list(schedule_hashes)
            ),
            "occurrence_snapshot_sha256": canonical_sha256(
                list(snapshot_hashes)
            ),
        },
    }
    return manifest, closure, tuple(windows)


def _full_cycle_mapping(sealed: Any) -> Mapping[str, Any]:
    value = integration_adapter._full_cycle_evidence(sealed.simulation)
    if not isinstance(value, Mapping):
        raise SealingError("full cycle evidence is not an object")
    return value


def _write_leaf_inputs(
    output_root: Path,
    relative_root: str,
    sealed: Any,
) -> None:
    _write_json(
        output_root / relative_root / "full-cycle-result.json",
        _full_cycle_mapping(sealed),
    )
    _write_json(
        output_root / relative_root / "cycle-receipts.json",
        [_to_mapping(row, "cycle receipt") for row in sealed.simulation.cycle_receipts],
    )
    _write_json(
        output_root / relative_root / "query-decision-records.json",
        [_to_mapping(row, "query decision") for row in sealed.query_records],
    )
    _write_json(
        output_root / relative_root / "decision-receipt.json",
        _to_mapping(sealed.receipt, "decision receipt"),
    )
    _write_json(
        output_root / relative_root / "score-free-accounting.json",
        _to_mapping(sealed.accounting, "score-free accounting"),
    )
    _write_json(
        output_root / relative_root / "score-free-accounting-evidence.json",
        _to_mapping(sealed.accounting_evidence, "accounting evidence"),
    )
    diagnostic = sealed.delayed_unbounded_diagnostic
    if diagnostic is not None:
        if sealed.arm is not Arm.DELAYED_EXACT:
            raise SealingError("unbounded diagnostic is attached to a non-delayed arm")
        _write_json(
            output_root / relative_root / _DELAYED_DIAGNOSTIC_FILE,
            _to_mapping(diagnostic, "delayed unbounded diagnostic"),
        )


def _admission_projection(receipts: Sequence[Mapping[str, Any]]) -> Tuple[Any, ...]:
    return tuple(
        (
            row.get("event_id"),
            row.get("occurrence_cycle"),
            row.get("admission_cycle"),
            row.get("admission_lane"),
        )
        for row in receipts
    )


def _observe_delayed_diagnostic(
    output_root: Path,
    relative_root: str,
    sealed: Any,
    assay_manifest_sha256: str,
    observed: Mapping[str, Mapping[str, Any]],
    bounded_full_cycle: Mapping[str, Any],
    bounded_receipts: Sequence[Mapping[str, Any]],
    accounting_evidence: Mapping[str, Any],
    files: Dict[str, Mapping[str, Any]],
) -> Any:
    diagnostic = sealed.delayed_unbounded_diagnostic
    bounded_full = any(
        row.get("disposition_reason") == "fifo_full_forced_bypass"
        for row in bounded_receipts
    )
    minimum_depth = _require_mapping(
        accounting_evidence.get("minimum_depth_evidence"),
        "accounting minimum-depth evidence",
    )
    if diagnostic is None:
        if bounded_full:
            raise SealingError("bounded pressure lacks its unbounded diagnostic")
        if minimum_depth.get("basis") != "bounded_peak_no_full_pressure" or any(
            minimum_depth.get(name) is not None
            for name in (
                "unbounded_diagnostic_evidence_sha256",
                "unbounded_diagnostic_config_sha256",
                "unbounded_diagnostic_decision_records_sha256",
                "unbounded_diagnostic_cycle_receipts_sha256",
            )
        ):
            raise SealingError("accounting names an absent unbounded diagnostic")
        return None
    if sealed.arm is not Arm.DELAYED_EXACT or not bounded_full:
        raise SealingError("unbounded diagnostic is outside a pressured delayed leaf")

    relative = "%s/%s" % (relative_root, _DELAYED_DIAGNOSTIC_FILE)
    entry = _observe_file(
        output_root,
        relative,
        kind=_DELAYED_DIAGNOSTIC_KIND,
        record_count=1,
    )
    files[relative] = entry
    diagnostic_value, _ = _read_json(output_root / relative)
    diagnostic_mapping = _require_mapping(
        diagnostic_value, "delayed diagnostic leaf"
    )
    diagnostic_receipts = _require_array(
        diagnostic_mapping.get("cycle_receipts"), "diagnostic cycle receipts"
    )
    if _admission_projection(bounded_receipts) != _admission_projection(
        tuple(_require_mapping(row, "diagnostic receipt") for row in diagnostic_receipts)
    ):
        raise SealingError("bounded and unbounded admission schedules differ")
    expected_accounting = {
        "basis": "independent_no_pressure_replay_peak",
        "bounded_decision_records_sha256": _require_sha(
            bounded_full_cycle.get("decision_records_sha256"),
            "bounded decision records hash",
        ),
        "bounded_cycle_receipts_sha256": observed["cycle-receipts.json"][
            "sha256"
        ],
        "unbounded_diagnostic_evidence_sha256": entry["evidence_sha256"],
        "unbounded_diagnostic_config_sha256": entry[
            "config_identity_sha256"
        ],
        "unbounded_diagnostic_decision_records_sha256": entry[
            "decision_records_sha256"
        ],
        "unbounded_diagnostic_cycle_receipts_sha256": entry[
            "cycle_receipts_sha256"
        ],
        "minimum_zero_loss_buffer_entries": entry["peak_fifo_depth"],
    }
    if any(minimum_depth.get(name) != value for name, value in expected_accounting.items()):
        raise SealingError("accounting and unbounded diagnostic bindings differ")
    return {
        "path": relative,
        "sha256": entry["sha256"],
        "evidence_sha256": entry["evidence_sha256"],
        "config_identity_sha256": entry["config_identity_sha256"],
        "input_events_sha256": entry["input_events_sha256"],
        "input_poses_sha256": entry["input_poses_sha256"],
        "decision_records_sha256": entry["decision_records_sha256"],
        "cycle_receipts_sha256": entry["cycle_receipts_sha256"],
        "peak_fifo_depth": entry["peak_fifo_depth"],
        "assay_authoritative_input_manifest_sha256": assay_manifest_sha256,
        "bounded_full_cycle_result_sha256": observed["full-cycle-result.json"][
            "sha256"
        ],
        "bounded_cycle_receipts_sha256": observed["cycle-receipts.json"][
            "sha256"
        ],
    }


def _observe_leaf(
    output_root: Path,
    relative_root: str,
    sealed: Any,
    assay_manifest_sha256: str,
    ray_events_sha256: str,
    expected_query_count: int,
    files: Dict[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    if len(sealed.query_records) != expected_query_count:
        raise SealingError("query projection count differs from its window summary")
    counts = {
        "full-cycle-result.json": 1,
        "cycle-receipts.json": len(sealed.simulation.cycle_receipts),
        "query-decision-records.json": len(sealed.query_records),
        "decision-receipt.json": 1,
        "score-free-accounting.json": 1,
        "score-free-accounting-evidence.json": 1,
    }
    kinds = dict((name, "object") for name in counts)
    kinds["cycle-receipts.json"] = "array"
    kinds["query-decision-records.json"] = "array"
    observed = {}  # type: Dict[str, Mapping[str, Any]]
    for name in counts:
        relative = "%s/%s" % (relative_root, name)
        entry = _observe_file(
            output_root,
            relative,
            kind=kinds[name],
            record_count=counts[name],
        )
        files[relative] = entry
        observed[name] = entry

    full_value, _ = _read_json(output_root / relative_root / "full-cycle-result.json")
    cycle_value, _ = _read_json(output_root / relative_root / "cycle-receipts.json")
    query_value, _ = _read_json(
        output_root / relative_root / "query-decision-records.json"
    )
    receipt_value, _ = _read_json(output_root / relative_root / "decision-receipt.json")
    accounting_evidence_value, _ = _read_json(
        output_root / relative_root / "score-free-accounting-evidence.json"
    )
    manifest_mapping = dict(_to_mapping(sealed.manifest, "score input manifest"))
    boundary = {
        "schema": "redred.mc_wtb.stage4_score_boundary_evidence/v1",
        "assay_authoritative_input_manifest_sha256": assay_manifest_sha256,
        "full_cycle_result_sha256": observed["full-cycle-result.json"]["sha256"],
        "cycle_receipts_sha256": observed["cycle-receipts.json"]["sha256"],
        "query_projection_sha256": observed["query-decision-records.json"]["sha256"],
    }
    expected_manifest_fields = {
        "assay_authoritative_input_manifest_sha256": boundary[
            "assay_authoritative_input_manifest_sha256"
        ],
        "full_cycle_result_sha256": boundary["full_cycle_result_sha256"],
        "cycle_receipts_sha256": boundary["cycle_receipts_sha256"],
        "query_projection_sha256": boundary["query_projection_sha256"],
        "decision_receipt_sha256": observed["decision-receipt.json"]["sha256"],
        "score_free_accounting_sha256": observed[
            "score-free-accounting.json"
        ]["sha256"],
        "ray_events_sha256": ray_events_sha256,
    }
    if any(
        manifest_mapping.get(field) != value
        for field, value in expected_manifest_fields.items()
    ):
        raise SealingError("score input manifest differs from observed leaf bytes")
    if (
        not isinstance(full_value, Mapping)
        or full_value.get("cycle_receipts_sha256")
        != observed["cycle-receipts.json"]["sha256"]
        or not isinstance(cycle_value, list)
        or not isinstance(query_value, list)
        or not isinstance(receipt_value, Mapping)
        or receipt_value.get("decision_records_sha256")
        != observed["query-decision-records.json"]["sha256"]
        or receipt_value.get("expected_events") != len(query_value)
        or receipt_value.get("retired_records") != len(query_value)
    ):
        raise SealingError("receipt or cycle evidence differs from observed arrays")

    _write_json(output_root / relative_root / "score-boundary-evidence.json", boundary)
    _write_json(
        output_root / relative_root / "score-input-manifest.json", manifest_mapping
    )
    for name in ("score-boundary-evidence.json", "score-input-manifest.json"):
        relative = "%s/%s" % (relative_root, name)
        entry = _observe_file(
            output_root, relative, kind="object", record_count=1
        )
        files[relative] = entry
        observed[name] = entry
    diagnostic_binding = _observe_delayed_diagnostic(
        output_root,
        relative_root,
        sealed,
        assay_manifest_sha256,
        observed,
        _require_mapping(full_value, "bounded full-cycle result"),
        tuple(_require_mapping(row, "bounded cycle receipt") for row in cycle_value),
        _require_mapping(accounting_evidence_value, "accounting evidence"),
        files,
    )
    return {
        "score_input_manifest_path": "%s/score-input-manifest.json" % relative_root,
        "score_input_manifest_sha256": observed["score-input-manifest.json"][
            "sha256"
        ],
        "score_boundary_evidence_path": "%s/score-boundary-evidence.json"
        % relative_root,
        "score_boundary_evidence_sha256": observed[
            "score-boundary-evidence.json"
        ]["sha256"],
        "delayed_unbounded_depth_diagnostic": diagnostic_binding,
    }


def _verify_seal_tree(root: Path, expected_manifest_sha256: str) -> Mapping[str, Any]:
    expected = _require_sha(expected_manifest_sha256, "expected campaign seal")
    value, payload = _read_json(root / "stage4-score-free-seal-manifest.json")
    if not isinstance(value, Mapping) or _sha256(payload) != expected:
        raise SealingError("campaign seal differs from its expected root")
    files = value.get("files")
    if not isinstance(files, Mapping):
        raise SealingError("campaign seal file index is absent")
    windows = value.get("windows")
    if (
        not isinstance(windows, list)
        or len(windows) != 24
        or value.get("window_count") != 24
        or value.get("arm_count") != len(_ARM_ORDER)
        or value.get("arm_window_count") != 24 * len(_ARM_ORDER)
    ):
        raise SealingError("campaign does not close the frozen 24-by-4 matrix")
    for relative, expected_entry in files.items():
        if (
            type(relative) is not str
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected_entry, Mapping)
        ):
            raise SealingError("campaign seal contains an unsafe file entry")
        observed = _observe_file(
            root,
            relative,
            kind=str(expected_entry.get("kind")),
            record_count=int(expected_entry.get("record_count", -1)),
        )
        if observed != expected_entry:
            raise SealingError("sealed artifact differs: %s" % relative)
    _verify_delayed_diagnostic_links(root, value)
    return value


def _verify_delayed_diagnostic_links(
    root: Path, campaign: Mapping[str, Any]
) -> None:
    """Verify the outer diagnostic link without widening bounded boundaries."""

    files = _require_mapping(campaign.get("files"), "campaign file index")
    windows = _require_array(campaign.get("windows"), "campaign windows")
    assay_sha256 = _require_sha(
        campaign.get("assay_manifest_sha256"), "campaign assay manifest hash"
    )
    expected_binding_fields = frozenset((
        "path",
        "sha256",
        "evidence_sha256",
        "config_identity_sha256",
        "input_events_sha256",
        "input_poses_sha256",
        "decision_records_sha256",
        "cycle_receipts_sha256",
        "peak_fifo_depth",
        "assay_authoritative_input_manifest_sha256",
        "bounded_full_cycle_result_sha256",
        "bounded_cycle_receipts_sha256",
    ))
    for pointer_value in windows:
        pointer = _require_mapping(pointer_value, "campaign window pointer")
        relative = pointer.get("path")
        if (
            type(relative) is not str
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise SealingError("campaign window path is unsafe")
        indexed_window = _require_mapping(
            files.get(relative), "window seal file index entry"
        )
        if indexed_window.get("sha256") != pointer.get("sha256"):
            raise SealingError("window pointer differs from file index")
        window, payload = _read_json(root / relative)
        window_mapping = _require_mapping(window, "window seal")
        if _sha256(payload) != pointer.get("sha256"):
            raise SealingError("window pointer hash differs")
        arms = _require_mapping(window_mapping.get("arms"), "window arm seals")
        if set(arms) != set(_ARM_ORDER):
            raise SealingError("window seal does not contain all four arms")
        window_root = str(Path(relative).parent)
        for arm_name in _ARM_ORDER:
            arm_binding = _require_mapping(arms[arm_name], "window arm seal")
            if "delayed_unbounded_depth_diagnostic" not in arm_binding:
                raise SealingError("window arm seal lacks diagnostic disposition")
            diagnostic_binding = arm_binding.get(
                "delayed_unbounded_depth_diagnostic"
            )
            leaf_root = "%s/arms/%s" % (window_root, arm_name)
            bounded_receipts_value, _ = _read_json(
                root / leaf_root / "cycle-receipts.json"
            )
            bounded_receipts = tuple(
                _require_mapping(row, "bounded receipt")
                for row in _require_array(
                    bounded_receipts_value, "bounded cycle receipts"
                )
            )
            bounded_full = any(
                row.get("disposition_reason") == "fifo_full_forced_bypass"
                for row in bounded_receipts
            )
            if arm_name != Arm.DELAYED_EXACT.value:
                if diagnostic_binding is not None:
                    raise SealingError("diagnostic is bound to a non-delayed arm")
                continue
            if diagnostic_binding is None:
                if bounded_full:
                    raise SealingError("pressured delayed seal lacks a diagnostic")
                accounting_value, _ = _read_json(
                    root / leaf_root / "score-free-accounting-evidence.json"
                )
                minimum_depth = _require_mapping(
                    _require_mapping(
                        accounting_value, "accounting evidence"
                    ).get("minimum_depth_evidence"),
                    "accounting minimum-depth evidence",
                )
                if minimum_depth.get("basis") != "bounded_peak_no_full_pressure" or any(
                    minimum_depth.get(name) is not None
                    for name in (
                        "unbounded_diagnostic_evidence_sha256",
                        "unbounded_diagnostic_config_sha256",
                        "unbounded_diagnostic_decision_records_sha256",
                        "unbounded_diagnostic_cycle_receipts_sha256",
                    )
                ):
                    raise SealingError("unpressured delayed accounting names a diagnostic")
                continue
            binding = _require_mapping(
                diagnostic_binding, "delayed diagnostic binding"
            )
            if frozenset(binding) != expected_binding_fields or not bounded_full:
                raise SealingError("delayed diagnostic binding shape differs")
            diagnostic_path = binding.get("path")
            expected_path = "%s/%s" % (leaf_root, _DELAYED_DIAGNOSTIC_FILE)
            if diagnostic_path != expected_path:
                raise SealingError("delayed diagnostic path differs")
            diagnostic_entry = _require_mapping(
                files.get(expected_path), "diagnostic file index entry"
            )
            for name in (
                "sha256",
                "evidence_sha256",
                "config_identity_sha256",
                "input_events_sha256",
                "input_poses_sha256",
                "decision_records_sha256",
                "cycle_receipts_sha256",
                "peak_fifo_depth",
            ):
                if binding.get(name) != diagnostic_entry.get(name):
                    raise SealingError("diagnostic outer binding differs")
            if binding.get("assay_authoritative_input_manifest_sha256") != assay_sha256:
                raise SealingError("diagnostic assay binding differs")
            bounded_full_path = "%s/full-cycle-result.json" % leaf_root
            bounded_receipt_path = "%s/cycle-receipts.json" % leaf_root
            if (
                binding.get("bounded_full_cycle_result_sha256")
                != _require_mapping(files.get(bounded_full_path), "bounded result index").get(
                    "sha256"
                )
                or binding.get("bounded_cycle_receipts_sha256")
                != _require_mapping(files.get(bounded_receipt_path), "bounded receipt index").get(
                    "sha256"
                )
            ):
                raise SealingError("diagnostic bounded-evidence binding differs")
            diagnostic_value, _ = _read_json(root / expected_path)
            diagnostic_receipts = tuple(
                _require_mapping(row, "diagnostic receipt")
                for row in _require_array(
                    _require_mapping(
                        diagnostic_value, "diagnostic evidence"
                    ).get("cycle_receipts"),
                    "diagnostic receipts",
                )
            )
            if _admission_projection(bounded_receipts) != _admission_projection(
                diagnostic_receipts
            ):
                raise SealingError("sealed diagnostic admission binding differs")
            accounting_value, _ = _read_json(
                root / leaf_root / "score-free-accounting-evidence.json"
            )
            minimum_depth = _require_mapping(
                _require_mapping(accounting_value, "accounting evidence").get(
                    "minimum_depth_evidence"
                ),
                "accounting minimum-depth evidence",
            )
            expected_accounting = {
                "unbounded_diagnostic_evidence_sha256": binding[
                    "evidence_sha256"
                ],
                "unbounded_diagnostic_config_sha256": binding[
                    "config_identity_sha256"
                ],
                "unbounded_diagnostic_decision_records_sha256": binding[
                    "decision_records_sha256"
                ],
                "unbounded_diagnostic_cycle_receipts_sha256": binding[
                    "cycle_receipts_sha256"
                ],
                "minimum_zero_loss_buffer_entries": binding["peak_fifo_depth"],
            }
            if any(
                minimum_depth.get(name) != expected
                for name, expected in expected_accounting.items()
            ):
                raise SealingError("sealed accounting diagnostic link differs")


def verify_score_free_seal(
    output_dir: Path, *, expected_seal_manifest_sha256: str
) -> Mapping[str, Any]:
    """Reopen and verify every byte under a previously frozen campaign root."""

    return _verify_seal_tree(Path(output_dir), expected_seal_manifest_sha256)


def _build_window_with_required_diagnostic(bundle: Any, window_id: str) -> Mapping[Any, Any]:
    """Build once normally, adding the independent replay only when required."""

    try:
        return integration_adapter.build_all_arm_window(bundle, window_id)
    except integration_adapter.IntegrationError as exc:
        if _REPLAY_REQUIRED not in str(exc):
            raise
    inputs = integration_adapter.build_window_cycle_inputs(bundle, window_id)
    try:
        diagnostic = run_delayed_unbounded_diagnostic(
            window_id=inputs.window_id,
            window_start_ns=inputs.window_start_ns,
            events=inputs.events,
            poses=inputs.dataset_poses,
            synthetic_test_mode=False,
        )
    except CycleModelError as exc:
        raise SealingError(
            "required delayed unbounded diagnostic did not terminate validly"
        ) from exc
    return integration_adapter.build_all_arm_window(
        bundle,
        window_id,
        delayed_unbounded_diagnostic=diagnostic,
    )


def seal_official_score_free(
    assay_dir: Path,
    output_dir: Path,
    *,
    expected_assay_manifest_sha256: str,
) -> SealResult:
    """Write and independently observe all 24-by-4 score-free seal leaves."""

    assay_root = Path(assay_dir)
    output = Path(output_dir)
    if output.exists():
        raise SealingError("output directory must not already exist")
    manifest, assay_closure, window_summaries = _observe_assay(
        assay_root, expected_assay_manifest_sha256
    )
    bundle = integration_adapter.load_assay_bundle(
        assay_root, expected_manifest_sha256=expected_assay_manifest_sha256
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".%s." % output.name, dir=str(output.parent))
    )
    try:
        files = {}  # type: Dict[str, Mapping[str, Any]]
        _write_json(temporary / "assay-closure.json", assay_closure)
        files["assay-closure.json"] = _observe_file(
            temporary, "assay-closure.json", kind="object", record_count=1
        )
        window_roots = []  # type: List[Mapping[str, Any]]
        for summary in window_summaries:
            window_id = str(summary["window_id"])
            integrated = _build_window_with_required_diagnostic(bundle, window_id)
            if set(integrated) != set(Arm):
                raise SealingError("window does not contain all four arms")
            first = integrated[Arm.ZOH_FRESHNESS]
            ray_values = [
                _to_mapping(row, "ray event") for row in first.ray_events
            ]
            if len(ray_values) != int(summary["selected_event_count"]):
                raise SealingError("ray event count differs from its window summary")
            for arm in Arm:
                other = [
                    _to_mapping(row, "ray event")
                    for row in integrated[arm].ray_events
                ]
                if other != ray_values:
                    raise SealingError("arm ray projections differ within a window")
            window_root = "windows/%s" % window_id
            ray_relative = "%s/ray-events.json" % window_root
            _write_json(temporary / ray_relative, ray_values)
            files[ray_relative] = _observe_file(
                temporary,
                ray_relative,
                kind="array",
                record_count=len(ray_values),
            )
            ray_sha256 = files[ray_relative]["sha256"]
            arms = {}  # type: Dict[str, Mapping[str, Any]]
            for arm in Arm:
                leaf_root = "%s/arms/%s" % (window_root, arm.value)
                _write_leaf_inputs(temporary, leaf_root, integrated[arm])
                arms[arm.value] = _observe_leaf(
                    temporary,
                    leaf_root,
                    integrated[arm],
                    expected_assay_manifest_sha256,
                    str(ray_sha256),
                    int(summary["query_event_count"]),
                    files,
                )
            window_seal = {
                "schema": "redred.mc_wtb.stage4_score_free_window_seal/v1",
                "window_id": window_id,
                "warmup_start_ns_inclusive": summary[
                    "warmup_start_ns_inclusive"
                ],
                "query_start_ns_inclusive": summary["query_start_ns_inclusive"],
                "query_end_ns_exclusive": summary["query_end_ns_exclusive"],
                "selected_event_count": summary["selected_event_count"],
                "query_event_count": summary["query_event_count"],
                "ordered_query_event_ids_sha256": summary[
                    "ordered_query_event_ids_sha256"
                ],
                "ray_events_path": ray_relative,
                "ray_events_sha256": ray_sha256,
                "arms": arms,
            }
            window_seal_relative = "%s/window-seal.json" % window_root
            _write_json(temporary / window_seal_relative, window_seal)
            files[window_seal_relative] = _observe_file(
                temporary,
                window_seal_relative,
                kind="object",
                record_count=1,
            )
            window_roots.append({
                "window_id": window_id,
                "path": window_seal_relative,
                "sha256": files[window_seal_relative]["sha256"],
            })
        campaign = {
            "schema": "redred.mc_wtb.stage4_score_free_campaign_seal/v1",
            "content_class": "SCORE_FREE_OBSERVER_EVIDENCE_ONLY",
            "assay_manifest_sha256": expected_assay_manifest_sha256,
            "assay_authority_sha256": assay_closure["assay_authority_sha256"],
            "assay_closure_sha256": files["assay-closure.json"]["sha256"],
            "comparison_contract_sha256": manifest[
                "comparison_contract_sha256"
            ],
            "registry_sha256": manifest["registry"]["sha256"],
            "window_count": len(window_roots),
            "arm_count": len(_ARM_ORDER),
            "arm_window_count": len(window_roots) * len(_ARM_ORDER),
            "window_order": [row["window_id"] for row in window_roots],
            "arm_order": list(_ARM_ORDER),
            "windows": window_roots,
            "files": dict(sorted(files.items())),
        }
        campaign_path = temporary / "stage4-score-free-seal-manifest.json"
        _write_json(campaign_path, campaign)
        observed_campaign, campaign_payload = _read_json(campaign_path)
        campaign_sha256 = _sha256(campaign_payload)
        _verify_seal_tree(temporary, campaign_sha256)
        os.replace(str(temporary), str(output))
        return SealResult(output, observed_campaign, campaign_sha256)
    except Exception:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise
