"""Strict score-free bridge from Stage-4 assay artifacts to cycle evidence.

This module deliberately never calls the Stage-4 scorer.  It constructs and
hash-binds only cycle decisions, receipt-v2 records, accounting, and ray/
provenance inputs that a later scoring phase may consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample as RecoveryPoseSample,
    RecoveryMode,
    interpolate_committed_bracket,
    normalize_quaternion_xyzw,
    recover_causal_cav,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    DecisionReceipt,
    DecisionRecord,
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_decision_records,
)
from benchmarks.redred_mc_wtb_stage4_contract.receipt import ARM_LABELS
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    SimulationResult,
    run_cycle_model,
)
from benchmarks.redred_mc_wtb_stage4_scoring import (
    RayEvent,
    ScoreFreeAccounting,
    ScoreInputManifest,
    ShadowRay,
)
class IntegrationError(ValueError):
    """Assay provenance or a score-free integration invariant failed."""


_EVENTS = "stage4_events.jsonl"
_BATCHES = "stage4_occurrence_batches.jsonl"
_SNAPSHOTS = "stage4_occurrence_pose_snapshots.jsonl"
_DATASET_POSES = "stage4_dataset_pose_packets.jsonl"
_ORACLE_POSES = "oracle_resampled_groundtruth_1khz.jsonl"
_ORACLE_SCHEDULE = "stage4_oracle_window_schedule.jsonl"
_MANIFEST = "stage4_input_manifest.json"
_ASSAY_FILES = (
    _EVENTS,
    _BATCHES,
    _SNAPSHOTS,
    _DATASET_POSES,
    _ORACLE_POSES,
    _ORACLE_SCHEDULE,
)
_REASON_ALIASES = {
    "missing_left_pose": "missing_bracket",
    "invalid_bracket": "invalid_pose",
    "full_pressure_oldest_bypass": "fifo_full_forced_bypass",
}
_FRESHNESS_REASONS = frozenset(("no_occurrence_pose", "stale_pose"))
_INVALID_REASONS = frozenset(("invalid_pose", "invalid_bracket", "missing_left_pose"))
_OPERATIONAL_REASONS = frozenset(("deadline_timeout", "full_pressure_oldest_bypass"))


def _duplicate_rejecting_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise IntegrationError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _decode_json(raw: bytes, where: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"), object_pairs_hook=_duplicate_rejecting_object
        )
    except IntegrationError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise IntegrationError("%s is not strict ASCII JSON" % where) from exc
    if not isinstance(value, Mapping):
        raise IntegrationError("%s must be a JSON object" % where)
    return value


def _load_jsonl(path: Path) -> Tuple[Mapping[str, Any], ...]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrationError("cannot read assay artifact: %s" % path.name) from exc
    if raw and not raw.endswith(b"\n"):
        raise IntegrationError("assay JSONL lacks its final newline: %s" % path.name)
    records = []  # type: List[Mapping[str, Any]]
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        value = _decode_json(line, "%s:%d" % (path.name, line_number))
        if canonical_json_bytes(value) != line:
            raise IntegrationError("assay JSONL is not canonical: %s" % path.name)
        records.append(value)
    return tuple(records)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationError("%s must be an object" % where)
    return value


def _require_int(value: Any, where: str, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrationError("%s must be an integer" % where)
    if minimum is not None and value < minimum:
        raise IntegrationError("%s is below its minimum" % where)
    return value


def _require_sha(value: Any, where: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IntegrationError("%s must be a lowercase SHA-256" % where)
    return value


def _validate_payload(row: Mapping[str, Any]) -> None:
    payload = row.get("payload_hex")
    if (
        type(payload) is not str
        or len(payload) != 26
        or any(character not in "0123456789abcdef" for character in payload)
    ):
        raise IntegrationError("event payload is not canonical 102-bit hex")
    value = int(payload, 16)
    if value >= 1 << 102:
        raise IntegrationError("event payload exceeds 102 bits")
    fields = (
        ("event_id", 24),
        ("window_event_ordinal", 11),
        ("timestamp_ns", 36),
        ("x", 8),
        ("y", 8),
        ("polarity", 1),
        ("causal_pose_source_index", 14),
    )
    for name, width in fields:
        expected = _require_int(row.get(name), "event.%s" % name, 0)
        if value & ((1 << width) - 1) != expected:
            raise IntegrationError("event payload field differs: %s" % name)
        value >>= width
    if value:
        raise IntegrationError("event payload has trailing bits")


@dataclass(frozen=True)
class AssayBundle:
    root: Path
    manifest: Mapping[str, Any]
    events: Tuple[Mapping[str, Any], ...]
    batches: Tuple[Mapping[str, Any], ...]
    snapshots: Tuple[Mapping[str, Any], ...]
    dataset_poses: Tuple[Mapping[str, Any], ...]
    oracle_poses: Tuple[Mapping[str, Any], ...]
    oracle_schedule: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class WindowCycleInputs:
    window_id: str
    window_start_ns: int
    window_end_ns: int
    event_rows: Tuple[Mapping[str, Any], ...]
    events: Tuple[Event, ...]
    dataset_poses: Tuple[PosePacket, ...]
    oracle_poses: Tuple[PosePacket, ...]
    dataset_quaternions: Mapping[int, Tuple[float, float, float, float]]
    oracle_quaternions: Mapping[int, Tuple[float, float, float, float]]


@dataclass(frozen=True)
class IntegratedArmWindow:
    arm: Arm
    simulation: SimulationResult
    query_records: Tuple[DecisionRecord, ...]
    receipt: DecisionReceipt
    accounting: ScoreFreeAccounting
    ray_events: Tuple[RayEvent, ...]
    manifest: ScoreInputManifest
    full_cycle_evidence_sha256: str
    query_projection_sha256: str


def load_assay_bundle(root: Path) -> AssayBundle:
    """Load and cryptographically close one score-free assay directory."""

    directory = Path(root)
    manifest_path = directory / _MANIFEST
    try:
        manifest_raw = manifest_path.read_bytes()
    except OSError as exc:
        raise IntegrationError("cannot read assay manifest") from exc
    manifest = _decode_json(manifest_raw, _MANIFEST)
    if canonical_json_bytes(manifest) != manifest_raw:
        raise IntegrationError("assay manifest is not canonical")
    if manifest.get("schema") != "redred.mc_wtb.stage4_score_free_inputs/v2":
        raise IntegrationError("assay manifest schema is not v2")
    contract = load_comparison_contract()
    if manifest.get("comparison_contract_sha256") != contract.canonical_sha256:
        raise IntegrationError("assay manifest contract hash differs")
    registry = _require_mapping(manifest.get("registry"), "manifest.registry")
    if registry.get("sha256") != contract.registry["sha256"]:
        raise IntegrationError("assay manifest registry hash differs")

    artifacts = _require_mapping(manifest.get("artifacts"), "manifest.artifacts")
    loaded = {}  # type: Dict[str, Tuple[Mapping[str, Any], ...]]
    for name in _ASSAY_FILES:
        artifact = _require_mapping(artifacts.get(name), "artifact %s" % name)
        if artifact.get("path") != name:
            raise IntegrationError("assay artifact path differs: %s" % name)
        path = directory / name
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise IntegrationError("missing assay artifact: %s" % name) from exc
        if _sha256_bytes(payload) != _require_sha(artifact.get("sha256"), name):
            raise IntegrationError("assay artifact hash differs: %s" % name)
        if len(payload) != _require_int(artifact.get("size_bytes"), name, 0):
            raise IntegrationError("assay artifact byte count differs: %s" % name)
        rows = _load_jsonl(path)
        if len(rows) != _require_int(artifact.get("record_count"), name, 0):
            raise IntegrationError("assay artifact record count differs: %s" % name)
        loaded[name] = rows

    authority = _require_mapping(
        manifest.get("authoritative_input_binding"),
        "manifest.authoritative_input_binding",
    )
    authority_body = dict(authority)
    supplied_binding = _require_sha(
        authority_body.pop("binding_sha256", None), "authority binding"
    )
    if canonical_sha256(authority_body) != supplied_binding:
        raise IntegrationError("authoritative assay binding hash differs")
    for key, filename in (
        ("dataset_pose_packet_stream", _DATASET_POSES),
        ("occurrence_pose_snapshot_stream", _SNAPSHOTS),
        ("oracle_pose_stream", _ORACLE_POSES),
        ("oracle_window_schedule_stream", _ORACLE_SCHEDULE),
    ):
        stream = _require_mapping(authority.get(key), "authority.%s" % key)
        if stream.get("path") != filename or stream.get("sha256") != artifacts[filename]["sha256"]:
            raise IntegrationError("authoritative assay stream binding differs: %s" % key)
    ordered = _require_mapping(
        authority.get("ordered_102bit_occurrence_records"), "ordered records"
    )
    payload_stream = b"".join(
        (str(row.get("payload_hex")) + "\n").encode("ascii")
        for row in loaded[_EVENTS]
    )
    if ordered.get("sha256") != _sha256_bytes(payload_stream):
        raise IntegrationError("ordered 102-bit occurrence binding differs")
    if ordered.get("record_count") != len(loaded[_EVENTS]):
        raise IntegrationError("ordered 102-bit occurrence count differs")
    source = _require_mapping(manifest.get("source"), "manifest.source")
    raw_sources = _require_mapping(
        authority.get("raw_source_streams"), "authority.raw_source_streams"
    )
    if raw_sources != {
        "events.txt_sha256": source.get("events_sha256"),
        "groundtruth.txt_sha256": source.get("groundtruth_sha256"),
        "calib.txt_sha256": source.get("calibration_sha256"),
    }:
        raise IntegrationError("raw source/calibration binding differs")
    generator_runtime = _require_mapping(
        manifest.get("generator_runtime"), "manifest.generator_runtime"
    )
    if authority.get("generator_code_sha256") != generator_runtime.get(
        "generator_code_sha256"
    ) or authority.get("runtime") != generator_runtime.get("runtime"):
        raise IntegrationError("generator/runtime authority binding differs")
    for row in loaded[_EVENTS]:
        _validate_payload(row)
    event_ids = tuple(_require_int(row.get("event_id"), "event_id", 0) for row in loaded[_EVENTS])
    if any(right <= left for left, right in zip(event_ids, event_ids[1:])):
        raise IntegrationError("assay event IDs are not globally ordered")

    for packet in loaded[_DATASET_POSES]:
        body = dict(packet)
        packet_hash = _require_sha(body.pop("packet_sha256", None), "pose packet hash")
        if canonical_sha256(body) != packet_hash:
            raise IntegrationError("dataset pose packet canonical hash differs")
    return AssayBundle(
        directory,
        manifest,
        loaded[_EVENTS],
        loaded[_BATCHES],
        loaded[_SNAPSHOTS],
        loaded[_DATASET_POSES],
        loaded[_ORACLE_POSES],
        loaded[_ORACLE_SCHEDULE],
    )


def _quaternion(value: Any, where: str) -> Tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise IntegrationError("%s must be an xyzw quaternion" % where)
    try:
        result = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise IntegrationError("%s contains a non-numeric component" % where) from exc
    if not all(math.isfinite(component) for component in result):
        raise IntegrationError("%s contains a non-finite component" % where)
    return result  # type: ignore[return-value]


def _window_limits(manifest: Mapping[str, Any], window_id: str) -> Tuple[int, int]:
    windows = manifest.get("windows")
    if not isinstance(windows, list):
        raise IntegrationError("manifest windows must be an array")
    matches = [row for row in windows if isinstance(row, Mapping) and row.get("window_id") == window_id]
    if len(matches) != 1:
        raise IntegrationError("window summary is absent or duplicated")
    row = matches[0]
    if "window_start_ns" not in row or "window_end_ns" not in row:
        raise IntegrationError(
            "UPSTREAM_WINDOW_LIMITS_NOT_SERIALIZED: window_start_ns/window_end_ns are required"
        )
    start = _require_int(row["window_start_ns"], "window_start_ns", 0)
    end = _require_int(row["window_end_ns"], "window_end_ns", 0)
    if end <= start:
        raise IntegrationError("window limits are not increasing")
    return start, end


def build_window_cycle_inputs(bundle: AssayBundle, window_id: str) -> WindowCycleInputs:
    """Convert one loaded assay window into all cycle-model input streams."""

    if not isinstance(bundle, AssayBundle):
        raise IntegrationError("bundle must be a validated AssayBundle")
    start, end = _window_limits(bundle.manifest, window_id)
    event_rows = tuple(row for row in bundle.events if row.get("window_id") == window_id)
    if not event_rows:
        raise IntegrationError("window has no assay events")
    events = tuple(
        Event(
            _require_int(row.get("event_id"), "event_id", 0),
            _require_int(row.get("timestamp_ns"), "event timestamp", 0),
            True,
        )
        for row in event_rows
    )

    dataset_rows = tuple(row for row in bundle.dataset_poses if row.get("window_id") == window_id)
    dataset_packets = []  # type: List[PosePacket]
    dataset_quaternions = {}  # type: Dict[int, Tuple[float, float, float, float]]
    for row in dataset_rows:
        pose_id = _require_int(row.get("source_pose_id"), "source_pose_id", 0)
        commit = _require_int(row.get("commit_cycle"), "dataset commit_cycle")
        quaternion = _quaternion(row.get("quaternion_xyzw"), "dataset quaternion")
        dataset_quaternions[pose_id] = quaternion
        try:
            dataset_packets.append(PosePacket(
                pose_id,
                _require_int(row.get("timestamp_ns"), "pose timestamp", 0),
                commit,
                PoseSource.DATASET,
                _require_sha(row.get("pose_value_sha256"), "pose value hash"),
            ))
        except CycleModelError as exc:
            if commit < 0:
                raise IntegrationError(
                    "UPSTREAM_SIGNED_HISTORY_CYCLE_UNSUPPORTED: "
                    "cycle PosePacket rejects pre-window commits"
                ) from exc
            raise IntegrationError("dataset pose cannot enter cycle model") from exc

    oracle_values = dict(
        (
            _require_int(row.get("oracle_pose_id"), "oracle_pose_id", 0),
            row,
        )
        for row in bundle.oracle_poses
    )
    schedule_rows = tuple(row for row in bundle.oracle_schedule if row.get("window_id") == window_id)
    oracle_packets = []  # type: List[PosePacket]
    oracle_quaternions = {}  # type: Dict[int, Tuple[float, float, float, float]]
    for schedule in schedule_rows:
        pose_id = _require_int(schedule.get("oracle_pose_id"), "oracle schedule pose ID", 0)
        value = _require_mapping(oracle_values.get(pose_id), "oracle packet")
        commit = _require_int(schedule.get("commit_cycle"), "oracle commit_cycle")
        quaternion = _quaternion(value.get("quaternion_xyzw"), "oracle quaternion")
        oracle_quaternions[pose_id] = quaternion
        try:
            oracle_packets.append(PosePacket(
                pose_id,
                _require_int(schedule.get("effective_timestamp_ns"), "oracle timestamp", 0),
                commit,
                PoseSource.ORACLE_1KHZ,
                _require_sha(schedule.get("pose_value_sha256"), "oracle pose hash"),
            ))
        except CycleModelError as exc:
            if commit < 0:
                raise IntegrationError(
                    "UPSTREAM_SIGNED_HISTORY_CYCLE_UNSUPPORTED: "
                    "cycle PosePacket rejects pre-window oracle commits"
                ) from exc
            raise IntegrationError("oracle pose cannot enter cycle model") from exc
    return WindowCycleInputs(
        window_id,
        start,
        end,
        event_rows,
        events,
        tuple(dataset_packets),
        tuple(oracle_packets),
        dataset_quaternions,
        oracle_quaternions,
    )


def _convert_record(record: Any) -> DecisionRecord:
    mapping = record.to_mapping()
    mapping["arm_semantic_label"] = ARM_LABELS[mapping["arm"]]
    mapping["disposition_reason"] = _REASON_ALIASES.get(
        mapping["disposition_reason"], mapping["disposition_reason"]
    )
    try:
        return DecisionRecord.from_mapping(mapping)
    except Exception as exc:
        if any(value < 0 for value in mapping["occurrence_pose_commit_cycles"]):
            raise IntegrationError(
                "UPSTREAM_SIGNED_HISTORY_CYCLE_UNSUPPORTED: "
                "receipt v2 rejects pre-window commits"
            ) from exc
        raise IntegrationError("cycle decision cannot satisfy receipt v2") from exc


def _validate_assay_snapshot_projection(
    bundle: AssayBundle,
    inputs: WindowCycleInputs,
    converted: Sequence[DecisionRecord],
) -> None:
    snapshots = dict(
        (
            (row.get("window_id"), row.get("occurrence_batch_id")),
            row,
        )
        for row in bundle.snapshots
        if row.get("window_id") == inputs.window_id
    )
    for event_row, record in zip(inputs.event_rows, converted):
        key = (inputs.window_id, event_row.get("occurrence_batch_id"))
        snapshot = _require_mapping(snapshots.get(key), "occurrence PoseSnapshot")
        poses = snapshot.get("pose_packets")
        if not isinstance(poses, list):
            raise IntegrationError("occurrence PoseSnapshot pose_packets is not an array")
        expected = tuple(
            (
                pose.get("source_pose_id"),
                pose.get("timestamp_ns"),
                pose.get("commit_cycle"),
                pose.get("pose_value_sha256"),
            )
            for pose in poses
        )
        actual = tuple(zip(
            record.occurrence_pose_ids,
            record.occurrence_pose_timestamps_ns,
            record.occurrence_pose_commit_cycles,
            record.occurrence_pose_sha256,
        ))
        if actual != expected:
            raise IntegrationError("cycle occurrence pose differs from authoritative assay snapshot")
        if event_row.get("occurrence_pose_snapshot_sha256") != snapshot.get("pose_snapshot_sha256"):
            raise IntegrationError("event does not bind its authoritative occurrence snapshot")


def _normalized_world_ray(
    sensor_ray: Sequence[float], quaternion: Sequence[float]
) -> Tuple[float, float, float]:
    if not isinstance(sensor_ray, (tuple, list)) or len(sensor_ray) != 3:
        raise IntegrationError("sensor ray must contain three components")
    try:
        vector = tuple(float(component) for component in sensor_ray)
    except (TypeError, ValueError, OverflowError) as exc:
        raise IntegrationError("sensor ray contains a non-numeric component") from exc
    x, y, z, w = normalize_quaternion_xyzw(quaternion)
    vx, vy, vz = vector
    cross_x = y * vz - z * vy
    cross_y = z * vx - x * vz
    cross_z = x * vy - y * vx
    second_x = y * cross_z - z * cross_y
    second_y = z * cross_x - x * cross_z
    second_z = x * cross_y - y * cross_x
    rotated = (
        vx + 2.0 * (w * cross_x + second_x),
        vy + 2.0 * (w * cross_y + second_y),
        vz + 2.0 * (w * cross_z + second_z),
    )
    norm = math.sqrt(math.fsum(component * component for component in rotated))
    if not math.isfinite(norm) or norm <= 0.0:
        raise IntegrationError("world ray is zero or non-finite")
    return tuple(component / norm for component in rotated)  # type: ignore[return-value]


def _pose_rows(record: DecisionRecord, used: bool) -> Tuple[Tuple[int, int, int, str], ...]:
    if used:
        return tuple(zip(
            record.used_pose_ids,
            record.used_pose_timestamps_ns,
            record.used_pose_commit_cycles,
            record.used_pose_sha256,
        ))
    return tuple(zip(
        record.occurrence_pose_ids,
        record.occurrence_pose_timestamps_ns,
        record.occurrence_pose_commit_cycles,
        record.occurrence_pose_sha256,
    ))


def _shadow_for_record(
    record: DecisionRecord,
    sensor_ray: Sequence[float],
    quaternions: Mapping[int, Tuple[float, float, float, float]],
) -> ShadowRay:
    occurrence = _pose_rows(record, False)
    used = _pose_rows(record, True)
    if record.arm == Arm.CAUSAL_CAV.value and record.disposition_reason == "causal_cav":
        selected = occurrence[-2:]
        samples = tuple(
            RecoveryPoseSample(timestamp, commit, quaternions[pose_id])
            for pose_id, timestamp, commit, _ in selected
        )
        recovery = recover_causal_cav(
            samples, record.event_timestamp_ns, record.occurrence_cycle
        )
        if recovery.mode is not RecoveryMode.CAV:
            raise IntegrationError("recovery geometry disagrees with cycle CAV selection")
        quaternion = recovery.quaternion_xyzw
        transform = "occurrence_cav"
    elif record.arm == Arm.DELAYED_EXACT.value:
        if len(used) != 2:
            raise IntegrationError(
                "UPSTREAM_DELAYED_RAW_SHADOW_ARITY_UNREPRESENTABLE: delayed_slerp requires two poses"
            )
        left, right = used
        bracket = interpolate_committed_bracket(
            RecoveryPoseSample(left[1], left[2], quaternions[left[0]]),
            RecoveryPoseSample(right[1], right[2], quaternions[right[0]]),
            record.event_timestamp_ns,
            record.retire_cycle,
        )
        quaternion = bracket.quaternion_xyzw
        selected = used
        transform = "delayed_slerp"
    else:
        if not occurrence:
            raise IntegrationError("score-input shadow requires an occurrence pose")
        selected = occurrence[-1:]
        quaternion = quaternions[selected[-1][0]]
        transform = (
            "oracle_prefix"
            if record.arm == Arm.ORACLE_1KHZ.value
            else "occurrence_zoh"
        )
    if quaternion is None:
        raise IntegrationError("pose recovery did not produce an orientation")
    return ShadowRay(
        record.arm,
        _normalized_world_ray(sensor_ray, quaternion),
        transform,
        tuple(row[0] for row in selected),
        tuple(row[1] for row in selected),
        tuple(row[2] for row in selected),
        tuple(row[3] for row in selected),
    )


def _full_cycle_evidence(result: SimulationResult) -> Mapping[str, Any]:
    return {
        "window_id": result.window_id,
        "arm": result.arm.value,
        "decision_records": [record.to_mapping() for record in result.records],
        "cycle_receipts": [receipt.to_mapping() for receipt in result.cycle_receipts],
        "decision_records_sha256": result.decision_records_sha256,
        "cycle_receipts_sha256": result.cycle_receipts_sha256,
        "common_serializer_cycles": list(result.common_serializer_cycles),
        "always_bypass_retire_cycles": list(result.always_bypass_retire_cycles),
        "policy_added_latency_cycles": list(result.policy_added_latency_cycles),
        "peak_ingress_staging_occupancy": result.peak_ingress_staging_occupancy,
        "peak_buffer_occupancy": result.peak_buffer_occupancy,
        "raw_ingress_lanes": result.raw_ingress_lanes,
        "ingress_staging_entries": result.ingress_staging_entries,
        "buffer_entries": result.buffer_entries,
        "event_record_bits": result.event_record_bits,
        "pose_packet_bits": result.pose_packet_bits,
        "event_lanes": result.event_lanes,
        "transform_pipeline_cycles": result.transform_pipeline_cycles,
    }


def _ceil_rate(records: int, bits: int, duration_ns: int) -> int:
    return (records * bits * 1_000_000_000 + duration_ns - 1) // duration_ns


def _derive_accounting(
    inputs: WindowCycleInputs,
    result: SimulationResult,
    converted: Sequence[DecisionRecord],
) -> ScoreFreeAccounting:
    query_indexes = tuple(
        index for index, row in enumerate(inputs.event_rows) if row.get("is_query") is True
    )
    query_records = tuple(converted[index] for index in query_indexes)
    freshness = []  # type: List[int]
    invalid = []  # type: List[int]
    operational = []  # type: List[int]
    for index in query_indexes:
        original = result.records[index]
        if original.disposition == "corrected_world_ray":
            continue
        elif original.disposition_reason in _FRESHNESS_REASONS:
            freshness.append(original.event_id)
        elif original.disposition_reason in _INVALID_REASONS:
            invalid.append(original.event_id)
        elif original.disposition_reason in _OPERATIONAL_REASONS:
            operational.append(original.event_id)
        else:
            raise IntegrationError("raw disposition lacks a frozen accounting category")
    baseline = tuple(
        (inputs.events[index].event_id, result.always_bypass_retire_cycles[index])
        for index in query_indexes
    )
    if tuple(record.event_id for record in query_records) != tuple(row[0] for row in baseline):
        raise IntegrationError("query accounting projection differs from cycle evidence")
    duration = inputs.window_end_ns - inputs.window_start_ns
    pose_count = len(inputs.oracle_poses) if result.arm is Arm.ORACLE_1KHZ else len(inputs.dataset_poses)
    buffer_bit_cycles = sum(
        max(0, receipt.retire_cycle - receipt.admission_cycle) * result.event_record_bits
        for receipt in result.cycle_receipts
    )
    contract = load_comparison_contract()
    pose_ring_entries = int(contract.timing["pose_ring_entries"])
    incremental_state = (
        result.ingress_staging_entries * result.event_record_bits
        + result.peak_buffer_occupancy * result.event_record_bits
        + pose_ring_entries * result.pose_packet_bits
    )
    attempted = tuple(
        result.records[index].event_id
        for index in query_indexes
        if result.records[index].disposition == "corrected_world_ray"
        or result.records[index].disposition_reason in _OPERATIONAL_REASONS
    )
    return ScoreFreeAccounting(
        inputs.window_id,
        result.arm.value,
        baseline,
        attempted,
        tuple(freshness),
        tuple(invalid),
        tuple(operational),
        result.peak_buffer_occupancy,
        result.peak_buffer_occupancy,
        buffer_bit_cycles,
        _ceil_rate(pose_count, result.pose_packet_bits, duration),
        _ceil_rate(len(inputs.events), result.event_record_bits, duration),
        incremental_state,
        0,
        0,
        0,
        0,
    )


def _artifact_bindings(
    bundle: AssayBundle,
    arm: Arm,
    full_cycle_evidence_sha256: str,
) -> Tuple[Tuple[str, str], ...]:
    contract = load_comparison_contract()
    package_root = Path(__file__).resolve().parents[1]
    assay_runtime = _require_mapping(
        bundle.manifest.get("generator_runtime"), "generator_runtime"
    )
    generator_code = _require_mapping(
        assay_runtime.get("generator_code_sha256"), "generator code hashes"
    )
    runtime = _require_mapping(assay_runtime.get("runtime"), "runtime binding")
    source = _require_mapping(bundle.manifest.get("source"), "manifest.source")
    cycle_binding = {
        "model_py_sha256": _sha256_file(
            package_root / "redred_mc_wtb_stage4_cyclemodel" / "model.py"
        ),
        "full_cycle_evidence_sha256": full_cycle_evidence_sha256,
    }
    bindings = {
        "protocol": contract.canonical_sha256,
        "registry": contract.registry["sha256"],
        "arm_parameters": canonical_sha256(contract.arms[arm.value]),
        "generator": canonical_sha256(generator_code),
        "cycle_model": canonical_sha256(cycle_binding),
        "scorer": _sha256_file(
            package_root / "redred_mc_wtb_stage4_scoring" / "scoring.py"
        ),
        "sources": canonical_sha256(source),
        "runtime": canonical_sha256(runtime),
    }
    return tuple(bindings.items())


def build_all_arm_window(
    bundle: AssayBundle, window_id: str
) -> Mapping[Arm, IntegratedArmWindow]:
    """Run all four score-free arms and seal their query projections."""

    inputs = build_window_cycle_inputs(bundle, window_id)
    simulations = {}  # type: Dict[Arm, SimulationResult]
    converted_by_arm = {}  # type: Dict[Arm, Tuple[DecisionRecord, ...]]
    for arm in Arm:
        poses = inputs.oracle_poses if arm is Arm.ORACLE_1KHZ else inputs.dataset_poses
        try:
            result = run_cycle_model(
                window_id=window_id,
                window_start_ns=inputs.window_start_ns,
                arm=arm,
                events=inputs.events,
                poses=poses,
            )
        except CycleModelError as exc:
            raise IntegrationError("cycle model rejected assay inputs for %s" % arm.value) from exc
        converted = tuple(_convert_record(record) for record in result.records)
        if arm is not Arm.ORACLE_1KHZ:
            _validate_assay_snapshot_projection(bundle, inputs, converted)
        for event_row, cycle_receipt in zip(inputs.event_rows, result.cycle_receipts):
            if (
                cycle_receipt.admission_cycle != event_row.get("presentation_cycle")
                or cycle_receipt.admission_lane != event_row.get("presentation_lane")
            ):
                raise IntegrationError(
                    "UPSTREAM_CYCLEMODEL_INGRESS_SCHEDULE_MISMATCH: "
                    "cycle admission differs from assay presentation"
                )
        simulations[arm] = result
        converted_by_arm[arm] = converted

    ray_events = []  # type: List[RayEvent]
    for index, event_row in enumerate(inputs.event_rows):
        sensor_ray = event_row.get("sensor_ray")
        if not isinstance(sensor_ray, list):
            raise IntegrationError("assay event sensor_ray must be an array")
        shadows = []  # type: List[ShadowRay]
        for arm in Arm:
            quaternions = (
                inputs.oracle_quaternions
                if arm is Arm.ORACLE_1KHZ
                else inputs.dataset_quaternions
            )
            shadows.append(
                _shadow_for_record(converted_by_arm[arm][index], sensor_ray, quaternions)
            )
        ray_events.append(RayEvent(
            window_id,
            inputs.events[index].event_id,
            inputs.events[index].timestamp_ns,
            _require_int(event_row.get("polarity"), "event polarity", 0),
            event_row.get("is_query") is True,
            tuple(float(value) for value in sensor_ray),
            tuple(shadows),
        ))
    ray_values = tuple(ray_events)
    ray_digest = canonical_sha256([event.to_mapping() for event in ray_values])

    integrated = {}  # type: Dict[Arm, IntegratedArmWindow]
    query_indexes = tuple(
        index for index, row in enumerate(inputs.event_rows) if row.get("is_query") is True
    )
    query_ids = tuple(inputs.events[index].event_id for index in query_indexes)
    if not query_ids:
        raise IntegrationError("window has no query projection")
    contract = load_comparison_contract()
    for arm in Arm:
        result = simulations[arm]
        converted = converted_by_arm[arm]
        query_records = tuple(converted[index] for index in query_indexes)
        receipt = validate_decision_records(
            contract,
            query_ids,
            query_records,
            expected_window_id=window_id,
            expected_arm=arm.value,
        )
        accounting = _derive_accounting(inputs, result, converted)
        evidence_hash = canonical_sha256(_full_cycle_evidence(result))
        manifest = ScoreInputManifest(
            window_id,
            arm.value,
            receipt.canonical_sha256(),
            accounting.canonical_sha256(),
            ray_digest,
            _artifact_bindings(bundle, arm, evidence_hash),
        )
        projection_hash = canonical_sha256({
            "full_cycle_evidence_sha256": evidence_hash,
            "query_event_ids": list(query_ids),
            "decision_receipt_sha256": receipt.canonical_sha256(),
            "score_free_accounting_sha256": accounting.canonical_sha256(),
            "ray_events_sha256": ray_digest,
            "score_input_manifest_sha256": manifest.canonical_sha256(),
        })
        integrated[arm] = IntegratedArmWindow(
            arm,
            result,
            query_records,
            receipt,
            accounting,
            ray_values,
            manifest,
            evidence_hash,
            projection_hash,
        )
    return integrated
