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
    GeometryError,
    PoseSample as RecoveryPoseSample,
    RecoveryMode,
    interpolate_committed_bracket,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import (
    Calibration,
    EventSample,
    SourceInputError,
    sensor_ray,
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
    ScoreBoundaryEvidence,
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
_ASSAY_EVENT_RECORD_FIELDS = frozenset(
    (
        "window_id",
        "event_id",
        "event_sequence_tag",
        "timestamp_ns",
        "x",
        "y",
        "polarity",
        "sensor_ray",
        "is_query",
        "window_event_ordinal",
        "occurrence_cycle",
        "equal_timestamp_cluster_id",
        "equal_timestamp_cluster_size",
        "occurrence_batch_id",
        "occurrence_lane",
        "occurrence_batch_size",
        "occurrence_pose_snapshot_sha256",
        "causal_pose_source_index",
        "payload_hex",
        "presentation_cycle",
        "presentation_lane",
        "serializer_queue_cycles",
    )
)
_REASON_ALIASES = {
    "missing_left_pose": "missing_bracket",
    "invalid_bracket": "invalid_pose",
    "full_pressure_oldest_bypass": "fifo_full_forced_bypass",
}
_ARM_CATEGORY_REASONS = {
    Arm.ZOH_FRESHNESS: {
        "corrected": frozenset(("fresh_zoh",)),
        "freshness": frozenset(("stale_pose",)),
        "invalid": frozenset(("no_occurrence_pose", "invalid_pose")),
        "operational": frozenset(),
    },
    Arm.CAUSAL_CAV: {
        "corrected": frozenset(("causal_cav", "fresh_zoh_fallback")),
        "freshness": frozenset(("stale_pose",)),
        "invalid": frozenset(("no_occurrence_pose", "invalid_pose")),
        "operational": frozenset(),
    },
    Arm.DELAYED_EXACT: {
        "corrected": frozenset(("bracket_interpolation",)),
        "freshness": frozenset(),
        "invalid": frozenset(("missing_bracket",)),
        "operational": frozenset(
            ("deadline_timeout", "fifo_full_forced_bypass", "invalid_pose")
        ),
    },
    Arm.ORACLE_1KHZ: {
        "corrected": frozenset(("oracle_fresh_zoh",)),
        "freshness": frozenset(("stale_pose",)),
        "invalid": frozenset(("no_occurrence_pose", "invalid_pose")),
        "operational": frozenset(),
    },
}

_STATE_COMPONENTS_BITS = (
    ("delayed_fifo_payload", 1024 * 102),
    ("ingress_capture_payload", 6 * 102),
    ("pose_ring_payload", 16 * 192),
    ("delayed_fifo_pointers_and_occupancy", 31),
    ("ingress_serializer_count_and_cursor", 6),
    ("pose_ring_write_pointer_and_valid_count", 9),
    ("pose_ring_live_reference_counters", 16 * 11),
    ("transform_pipeline_payload", 2 * 102),
    ("atomic_pose_ingress_staging", 192),
    ("global_cycle_and_deadline_counter", 21),
    ("expected_and_retired_receipt_counters", 28),
)
_CONSERVATIVE_INCREMENTAL_STATE_BITS = 108_799
_CONSERVATIVE_POSE_BANDWIDTH_BITS_PER_SECOND = 192_000
_EVENT_RECORD_BITS = 102
_EVENT_SEQUENCE_TAG_BITS = 24
_EVENT_SEQUENCE_TAG_MODULUS = 1 << _EVENT_SEQUENCE_TAG_BITS
_MAXIMUM_SIMULTANEOUS_LIVE_REFERENCES = 1032
_SOURCE_EVENT_ID_SPAN_LIMIT = 1 << 23
_EXPECTED_EVENT_RECORD_IDENTITY = {
    "payload_field": "transport_sequence_tag_not_dataset_event_index",
    "transport_sequence_tag_bits": 24,
    "transport_sequence_tag_modulus": 1 << 24,
    "transport_sequence_tag_rule": "source_event_id_modulo_2^24",
    "independent_reset_domain": "each_independently_simulated_window",
    "per_window_transport_sequence_tag_uniqueness_required": True,
    "max_source_event_id_span_per_window_rule": (
        "serialized_max_source_event_id_minus_min_source_event_id_strictly_less_than_2^23"
    ),
    "global_selected_transport_tags_unique": True,
    "global_selected_transport_tag_scope": "frozen_24_window_assay_artifact",
    "cross_window_source_event_id_range_used_as_live_span": False,
    "full_source_event_id_scope": (
        "score_free_artifacts_and_receipts_verification_only"
    ),
    "full_source_event_id_hardware_state_bits": 0,
    "timestamp_bits": 36,
    "timestamp_role": "retained_functional_motion_data",
    "maximum_simultaneous_live_records": 1032,
    "maximum_simultaneous_live_records_role": (
        "capacity_fact_only_not_wrap_safety_evidence"
    ),
    "serial_number_half_range": 1 << 23,
    "wrap_safety_source_event_id_span_rule": (
        "every_simultaneously_live_or_replayable_set_max_source_event_id_minus_min_source_event_id_strictly_less_than_2^23"
    ),
    "cycle_observer_alias_policy": (
        "verify_transport_tags_fail_closed_on_alias_and_never_use_full_source_event_ids_to_mask_collision"
    ),
    "mismatch_collision_or_span_violation": "fail_closed_before_scoring",
}
_EXPECTED_FIFO_MINIMUM_ZERO_LOSS_RULE = {
    "bounded_peak_authoritative_if": (
        "fifo_full_forced_bypass_count_is_zero_and_full_conservation_holds"
    ),
    "authoritative_bounded_value": "observed_peak_buffer_entries",
    "bounded_peak_authoritative_if_any_fifo_full_forced_bypass": False,
    "otherwise": (
        "fail_closed_unless_separate_score_free_unbounded_depth_replay_proves_depth"
    ),
    "unbounded_depth_replay_method": (
        "same_arrivals_ordering_service_deadline_and_retirement_without_fifo_pressure_action"
    ),
    "unbounded_depth_replay_may_change_bounded_decisions": False,
    "nontermination_unbounded_growth_or_unaccounted_event": "hard_stop",
    "proven_depth_above_bounded_entries": "hard_stop",
}
_CALIBRATION_FIELDS = (
    "width",
    "height",
    "fx",
    "fy",
    "cx",
    "cy",
    "k1",
    "k2",
    "p1",
    "p2",
    "k3",
)
_SENSOR_RAY_GENERATOR_RULE = "radtan_inverse_newton_then_normalized_sensor_ray"


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
        ("event_sequence_tag", _EVENT_SEQUENCE_TAG_BITS),
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


def _validate_window_event_tags(rows: Sequence[Mapping[str, Any]]) -> None:
    seen = {}  # type: Dict[int, Tuple[str, int]]
    ids_by_window = {}  # type: Dict[str, List[int]]
    for row in rows:
        window_id = row.get("window_id")
        if type(window_id) is not str:
            raise IntegrationError("assay event window_id must be an exact string")
        event_id = _require_int(row.get("event_id"), "event.event_id", 0)
        tag = _require_int(
            row.get("event_sequence_tag"), "event.event_sequence_tag", 0
        )
        if tag >= _EVENT_SEQUENCE_TAG_MODULUS:
            raise IntegrationError("event_sequence_tag exceeds 24 bits")
        if tag != event_id % _EVENT_SEQUENCE_TAG_MODULUS:
            raise IntegrationError("event_sequence_tag differs from event_id modulo 2^24")
        if tag in seen:
            raise IntegrationError(
                "event_sequence_tag is not globally unique in selected assay events"
            )
        seen[tag] = (window_id, event_id)
        ids_by_window.setdefault(window_id, []).append(event_id)
    for window_id, event_ids in ids_by_window.items():
        source_span = max(event_ids) - min(event_ids)
        if source_span >= _SOURCE_EVENT_ID_SPAN_LIMIT:
            raise IntegrationError(
                "window source_event_id_span must be less than 2^23: %s"
                % window_id
            )


def _validate_event_tag_manifest_evidence(
    rows: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> None:
    tags = [
        _require_int(row.get("event_sequence_tag"), "event.event_sequence_tag", 0)
        for row in rows
    ]
    expected_tag_hash = canonical_sha256(tags)
    event_inputs = _require_mapping(manifest.get("event_inputs"), "event_inputs")
    if (
        _require_int(event_inputs.get("selected_event_count"), "selected_event_count", 0)
        != len(tags)
        or _require_int(
            event_inputs.get("event_sequence_tag_count"),
            "event_sequence_tag_count",
            0,
        )
        != len(tags)
        or event_inputs.get("event_sequence_tags_globally_unique") is not True
        or _require_sha(
            event_inputs.get("ordered_event_sequence_tags_sha256"),
            "ordered selected event_sequence_tag hash",
        )
        != expected_tag_hash
        or event_inputs.get("window_reset_domains") is not True
        or _require_int(
            event_inputs.get("window_source_event_id_span_limit_exclusive"),
            "window source_event_id span limit",
            1,
        )
        != _SOURCE_EVENT_ID_SPAN_LIMIT
    ):
        raise IntegrationError("manifest-wide event_sequence_tag evidence differs")

    windows = manifest.get("windows")
    if not isinstance(windows, list):
        raise IntegrationError("manifest windows must be an array")
    rows_by_window = {}  # type: Dict[str, List[Mapping[str, Any]]]
    for row in rows:
        rows_by_window.setdefault(str(row.get("window_id")), []).append(row)
    manifest_window_ids = []  # type: List[str]
    summaries_by_window = {}  # type: Dict[str, Mapping[str, Any]]
    for summary in windows:
        if not isinstance(summary, Mapping):
            raise IntegrationError("manifest window summary must be an object")
        window_id = summary.get("window_id")
        if type(window_id) is not str:
            raise IntegrationError("manifest window_id must be an exact string")
        if window_id in summaries_by_window:
            raise IntegrationError("event tag window summary is duplicated")
        manifest_window_ids.append(window_id)
        summaries_by_window[window_id] = summary
    if tuple(manifest_window_ids) != tuple(rows_by_window):
        raise IntegrationError("event tag window evidence population or order differs")

    window_evidence = []  # type: List[Mapping[str, Any]]
    for window_id, window_rows in rows_by_window.items():
        summary = summaries_by_window[window_id]
        event_ids = [
            _require_int(row.get("event_id"), "event.event_id", 0)
            for row in window_rows
        ]
        window_tags = [
            _require_int(
                row.get("event_sequence_tag"), "event.event_sequence_tag", 0
            )
            for row in window_rows
        ]
        expected_min = min(event_ids)
        expected_max = max(event_ids)
        expected_span = expected_max - expected_min
        if (
            _require_int(
                summary.get("selected_event_count"),
                "window selected_event_count",
                0,
            )
            != len(window_rows)
            or _require_int(
                summary.get("min_source_event_id"),
                "window source_event_id_min",
                0,
            )
            != expected_min
            or _require_int(
                summary.get("max_source_event_id"),
                "window source_event_id_max",
                0,
            )
            != expected_max
            or _require_int(
                summary.get("source_event_id_span"),
                "window source_event_id_span",
                0,
            )
            != expected_span
            or expected_span >= _SOURCE_EVENT_ID_SPAN_LIMIT
            or _require_sha(
                summary.get("ordered_event_sequence_tags_sha256"),
                "window ordered event_sequence_tag hash",
            )
            != canonical_sha256(window_tags)
        ):
            raise IntegrationError("per-window event_sequence_tag evidence differs")
        window_evidence.append({
            "window_id": window_id,
            "min_source_event_id": expected_min,
            "max_source_event_id": expected_max,
            "source_event_id_span": expected_span,
            "ordered_event_sequence_tags_sha256": canonical_sha256(window_tags),
        })
    expected_window_evidence_hash = canonical_sha256(window_evidence)
    if _require_sha(
        event_inputs.get("window_source_event_id_evidence_sha256"),
        "window source_event_id evidence hash",
    ) != expected_window_evidence_hash:
        raise IntegrationError("manifest window event tag evidence hash differs")

    authority = _require_mapping(
        manifest.get("authoritative_input_binding"),
        "manifest.authoritative_input_binding",
    )
    authority_tags = _require_mapping(
        authority.get("event_sequence_tags"), "authority.event_sequence_tags"
    )
    expected_authority_tags = {
        "derivation": "event_id_mod_2^24",
        "bits": _EVENT_SEQUENCE_TAG_BITS,
        "event_sequence_tag_count": len(tags),
        "event_sequence_tags_globally_unique": True,
        "ordered_event_sequence_tags_sha256": expected_tag_hash,
        "window_reset_domains": True,
        "window_source_event_id_span_limit_exclusive": (
            _SOURCE_EVENT_ID_SPAN_LIMIT
        ),
        "window_source_event_id_evidence_sha256": expected_window_evidence_hash,
    }
    if dict(authority_tags) != expected_authority_tags:
        raise IntegrationError("authoritative event_sequence_tag evidence differs")


def _validate_event_record_shape(row: Mapping[str, Any]) -> None:
    actual = frozenset(row)
    if actual != _ASSAY_EVENT_RECORD_FIELDS:
        missing = sorted(_ASSAY_EVENT_RECORD_FIELDS - actual)
        extra = sorted(actual - _ASSAY_EVENT_RECORD_FIELDS)
        raise IntegrationError(
            "assay event record field set differs: missing=%r extra=%r"
            % (missing, extra)
        )
    if type(row["is_query"]) is not bool:
        raise IntegrationError("assay event is_query must be an exact bool")


def _validate_event_record_identity_contract(value: Any) -> Mapping[str, Any]:
    timing = _require_mapping(value, "contract.timing")
    identity = _require_mapping(
        timing.get("event_record_identity"), "contract event record identity"
    )
    if (
        dict(identity) != _EXPECTED_EVENT_RECORD_IDENTITY
        or timing.get("event_record_bits") != _EVENT_RECORD_BITS
        or timing.get("event_record_includes_causal_pose_index_bits") != 14
    ):
        raise IntegrationError("event record identity contract differs")
    return identity


def _validate_score_free_accounting_contract(value: Any) -> Mapping[str, Any]:
    accounting = _require_mapping(value, "contract.score_free_accounting")
    if accounting.get("schema") != "redred.mc_wtb.stage4_score_free_accounting/v1":
        raise IntegrationError("score-free accounting schema differs")

    corrected = _require_mapping(
        accounting.get("corrected_reason_allowlist_by_arm"),
        "score-free corrected reason taxonomy",
    )
    raw = _require_mapping(
        accounting.get("raw_reason_classification_by_arm"),
        "score-free raw reason taxonomy",
    )
    arm_names = frozenset(arm.value for arm in Arm)
    if frozenset(corrected) != arm_names or frozenset(raw) != arm_names:
        raise IntegrationError("score-free accounting arm taxonomy differs")
    category_labels = {
        "freshness": "freshness_veto",
        "invalid": "invalid_pose_bypass",
        "operational": "operational_waste",
    }
    for arm, policy in _ARM_CATEGORY_REASONS.items():
        corrected_reasons = corrected[arm.value]
        if not isinstance(corrected_reasons, list) or frozenset(
            corrected_reasons
        ) != policy["corrected"]:
            raise IntegrationError("score-free corrected reason taxonomy differs")
        expected_raw = {}
        for implementation_name, contract_name in category_labels.items():
            expected_raw.update(
                (reason, contract_name) for reason in policy[implementation_name]
            )
        if dict(_require_mapping(raw[arm.value], "score-free raw arm taxonomy")) != expected_raw:
            raise IntegrationError("score-free raw reason taxonomy differs")
    if (
        accounting.get("corrected_disposition") != "corrected_world_ray"
        or accounting.get("corrected_disposition_classification")
        != ["attempted_correction"]
        or accounting.get("unknown_arm_disposition_reason") != "protocol_failure"
    ):
        raise IntegrationError("score-free disposition taxonomy differs")

    state = _require_mapping(
        accounting.get("common_state_envelope"),
        "score-free common state envelope",
    )
    if (
        state.get("components_bits") != dict(_STATE_COMPONENTS_BITS)
        or state.get("component_count") != len(_STATE_COMPONENTS_BITS)
        or state.get("incremental_state_bits")
        != _CONSERVATIVE_INCREMENTAL_STATE_BITS
        or state.get("live_reference_counter_entries") != 16
        or state.get("live_reference_counter_width_bits") != 11
        or state.get("maximum_simultaneous_live_references")
        != _MAXIMUM_SIMULTANEOUS_LIVE_REFERENCES
        or _MAXIMUM_SIMULTANEOUS_LIVE_REFERENCES
        >= _EVENT_SEQUENCE_TAG_MODULUS
        or sum(value for _, value in _STATE_COMPONENTS_BITS)
        != _CONSERVATIVE_INCREMENTAL_STATE_BITS
    ):
        raise IntegrationError("score-free 11-component state accounting differs")

    pose = _require_mapping(
        accounting.get("pose_interface"), "score-free pose interface"
    )
    if (
        pose.get("packet_bits") != 192
        or pose.get("packets_per_second") != 1000
        or pose.get("pose_bandwidth_bits_per_second")
        != _CONSERVATIVE_POSE_BANDWIDTH_BITS_PER_SECOND
    ):
        raise IntegrationError("score-free pose bandwidth accounting differs")

    event_bandwidth = _require_mapping(
        accounting.get("query_event_bandwidth"),
        "score-free event bandwidth",
    )
    residence = _require_mapping(
        accounting.get("residence_bit_cycles"),
        "score-free residence bit-cycles",
    )
    if (
        event_bandwidth.get("record_bits") != _EVENT_RECORD_BITS
        or residence.get("record_bits") != _EVENT_RECORD_BITS
        or residence.get("buffer_bit_cycles_rule")
        != "102*(sum_all_events(admission_cycle-occurrence_cycle)+indicator_arm_is_delayed_exact*sum_all_events(retire_cycle-admission_cycle))"
    ):
        raise IntegrationError("score-free 102-bit event accounting differs")

    fifo = _require_mapping(accounting.get("delayed_fifo"), "score-free delayed FIFO")
    minimum_rule = _require_mapping(
        fifo.get("minimum_zero_loss_buffer_entries"),
        "score-free delayed FIFO conditional rule",
    )
    if (
        fifo.get("bounded_entries") != 1024
        or fifo.get("full_action") != "oldest_eligible_head_ordered_raw_bypass"
        or fifo.get("full_reason") != "fifo_full_forced_bypass"
        or fifo.get("full_classification") != "operational_waste"
        or fifo.get("external_or_unbounded_overflow_queue_allowed") is not False
        or dict(minimum_rule) != _EXPECTED_FIFO_MINIMUM_ZERO_LOSS_RULE
    ):
        raise IntegrationError("score-free delayed FIFO conditional rule differs")
    return accounting


@dataclass(frozen=True)
class AssayBundle:
    root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    authority_sha256: str
    calibration: Calibration
    calibration_authority_sha256: str
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
    query_start_ns: int
    window_end_ns: int
    event_rows: Tuple[Mapping[str, Any], ...]
    events: Tuple[Event, ...]
    sensor_rays: Tuple[Tuple[float, float, float], ...]
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
    accounting_evidence: "ScoreFreeAccountingEvidence"
    ray_events: Tuple[RayEvent, ...]
    manifest: ScoreInputManifest
    boundary_evidence: ScoreBoundaryEvidence
    full_cycle_evidence_sha256: str
    cycle_receipts_sha256: str
    query_projection_sha256: str


@dataclass(frozen=True)
class ScoreFreeAccountingEvidence:
    """Auditable score-free derivation of categories and fixed hardware costs."""

    window_id: str
    arm: str
    category_reason_policy: Tuple[Tuple[str, Tuple[str, ...]], ...]
    category_event_ids: Tuple[Tuple[str, Tuple[int, ...]], ...]
    state_components_bits: Tuple[Tuple[str, int], ...]
    pose_bandwidth_components_bps: Tuple[Tuple[str, int], ...]
    event_bandwidth_basis: Tuple[Tuple[str, int], ...]
    buffer_entry_cycle_components: Tuple[Tuple[str, int], ...]
    buffer_bit_cycles: int
    pose_ring_accounting_sha256: str

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "schema": "redred.mc_wtb.stage4_score_free_accounting_evidence/v1",
            "window_id": self.window_id,
            "arm": self.arm,
            "category_reason_policy": {
                name: list(values) for name, values in self.category_reason_policy
            },
            "category_event_ids": {
                name: list(values) for name, values in self.category_event_ids
            },
            "state_components_bits": dict(self.state_components_bits),
            "state_total_bits": sum(value for _, value in self.state_components_bits),
            "pose_bandwidth_components_bps": dict(
                self.pose_bandwidth_components_bps
            ),
            "pose_bandwidth_total_bps": sum(
                value for _, value in self.pose_bandwidth_components_bps
            ),
            "event_bandwidth_basis": dict(self.event_bandwidth_basis),
            "buffer_entry_cycle_components": dict(
                self.buffer_entry_cycle_components
            ),
            "buffer_bit_cycles": self.buffer_bit_cycles,
            "pose_ring_accounting_sha256": self.pose_ring_accounting_sha256,
        }

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.to_mapping())


def _pose_value_sha256(
    pose_id: int, timestamp_ns: int, quaternion: Sequence[float]
) -> str:
    return canonical_sha256({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion),
    })


def _validate_pose_value(
    row: Mapping[str, Any], id_field: str, timestamp_field: str, where: str
) -> None:
    pose_id = _require_int(row.get(id_field), "%s pose ID" % where, 0)
    timestamp_ns = _require_int(
        row.get(timestamp_field), "%s timestamp" % where, 0
    )
    quaternion = _quaternion(row.get("quaternion_xyzw"), "%s quaternion" % where)
    supplied = _require_sha(row.get("pose_value_sha256"), "%s pose value" % where)
    if supplied != _pose_value_sha256(pose_id, timestamp_ns, quaternion):
        raise IntegrationError("%s pose value hash differs" % where)


def _load_calibration_authority(
    authority: Mapping[str, Any],
    manifest: Mapping[str, Any],
    source: Mapping[str, Any],
) -> Tuple[Calibration, str]:
    calibration_authority = _require_mapping(
        authority.get("calibration_model"), "authority.calibration_model"
    )
    body = dict(calibration_authority)
    authority_sha256 = _require_sha(
        body.pop("authority_sha256", None), "calibration authority"
    )
    if canonical_sha256(body) != authority_sha256:
        raise IntegrationError("calibration authority canonical hash differs")
    if set(body) != {
        "schema",
        "source_path",
        "source_sha256",
        "sensor_ray_generator_rule",
        "model",
    }:
        raise IntegrationError("calibration authority fields differ")
    calibration_source_sha256 = _require_sha(
        source.get("calibration_sha256"), "source calibration hash"
    )
    if (
        body.get("schema") != "redred.mc_wtb.stage4_calibration_authority/v1"
        or body.get("source_path") != "calib.txt"
        or body.get("source_sha256") != calibration_source_sha256
        or body.get("sensor_ray_generator_rule") != _SENSOR_RAY_GENERATOR_RULE
    ):
        raise IntegrationError("calibration authority provenance differs")
    event_inputs = _require_mapping(manifest.get("event_inputs"), "event_inputs")
    if (
        event_inputs.get("calibration_authority_sha256") != authority_sha256
        or event_inputs.get("ray_model") != _SENSOR_RAY_GENERATOR_RULE
    ):
        raise IntegrationError("event inputs do not bind calibration authority")
    model = _require_mapping(body.get("model"), "calibration authority model")
    if set(model) != set(_CALIBRATION_FIELDS):
        raise IntegrationError("calibration authority fields differ")
    for name in ("width", "height"):
        _require_int(model.get(name), "calibration %s" % name, 1)
    for name in _CALIBRATION_FIELDS[2:]:
        value = model.get(name)
        if type(value) is not float or not math.isfinite(value):
            raise IntegrationError("calibration %s must be an exact finite float" % name)
    try:
        calibration = Calibration(**{name: model[name] for name in _CALIBRATION_FIELDS})
    except SourceInputError as exc:
        raise IntegrationError("calibration authority model is invalid") from exc
    return calibration, authority_sha256


def _recompute_sensor_ray(
    row: Mapping[str, Any], calibration: Calibration
) -> Tuple[float, float, float]:
    event = EventSample(
        event_id=_require_int(row.get("event_id"), "event_id", 0),
        timestamp_ns=_require_int(row.get("timestamp_ns"), "event timestamp", 0),
        x=_require_int(row.get("x"), "event x", 0),
        y=_require_int(row.get("y"), "event y", 0),
        polarity=_require_int(row.get("polarity"), "event polarity", 0),
    )
    try:
        recovered = sensor_ray(event, calibration)
    except SourceInputError as exc:
        raise IntegrationError("payload-bound sensor-ray recovery failed") from exc
    serialized = row.get("sensor_ray")
    if (
        type(serialized) is not list
        or len(serialized) != 3
        or any(type(value) is not float or not math.isfinite(value) for value in serialized)
    ):
        raise IntegrationError("serialized sensor ray must contain exact finite floats")
    if serialized != list(recovered):
        raise IntegrationError("sensor ray differs from calibration recovery")
    return recovered


def load_assay_bundle(
    root: Path, *, expected_manifest_sha256: str
) -> AssayBundle:
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
    expected_manifest = _require_sha(
        expected_manifest_sha256, "expected canonical assay manifest"
    )
    manifest_sha256 = _sha256_bytes(manifest_raw)
    if manifest_sha256 != expected_manifest:
        raise IntegrationError("canonical assay manifest hash differs from caller seal")
    if manifest.get("schema") != "redred.mc_wtb.stage4_score_free_inputs/v2":
        raise IntegrationError("assay manifest schema is not v2")
    contract = load_comparison_contract()
    _validate_event_record_identity_contract(contract.timing)
    _validate_score_free_accounting_contract(
        contract.as_dict().get("score_free_accounting")
    )
    if manifest.get("comparison_contract_sha256") != contract.canonical_sha256:
        raise IntegrationError("assay manifest contract hash differs")
    registry = _require_mapping(manifest.get("registry"), "manifest.registry")
    if registry.get("sha256") != contract.registry["sha256"]:
        raise IntegrationError("assay manifest registry hash differs")
    if registry.get("forbidden_interval_selected_records") != 0:
        raise IntegrationError("assay manifest reports forbidden selected records")
    forbidden = tuple(contract.registry["forbidden_interval_ns"])

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
    calibration, calibration_authority_sha256 = _load_calibration_authority(
        authority, manifest, source
    )
    generator_runtime = _require_mapping(
        manifest.get("generator_runtime"), "manifest.generator_runtime"
    )
    if authority.get("generator_code_sha256") != generator_runtime.get(
        "generator_code_sha256"
    ) or authority.get("runtime") != generator_runtime.get("runtime"):
        raise IntegrationError("generator/runtime authority binding differs")
    for row in loaded[_EVENTS]:
        _validate_event_record_shape(row)
        _validate_payload(row)
        _recompute_sensor_ray(row, calibration)
        timestamp_ns = _require_int(row.get("timestamp_ns"), "event timestamp", 0)
        if forbidden[0] <= timestamp_ns < forbidden[1]:
            raise IntegrationError("forbidden event reached assay inputs")
    _validate_window_event_tags(loaded[_EVENTS])
    _validate_event_tag_manifest_evidence(loaded[_EVENTS], manifest)
    event_ids = tuple(_require_int(row.get("event_id"), "event_id", 0) for row in loaded[_EVENTS])
    if any(right <= left for left, right in zip(event_ids, event_ids[1:])):
        raise IntegrationError("assay event IDs are not globally ordered")

    dataset_by_key = {}  # type: Dict[Tuple[str, int], Mapping[str, Any]]
    for packet in loaded[_DATASET_POSES]:
        _validate_pose_value(
            packet, "source_pose_id", "timestamp_ns", "dataset packet"
        )
        body = dict(packet)
        packet_hash = _require_sha(body.pop("packet_sha256", None), "pose packet hash")
        if canonical_sha256(body) != packet_hash:
            raise IntegrationError("dataset pose packet canonical hash differs")
        commit_cycle = _require_int(packet.get("commit_cycle"), "pose commit cycle")
        if (
            packet.get("arrival_cycle") != commit_cycle
            or packet.get("visible_cycle") != commit_cycle + 1
        ):
            raise IntegrationError("dataset pose packet timing semantics differ")
        if forbidden[0] <= int(packet["timestamp_ns"]) < forbidden[1]:
            raise IntegrationError("forbidden dataset pose reached assay inputs")
        key = (
            str(packet.get("window_id")),
            _require_int(packet.get("source_pose_id"), "source_pose_id", 0),
        )
        if key in dataset_by_key:
            raise IntegrationError("dataset pose packet identity is duplicated")
        dataset_by_key[key] = packet

    dataset_stream_sha256 = _require_sha(
        artifacts[_DATASET_POSES].get("sha256"), "dataset stream hash"
    )
    snapshots_by_key = {}  # type: Dict[Tuple[str, int], Mapping[str, Any]]
    for snapshot in loaded[_SNAPSHOTS]:
        snapshot_body = dict(snapshot)
        snapshot_hash = _require_sha(
            snapshot_body.pop("pose_snapshot_sha256", None), "pose snapshot hash"
        )
        if canonical_sha256(snapshot_body) != snapshot_hash:
            raise IntegrationError("occurrence PoseSnapshot canonical hash differs")
        if snapshot.get("dataset_pose_packet_stream_sha256") != dataset_stream_sha256:
            raise IntegrationError("occurrence PoseSnapshot dataset authority differs")
        window_id = snapshot.get("window_id")
        batch_id = _require_int(
            snapshot.get("occurrence_batch_id"), "snapshot batch ID", 0
        )
        key = (str(window_id), batch_id)
        if key in snapshots_by_key:
            raise IntegrationError("occurrence PoseSnapshot identity is duplicated")
        pose_packets = snapshot.get("pose_packets")
        if not isinstance(pose_packets, list) or len(pose_packets) != 2:
            raise IntegrationError("occurrence PoseSnapshot must contain two packets")
        occurrence_cycle = _require_int(
            snapshot.get("occurrence_cycle"), "snapshot occurrence cycle", 0
        )
        eligible = tuple(
            packet
            for (packet_window, _), packet in dataset_by_key.items()
            if packet_window == str(window_id)
            and _require_int(packet.get("commit_cycle"), "packet commit cycle")
            < occurrence_cycle
        )
        if len(eligible) < 2 or tuple(
            packet.get("source_pose_id") for packet in eligible[-2:]
        ) != tuple(pose.get("source_pose_id") for pose in pose_packets):
            raise IntegrationError(
                "occurrence PoseSnapshot is not the true latest pre-edge pair"
            )
        for ordinal, pose in enumerate(pose_packets):
            pose_row = _require_mapping(pose, "snapshot pose packet")
            _validate_pose_value(
                pose_row,
                "source_pose_id",
                "timestamp_ns",
                "snapshot packet",
            )
            source_key = (
                str(window_id),
                _require_int(
                    pose_row.get("source_pose_id"), "snapshot source_pose_id", 0
                ),
            )
            source_packet = _require_mapping(
                dataset_by_key.get(source_key), "snapshot source packet"
            )
            expected_subset = {
                name: source_packet.get(name)
                for name in (
                    "source_pose_id",
                    "timestamp_ns",
                    "quaternion_xyzw",
                    "pose_value_sha256",
                    "packet_sha256",
                    "commit_cycle",
                    "visible_cycle",
                )
            }
            if dict(pose_row) != expected_subset:
                raise IntegrationError(
                    "snapshot packet %d differs from dataset authority" % ordinal
                )
        snapshots_by_key[key] = snapshot

    cluster_snapshots = {}  # type: Dict[Tuple[str, int], str]
    for event in loaded[_EVENTS]:
        event_key = (
            str(event.get("window_id")),
            _require_int(event.get("occurrence_batch_id"), "event batch ID", 0),
        )
        snapshot = _require_mapping(
            snapshots_by_key.get(event_key), "event occurrence PoseSnapshot"
        )
        event_snapshot_hash = _require_sha(
            event.get("occurrence_pose_snapshot_sha256"), "event snapshot hash"
        )
        if event_snapshot_hash != snapshot.get("pose_snapshot_sha256"):
            raise IntegrationError("event occurrence PoseSnapshot hash differs")
        cluster_key = (
            str(event.get("window_id")),
            _require_int(event.get("timestamp_ns"), "event timestamp", 0),
        )
        prior = cluster_snapshots.setdefault(cluster_key, event_snapshot_hash)
        if prior != event_snapshot_hash:
            raise IntegrationError("equal-timestamp cluster has multiple snapshots")

    oracle_by_id = {}  # type: Dict[int, Mapping[str, Any]]
    oracle_packet_hashes = []  # type: List[str]
    for packet in loaded[_ORACLE_POSES]:
        _validate_pose_value(
            packet,
            "oracle_pose_id",
            "effective_timestamp_ns",
            "oracle packet",
        )
        pose_id = _require_int(packet.get("oracle_pose_id"), "oracle_pose_id", 0)
        packet_body = dict(packet)
        packet_sha256 = _require_sha(
            packet_body.pop("packet_sha256", None), "oracle packet hash"
        )
        if canonical_sha256(packet_body) != packet_sha256:
            raise IntegrationError("oracle pose packet canonical hash differs")
        if pose_id in oracle_by_id:
            raise IntegrationError("oracle pose identity is duplicated")
        if (
            forbidden[0]
            <= int(packet["effective_timestamp_ns"])
            < forbidden[1]
        ):
            raise IntegrationError("forbidden oracle pose reached assay inputs")
        oracle_packet_hashes.append(packet_sha256)
        oracle_by_id[pose_id] = packet
    oracle_authority = _require_mapping(
        authority.get("oracle_pose_stream"), "authority.oracle_pose_stream"
    )
    if (
        oracle_authority.get("packet_sha256_rule")
        != "canonical_sha256_of_record_without_packet_sha256"
        or oracle_authority.get("ordered_packet_sha256")
        != canonical_sha256(oracle_packet_hashes)
    ):
        raise IntegrationError("oracle packet hash authority differs")
    for schedule in loaded[_ORACLE_SCHEDULE]:
        pose_id = _require_int(
            schedule.get("oracle_pose_id"), "oracle schedule pose ID", 0
        )
        packet = _require_mapping(oracle_by_id.get(pose_id), "oracle schedule packet")
        if (
            schedule.get("effective_timestamp_ns")
            != packet.get("effective_timestamp_ns")
            or schedule.get("pose_value_sha256") != packet.get("pose_value_sha256")
            or schedule.get("packet_sha256") != packet.get("packet_sha256")
        ):
            raise IntegrationError("oracle schedule differs from pose packet authority")
        effective_cycle = _require_int(
            schedule.get("effective_cycle"), "oracle effective cycle"
        )
        if (
            schedule.get("commit_cycle") != effective_cycle + 1
            or schedule.get("visible_cycle") != effective_cycle + 2
        ):
            raise IntegrationError("oracle schedule timing semantics differ")
    return AssayBundle(
        directory,
        manifest,
        manifest_sha256,
        supplied_binding,
        calibration,
        calibration_authority_sha256,
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


def _window_limits(
    manifest: Mapping[str, Any], window_id: str
) -> Tuple[int, int, int]:
    windows = manifest.get("windows")
    if not isinstance(windows, list):
        raise IntegrationError("manifest windows must be an array")
    matches = [row for row in windows if isinstance(row, Mapping) and row.get("window_id") == window_id]
    if len(matches) != 1:
        raise IntegrationError("window summary is absent or duplicated")
    row = matches[0]
    if any(
        name not in row
        for name in (
            "warmup_start_ns_inclusive",
            "query_start_ns_inclusive",
            "query_end_ns_exclusive",
        )
    ):
        raise IntegrationError("assay window summary lacks frozen registry bounds")
    start = _require_int(
        row["warmup_start_ns_inclusive"], "warmup_start_ns_inclusive", 0
    )
    query_start = _require_int(
        row["query_start_ns_inclusive"], "query_start_ns_inclusive", 0
    )
    end = _require_int(
        row["query_end_ns_exclusive"], "query_end_ns_exclusive", 0
    )
    if not start <= query_start < end:
        raise IntegrationError("window limits are not increasing")
    return start, query_start, end


def build_window_cycle_inputs(bundle: AssayBundle, window_id: str) -> WindowCycleInputs:
    """Convert one loaded assay window into all cycle-model input streams."""

    if not isinstance(bundle, AssayBundle):
        raise IntegrationError("bundle must be a validated AssayBundle")
    _validate_window_event_tags(bundle.events)
    _validate_event_tag_manifest_evidence(bundle.events, bundle.manifest)
    start, query_start, end = _window_limits(bundle.manifest, window_id)
    event_rows = tuple(row for row in bundle.events if row.get("window_id") == window_id)
    if not event_rows:
        raise IntegrationError("window has no assay events")
    for row in event_rows:
        _validate_event_record_shape(row)
        _validate_payload(row)
        timestamp_ns = _require_int(row.get("timestamp_ns"), "event timestamp", 0)
        if not start <= timestamp_ns < end:
            raise IntegrationError("assay event lies outside serialized window limits")
        if (row.get("is_query") is True) != (query_start <= timestamp_ns < end):
            raise IntegrationError("assay query label differs from serialized query interval")
    window_summary = next(
        row
        for row in bundle.manifest["windows"]
        if isinstance(row, Mapping) and row.get("window_id") == window_id
    )
    if sum(row.get("is_query") is True for row in event_rows) != _require_int(
        window_summary.get("query_event_count"), "window query_event_count", 0
    ):
        raise IntegrationError("assay query count differs from manifest window summary")
    recovered_sensor_rays = tuple(
        _recompute_sensor_ray(row, bundle.calibration) for row in event_rows
    )
    events = tuple(
        Event(
            _require_int(row.get("event_id"), "event_id", 0),
            _require_int(row.get("timestamp_ns"), "event timestamp", 0),
            transform_guard_valid=True,
            causal_pose_index=_require_int(
                row.get("causal_pose_source_index"),
                "payload-bound causal pose index",
                0,
            ),
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
            raise IntegrationError("oracle pose cannot enter cycle model") from exc
    return WindowCycleInputs(
        window_id,
        start,
        query_start,
        end,
        event_rows,
        events,
        recovered_sensor_rays,
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
    try:
        return rotate_sensor_ray_to_world(quaternion, sensor_ray)
    except GeometryError as exc:
        raise IntegrationError("reviewed world-ray rotation rejected input") from exc


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
    authoritative_pose_packets: Sequence[PosePacket] = (),
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
        selected = used
        if len(selected) != 2:
            if not occurrence:
                raise IntegrationError(
                    "authoritative delayed shadow lacks an occurrence-left pose"
                )
            left_id = occurrence[-1][0]
            packet_by_id = {
                packet.pose_id: packet for packet in authoritative_pose_packets
            }
            left_packet = packet_by_id.get(left_id)
            right_packet = next(
                (
                    packet
                    for packet in authoritative_pose_packets
                    if packet.timestamp_ns > record.event_timestamp_ns
                ),
                None,
            )
            if left_packet is None or right_packet is None:
                raise IntegrationError(
                    "authoritative delayed offline bracket is unavailable"
                )
            selected = (
                (
                    left_packet.pose_id,
                    left_packet.timestamp_ns,
                    left_packet.commit_cycle,
                    left_packet.pose_sha256,
                ),
                (
                    right_packet.pose_id,
                    right_packet.timestamp_ns,
                    right_packet.commit_cycle,
                    right_packet.pose_sha256,
                ),
            )
        left, right = selected
        bracket = interpolate_committed_bracket(
            RecoveryPoseSample(left[1], left[2], quaternions[left[0]]),
            RecoveryPoseSample(right[1], right[2], quaternions[right[0]]),
            record.event_timestamp_ns,
            max(record.retire_cycle, right[2] + 1),
        )
        quaternion = bracket.quaternion_xyzw
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
        "schema": "redred.mc_wtb.stage4_full_cycle_result_evidence/v1",
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
        "causal_pose_index_bits_in_event_record": (
            result.causal_pose_index_bits_in_event_record
        ),
        "pose_packet_bits": result.pose_packet_bits,
        "event_lanes": result.event_lanes,
        "transform_pipeline_cycles": result.transform_pipeline_cycles,
        "dataset_pose_arrival_assumption": result.dataset_pose_arrival_assumption,
        "arm_disposition_label": result.arm_disposition_label,
        "synthetic_test_mode": result.synthetic_test_mode,
        "all_event_pose_indices_verified": result.all_event_pose_indices_verified,
        "pose_ring_entries": result.pose_ring_entries,
        "pose_ring_state_bits": result.pose_ring_state_bits,
        "pose_ring_accounting": result.pose_ring_accounting.to_mapping(),
        "pose_ring_accounting_sha256": result.pose_ring_accounting_sha256,
    }


def _ceil_rate(records: int, bits: int, duration_ns: int) -> int:
    return (records * bits * 1_000_000_000 + duration_ns - 1) // duration_ns


def _validate_live_event_id_scope(
    result: SimulationResult, maximum_live: int, source_id_span_limit: int
) -> None:
    occurrences = {}  # type: Dict[int, List[int]]
    retirements = {}  # type: Dict[int, List[int]]
    receipt_event_ids = set()
    for receipt in result.cycle_receipts:
        if receipt.retire_cycle < receipt.occurrence_cycle:
            raise IntegrationError("cycle receipt has negative live tag interval")
        if receipt.event_id in receipt_event_ids:
            raise IntegrationError("cycle receipt repeats a full event_id")
        receipt_event_ids.add(receipt.event_id)
        occurrences.setdefault(receipt.occurrence_cycle, []).append(receipt.event_id)
        retirements.setdefault(receipt.retire_cycle, []).append(receipt.event_id)
    live_ids = set()  # type: set
    peak = 0
    boundaries = sorted(set(occurrences) | set(retirements))
    for cycle in boundaries:
        for event_id in occurrences.get(cycle, ()):
            if event_id in live_ids:
                raise IntegrationError("live full event_id is duplicated")
            live_ids.add(event_id)
        peak = max(peak, len(live_ids))
        if live_ids and max(live_ids) - min(live_ids) >= source_id_span_limit:
            raise IntegrationError(
                "live source_event_id span must be less than 2^23"
            )
        for event_id in retirements.get(cycle, ()):
            if event_id not in live_ids:
                raise IntegrationError("cycle receipt live tag scope underflowed")
            live_ids.remove(event_id)
    if live_ids:
        raise IntegrationError("cycle receipt live tag scope does not conserve")
    if peak > maximum_live:
        raise IntegrationError("event_sequence_tag maximum live count exceeded")


def _derive_accounting(
    inputs: WindowCycleInputs,
    result: SimulationResult,
    converted: Sequence[DecisionRecord],
) -> Tuple[ScoreFreeAccounting, ScoreFreeAccountingEvidence]:
    contract = load_comparison_contract()
    identity_contract = _validate_event_record_identity_contract(contract.timing)
    accounting_contract = _validate_score_free_accounting_contract(
        contract.as_dict().get("score_free_accounting")
    )
    if len(converted) != len(result.records) or tuple(
        record.event_id for record in converted
    ) != tuple(record.event_id for record in result.records):
        raise IntegrationError("receipt-v2 conversion differs from full cycle result")
    query_indexes = tuple(
        index for index, row in enumerate(inputs.event_rows) if row.get("is_query") is True
    )
    query_records = tuple(converted[index] for index in query_indexes)
    freshness = []  # type: List[int]
    invalid = []  # type: List[int]
    operational = []  # type: List[int]
    policy = _ARM_CATEGORY_REASONS[result.arm]
    corrected = []  # type: List[int]
    for index in query_indexes:
        original = result.records[index]
        if original.disposition == "corrected_world_ray":
            if original.disposition_reason not in policy["corrected"]:
                raise IntegrationError("corrected disposition reason is not arm-frozen")
            corrected.append(original.event_id)
            continue
        elif original.disposition_reason in policy["freshness"]:
            freshness.append(original.event_id)
        elif original.disposition_reason in policy["invalid"]:
            invalid.append(original.event_id)
        elif original.disposition_reason in policy["operational"]:
            operational.append(original.event_id)
        else:
            raise IntegrationError("raw disposition lacks a frozen accounting category")
    baseline = tuple(
        (inputs.events[index].event_id, result.always_bypass_retire_cycles[index])
        for index in query_indexes
    )
    if tuple(record.event_id for record in query_records) != tuple(row[0] for row in baseline):
        raise IntegrationError("query accounting projection differs from cycle evidence")
    query_duration = inputs.window_end_ns - inputs.query_start_ns
    serializer_entry_cycles = sum(
        receipt.admission_cycle - receipt.occurrence_cycle
        for receipt in result.cycle_receipts
    )
    delayed_fifo_entry_cycles = (
        sum(
            receipt.retire_cycle - receipt.admission_cycle
            for receipt in result.cycle_receipts
        )
        if result.arm is Arm.DELAYED_EXACT
        else 0
    )
    if serializer_entry_cycles < 0 or delayed_fifo_entry_cycles < 0:
        raise IntegrationError("cycle evidence contains negative residency")
    buffer_bit_cycles = result.event_record_bits * (
        serializer_entry_cycles + delayed_fifo_entry_cycles
    )
    fifo_contract = _require_mapping(
        accounting_contract.get("delayed_fifo"), "score-free delayed FIFO"
    )
    if (
        result.event_record_bits != _EVENT_RECORD_BITS
        or result.pose_packet_bits != 192
        or result.buffer_entries != fifo_contract["bounded_entries"]
    ):
        raise IntegrationError("cycle-model hardware accounting differs from contract")
    _validate_live_event_id_scope(
        result,
        int(identity_contract["maximum_simultaneous_live_records"]),
        int(identity_contract["serial_number_half_range"]),
    )
    attempted_ids = set(corrected + operational)
    attempted = tuple(
        result.records[index].event_id
        for index in query_indexes
        if result.records[index].event_id in attempted_ids
    )
    event_bandwidth = _ceil_rate(
        len(query_indexes), result.event_record_bits, query_duration
    )
    if any(
        result.records[index].disposition_reason == "fifo_full_forced_bypass"
        for index in range(len(result.records))
    ):
        raise IntegrationError(
            "UNBOUNDED_REPLAY_REQUIRED_FOR_MINIMUM_ZERO_LOSS_DEPTH: "
            "full-pressure bypass prevents a bounded-peak claim"
        )
    accounting = ScoreFreeAccounting(
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
        _CONSERVATIVE_POSE_BANDWIDTH_BITS_PER_SECOND,
        event_bandwidth,
        _CONSERVATIVE_INCREMENTAL_STATE_BITS,
        0,
        0,
        0,
        0,
    )
    category_reason_policy = tuple(
        (
            name,
            tuple(sorted(policy[name])),
        )
        for name in ("corrected", "freshness", "invalid", "operational")
    )
    category_event_ids = (
        ("attempted_correction", attempted),
        ("freshness_veto", tuple(freshness)),
        ("invalid_pose_bypass", tuple(invalid)),
        ("operational_waste", tuple(operational)),
    )
    evidence = ScoreFreeAccountingEvidence(
        inputs.window_id,
        result.arm.value,
        category_reason_policy,
        category_event_ids,
        _STATE_COMPONENTS_BITS,
        (("global_1khz_pose_interface_1000x192", 1_000 * 192),),
        (
            ("query_event_count", len(query_indexes)),
            ("event_record_bits", result.event_record_bits),
            ("query_interval_ns", query_duration),
            ("integer_ceiling_rate_bps", event_bandwidth),
        ),
        (
            ("serializer_admission_minus_occurrence", serializer_entry_cycles),
            ("delayed_fifo_retire_minus_admission", delayed_fifo_entry_cycles),
        ),
        buffer_bit_cycles,
        result.pose_ring_accounting_sha256,
    )
    if (
        sum(value for _, value in evidence.state_components_bits)
        != accounting.incremental_state_bits
        or sum(value for _, value in evidence.pose_bandwidth_components_bps)
        != accounting.pose_bandwidth_bits_per_second
    ):
        raise IntegrationError("score-free accounting evidence differs from totals")
    return accounting, evidence


def _artifact_bindings(
    bundle: AssayBundle,
    arm: Arm,
    full_cycle_evidence_sha256: str,
    accounting_evidence_sha256: str,
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
    integration_code = {
        "assay_generator_code_sha256": generator_code,
        "integration_adapter_py_sha256": _sha256_file(Path(__file__).resolve()),
        "pose_recovery_geometry_py_sha256": _sha256_file(
            package_root / "redred_mc_wtb_pose_recovery" / "geometry.py"
        ),
    }
    cycle_binding = {
        "model_py_sha256": _sha256_file(
            package_root / "redred_mc_wtb_stage4_cyclemodel" / "model.py"
        ),
        "full_cycle_evidence_sha256": full_cycle_evidence_sha256,
        "score_free_accounting_evidence_sha256": accounting_evidence_sha256,
    }
    source_binding = {
        "source": source,
        "assay_manifest_sha256": bundle.manifest_sha256,
        "assay_authority_sha256": bundle.authority_sha256,
    }
    bindings = {
        "protocol": contract.canonical_sha256,
        "registry": contract.registry["sha256"],
        "arm_parameters": canonical_sha256(contract.arms[arm.value]),
        "generator": canonical_sha256(integration_code),
        "cycle_model": canonical_sha256(cycle_binding),
        "scorer": _sha256_file(
            package_root / "redred_mc_wtb_stage4_scoring" / "scoring.py"
        ),
        "sources": canonical_sha256(source_binding),
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
        arm_events = inputs.events
        if arm is Arm.ORACLE_1KHZ:
            # The physical 14-bit field is validated from the packed assay
            # record, but oracle pose identity is independently selected by
            # the global millisecond phase and must not consume that field.
            arm_events = tuple(
                Event(
                    event.event_id,
                    event.timestamp_ns,
                    transform_guard_valid=event.transform_guard_valid,
                    causal_pose_index=None,
                )
                for event in inputs.events
            )
        try:
            result = run_cycle_model(
                window_id=window_id,
                window_start_ns=inputs.window_start_ns,
                arm=arm,
                events=arm_events,
                poses=poses,
            )
        except CycleModelError as exc:
            raise IntegrationError("cycle model rejected assay inputs for %s" % arm.value) from exc
        if result.synthetic_test_mode:
            raise IntegrationError("integration cycle result used synthetic test mode")
        if arm is not Arm.ORACLE_1KHZ:
            if not result.all_event_pose_indices_verified:
                raise IntegrationError("cycle result did not verify every event pose index")
            for event, cycle_receipt in zip(inputs.events, result.cycle_receipts):
                if (
                    not cycle_receipt.causal_pose_index_applicable
                    or not cycle_receipt.causal_pose_index_verified
                    or cycle_receipt.event_causal_pose_index
                    != event.causal_pose_index
                ):
                    raise IntegrationError(
                        "cycle receipt does not prove the payload-bound pose index"
                    )
        converted = tuple(_convert_record(record) for record in result.records)
        if arm is not Arm.ORACLE_1KHZ:
            _validate_assay_snapshot_projection(bundle, inputs, converted)
        for event_row, cycle_receipt in zip(inputs.event_rows, result.cycle_receipts):
            if (
                cycle_receipt.admission_cycle != event_row.get("presentation_cycle")
                or cycle_receipt.admission_lane != event_row.get("presentation_lane")
            ):
                raise IntegrationError(
                    "cycle admission differs from assay presentation"
                )
        simulations[arm] = result
        converted_by_arm[arm] = converted

    ray_events = []  # type: List[RayEvent]
    for index, event_row in enumerate(inputs.event_rows):
        recovered_sensor_ray = inputs.sensor_rays[index]
        shadows = []  # type: List[ShadowRay]
        for arm in Arm:
            quaternions = (
                inputs.oracle_quaternions
                if arm is Arm.ORACLE_1KHZ
                else inputs.dataset_quaternions
            )
            shadows.append(
                _shadow_for_record(
                    converted_by_arm[arm][index],
                    recovered_sensor_ray,
                    quaternions,
                    inputs.dataset_poses if arm is Arm.DELAYED_EXACT else (),
                )
            )
        ray_events.append(RayEvent(
            window_id,
            inputs.events[index].event_id,
            inputs.events[index].timestamp_ns,
            _require_int(event_row.get("polarity"), "event polarity", 0),
            event_row.get("is_query") is True,
            recovered_sensor_ray,
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
        accounting, accounting_evidence = _derive_accounting(
            inputs, result, converted
        )
        evidence_hash = canonical_sha256(_full_cycle_evidence(result))
        query_projection_sha256 = receipt.decision_records_sha256
        manifest = ScoreInputManifest(
            window_id=window_id,
            arm=arm.value,
            decision_receipt_sha256=receipt.canonical_sha256(),
            score_free_accounting_sha256=accounting.canonical_sha256(),
            ray_events_sha256=ray_digest,
            assay_authoritative_input_manifest_sha256=bundle.manifest_sha256,
            full_cycle_result_sha256=evidence_hash,
            cycle_receipts_sha256=result.cycle_receipts_sha256,
            query_projection_sha256=query_projection_sha256,
            artifact_sha256=_artifact_bindings(
                bundle,
                arm,
                evidence_hash,
                accounting_evidence.canonical_sha256(),
            ),
        )
        boundary_evidence = ScoreBoundaryEvidence(
            assay_authoritative_input_manifest_sha256=bundle.manifest_sha256,
            full_cycle_result_sha256=evidence_hash,
            cycle_receipts_sha256=result.cycle_receipts_sha256,
            query_projection_sha256=query_projection_sha256,
        )
        integrated[arm] = IntegratedArmWindow(
            arm,
            result,
            query_records,
            receipt,
            accounting,
            accounting_evidence,
            ray_values,
            manifest,
            boundary_evidence,
            evidence_hash,
            result.cycle_receipts_sha256,
            query_projection_sha256,
        )
    return integrated
