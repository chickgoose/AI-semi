"""Sealed, score-free current-CAV decisions for Stage-3 candidates.

This module deliberately accepts neutral objects by exact field shape instead
of importing their evaluator-owned classes.  It snapshots those objects,
executes only a supplied current-CAV cycle runner, and projects the runner's
records to the causal information required by RG3, DSPB, and PLL adapters.

Reference banks, labels, selector metadata, quality values, and losses are not
representable by this API.  A runner profile identifies the physical replay
implementation while the builder independently locks event edges, visible
poses, route taxonomy, and used-pose provenance.  A future logical-ingress
runner can therefore replace the bounded runner without changing the exposed
current-CAV semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
from typing import Callable, Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    PosePacket,
    PoseSource,
    pose_timestamp_to_cycle,
    run_cycle_model,
    timestamp_to_cycle,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel.model import CAV_MAX_HORIZON_NS


TRACE_SCHEMA = "redred.mc_wtb_predictor_stage3.current_cav_trace/v1"
BASELINE_SCHEMA = "redred.mc_wtb_predictor_stage3.current_cav_baseline/v1"
PROFILE_SCHEMA = "redred.mc_wtb_predictor_stage3.current_cav_runner_profile/v1"
NEUTRAL_INPUT_SCHEMA = "redred.mc_wtb.current_cav_neutral_inputs/v1"
ZOH_MAX_AGE_NS = 1_000_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRY_FIELDS = frozenset((
    "window_id",
    "warmup_start_ns_inclusive",
    "query_start_ns_inclusive",
    "query_end_ns_exclusive",
))
_EVENT_FIELDS = frozenset((
    "event_id",
    "timestamp_ns",
    "polarity",
    "is_query",
    "sensor_ray",
    "causal_pose_source_index",
    "event_content_sha256",
    "transform_guard_valid",
))
_POSE_FIELDS = frozenset((
    "pose_id",
    "timestamp_ns",
    "commit_cycle",
    "quaternion_xyzw",
    "pose_sha256",
    "value_valid",
    "arithmetic_valid",
))
_PROFILE_MAPPING_FIELDS = frozenset((
    "schema",
    "profile_id",
    "profile_mapping_json",
    "profile_mapping_sha256",
    "semantic_contract_sha256",
    "semantics",
))
_DECISION_MAPPING_FIELDS = frozenset((
    "window_id",
    "event_id",
    "event_timestamp_ns",
    "occurrence_cycle",
    "occurrence_pose_ids",
    "occurrence_pose_timestamps_ns",
    "occurrence_pose_commit_cycles",
    "occurrence_pose_sha256",
    "used_pose_ids",
    "used_pose_timestamps_ns",
    "used_pose_commit_cycles",
    "used_pose_sha256",
    "disposition",
    "disposition_reason",
    "decision_sha256",
))
_SIMULATION_MAPPING_FIELDS = frozenset((
    "records",
    "decision_records_sha256",
    "synthetic_test_mode",
    "all_event_pose_indices_verified",
))
_WINDOW_MAPPING_FIELDS = frozenset((
    "registry",
    "input_events",
    "input_poses",
    "simulation",
    "window_sha256",
))
_TRACE_MAPPING_FIELDS = frozenset((
    "schema",
    "profile",
    "profile_sha256",
    "neutral_input_sha256",
    "baseline_schema",
    "baseline_decisions_sha256",
    "windows",
    "aggregate_sha256",
))
_IMPLICIT_PROFILE_FIELDS = frozenset((
    "schema",
    "profile_id",
    "invocation",
))
_INJECTED_PROFILE_FIELDS = frozenset((
    "schema",
    "profile_id",
    "raw_ingress_lanes",
    "ingress_staging_entries",
    "event_service_lanes",
    "scope",
))
_SEMANTIC_CONTRACT = {
    "arm": Arm.CAUSAL_CAV.value,
    "event_edge": "ceil((timestamp_ns-window_start_ns)*1000/6500)",
    "pose_visibility": (
        "commit_cycle_strictly_less_than_decision_edge_and_"
        "timestamp_not_after_event"
    ),
    "occurrence_snapshot": "latest_two_visible_dataset_poses",
    "same_edge_priority": "event_observes_old_pose_state",
    "candidate_gate": "disposition_reason_exactly_causal_cav",
}
SEMANTIC_CONTRACT_SHA256 = canonical_sha256(_SEMANTIC_CONTRACT)


class CurrentCAVTraceError(ValueError):
    """A neutral input, current-CAV replay, or trace seal failed."""


def _integer(value: object, where: str, *, signed: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CurrentCAVTraceError("%s must be an integer" % where)
    if not signed and value < 0:
        raise CurrentCAVTraceError("%s must be nonnegative" % where)
    return value


def _text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise CurrentCAVTraceError("%s must be nonempty text" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CurrentCAVTraceError("%s must be lowercase SHA-256" % where)
    return value


def _unit_tuple(value: object, length: int, where: str) -> Tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CurrentCAVTraceError("%s must be an ordered numeric sequence" % where)
    if len(value) != length:
        raise CurrentCAVTraceError("%s has the wrong cardinality" % where)
    result = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise CurrentCAVTraceError("%s components must be finite numbers" % where)
        converted = float(component)
        if not math.isfinite(converted):
            raise CurrentCAVTraceError("%s components must be finite numbers" % where)
        result.append(converted)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise CurrentCAVTraceError("%s must have unit norm" % where)
    return tuple(result)


def _exact_fields(value: object, expected: frozenset, where: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        body = value
    else:
        try:
            body = vars(value)
        except TypeError as exc:
            raise CurrentCAVTraceError("%s has no field mapping" % where) from exc
    if frozenset(body) != expected:
        raise CurrentCAVTraceError("%s field schema differs" % where)
    return body


def _mapping_list(
    value: object, where: str, *, nonempty: bool = True
) -> Sequence[object]:
    if type(value) is not list:
        raise CurrentCAVTraceError("%s must be an exact JSON array" % where)
    if nonempty and not value:
        raise CurrentCAVTraceError("%s must be nonempty" % where)
    return value


def canonical_event_content_sha256(
    event_id: int,
    timestamp_ns: int,
    polarity: int,
    is_query: bool,
    sensor_ray: Sequence[float],
    causal_pose_source_index: int,
    transform_guard_valid: bool = True,
) -> str:
    """Return the score-free neutral event identity."""

    return canonical_sha256({
        "event_id": event_id,
        "timestamp_ns": timestamp_ns,
        "polarity": polarity,
        "is_query": is_query,
        "sensor_ray": list(sensor_ray),
        "causal_pose_source_index": causal_pose_source_index,
        "transform_guard_valid": transform_guard_valid,
    })


def canonical_pose_value_sha256(
    pose_id: int, timestamp_ns: int, quaternion_xyzw: Sequence[float]
) -> str:
    """Return the score-free neutral pose identity."""

    return canonical_sha256({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion_xyzw),
    })


@dataclass(frozen=True)
class CycleRunnerProfile:
    """Immutable snapshot of an optional cycle-runner profile."""

    profile_id: str
    profile_mapping_json: str
    profile_mapping_sha256: str
    semantic_contract_sha256: str = SEMANTIC_CONTRACT_SHA256

    def __post_init__(self) -> None:
        _text(self.profile_id, "cycle runner profile ID")
        if type(self.profile_mapping_json) is not str or not self.profile_mapping_json:
            raise CurrentCAVTraceError("cycle runner profile JSON differs")
        try:
            mapping = json.loads(self.profile_mapping_json)
        except (TypeError, ValueError) as exc:
            raise CurrentCAVTraceError("cycle runner profile JSON differs") from exc
        if not isinstance(mapping, Mapping):
            raise CurrentCAVTraceError("cycle runner profile must encode an object")
        if canonical_json_bytes(mapping).decode("ascii") != self.profile_mapping_json:
            raise CurrentCAVTraceError("cycle runner profile is not canonical JSON")
        if self.profile_mapping_sha256 != canonical_sha256(mapping):
            raise CurrentCAVTraceError("cycle runner profile mapping digest differs")
        if self.semantic_contract_sha256 != SEMANTIC_CONTRACT_SHA256:
            raise CurrentCAVTraceError("cycle runner semantic contract differs")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "profile_mapping_json": self.profile_mapping_json,
            "profile_mapping_sha256": self.profile_mapping_sha256,
            "semantic_contract_sha256": self.semantic_contract_sha256,
            "semantics": dict(_SEMANTIC_CONTRACT),
        }


def _profile_snapshot(value: object) -> CycleRunnerProfile:
    if value is None:
        mapping = {
            "schema": "redred.mc_wtb.stage4_cyclemodel.implicit_profile/v1",
            "profile_id": "STAGE4_FROZEN_PHYSICAL_6X6_IMPLICIT_V1",
            "invocation": "run_cycle_model_without_ingress_profile_keyword",
        }
    else:
        try:
            mapping = value.to_mapping()  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise CurrentCAVTraceError(
                "injected cycle profile has no canonical mapping"
            ) from exc
        if not isinstance(mapping, Mapping):
            raise CurrentCAVTraceError("injected cycle profile mapping differs")
        if frozenset(mapping) != _INJECTED_PROFILE_FIELDS:
            raise CurrentCAVTraceError("injected cycle profile field schema differs")
        _text(mapping.get("schema"), "injected cycle profile schema")
        profile_id = mapping.get("profile_id")
        _text(profile_id, "injected cycle profile ID")
        raw_lanes = _integer(
            mapping.get("raw_ingress_lanes"), "injected raw ingress lanes"
        )
        staging = _integer(
            mapping.get("ingress_staging_entries"),
            "injected ingress staging entries",
        )
        service = _integer(
            mapping.get("event_service_lanes"), "injected event service lanes"
        )
        if raw_lanes == 0 or service == 0 or staging < raw_lanes:
            raise CurrentCAVTraceError("injected cycle profile capacity differs")
        _text(mapping.get("scope"), "injected cycle profile scope")
    try:
        encoded = canonical_json_bytes(mapping)
        text = encoded.decode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CurrentCAVTraceError("cycle profile is not canonical JSON") from exc
    return CycleRunnerProfile(
        str(mapping["profile_id"]), text, canonical_sha256(mapping)
    )


DEFAULT_CYCLE_PROFILE = _profile_snapshot(None)


@dataclass(frozen=True)
class TraceRegistryWindow:
    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int

    def __post_init__(self) -> None:
        _text(self.window_id, "window ID")
        start = _integer(self.warmup_start_ns_inclusive, "warmup start")
        query = _integer(self.query_start_ns_inclusive, "query start")
        end = _integer(self.query_end_ns_exclusive, "query end")
        if not start < query < end:
            raise CurrentCAVTraceError("neutral window bounds are not increasing")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "warmup_start_ns_inclusive": self.warmup_start_ns_inclusive,
            "query_start_ns_inclusive": self.query_start_ns_inclusive,
            "query_end_ns_exclusive": self.query_end_ns_exclusive,
        }


@dataclass(frozen=True)
class TraceEventInput:
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: Tuple[float, float, float]
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool

    def __post_init__(self) -> None:
        _integer(self.event_id, "event ID")
        _integer(self.timestamp_ns, "event timestamp")
        if isinstance(self.polarity, bool) or self.polarity not in (0, 1):
            raise CurrentCAVTraceError("event polarity must be integer zero or one")
        if type(self.is_query) is not bool or type(self.transform_guard_valid) is not bool:
            raise CurrentCAVTraceError("event flags must be exact bools")
        ray = _unit_tuple(self.sensor_ray, 3, "sensor ray")
        object.__setattr__(self, "sensor_ray", ray)
        _integer(self.causal_pose_source_index, "event causal pose source index")
        supplied = _sha256(self.event_content_sha256, "event content digest")
        expected = canonical_event_content_sha256(
            self.event_id,
            self.timestamp_ns,
            self.polarity,
            self.is_query,
            self.sensor_ray,
            self.causal_pose_source_index,
            self.transform_guard_valid,
        )
        if supplied != expected:
            raise CurrentCAVTraceError("event content digest differs")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "event_id": self.event_id,
            "timestamp_ns": self.timestamp_ns,
            "polarity": self.polarity,
            "is_query": self.is_query,
            "sensor_ray": list(self.sensor_ray),
            "causal_pose_source_index": self.causal_pose_source_index,
            "event_content_sha256": self.event_content_sha256,
            "transform_guard_valid": self.transform_guard_valid,
        }


@dataclass(frozen=True)
class TracePoseInput:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Tuple[float, float, float, float]
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool

    def __post_init__(self) -> None:
        _integer(self.pose_id, "pose ID")
        _integer(self.timestamp_ns, "pose timestamp")
        _integer(self.commit_cycle, "pose commit cycle", signed=True)
        quaternion = _unit_tuple(self.quaternion_xyzw, 4, "pose quaternion")
        object.__setattr__(self, "quaternion_xyzw", quaternion)
        supplied = _sha256(self.pose_sha256, "pose content digest")
        if type(self.value_valid) is not bool or type(self.arithmetic_valid) is not bool:
            raise CurrentCAVTraceError("pose validity flags must be exact bools")
        expected = canonical_pose_value_sha256(
            self.pose_id, self.timestamp_ns, self.quaternion_xyzw
        )
        if supplied != expected:
            raise CurrentCAVTraceError("pose content digest differs")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "pose_id": self.pose_id,
            "timestamp_ns": self.timestamp_ns,
            "commit_cycle": self.commit_cycle,
            "quaternion_xyzw": list(self.quaternion_xyzw),
            "pose_sha256": self.pose_sha256,
            "value_valid": self.value_valid,
            "arithmetic_valid": self.arithmetic_valid,
        }


@dataclass(frozen=True)
class CurrentCAVDecision:
    """Candidate-visible projection of one current-CAV cycle decision."""

    window_id: str
    event_id: int
    event_timestamp_ns: int
    occurrence_cycle: int
    occurrence_pose_ids: Tuple[int, ...]
    occurrence_pose_timestamps_ns: Tuple[int, ...]
    occurrence_pose_commit_cycles: Tuple[int, ...]
    occurrence_pose_sha256: Tuple[str, ...]
    used_pose_ids: Tuple[int, ...]
    used_pose_timestamps_ns: Tuple[int, ...]
    used_pose_commit_cycles: Tuple[int, ...]
    used_pose_sha256: Tuple[str, ...]
    disposition: str
    disposition_reason: str
    decision_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.window_id, "decision window ID")
        _integer(self.event_id, "decision event ID")
        _integer(self.event_timestamp_ns, "decision event timestamp")
        _integer(self.occurrence_cycle, "decision occurrence cycle")
        for identifiers, timestamps, commits, digests, where in (
            (
                self.occurrence_pose_ids,
                self.occurrence_pose_timestamps_ns,
                self.occurrence_pose_commit_cycles,
                self.occurrence_pose_sha256,
                "occurrence pose evidence",
            ),
            (
                self.used_pose_ids,
                self.used_pose_timestamps_ns,
                self.used_pose_commit_cycles,
                self.used_pose_sha256,
                "used pose evidence",
            ),
        ):
            if not len(identifiers) == len(timestamps) == len(commits) == len(digests):
                raise CurrentCAVTraceError("%s cardinality differs" % where)
            if identifiers != tuple(sorted(set(identifiers))):
                raise CurrentCAVTraceError("%s IDs are not unique and ordered" % where)
            for identifier in identifiers:
                _integer(identifier, "%s ID" % where)
            for timestamp in timestamps:
                _integer(timestamp, "%s timestamp" % where)
            for commit in commits:
                _integer(commit, "%s commit cycle" % where, signed=True)
            for digest in digests:
                _sha256(digest, "%s digest" % where)
        if not set(self.used_pose_ids).issubset(set(self.occurrence_pose_ids)):
            raise CurrentCAVTraceError("used poses are not occurrence-visible")
        _text(self.disposition, "baseline disposition")
        _text(self.disposition_reason, "baseline disposition reason")
        object.__setattr__(
            self, "decision_sha256", canonical_sha256(self._body_mapping())
        )

    def _body_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "event_timestamp_ns": self.event_timestamp_ns,
            "occurrence_cycle": self.occurrence_cycle,
            "occurrence_pose_ids": list(self.occurrence_pose_ids),
            "occurrence_pose_timestamps_ns": list(
                self.occurrence_pose_timestamps_ns
            ),
            "occurrence_pose_commit_cycles": list(
                self.occurrence_pose_commit_cycles
            ),
            "occurrence_pose_sha256": list(self.occurrence_pose_sha256),
            "used_pose_ids": list(self.used_pose_ids),
            "used_pose_timestamps_ns": list(self.used_pose_timestamps_ns),
            "used_pose_commit_cycles": list(self.used_pose_commit_cycles),
            "used_pose_sha256": list(self.used_pose_sha256),
            "disposition": self.disposition,
            "disposition_reason": self.disposition_reason,
        }

    def to_mapping(self) -> Mapping[str, object]:
        return dict(self._body_mapping(), decision_sha256=self.decision_sha256)


@dataclass(frozen=True)
class CurrentCAVSimulationTrace:
    records: Tuple[CurrentCAVDecision, ...]
    decision_records_sha256: str = field(init=False)
    synthetic_test_mode: bool = False
    all_event_pose_indices_verified: bool = True

    def __post_init__(self) -> None:
        if type(self.records) is not tuple or not self.records:
            raise CurrentCAVTraceError("simulation records must be a nonempty tuple")
        if self.synthetic_test_mode or not self.all_event_pose_indices_verified:
            raise CurrentCAVTraceError("simulation is not integration-authenticated")
        object.__setattr__(
            self,
            "decision_records_sha256",
            canonical_sha256([record.to_mapping() for record in self.records]),
        )

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "records": [record.to_mapping() for record in self.records],
            "decision_records_sha256": self.decision_records_sha256,
            "synthetic_test_mode": self.synthetic_test_mode,
            "all_event_pose_indices_verified": (
                self.all_event_pose_indices_verified
            ),
        }


@dataclass(frozen=True)
class CurrentCAVWindowTrace:
    registry: TraceRegistryWindow
    input_events: Tuple[TraceEventInput, ...]
    input_poses: Tuple[TracePoseInput, ...]
    simulation: CurrentCAVSimulationTrace
    window_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.registry) is not TraceRegistryWindow:
            raise CurrentCAVTraceError("trace registry type differs")
        if not self.input_events or not self.input_poses:
            raise CurrentCAVTraceError("trace inputs must be nonempty")
        if len(self.input_events) != len(self.simulation.records):
            raise CurrentCAVTraceError("trace changed event cardinality")
        object.__setattr__(self, "window_sha256", canonical_sha256(self._body_mapping()))

    def _body_mapping(self) -> Mapping[str, object]:
        return {
            "registry": self.registry.to_mapping(),
            "input_events": [event.to_mapping() for event in self.input_events],
            "input_poses": [pose.to_mapping() for pose in self.input_poses],
            "simulation": self.simulation.to_mapping(),
        }

    def to_mapping(self) -> Mapping[str, object]:
        return dict(self._body_mapping(), window_sha256=self.window_sha256)


@dataclass(frozen=True)
class CurrentCAVTrace:
    profile: CycleRunnerProfile
    windows: Tuple[CurrentCAVWindowTrace, ...]
    neutral_input_sha256: str
    baseline_decisions_sha256: str
    profile_sha256: str
    aggregate_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.profile) is not CycleRunnerProfile:
            raise CurrentCAVTraceError("trace profile type differs")
        if type(self.windows) is not tuple or not self.windows:
            raise CurrentCAVTraceError("trace windows must be a nonempty tuple")
        _sha256(self.neutral_input_sha256, "neutral input digest")
        _sha256(self.baseline_decisions_sha256, "baseline decisions digest")
        if self.profile_sha256 != canonical_sha256(self.profile.to_mapping()):
            raise CurrentCAVTraceError("trace profile digest differs")
        object.__setattr__(self, "aggregate_sha256", canonical_sha256(self._body_mapping()))

    def _body_mapping(self) -> Mapping[str, object]:
        return {
            "schema": TRACE_SCHEMA,
            "profile": self.profile.to_mapping(),
            "profile_sha256": self.profile_sha256,
            "neutral_input_sha256": self.neutral_input_sha256,
            "baseline_schema": BASELINE_SCHEMA,
            "baseline_decisions_sha256": self.baseline_decisions_sha256,
            "windows": [window.to_mapping() for window in self.windows],
        }

    def to_mapping(self) -> Mapping[str, object]:
        return dict(self._body_mapping(), aggregate_sha256=self.aggregate_sha256)


CycleRunner = Callable[..., object]


def run_frozen_current_cav_profile(
    *,
    window_id: str,
    window_start_ns: int,
    arm: Arm,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
    synthetic_test_mode: bool = False,
) -> object:
    """Named compatibility wrapper around the frozen bounded cycle runner."""

    return run_cycle_model(
        window_id=window_id,
        window_start_ns=window_start_ns,
        arm=arm,
        events=events,
        poses=poses,
        synthetic_test_mode=synthetic_test_mode,
    )


def _snapshot_registry(value: object) -> TraceRegistryWindow:
    row = _exact_fields(value, _REGISTRY_FIELDS, "neutral registry window")
    return TraceRegistryWindow(
        row["window_id"],  # type: ignore[arg-type]
        row["warmup_start_ns_inclusive"],  # type: ignore[arg-type]
        row["query_start_ns_inclusive"],  # type: ignore[arg-type]
        row["query_end_ns_exclusive"],  # type: ignore[arg-type]
    )


def _snapshot_event(value: object) -> TraceEventInput:
    row = _exact_fields(value, _EVENT_FIELDS, "neutral event")
    ray = _unit_tuple(row["sensor_ray"], 3, "sensor ray")
    return TraceEventInput(
        row["event_id"],  # type: ignore[arg-type]
        row["timestamp_ns"],  # type: ignore[arg-type]
        row["polarity"],  # type: ignore[arg-type]
        row["is_query"],  # type: ignore[arg-type]
        ray,  # type: ignore[arg-type]
        row["causal_pose_source_index"],  # type: ignore[arg-type]
        row["event_content_sha256"],  # type: ignore[arg-type]
        row["transform_guard_valid"],  # type: ignore[arg-type]
    )


def _snapshot_pose(value: object) -> TracePoseInput:
    row = _exact_fields(value, _POSE_FIELDS, "neutral pose")
    quaternion = _unit_tuple(row["quaternion_xyzw"], 4, "pose quaternion")
    return TracePoseInput(
        row["pose_id"],  # type: ignore[arg-type]
        row["timestamp_ns"],  # type: ignore[arg-type]
        row["commit_cycle"],  # type: ignore[arg-type]
        quaternion,  # type: ignore[arg-type]
        row["pose_sha256"],  # type: ignore[arg-type]
        row["value_valid"],  # type: ignore[arg-type]
        row["arithmetic_valid"],  # type: ignore[arg-type]
    )


def _validated_inputs(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
) -> Tuple[
    Tuple[TraceRegistryWindow, ...],
    Mapping[str, Tuple[TraceEventInput, ...]],
    Mapping[str, Tuple[TracePoseInput, ...]],
]:
    if isinstance(registry, (str, bytes)) or not isinstance(registry, Sequence):
        raise CurrentCAVTraceError("neutral registry must be an ordered sequence")
    windows = tuple(_snapshot_registry(row) for row in registry)
    if not windows:
        raise CurrentCAVTraceError("neutral registry must be nonempty")
    identifiers = tuple(window.window_id for window in windows)
    if len(set(identifiers)) != len(identifiers):
        raise CurrentCAVTraceError("neutral registry window IDs repeat")
    for left, right in zip(windows, windows[1:]):
        if (
            left.warmup_start_ns_inclusive >= right.warmup_start_ns_inclusive
            or left.query_start_ns_inclusive >= right.query_start_ns_inclusive
            or left.query_end_ns_exclusive > right.query_start_ns_inclusive
        ):
            raise CurrentCAVTraceError("neutral query windows overlap or move backwards")
    try:
        event_source = dict(event_streams)
        pose_source = dict(pose_streams)
    except (TypeError, ValueError) as exc:
        raise CurrentCAVTraceError("neutral streams must be mappings") from exc
    if set(event_source) != set(identifiers) or set(pose_source) != set(identifiers):
        raise CurrentCAVTraceError("neutral stream window identities differ")

    checked_events: Dict[str, Tuple[TraceEventInput, ...]] = {}
    checked_poses: Dict[str, Tuple[TracePoseInput, ...]] = {}
    query_ids = set()
    for window in windows:
        events = tuple(_snapshot_event(row) for row in event_source[window.window_id])
        poses = tuple(_snapshot_pose(row) for row in pose_source[window.window_id])
        if not events or not poses:
            raise CurrentCAVTraceError("neutral event and pose streams must be nonempty")
        if any(
            right.event_id <= left.event_id
            or right.timestamp_ns < left.timestamp_ns
            for left, right in zip(events, events[1:])
        ):
            raise CurrentCAVTraceError("neutral events are not strictly ID-ordered")
        if any(
            right.pose_id <= left.pose_id
            or right.timestamp_ns <= left.timestamp_ns
            for left, right in zip(poses, poses[1:])
        ):
            raise CurrentCAVTraceError("neutral poses are not strictly chronological")
        for pose in poses:
            expected_commit = pose_timestamp_to_cycle(
                pose.timestamp_ns, window.warmup_start_ns_inclusive
            )
            if pose.commit_cycle != expected_commit:
                raise CurrentCAVTraceError("neutral pose commit cycle differs")
        has_query = False
        for event in events:
            if not (
                window.warmup_start_ns_inclusive
                <= event.timestamp_ns
                < window.query_end_ns_exclusive
            ):
                raise CurrentCAVTraceError("neutral event lies outside its window")
            expected_query = event.timestamp_ns >= window.query_start_ns_inclusive
            if event.is_query != expected_query:
                raise CurrentCAVTraceError("neutral event query membership differs")
            if event.is_query:
                has_query = True
                if event.event_id in query_ids:
                    raise CurrentCAVTraceError("query event ID repeats across windows")
                query_ids.add(event.event_id)
        if not has_query:
            raise CurrentCAVTraceError("neutral window has no query event")
        checked_events[window.window_id] = events
        checked_poses[window.window_id] = poses
    return windows, checked_events, checked_poses


def _expected_route(
    event: TraceEventInput, occurrence: Sequence[TracePoseInput]
) -> Tuple[Tuple[TracePoseInput, ...], str, str]:
    snapshot = tuple(occurrence)
    if not snapshot:
        return (), "raw_bypass", "no_occurrence_pose"
    latest = snapshot[-1]
    age = event.timestamp_ns - latest.timestamp_ns
    if len(snapshot) == 2:
        previous = snapshot[0]
        interval = latest.timestamp_ns - previous.timestamp_ns
        horizon = min(CAV_MAX_HORIZON_NS, interval)
        if (
            age <= horizon
            and previous.value_valid
            and latest.value_valid
            and previous.arithmetic_valid
            and latest.arithmetic_valid
            and event.transform_guard_valid
        ):
            return snapshot, "corrected_world_ray", "causal_cav"
    if latest.value_valid and latest.arithmetic_valid and age <= ZOH_MAX_AGE_NS:
        return (latest,), "corrected_world_ray", "fresh_zoh_fallback"
    if not latest.value_valid or not latest.arithmetic_valid:
        return (latest,), "raw_bypass", "invalid_pose"
    return (latest,), "raw_bypass", "stale_pose"


def _provenance(
    poses: Sequence[TracePoseInput],
) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[int, ...], Tuple[str, ...]]:
    return (
        tuple(pose.pose_id for pose in poses),
        tuple(pose.timestamp_ns for pose in poses),
        tuple(pose.commit_cycle for pose in poses),
        tuple(pose.pose_sha256 for pose in poses),
    )


def _project_record(
    window: TraceRegistryWindow,
    event: TraceEventInput,
    poses: Sequence[TracePoseInput],
    record: object,
) -> CurrentCAVDecision:
    edge = timestamp_to_cycle(event.timestamp_ns, window.warmup_start_ns_inclusive)
    visible = tuple(
        pose for pose in poses
        if pose.commit_cycle < edge and pose.timestamp_ns <= event.timestamp_ns
    )
    occurrence = visible[-2:]
    latest_id = occurrence[-1].pose_id if occurrence else None
    if latest_id is None or event.causal_pose_source_index != latest_id:
        raise CurrentCAVTraceError("event causal pose source index differs")
    selected, disposition, reason = _expected_route(event, occurrence)
    occurrence_evidence = _provenance(occurrence)
    used_evidence = _provenance(selected)
    required = {
        "window_id": window.window_id,
        "event_id": event.event_id,
        "event_timestamp_ns": event.timestamp_ns,
        "occurrence_cycle": edge,
        "occurrence_pose_ids": occurrence_evidence[0],
        "occurrence_pose_timestamps_ns": occurrence_evidence[1],
        "occurrence_pose_commit_cycles": occurrence_evidence[2],
        "occurrence_pose_sha256": occurrence_evidence[3],
        "used_pose_ids": used_evidence[0],
        "used_pose_timestamps_ns": used_evidence[1],
        "used_pose_commit_cycles": used_evidence[2],
        "used_pose_sha256": used_evidence[3],
        "disposition": disposition,
        "disposition_reason": reason,
    }
    for name, expected in required.items():
        try:
            observed = getattr(record, name)
        except AttributeError as exc:
            raise CurrentCAVTraceError("cycle decision field is missing") from exc
        if observed != expected:
            raise CurrentCAVTraceError("cycle decision %s differs" % name)
    try:
        arm = record.arm
        future = record.intentional_future_pose_use
    except AttributeError:
        arm = Arm.CAUSAL_CAV.value
        future = False
    if arm != Arm.CAUSAL_CAV.value or future is not False:
        raise CurrentCAVTraceError("cycle decision is not strictly causal current CAV")
    return CurrentCAVDecision(**required)  # type: ignore[arg-type]


def _neutral_input_mapping(
    windows: Sequence[TraceRegistryWindow],
    events: Mapping[str, Sequence[TraceEventInput]],
    poses: Mapping[str, Sequence[TracePoseInput]],
) -> Mapping[str, object]:
    return {
        "schema": NEUTRAL_INPUT_SCHEMA,
        "registry": [window.to_mapping() for window in windows],
        "windows": [
            {
                "window_id": window.window_id,
                "events": [event.to_mapping() for event in events[window.window_id]],
                "poses": [pose.to_mapping() for pose in poses[window.window_id]],
            }
            for window in windows
        ],
    }


def _load_profile(value: object) -> CycleRunnerProfile:
    row = _exact_fields(value, _PROFILE_MAPPING_FIELDS, "trace profile")
    profile = CycleRunnerProfile(
        row["profile_id"],  # type: ignore[arg-type]
        row["profile_mapping_json"],  # type: ignore[arg-type]
        row["profile_mapping_sha256"],  # type: ignore[arg-type]
        row["semantic_contract_sha256"],  # type: ignore[arg-type]
    )
    mapping = json.loads(profile.profile_mapping_json)
    if mapping.get("profile_id") != profile.profile_id:
        raise CurrentCAVTraceError("trace profile identity differs")
    if frozenset(mapping) == _IMPLICIT_PROFILE_FIELDS:
        if mapping != json.loads(DEFAULT_CYCLE_PROFILE.profile_mapping_json):
            raise CurrentCAVTraceError("trace implicit profile differs")
    elif frozenset(mapping) == _INJECTED_PROFILE_FIELDS:
        _text(mapping.get("schema"), "trace injected profile schema")
        raw_lanes = _integer(
            mapping.get("raw_ingress_lanes"), "trace injected raw ingress lanes"
        )
        staging = _integer(
            mapping.get("ingress_staging_entries"),
            "trace injected ingress staging entries",
        )
        service = _integer(
            mapping.get("event_service_lanes"),
            "trace injected event service lanes",
        )
        if raw_lanes == 0 or service == 0 or staging < raw_lanes:
            raise CurrentCAVTraceError("trace injected profile capacity differs")
        _text(mapping.get("scope"), "trace injected profile scope")
    else:
        raise CurrentCAVTraceError("trace profile field schema differs")
    if profile.to_mapping() != row:
        raise CurrentCAVTraceError("trace profile mapping differs")
    return profile


def _decision_tuple(value: object, where: str) -> Tuple[object, ...]:
    return tuple(_mapping_list(value, where, nonempty=False))


def _load_decision(value: object) -> CurrentCAVDecision:
    row = _exact_fields(value, _DECISION_MAPPING_FIELDS, "trace decision")
    decision = CurrentCAVDecision(
        row["window_id"],  # type: ignore[arg-type]
        row["event_id"],  # type: ignore[arg-type]
        row["event_timestamp_ns"],  # type: ignore[arg-type]
        row["occurrence_cycle"],  # type: ignore[arg-type]
        _decision_tuple(row["occurrence_pose_ids"], "occurrence pose IDs"),  # type: ignore[arg-type]
        _decision_tuple(
            row["occurrence_pose_timestamps_ns"],
            "occurrence pose timestamps",
        ),  # type: ignore[arg-type]
        _decision_tuple(
            row["occurrence_pose_commit_cycles"],
            "occurrence pose commit cycles",
        ),  # type: ignore[arg-type]
        _decision_tuple(
            row["occurrence_pose_sha256"], "occurrence pose digests"
        ),  # type: ignore[arg-type]
        _decision_tuple(row["used_pose_ids"], "used pose IDs"),  # type: ignore[arg-type]
        _decision_tuple(
            row["used_pose_timestamps_ns"], "used pose timestamps"
        ),  # type: ignore[arg-type]
        _decision_tuple(
            row["used_pose_commit_cycles"], "used pose commit cycles"
        ),  # type: ignore[arg-type]
        _decision_tuple(row["used_pose_sha256"], "used pose digests"),  # type: ignore[arg-type]
        row["disposition"],  # type: ignore[arg-type]
        row["disposition_reason"],  # type: ignore[arg-type]
    )
    if decision.to_mapping() != row:
        raise CurrentCAVTraceError("trace decision seal differs")
    return decision


def load_current_cav_trace(value: object) -> CurrentCAVTrace:
    """Reconstruct and authenticate a sealed trace without running a cycle model."""

    row = _exact_fields(value, _TRACE_MAPPING_FIELDS, "current-CAV trace")
    if row.get("schema") != TRACE_SCHEMA:
        raise CurrentCAVTraceError("current-CAV trace schema differs")
    if row.get("baseline_schema") != BASELINE_SCHEMA:
        raise CurrentCAVTraceError("current-CAV baseline schema differs")
    profile = _load_profile(row["profile"])
    supplied_windows = _mapping_list(row["windows"], "trace windows")

    raw_windows = []
    registry_rows = []
    event_streams: Dict[str, Tuple[TraceEventInput, ...]] = {}
    pose_streams: Dict[str, Tuple[TracePoseInput, ...]] = {}
    for supplied in supplied_windows:
        window_row = _exact_fields(
            supplied, _WINDOW_MAPPING_FIELDS, "trace window"
        )
        registry = _snapshot_registry(window_row["registry"])
        events = tuple(
            _snapshot_event(event)
            for event in _mapping_list(window_row["input_events"], "trace events")
        )
        poses = tuple(
            _snapshot_pose(pose)
            for pose in _mapping_list(window_row["input_poses"], "trace poses")
        )
        raw_windows.append(window_row)
        registry_rows.append(registry)
        event_streams[registry.window_id] = events
        pose_streams[registry.window_id] = poses

    windows, checked_events, checked_poses = _validated_inputs(
        tuple(registry_rows), event_streams, pose_streams
    )
    trace_windows = []
    for supplied, window in zip(raw_windows, windows):
        events = checked_events[window.window_id]
        poses = checked_poses[window.window_id]
        simulation_row = _exact_fields(
            supplied["simulation"],
            _SIMULATION_MAPPING_FIELDS,
            "trace simulation",
        )
        if (
            type(simulation_row["synthetic_test_mode"]) is not bool
            or type(simulation_row["all_event_pose_indices_verified"]) is not bool
        ):
            raise CurrentCAVTraceError("trace simulation flags must be exact bools")
        raw_decisions = _mapping_list(
            simulation_row["records"], "trace decision records"
        )
        if len(raw_decisions) != len(events):
            raise CurrentCAVTraceError("trace changed event cardinality")
        loaded_decisions = tuple(
            _load_decision(decision) for decision in raw_decisions
        )
        checked_decisions = tuple(
            _project_record(window, event, poses, decision)
            for event, decision in zip(events, loaded_decisions)
        )
        if checked_decisions != loaded_decisions:
            raise CurrentCAVTraceError("trace decision semantics differ")
        simulation = CurrentCAVSimulationTrace(
            checked_decisions,
            synthetic_test_mode=simulation_row["synthetic_test_mode"],  # type: ignore[arg-type]
            all_event_pose_indices_verified=(
                simulation_row["all_event_pose_indices_verified"]  # type: ignore[arg-type]
            ),
        )
        if simulation.to_mapping() != simulation_row:
            raise CurrentCAVTraceError("trace simulation seal differs")
        loaded_window = CurrentCAVWindowTrace(window, events, poses, simulation)
        if loaded_window.to_mapping() != supplied:
            raise CurrentCAVTraceError("trace window seal differs")
        trace_windows.append(loaded_window)

    neutral_sha256 = canonical_sha256(_neutral_input_mapping(
        windows, checked_events, checked_poses
    ))
    if row["neutral_input_sha256"] != neutral_sha256:
        raise CurrentCAVTraceError("trace neutral input digest differs")
    baseline_sha256 = canonical_sha256({
        "schema": BASELINE_SCHEMA,
        "windows": [
            {
                "window_id": window.registry.window_id,
                "decisions": [
                    decision.to_mapping()
                    for decision in window.simulation.records
                ],
            }
            for window in trace_windows
        ],
    })
    if row["baseline_decisions_sha256"] != baseline_sha256:
        raise CurrentCAVTraceError("trace baseline decisions digest differs")
    profile_sha256 = canonical_sha256(profile.to_mapping())
    if row["profile_sha256"] != profile_sha256:
        raise CurrentCAVTraceError("trace profile digest differs")
    trace = CurrentCAVTrace(
        profile,
        tuple(trace_windows),
        neutral_sha256,
        baseline_sha256,
        profile_sha256,
    )
    try:
        supplied_bytes = canonical_json_bytes(row)
        reconstructed_bytes = canonical_json_bytes(trace.to_mapping())
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CurrentCAVTraceError("current-CAV trace is not canonical JSON") from exc
    if supplied_bytes != reconstructed_bytes:
        raise CurrentCAVTraceError("current-CAV trace mapping differs")
    return trace


def build_current_cav_trace(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
    *,
    cycle_runner: CycleRunner = run_cycle_model,
    cycle_profile: object = None,
    cycle_runner_owns_profile: bool = False,
) -> CurrentCAVTrace:
    """Build a sealed, event-for-event score-free current-CAV trace."""

    if not callable(cycle_runner):
        raise CurrentCAVTraceError("cycle runner must be callable")
    if type(cycle_runner_owns_profile) is not bool:
        raise CurrentCAVTraceError("cycle_runner_owns_profile must be bool")
    profile_snapshot = _profile_snapshot(cycle_profile)
    windows, events_by_window, poses_by_window = _validated_inputs(
        registry, event_streams, pose_streams
    )
    trace_windows = []
    for window in windows:
        events = events_by_window[window.window_id]
        poses = poses_by_window[window.window_id]
        cycle_events = tuple(Event(
            event.event_id,
            event.timestamp_ns,
            event.transform_guard_valid,
            event.causal_pose_source_index,
        ) for event in events)
        cycle_poses = tuple(PosePacket(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            PoseSource.DATASET,
            pose.pose_sha256,
            pose.value_valid,
            pose.arithmetic_valid,
        ) for pose in poses)
        try:
            runner_arguments = dict(
                window_id=window.window_id,
                window_start_ns=window.warmup_start_ns_inclusive,
                arm=Arm.CAUSAL_CAV,
                events=cycle_events,
                poses=cycle_poses,
                synthetic_test_mode=False,
            )
            if cycle_profile is not None and not cycle_runner_owns_profile:
                runner_arguments["ingress_profile"] = cycle_profile
            result = cycle_runner(**runner_arguments)
            raw_records = tuple(result.records)
            synthetic = result.synthetic_test_mode
            verified = result.all_event_pose_indices_verified
        except CurrentCAVTraceError:
            raise
        except (AttributeError, TypeError, ValueError) as exc:
            raise CurrentCAVTraceError("current-CAV cycle runner failed") from exc
        if type(synthetic) is not bool or type(verified) is not bool:
            raise CurrentCAVTraceError("cycle runner status fields differ")
        if synthetic or not verified:
            raise CurrentCAVTraceError("cycle runner did not authenticate pose indices")
        if len(raw_records) != len(events):
            raise CurrentCAVTraceError("cycle runner changed event cardinality")
        decisions = tuple(
            _project_record(window, event, poses, record)
            for event, record in zip(events, raw_records)
        )
        simulation = CurrentCAVSimulationTrace(decisions)
        trace_windows.append(CurrentCAVWindowTrace(
            window, events, poses, simulation
        ))

    neutral_sha = canonical_sha256(
        _neutral_input_mapping(windows, events_by_window, poses_by_window)
    )
    baseline_sha = canonical_sha256({
        "schema": BASELINE_SCHEMA,
        "windows": [
            {
                "window_id": window.registry.window_id,
                "decisions": [
                    decision.to_mapping() for decision in window.simulation.records
                ],
            }
            for window in trace_windows
        ],
    })
    profile_sha = canonical_sha256(profile_snapshot.to_mapping())
    return CurrentCAVTrace(
        profile_snapshot,
        tuple(trace_windows),
        neutral_sha,
        baseline_sha,
        profile_sha,
    )


def verify_current_cav_trace(
    trace: object,
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
    *,
    cycle_runner: CycleRunner = run_cycle_model,
    cycle_profile: object = None,
) -> str:
    """Rebuild a trace and return its authenticated aggregate digest."""

    if type(trace) is not CurrentCAVTrace:
        raise CurrentCAVTraceError("current-CAV trace type differs")
    expected = build_current_cav_trace(
        registry,
        event_streams,
        pose_streams,
        cycle_runner=cycle_runner,
        cycle_profile=cycle_profile,
    )
    if trace != expected:
        raise CurrentCAVTraceError("current-CAV trace differs from replay")
    return expected.aggregate_sha256


__all__ = (
    "BASELINE_SCHEMA",
    "CycleRunner",
    "CycleRunnerProfile",
    "CurrentCAVDecision",
    "CurrentCAVSimulationTrace",
    "CurrentCAVTrace",
    "CurrentCAVTraceError",
    "CurrentCAVWindowTrace",
    "DEFAULT_CYCLE_PROFILE",
    "NEUTRAL_INPUT_SCHEMA",
    "PROFILE_SCHEMA",
    "SEMANTIC_CONTRACT_SHA256",
    "TRACE_SCHEMA",
    "TraceEventInput",
    "TracePoseInput",
    "TraceRegistryWindow",
    "build_current_cav_trace",
    "canonical_event_content_sha256",
    "canonical_pose_value_sha256",
    "load_current_cav_trace",
    "run_frozen_current_cav_profile",
    "verify_current_cav_trace",
)
