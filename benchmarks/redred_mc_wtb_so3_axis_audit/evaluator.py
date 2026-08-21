"""Selector-neutral evaluator for the frozen current Stage-4 causal CAV arm.

This module deliberately has no SO(3)-axis selector or analyzer dependency.
Its complete authority is a neutral ordered registry plus per-window event and
pose streams.  Axis labels, balance classes, thresholds, and selector feature
values are not representable by the input API.

The evaluator reuses the frozen Stage-4 cycle model, causal CAV quaternion
geometry, and past-only causal reference bank.  It does not call the Stage-4
scorer, aggregate disposition logic, sealer, or score runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    ReferenceObservation,
)
from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample as RecoveryPoseSample,
    RecoveryMode,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    DecisionRecord,
    Event,
    PosePacket,
    PoseSource,
    SimulationResult,
    run_cycle_model,
)


Ray = Tuple[float, float, float]
QuaternionXYZW = Tuple[float, float, float, float]
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
_CAV_WINDOW_FIELDS = frozenset((
    "registry",
    "input_events",
    "input_poses",
    "simulation",
    "query_events",
    "query_decisions_sha256",
))
_CAV_REGISTRY_FIELDS = frozenset((
    "registry_sha256",
    "neutral_input_sha256",
    "windows",
))
_REFERENCE_CAPACITY_PER_POLARITY = 256
_REFERENCE_MAX_AGE_NS = 2_000_000
_POSITIVE_WINDOW_THRESHOLD = 1.0e-6


class CurrentCAVEvaluationError(ValueError):
    """The neutral input or current-CAV evaluation contract failed."""


def _exact_dataclass(
    value: object,
    expected_type: type,
    expected_fields: frozenset,
    where: str,
) -> None:
    if type(value) is not expected_type:
        raise CurrentCAVEvaluationError("%s must have its exact dataclass type" % where)
    try:
        actual_fields = frozenset(vars(value))
    except TypeError as exc:
        raise CurrentCAVEvaluationError("%s has no dataclass field mapping" % where) from exc
    if actual_fields != expected_fields:
        raise CurrentCAVEvaluationError("%s dataclass field set differs" % where)


def _nonnegative_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CurrentCAVEvaluationError("%s must be a non-negative integer" % where)
    return value


def _signed_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CurrentCAVEvaluationError("%s must be an integer" % where)
    return value


def _nonempty_text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise CurrentCAVEvaluationError("%s must be non-empty text" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CurrentCAVEvaluationError("%s must be lowercase SHA-256" % where)
    return value


def _finite_tuple(value: object, length: int, where: str) -> Tuple[float, ...]:
    if type(value) not in (tuple, list) or len(value) != length:  # type: ignore[arg-type]
        raise CurrentCAVEvaluationError(
            "%s must contain exactly %d components" % (where, length)
        )
    result = []
    for index, component in enumerate(value):  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise CurrentCAVEvaluationError("%s[%d] must be finite" % (where, index))
        converted = float(component)
        if not math.isfinite(converted):
            raise CurrentCAVEvaluationError("%s[%d] must be finite" % (where, index))
        result.append(converted)
    return tuple(result)


def _unit_ray(value: object, where: str) -> Ray:
    result = _finite_tuple(value, 3, where)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if abs(norm - 1.0) > 1.0e-9:
        raise CurrentCAVEvaluationError("%s must be a normalized ray" % where)
    return result  # type: ignore[return-value]


def _quaternion(value: object, where: str) -> QuaternionXYZW:
    result = _finite_tuple(value, 4, where)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise CurrentCAVEvaluationError("%s must be a unit quaternion" % where)
    # Preserve the hash-bound serialized values.  Frozen geometry normalizes
    # internally before every quaternion operation.
    return result  # type: ignore[return-value]


def canonical_event_content_sha256(
    event_id: int,
    timestamp_ns: int,
    polarity: int,
    is_query: bool,
    sensor_ray: Sequence[float],
    causal_pose_source_index: int,
    transform_guard_valid: bool = True,
) -> str:
    """Bind every evaluator-visible event value, including the sensor ray."""

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
    pose_id: int,
    timestamp_ns: int,
    quaternion_xyzw: Sequence[float],
) -> str:
    """Return the frozen pose-value identity used by the Stage-4 assay."""

    return canonical_sha256({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion_xyzw),
    })


@dataclass(frozen=True)
class NeutralRegistryWindow:
    """The complete selector-independent registry interface for one window."""

    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralRegistryWindow, _REGISTRY_FIELDS, "registry")
        object.__setattr__(self, "window_id", _nonempty_text(self.window_id, "window_id"))
        for field in (
            "warmup_start_ns_inclusive",
            "query_start_ns_inclusive",
            "query_end_ns_exclusive",
        ):
            object.__setattr__(self, field, _nonnegative_int(getattr(self, field), field))
        if not (
            self.warmup_start_ns_inclusive
            < self.query_start_ns_inclusive
            < self.query_end_ns_exclusive
        ):
            raise CurrentCAVEvaluationError("neutral registry bounds are not increasing")

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "warmup_start_ns_inclusive": self.warmup_start_ns_inclusive,
            "query_start_ns_inclusive": self.query_start_ns_inclusive,
            "query_end_ns_exclusive": self.query_end_ns_exclusive,
        }


def load_neutral_registry(
    rows: Sequence[Mapping[str, object]],
) -> Tuple[NeutralRegistryWindow, ...]:
    """Validate exact registry bounds, rejecting selector fields and metadata."""

    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise CurrentCAVEvaluationError("neutral registry must be a non-empty sequence")
    windows = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CurrentCAVEvaluationError("neutral registry row must be an object")
        if frozenset(row) != _REGISTRY_FIELDS:
            raise CurrentCAVEvaluationError(
                "neutral registry row %d must contain bounds only" % index
            )
        windows.append(NeutralRegistryWindow(
            row["window_id"],  # type: ignore[arg-type]
            row["warmup_start_ns_inclusive"],  # type: ignore[arg-type]
            row["query_start_ns_inclusive"],  # type: ignore[arg-type]
            row["query_end_ns_exclusive"],  # type: ignore[arg-type]
        ))
    identifiers = tuple(window.window_id for window in windows)
    if len(set(identifiers)) != len(identifiers):
        raise CurrentCAVEvaluationError("neutral registry window IDs are duplicated")
    for left, right in zip(windows, windows[1:]):
        if left.query_end_ns_exclusive > right.warmup_start_ns_inclusive:
            raise CurrentCAVEvaluationError("neutral registry windows overlap or move backwards")
    return tuple(windows)


@dataclass(frozen=True)
class NeutralEventInput:
    """One event record after score-free source and payload validation."""

    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: Ray
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool = True

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralEventInput, _EVENT_FIELDS, "event")
        object.__setattr__(self, "event_id", _nonnegative_int(self.event_id, "event_id"))
        object.__setattr__(
            self, "timestamp_ns", _nonnegative_int(self.timestamp_ns, "event timestamp")
        )
        if isinstance(self.polarity, bool) or self.polarity not in (0, 1):
            raise CurrentCAVEvaluationError("event polarity must be integer zero or one")
        if type(self.is_query) is not bool:
            raise CurrentCAVEvaluationError("event is_query must be bool")
        object.__setattr__(self, "sensor_ray", _unit_ray(self.sensor_ray, "sensor ray"))
        object.__setattr__(
            self,
            "causal_pose_source_index",
            _nonnegative_int(self.causal_pose_source_index, "causal pose source index"),
        )
        if type(self.transform_guard_valid) is not bool:
            raise CurrentCAVEvaluationError("transform_guard_valid must be bool")
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
            raise CurrentCAVEvaluationError("event content digest differs")

    def to_content_mapping(self) -> Mapping[str, object]:
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
class NeutralPoseInput:
    """One hash-bound dataset pose packet and its orientation value."""

    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: QuaternionXYZW
    pose_sha256: str
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralPoseInput, _POSE_FIELDS, "pose")
        object.__setattr__(self, "pose_id", _nonnegative_int(self.pose_id, "pose_id"))
        object.__setattr__(
            self, "timestamp_ns", _nonnegative_int(self.timestamp_ns, "pose timestamp")
        )
        object.__setattr__(self, "commit_cycle", _signed_int(self.commit_cycle, "commit cycle"))
        object.__setattr__(
            self, "quaternion_xyzw", _quaternion(self.quaternion_xyzw, "pose quaternion")
        )
        object.__setattr__(self, "pose_sha256", _sha256(self.pose_sha256, "pose digest"))
        if type(self.value_valid) is not bool or type(self.arithmetic_valid) is not bool:
            raise CurrentCAVEvaluationError("pose validity flags must be bool")
        expected = canonical_pose_value_sha256(
            self.pose_id, self.timestamp_ns, self.quaternion_xyzw
        )
        if self.pose_sha256 != expected:
            raise CurrentCAVEvaluationError("pose content digest differs")

    def to_content_mapping(self) -> Mapping[str, object]:
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
class CAVEventEvaluation:
    """One query event's exact decision, references, and joined losses."""

    decision: DecisionRecord
    sensor_loss: float
    world_shadow_loss: float
    policy_loss: float
    enabled: bool
    quality_waste: bool
    sensor_reference_event_id: int
    world_reference_event_id: int
    occurrence_latency_cycles: int
    added_latency_cycles: int

    def to_loss_mapping(self) -> Mapping[str, object]:
        return {
            "event_id": self.decision.event_id,
            "sensor_loss": self.sensor_loss,
            "world_shadow_loss": self.world_shadow_loss,
            "policy_loss": self.policy_loss,
            "enabled": self.enabled,
            "quality_waste": self.quality_waste,
            "sensor_reference_event_id": self.sensor_reference_event_id,
            "world_reference_event_id": self.world_reference_event_id,
            "occurrence_latency_cycles": self.occurrence_latency_cycles,
            "added_latency_cycles": self.added_latency_cycles,
        }


@dataclass(frozen=True)
class LatencySummary:
    count: int
    mean_cycles: float
    p50_cycles: int
    p95_cycles: int
    p99_cycles: int
    max_cycles: int

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "count": self.count,
            "mean_cycles": self.mean_cycles,
            "p50_cycles": self.p50_cycles,
            "p95_cycles": self.p95_cycles,
            "p99_cycles": self.p99_cycles,
            "max_cycles": self.max_cycles,
        }


def _latency_summary(values: Iterable[Tuple[int, int]]) -> LatencySummary:
    checked = []
    for event_id, cycles in values:
        checked.append((
            _nonnegative_int(cycles, "latency cycles"),
            _nonnegative_int(event_id, "latency event ID"),
        ))
    if not checked:
        raise CurrentCAVEvaluationError("latency population is empty")
    checked.sort()
    count = len(checked)

    def rank(fraction: float) -> int:
        return checked[int(math.ceil(fraction * count)) - 1][0]

    return LatencySummary(
        count,
        math.fsum(float(row[0]) for row in checked) / count,
        rank(0.50),
        rank(0.95),
        rank(0.99),
        checked[-1][0],
    )


def _ordered_sum(events: Sequence[CAVEventEvaluation], field: str) -> float:
    return math.fsum(
        float(getattr(event, field))
        for event in sorted(events, key=lambda item: item.decision.event_id)
    )


def _effect(events: Sequence[CAVEventEvaluation]) -> float:
    sensor = _ordered_sum(events, "sensor_loss")
    if not math.isfinite(sensor) or sensor <= 0.0:
        raise CurrentCAVEvaluationError("sensor loss denominator must be positive")
    result = 1.0 - _ordered_sum(events, "policy_loss") / sensor
    if not math.isfinite(result):
        raise CurrentCAVEvaluationError("current-CAV effect is non-finite")
    return result


@dataclass(frozen=True)
class CAVWindowEvaluation:
    registry: NeutralRegistryWindow
    input_events: Tuple[NeutralEventInput, ...]
    input_poses: Tuple[NeutralPoseInput, ...]
    simulation: SimulationResult
    query_events: Tuple[CAVEventEvaluation, ...]
    query_decisions_sha256: str

    @property
    def accepted_events(self) -> int:
        return len(self.query_events)

    @property
    def enabled_events(self) -> int:
        return sum(event.enabled for event in self.query_events)

    @property
    def quality_waste_events(self) -> int:
        return sum(event.quality_waste for event in self.query_events)

    @property
    def sensor_loss_sum(self) -> float:
        return _ordered_sum(self.query_events, "sensor_loss")

    @property
    def policy_loss_sum(self) -> float:
        return _ordered_sum(self.query_events, "policy_loss")

    @property
    def all_event_effect(self) -> float:
        return _effect(self.query_events)

    @property
    def positive_window(self) -> bool:
        return self.all_event_effect > _POSITIVE_WINDOW_THRESHOLD

    @property
    def enable_rate(self) -> float:
        return float(self.enabled_events) / self.accepted_events

    @property
    def quality_waste_rate(self) -> Optional[float]:
        if self.enabled_events == 0:
            return None
        return float(self.quality_waste_events) / self.enabled_events

    @property
    def occurrence_latency(self) -> LatencySummary:
        return _latency_summary(
            (event.decision.event_id, event.occurrence_latency_cycles)
            for event in self.query_events
        )

    @property
    def added_latency(self) -> LatencySummary:
        return _latency_summary(
            (event.decision.event_id, event.added_latency_cycles)
            for event in self.query_events
        )


@dataclass(frozen=True)
class CAVRegistryEvaluation:
    registry_sha256: str
    neutral_input_sha256: str
    windows: Tuple[CAVWindowEvaluation, ...]

    @property
    def query_events(self) -> Tuple[CAVEventEvaluation, ...]:
        return tuple(event for window in self.windows for event in window.query_events)

    @property
    def accepted_events(self) -> int:
        return len(self.query_events)

    @property
    def enabled_events(self) -> int:
        return sum(event.enabled for event in self.query_events)

    @property
    def quality_waste_events(self) -> int:
        return sum(event.quality_waste for event in self.query_events)

    @property
    def all_event_effect(self) -> float:
        return _effect(self.query_events)

    @property
    def positive_windows(self) -> int:
        return sum(window.positive_window for window in self.windows)

    @property
    def enable_rate(self) -> float:
        return float(self.enabled_events) / self.accepted_events

    @property
    def quality_waste_rate(self) -> Optional[float]:
        if self.enabled_events == 0:
            return None
        return float(self.quality_waste_events) / self.enabled_events

    @property
    def occurrence_latency(self) -> LatencySummary:
        return _latency_summary(
            (event.decision.event_id, event.occurrence_latency_cycles)
            for event in self.query_events
        )

    @property
    def added_latency(self) -> LatencySummary:
        return _latency_summary(
            (event.decision.event_id, event.added_latency_cycles)
            for event in self.query_events
        )


def _world_shadow(
    decision: DecisionRecord,
    sensor_ray: Ray,
    poses_by_id: Mapping[int, NeutralPoseInput],
) -> Ray:
    occurrence = tuple(zip(
        decision.occurrence_pose_ids,
        decision.occurrence_pose_timestamps_ns,
        decision.occurrence_pose_commit_cycles,
        decision.occurrence_pose_sha256,
    ))
    if not occurrence:
        raise CurrentCAVEvaluationError(
            "current-CAV world shadow lacks an occurrence pose"
        )
    if decision.disposition_reason == "causal_cav":
        selected = occurrence[-2:]
        if len(selected) != 2:
            raise CurrentCAVEvaluationError("causal CAV lacks its two-pose snapshot")
        samples = tuple(
            RecoveryPoseSample(timestamp, commit, poses_by_id[pose_id].quaternion_xyzw)
            for pose_id, timestamp, commit, _ in selected
        )
        recovered = recover_causal_cav(
            samples, decision.event_timestamp_ns, decision.occurrence_cycle
        )
        if recovered.mode is not RecoveryMode.CAV or recovered.quaternion_xyzw is None:
            raise CurrentCAVEvaluationError(
                "frozen geometry disagrees with the cycle-model CAV decision"
            )
        quaternion = recovered.quaternion_xyzw
    else:
        # This is deliberately the score-only occurrence ZOH shadow for both
        # fresh fallback and raw bypass.  Runtime enable still determines
        # whether policy loss takes this world loss or the sensor loss.
        quaternion = poses_by_id[occurrence[-1][0]].quaternion_xyzw
    return rotate_sensor_ray_to_world(quaternion, sensor_ray)


def _validate_registry_window(value: object) -> NeutralRegistryWindow:
    _exact_dataclass(value, NeutralRegistryWindow, _REGISTRY_FIELDS, "registry")
    window = value  # type: ignore[assignment]
    _nonempty_text(window.window_id, "window_id")
    start = _nonnegative_int(
        window.warmup_start_ns_inclusive, "warmup_start_ns_inclusive"
    )
    query = _nonnegative_int(
        window.query_start_ns_inclusive, "query_start_ns_inclusive"
    )
    end = _nonnegative_int(
        window.query_end_ns_exclusive, "query_end_ns_exclusive"
    )
    if not start < query < end:
        raise CurrentCAVEvaluationError("neutral registry bounds are not increasing")
    return window


def _validate_event_input(value: object) -> NeutralEventInput:
    _exact_dataclass(value, NeutralEventInput, _EVENT_FIELDS, "event")
    event = value  # type: ignore[assignment]
    _nonnegative_int(event.event_id, "event_id")
    _nonnegative_int(event.timestamp_ns, "event timestamp")
    if isinstance(event.polarity, bool) or event.polarity not in (0, 1):
        raise CurrentCAVEvaluationError("event polarity must be integer zero or one")
    if type(event.is_query) is not bool or type(event.transform_guard_valid) is not bool:
        raise CurrentCAVEvaluationError("event flags must be exact bools")
    if type(event.sensor_ray) is not tuple:
        raise CurrentCAVEvaluationError("sensor ray must retain its canonical tuple type")
    sensor_ray = _unit_ray(event.sensor_ray, "sensor ray")
    _nonnegative_int(event.causal_pose_source_index, "causal pose source index")
    _sha256(event.event_content_sha256, "event content digest")
    expected = canonical_event_content_sha256(
        event.event_id,
        event.timestamp_ns,
        event.polarity,
        event.is_query,
        sensor_ray,
        event.causal_pose_source_index,
        event.transform_guard_valid,
    )
    if event.event_content_sha256 != expected:
        raise CurrentCAVEvaluationError("event content digest differs")
    return event


def _validate_pose_input(value: object) -> NeutralPoseInput:
    _exact_dataclass(value, NeutralPoseInput, _POSE_FIELDS, "pose")
    pose = value  # type: ignore[assignment]
    _nonnegative_int(pose.pose_id, "pose_id")
    _nonnegative_int(pose.timestamp_ns, "pose timestamp")
    _signed_int(pose.commit_cycle, "commit cycle")
    if type(pose.quaternion_xyzw) is not tuple:
        raise CurrentCAVEvaluationError(
            "pose quaternion must retain its canonical tuple type"
        )
    quaternion = _quaternion(pose.quaternion_xyzw, "pose quaternion")
    _sha256(pose.pose_sha256, "pose digest")
    if type(pose.value_valid) is not bool or type(pose.arithmetic_valid) is not bool:
        raise CurrentCAVEvaluationError("pose validity flags must be bool")
    expected = canonical_pose_value_sha256(
        pose.pose_id, pose.timestamp_ns, quaternion
    )
    if pose.pose_sha256 != expected:
        raise CurrentCAVEvaluationError("pose content digest differs")
    return pose


def _neutral_input_mapping(
    windows: Sequence[CAVWindowEvaluation],
) -> Mapping[str, object]:
    return {
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": [window.registry.to_mapping() for window in windows],
        "windows": [
            {
                "window_id": window.registry.window_id,
                "events": [
                    event.to_content_mapping() for event in window.input_events
                ],
                "poses": [pose.to_content_mapping() for pose in window.input_poses],
            }
            for window in windows
        ],
    }


def verify_current_cav_evaluation_integrity(
    evaluation: CAVRegistryEvaluation,
) -> str:
    """Recompute the aggregate neutral-input binding from retained inputs."""

    _exact_dataclass(
        evaluation,
        CAVRegistryEvaluation,
        _CAV_REGISTRY_FIELDS,
        "registry evaluation",
    )
    _sha256(evaluation.registry_sha256, "registry digest")
    _sha256(evaluation.neutral_input_sha256, "neutral input digest")
    if type(evaluation.windows) is not tuple or not evaluation.windows:
        raise CurrentCAVEvaluationError("evaluation windows must be a non-empty tuple")
    identifiers = []
    for window in evaluation.windows:
        _exact_dataclass(
            window,
            CAVWindowEvaluation,
            _CAV_WINDOW_FIELDS,
            "window evaluation",
        )
        registry = _validate_registry_window(window.registry)
        if type(window.input_events) is not tuple or not window.input_events:
            raise CurrentCAVEvaluationError("retained event inputs must be a tuple")
        if type(window.input_poses) is not tuple or not window.input_poses:
            raise CurrentCAVEvaluationError("retained pose inputs must be a tuple")
        events = tuple(_validate_event_input(event) for event in window.input_events)
        tuple(_validate_pose_input(pose) for pose in window.input_poses)
        for event in events:
            if not (
                registry.warmup_start_ns_inclusive
                <= event.timestamp_ns
                < registry.query_end_ns_exclusive
            ):
                raise CurrentCAVEvaluationError(
                    "retained event lies outside registry bounds"
                )
            if event.is_query != (
                registry.query_start_ns_inclusive <= event.timestamp_ns
            ):
                raise CurrentCAVEvaluationError(
                    "retained event query flag differs from bounds"
                )
        if window.simulation.window_id != registry.window_id:
            raise CurrentCAVEvaluationError("simulation window identity differs")
        identifiers.append(registry.window_id)
    if len(set(identifiers)) != len(identifiers):
        raise CurrentCAVEvaluationError("evaluation window IDs are duplicated")
    registry_mapping = [window.registry.to_mapping() for window in evaluation.windows]
    expected_registry_sha256 = canonical_sha256(registry_mapping)
    if evaluation.registry_sha256 != expected_registry_sha256:
        raise CurrentCAVEvaluationError("registry digest differs")
    expected_input_sha256 = canonical_sha256(
        _neutral_input_mapping(evaluation.windows)
    )
    if evaluation.neutral_input_sha256 != expected_input_sha256:
        raise CurrentCAVEvaluationError("neutral input digest differs")
    return expected_input_sha256


def evaluate_current_cav_window(
    registry: NeutralRegistryWindow,
    events: Sequence[NeutralEventInput],
    poses: Sequence[NeutralPoseInput],
) -> CAVWindowEvaluation:
    """Evaluate one neutral window with frozen CAV and two causal banks."""

    registry = _validate_registry_window(registry)
    event_values = tuple(_validate_event_input(row) for row in events)
    pose_values = tuple(_validate_pose_input(row) for row in poses)
    if not event_values:
        raise CurrentCAVEvaluationError("event stream must not be empty")
    if not pose_values:
        raise CurrentCAVEvaluationError("pose stream must not be empty")
    for event in event_values:
        if not (
            registry.warmup_start_ns_inclusive
            <= event.timestamp_ns
            < registry.query_end_ns_exclusive
        ):
            raise CurrentCAVEvaluationError("event lies outside neutral registry bounds")
        expected_query = registry.query_start_ns_inclusive <= event.timestamp_ns
        if event.is_query != expected_query:
            raise CurrentCAVEvaluationError("event query label differs from registry bounds")
    if not any(event.is_query for event in event_values):
        raise CurrentCAVEvaluationError("neutral window has no query events")

    cycle_events = tuple(
        Event(
            event.event_id,
            event.timestamp_ns,
            transform_guard_valid=event.transform_guard_valid,
            causal_pose_index=event.causal_pose_source_index,
        )
        for event in event_values
    )
    cycle_poses = tuple(
        PosePacket(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            PoseSource.DATASET,
            pose.pose_sha256,
            pose.value_valid,
            pose.arithmetic_valid,
        )
        for pose in pose_values
    )
    simulation = run_cycle_model(
        window_id=registry.window_id,
        window_start_ns=registry.warmup_start_ns_inclusive,
        arm=Arm.CAUSAL_CAV,
        events=cycle_events,
        poses=cycle_poses,
    )
    if simulation.synthetic_test_mode or not simulation.all_event_pose_indices_verified:
        raise CurrentCAVEvaluationError("cycle model did not verify every pose index")
    if len(simulation.records) != len(event_values):
        raise CurrentCAVEvaluationError("cycle model changed event cardinality")

    poses_by_id = {pose.pose_id: pose for pose in pose_values}
    world_rays = tuple(
        _world_shadow(decision, event.sensor_ray, poses_by_id)
        for event, decision in zip(event_values, simulation.records)
    )
    config = CausalReferenceConfig(
        _REFERENCE_CAPACITY_PER_POLARITY, _REFERENCE_MAX_AGE_NS
    )
    sensor_scores = CausalReferenceBank(config).process(
        ReferenceObservation(
            event.event_id, event.timestamp_ns, event.polarity, event.sensor_ray
        )
        for event in event_values
    )
    world_scores = CausalReferenceBank(config).process(
        ReferenceObservation(
            event.event_id, event.timestamp_ns, event.polarity, world_ray
        )
        for event, world_ray in zip(event_values, world_rays)
    )
    sensor_by_id = {score.event_id: score for score in sensor_scores}
    world_by_id = {score.event_id: score for score in world_scores}
    baseline_by_id = dict(zip(
        (event.event_id for event in event_values),
        simulation.always_bypass_retire_cycles,
    ))

    query = []
    for event, decision in zip(event_values, simulation.records):
        if not event.is_query:
            continue
        sensor = sensor_by_id[event.event_id]
        world = world_by_id[event.event_id]
        if (
            not sensor.reference_available
            or sensor.angular_cost_rad is None
            or sensor.reference_event_id is None
            or not world.reference_available
            or world.angular_cost_rad is None
            or world.reference_event_id is None
        ):
            raise CurrentCAVEvaluationError(
                "query event lacks a same-frame causal reference"
            )
        enabled = decision.disposition == "corrected_world_ray"
        sensor_loss = float(sensor.angular_cost_rad)
        world_loss = float(world.angular_cost_rad)
        policy_loss = world_loss if enabled else sensor_loss
        baseline_cycle = baseline_by_id[event.event_id]
        if decision.retire_cycle < baseline_cycle:
            raise CurrentCAVEvaluationError("CAV retires before the bypass baseline")
        query.append(CAVEventEvaluation(
            decision,
            sensor_loss,
            world_loss,
            policy_loss,
            enabled,
            enabled and world_loss >= sensor_loss,
            sensor.reference_event_id,
            world.reference_event_id,
            decision.retire_cycle - decision.occurrence_cycle,
            decision.retire_cycle - baseline_cycle,
        ))
    query_decisions = [event.decision.to_mapping() for event in query]
    result = CAVWindowEvaluation(
        registry,
        event_values,
        pose_values,
        simulation,
        tuple(query),
        canonical_sha256(query_decisions),
    )
    # Fail at the window boundary, not later during aggregate reporting.
    _ = result.all_event_effect
    return result


def evaluate_current_cav_registry(
    registry: Sequence[NeutralRegistryWindow],
    event_streams: Mapping[str, Sequence[NeutralEventInput]],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
) -> CAVRegistryEvaluation:
    """Evaluate an ordered neutral registry without accepting selector data."""

    windows = tuple(_validate_registry_window(row) for row in registry)
    if not windows:
        raise CurrentCAVEvaluationError("registry must contain neutral windows")
    identifiers = tuple(window.window_id for window in windows)
    expected = set(identifiers)
    if set(event_streams) != expected or set(pose_streams) != expected:
        raise CurrentCAVEvaluationError("stream window IDs differ from neutral registry")
    checked_events = {
        window.window_id: tuple(
            _validate_event_input(row) for row in event_streams[window.window_id]
        )
        for window in windows
    }
    checked_poses = {
        window.window_id: tuple(
            _validate_pose_input(row) for row in pose_streams[window.window_id]
        )
        for window in windows
    }
    results = tuple(
        evaluate_current_cav_window(
            window, checked_events[window.window_id], checked_poses[window.window_id]
        )
        for window in windows
    )
    event_ids = tuple(
        event.decision.event_id for window in results for event in window.query_events
    )
    if len(set(event_ids)) != len(event_ids):
        raise CurrentCAVEvaluationError("query event IDs repeat across registry windows")
    registry_mapping = [window.to_mapping() for window in windows]
    result = CAVRegistryEvaluation(
        canonical_sha256(registry_mapping),
        canonical_sha256(_neutral_input_mapping(results)),
        results,
    )
    verify_current_cav_evaluation_integrity(result)
    _ = result.all_event_effect
    return result
