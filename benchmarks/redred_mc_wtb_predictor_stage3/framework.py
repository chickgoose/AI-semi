"""Candidate-neutral, score-blind framework for Stage-3 pose predictors.

The wrapper owns causal visibility, immutable state publication, ordered-event
conservation, equal-timestamp atomicity, and the common fallback chain.  A
candidate implementation receives no event/window/sequence identity, selector
label, query membership, score, or outcome value.

Candidate algorithms are intentionally absent from this module.  Later RG3,
DSPB, and PLL packages may implement :class:`CandidateModel`, but they must use
the immutable state payload supplied by this framework as their only mutable
history.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import math
import re
from typing import Callable, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


Ray = Tuple[float, float, float]
QuaternionXYZW = Tuple[float, float, float, float]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FRESH_ZOH_MAX_AGE_NS = 1_000_000


class PredictorFrameworkError(ValueError):
    """A common Stage-3 causality, identity, or fallback invariant failed."""


def _nonnegative_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PredictorFrameworkError("%s must be a non-negative integer" % where)
    return value


def _nonempty_text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise PredictorFrameworkError("%s must be non-empty text" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise PredictorFrameworkError("%s must be lowercase SHA-256" % where)
    return value


def _finite_tuple(value: object, length: int, where: str) -> Tuple[float, ...]:
    if type(value) is not tuple or len(value) != length:  # type: ignore[arg-type]
        raise PredictorFrameworkError(
            "%s must be an immutable %d-tuple" % (where, length)
        )
    output = []
    for index, component in enumerate(value):  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise PredictorFrameworkError("%s[%d] must be finite" % (where, index))
        converted = float(component)
        if not math.isfinite(converted):
            raise PredictorFrameworkError("%s[%d] must be finite" % (where, index))
        output.append(converted)
    return tuple(output)


def _unit_tuple(
    value: object, length: int, where: str
) -> Tuple[float, ...]:
    output = _finite_tuple(value, length, where)
    norm = math.sqrt(math.fsum(component * component for component in output))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise PredictorFrameworkError("%s must have unit norm" % where)
    return output


@dataclass(frozen=True)
class EventEnvelope:
    """Trusted wrapper metadata for one conserved input event.

    Identity, absolute time, cycles, and query membership never enter the
    candidate-facing :class:`PredictorEventView`.
    """

    event_id: int
    timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int
    polarity: int
    sensor_ray: Ray
    is_query: bool
    transform_guard_valid: bool = True

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "event ID")
        _nonnegative_int(self.timestamp_ns, "event timestamp")
        occurrence = _nonnegative_int(self.occurrence_cycle, "occurrence cycle")
        decision = _nonnegative_int(self.decision_cycle, "decision cycle")
        if occurrence >= decision:
            raise PredictorFrameworkError(
                "event record must be visible strictly before its decision edge"
            )
        if isinstance(self.polarity, bool) or self.polarity not in (0, 1):
            raise PredictorFrameworkError("event polarity must be integer zero or one")
        object.__setattr__(
            self, "sensor_ray", _unit_tuple(self.sensor_ray, 3, "sensor ray")
        )
        if type(self.is_query) is not bool or type(self.transform_guard_valid) is not bool:
            raise PredictorFrameworkError("event flags must be exact bools")


@dataclass(frozen=True)
class PoseEnvelope:
    """Trusted wrapper metadata for one supplied authoritative pose commit."""

    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: QuaternionXYZW
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        _nonnegative_int(self.pose_id, "pose ID")
        _nonnegative_int(self.timestamp_ns, "pose timestamp")
        _nonnegative_int(self.commit_cycle, "pose commit cycle")
        object.__setattr__(
            self,
            "quaternion_xyzw",
            _unit_tuple(self.quaternion_xyzw, 4, "pose quaternion"),
        )
        if type(self.value_valid) is not bool or type(self.arithmetic_valid) is not bool:
            raise PredictorFrameworkError("pose validity flags must be exact bools")


@dataclass(frozen=True)
class PredictorPoseView:
    """Identity-free pose value exposed to a candidate for one event."""

    quaternion_xyzw: QuaternionXYZW
    age_ns: int
    inter_pose_delta_ns: Optional[int]
    value_valid: bool
    arithmetic_valid: bool


@dataclass(frozen=True)
class PredictorEventView:
    """Complete score-blind and identity-free candidate input for one event."""

    polarity: int
    sensor_ray: Ray
    transform_guard_valid: bool
    visible_poses: Tuple[PredictorPoseView, ...]

    @property
    def latest_pose_age_ns(self) -> Optional[int]:
        if not self.visible_poses:
            return None
        return self.visible_poses[-1].age_ns


@dataclass(frozen=True)
class PredictorPoseCommitView:
    """Identity-free authoritative pose information published after a cycle."""

    quaternion_xyzw: QuaternionXYZW
    inter_pose_delta_ns: Optional[int]
    value_valid: bool
    arithmetic_valid: bool


@dataclass(frozen=True)
class PredictionOutput:
    """Orientation selected for geometry; ``None`` means sensor-fixed bypass."""

    quaternion_xyzw: Optional[QuaternionXYZW]

    def __post_init__(self) -> None:
        if self.quaternion_xyzw is not None:
            object.__setattr__(
                self,
                "quaternion_xyzw",
                _unit_tuple(self.quaternion_xyzw, 4, "prediction quaternion"),
            )

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "quaternion_xyzw": (
                None
                if self.quaternion_xyzw is None
                else list(self.quaternion_xyzw)
            )
        }


@dataclass(frozen=True)
class CandidateAttempt:
    """Structured candidate result; exceptions are protocol failures, not fallback."""

    output: Optional[PredictionOutput]
    failure_reason: Optional[str]
    used_pose_slots: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (self.output is None) == (self.failure_reason is None):
            raise PredictorFrameworkError(
                "candidate attempt must contain exactly one of output or failure"
            )
        if self.failure_reason is not None:
            _nonempty_text(self.failure_reason, "candidate failure reason")
            if self.used_pose_slots:
                raise PredictorFrameworkError("failed candidate used pose slots")
        elif self.output is not None and self.output.quaternion_xyzw is None:
            raise PredictorFrameworkError("candidate success must provide orientation")
        if type(self.used_pose_slots) is not tuple:
            raise PredictorFrameworkError("used pose slots must be an immutable tuple")
        for slot in self.used_pose_slots:
            _nonnegative_int(slot, "used pose slot")
        if len(set(self.used_pose_slots)) != len(self.used_pose_slots):
            raise PredictorFrameworkError("used pose slots repeat")

    @classmethod
    def success(
        cls,
        quaternion_xyzw: QuaternionXYZW,
        used_pose_slots: Tuple[int, ...] = (),
    ) -> "CandidateAttempt":
        return cls(PredictionOutput(quaternion_xyzw), None, used_pose_slots)

    @classmethod
    def failure(cls, reason: str) -> "CandidateAttempt":
        return cls(None, reason, ())

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "output": None if self.output is None else self.output.to_mapping(),
            "failure_reason": self.failure_reason,
            "used_pose_slots": list(self.used_pose_slots),
        }


@dataclass(frozen=True)
class FallbackContext:
    """Trusted context visible only to frozen baseline/fallback hooks."""

    event: EventEnvelope
    visible_poses: Tuple[PoseEnvelope, ...]

    @property
    def latest_pose_age_ns(self) -> Optional[int]:
        if not self.visible_poses:
            return None
        return self.event.timestamp_ns - self.visible_poses[-1].timestamp_ns


@dataclass(frozen=True)
class FallbackAttempt:
    output: Optional[PredictionOutput]
    failure_reason: Optional[str]
    used_pose_ids: Tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (self.output is None) == (self.failure_reason is None):
            raise PredictorFrameworkError(
                "fallback attempt must contain exactly one of output or failure"
            )
        if self.failure_reason is not None:
            _nonempty_text(self.failure_reason, "fallback failure reason")
            if self.used_pose_ids:
                raise PredictorFrameworkError("failed fallback used pose IDs")
        if type(self.used_pose_ids) is not tuple:
            raise PredictorFrameworkError("fallback pose IDs must be a tuple")
        for pose_id in self.used_pose_ids:
            _nonnegative_int(pose_id, "fallback pose ID")
        if len(set(self.used_pose_ids)) != len(self.used_pose_ids):
            raise PredictorFrameworkError("fallback pose IDs repeat")

    @classmethod
    def success(
        cls,
        quaternion_xyzw: Optional[QuaternionXYZW],
        used_pose_ids: Tuple[int, ...] = (),
    ) -> "FallbackAttempt":
        return cls(PredictionOutput(quaternion_xyzw), None, used_pose_ids)

    @classmethod
    def failure(cls, reason: str) -> "FallbackAttempt":
        return cls(None, reason, ())


FallbackHook = Callable[[FallbackContext], FallbackAttempt]


def _default_sensor_fixed(_: FallbackContext) -> FallbackAttempt:
    return FallbackAttempt.success(None)


@dataclass(frozen=True)
class FallbackHooks:
    """Frozen hooks for exact current-CAV, fresh-ZOH, and raw bypass behavior."""

    current_cav: FallbackHook
    fresh_zoh: FallbackHook
    sensor_fixed: FallbackHook = _default_sensor_fixed
    fresh_zoh_max_age_ns: int = _FRESH_ZOH_MAX_AGE_NS

    def __post_init__(self) -> None:
        if not callable(self.current_cav) or not callable(self.fresh_zoh):
            raise PredictorFrameworkError("fallback hooks must be callable")
        if not callable(self.sensor_fixed):
            raise PredictorFrameworkError("sensor-fixed hook must be callable")
        age = _nonnegative_int(self.fresh_zoh_max_age_ns, "fresh-ZOH maximum age")
        if age != _FRESH_ZOH_MAX_AGE_NS:
            raise PredictorFrameworkError("fresh-ZOH maximum age must remain exactly 1 ms")


@dataclass(frozen=True)
class PredictorStateVersion:
    """Immutable, content-addressed candidate state published at a future edge."""

    model_id: str
    version_id: int
    effective_cycle: int
    parent_state_sha256: Optional[str]
    transition_reason: str
    payload: bytes
    state_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _nonempty_text(self.model_id, "model ID")
        _nonnegative_int(self.version_id, "state version ID")
        _nonnegative_int(self.effective_cycle, "state effective cycle")
        if self.parent_state_sha256 is not None:
            _sha256(self.parent_state_sha256, "parent state digest")
        _nonempty_text(self.transition_reason, "state transition reason")
        if type(self.payload) is not bytes:
            raise PredictorFrameworkError("state payload must be immutable bytes")
        body = {
            "model_id": self.model_id,
            "version_id": self.version_id,
            "effective_cycle": self.effective_cycle,
            "parent_state_sha256": self.parent_state_sha256,
            "transition_reason": self.transition_reason,
            "payload_hex": self.payload.hex(),
        }
        object.__setattr__(self, "state_sha256", canonical_sha256(body))


@dataclass(frozen=True)
class StateUpdate:
    payload: bytes
    reason: str

    def __post_init__(self) -> None:
        if type(self.payload) is not bytes:
            raise PredictorFrameworkError("state update payload must be immutable bytes")
        _nonempty_text(self.reason, "state update reason")


@dataclass(frozen=True)
class CandidateStateView:
    """Opaque state bytes exposed without wrapper version/cycle/identity data."""

    payload: bytes

    def __post_init__(self) -> None:
        if type(self.payload) is not bytes:
            raise PredictorFrameworkError("candidate state payload must be immutable bytes")


@dataclass(frozen=True)
class EventClusterCommitView:
    """One equal-timestamp cluster, stripped of identity and absolute time."""

    events: Tuple[PredictorEventView, ...]
    candidate_attempts: Tuple[Optional[CandidateAttempt], ...]

    def __post_init__(self) -> None:
        if not self.events or len(self.events) != len(self.candidate_attempts):
            raise PredictorFrameworkError("event cluster commit cardinality differs")


@dataclass(frozen=True)
class CycleCommitView:
    """All causal observations from one edge, published atomically afterwards."""

    pose_commits: Tuple[PredictorPoseCommitView, ...]
    event_clusters: Tuple[EventClusterCommitView, ...]


class CandidateModel(ABC):
    """Interface only; candidate algorithms live in separate packages.

    Implementations must be behaviorally stateless.  All persistent history is
    encoded in the immutable ``state.payload`` and returned through
    :meth:`commit_cycle`.
    """

    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def configuration_sha256(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def initial_state_payload(self) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def predict(
        self, event: PredictorEventView, state: CandidateStateView
    ) -> CandidateAttempt:
        raise NotImplementedError

    @abstractmethod
    def commit_cycle(
        self, observations: CycleCommitView, state: CandidateStateView
    ) -> Optional[StateUpdate]:
        """Return state effective on the following cycle, or ``None``."""

        raise NotImplementedError


class DecisionRoute(Enum):
    CANDIDATE = "candidate"
    CURRENT_CAV = "current_cav"
    FRESH_ZOH = "fresh_zoh"
    SENSOR_FIXED = "sensor_fixed"


@dataclass(frozen=True)
class PredictorDecision:
    """Append-only wrapper receipt for one conserved event."""

    event_id: int
    event_timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int
    is_query: bool
    model_id: str
    configuration_sha256: str
    state_version_id: int
    state_sha256: str
    route: DecisionRoute
    candidate_attempted: bool
    candidate_used: bool
    output: PredictionOutput
    used_pose_ids: Tuple[int, ...]
    fallback_trace: Tuple[str, ...]
    decision_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "decision event ID")
        _nonnegative_int(self.event_timestamp_ns, "decision event timestamp")
        _nonnegative_int(self.occurrence_cycle, "decision occurrence cycle")
        _nonnegative_int(self.decision_cycle, "decision cycle")
        if type(self.is_query) is not bool:
            raise PredictorFrameworkError("decision query flag must be bool")
        _nonempty_text(self.model_id, "decision model ID")
        _sha256(self.configuration_sha256, "candidate configuration digest")
        _nonnegative_int(self.state_version_id, "decision state version")
        _sha256(self.state_sha256, "decision state digest")
        if type(self.route) is not DecisionRoute:
            raise PredictorFrameworkError("decision route differs")
        if type(self.candidate_attempted) is not bool or type(self.candidate_used) is not bool:
            raise PredictorFrameworkError("candidate flags must be bool")
        if self.candidate_used != (self.route is DecisionRoute.CANDIDATE):
            raise PredictorFrameworkError("candidate-use flag differs from route")
        if self.candidate_used and not self.candidate_attempted:
            raise PredictorFrameworkError("candidate used without an attempt")
        if type(self.used_pose_ids) is not tuple or type(self.fallback_trace) is not tuple:
            raise PredictorFrameworkError("decision evidence must be immutable tuples")
        for reason in self.fallback_trace:
            _nonempty_text(reason, "fallback trace reason")
        body = self.to_mapping(include_digest=False)
        object.__setattr__(self, "decision_sha256", canonical_sha256(body))

    def to_mapping(self, include_digest: bool = True) -> Mapping[str, object]:
        output = {
            "event_id": self.event_id,
            "event_timestamp_ns": self.event_timestamp_ns,
            "occurrence_cycle": self.occurrence_cycle,
            "decision_cycle": self.decision_cycle,
            "is_query": self.is_query,
            "model_id": self.model_id,
            "configuration_sha256": self.configuration_sha256,
            "state_version_id": self.state_version_id,
            "state_sha256": self.state_sha256,
            "route": self.route.value,
            "candidate_attempted": self.candidate_attempted,
            "candidate_used": self.candidate_used,
            "output": self.output.to_mapping(),
            "used_pose_ids": list(self.used_pose_ids),
            "fallback_trace": list(self.fallback_trace),
        }
        if include_digest:
            output = dict(output, decision_sha256=self.decision_sha256)
        return output


@dataclass(frozen=True)
class CycleStateReceipt:
    """Wrapper-only binding from causal cycle observations to future state."""

    observation_cycle: int
    prior_state_version_id: int
    prior_state_sha256: str
    pose_commit_ids: Tuple[int, ...]
    event_cluster_ids: Tuple[Tuple[int, ...], ...]
    next_state_version_id: Optional[int]
    next_state_sha256: Optional[str]
    next_state_effective_cycle: Optional[int]
    receipt_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        cycle = _nonnegative_int(self.observation_cycle, "receipt observation cycle")
        _nonnegative_int(self.prior_state_version_id, "receipt prior state version")
        _sha256(self.prior_state_sha256, "receipt prior state digest")
        if type(self.pose_commit_ids) is not tuple or type(self.event_cluster_ids) is not tuple:
            raise PredictorFrameworkError("cycle receipt identities must be tuples")
        for pose_id in self.pose_commit_ids:
            _nonnegative_int(pose_id, "cycle receipt pose ID")
        for cluster in self.event_cluster_ids:
            if type(cluster) is not tuple or not cluster:
                raise PredictorFrameworkError("cycle receipt event cluster differs")
            for event_id in cluster:
                _nonnegative_int(event_id, "cycle receipt event ID")
        next_values = (
            self.next_state_version_id,
            self.next_state_sha256,
            self.next_state_effective_cycle,
        )
        if any(value is None for value in next_values):
            if not all(value is None for value in next_values):
                raise PredictorFrameworkError("cycle receipt next-state tuple is partial")
        else:
            _nonnegative_int(self.next_state_version_id, "receipt next state version")
            _sha256(self.next_state_sha256, "receipt next state digest")
            if self.next_state_effective_cycle != cycle + 1:
                raise PredictorFrameworkError(
                    "cycle receipt state is not effective on the following cycle"
                )
        object.__setattr__(
            self, "receipt_sha256", canonical_sha256(self.to_mapping(False))
        )

    def to_mapping(self, include_digest: bool = True) -> Mapping[str, object]:
        output = {
            "observation_cycle": self.observation_cycle,
            "prior_state_version_id": self.prior_state_version_id,
            "prior_state_sha256": self.prior_state_sha256,
            "pose_commit_ids": list(self.pose_commit_ids),
            "event_cluster_ids": [list(cluster) for cluster in self.event_cluster_ids],
            "next_state_version_id": self.next_state_version_id,
            "next_state_sha256": self.next_state_sha256,
            "next_state_effective_cycle": self.next_state_effective_cycle,
        }
        if include_digest:
            output = dict(output, receipt_sha256=self.receipt_sha256)
        return output


@dataclass(frozen=True)
class PredictorRunResult:
    model_id: str
    configuration_sha256: str
    ordered_event_ids: Tuple[int, ...]
    ordered_query_event_ids: Tuple[int, ...]
    decisions: Tuple[PredictorDecision, ...]
    state_versions: Tuple[PredictorStateVersion, ...]
    cycle_state_receipts: Tuple[CycleStateReceipt, ...]
    decision_records_sha256: str
    cycle_state_receipts_sha256: str

    @property
    def query_decisions(self) -> Tuple[PredictorDecision, ...]:
        return tuple(decision for decision in self.decisions if decision.is_query)


def _validate_input_order(
    events: Tuple[EventEnvelope, ...], poses: Tuple[PoseEnvelope, ...]
) -> None:
    if not events:
        raise PredictorFrameworkError("event stream must not be empty")
    event_ids = tuple(event.event_id for event in events)
    if len(set(event_ids)) != len(event_ids):
        raise PredictorFrameworkError("event IDs repeat")
    for left, right in zip(events, events[1:]):
        if right.timestamp_ns < left.timestamp_ns:
            raise PredictorFrameworkError("event timestamps are not ordered")
        if right.decision_cycle < left.decision_cycle:
            raise PredictorFrameworkError("event decision cycles are not ordered")
        if right.timestamp_ns == left.timestamp_ns and (
            right.occurrence_cycle != left.occurrence_cycle
            or right.decision_cycle != left.decision_cycle
        ):
            raise PredictorFrameworkError(
                "equal-timestamp event cluster does not share immutable edges"
            )
    pose_ids = tuple(pose.pose_id for pose in poses)
    if len(set(pose_ids)) != len(pose_ids):
        raise PredictorFrameworkError("pose IDs repeat")
    for left, right in zip(poses, poses[1:]):
        if (right.commit_cycle, right.timestamp_ns, right.pose_id) < (
            left.commit_cycle,
            left.timestamp_ns,
            left.pose_id,
        ):
            raise PredictorFrameworkError("pose commits are not ordered")
        if right.timestamp_ns <= left.timestamp_ns:
            raise PredictorFrameworkError("pose timestamps are not strictly increasing")


def _predictor_event_view(
    event: EventEnvelope, visible: Tuple[PoseEnvelope, ...]
) -> PredictorEventView:
    views = []
    previous_timestamp = None  # type: Optional[int]
    for pose in visible:
        delta = (
            None
            if previous_timestamp is None
            else pose.timestamp_ns - previous_timestamp
        )
        views.append(PredictorPoseView(
            pose.quaternion_xyzw,
            event.timestamp_ns - pose.timestamp_ns,
            delta,
            pose.value_valid,
            pose.arithmetic_valid,
        ))
        previous_timestamp = pose.timestamp_ns
    return PredictorEventView(
        event.polarity,
        event.sensor_ray,
        event.transform_guard_valid,
        tuple(views),
    )


def _pose_commit_views(
    commits: Tuple[PoseEnvelope, ...], prior_poses: Tuple[PoseEnvelope, ...]
) -> Tuple[PredictorPoseCommitView, ...]:
    output = []
    previous_timestamp = prior_poses[-1].timestamp_ns if prior_poses else None
    for pose in commits:
        delta = (
            None
            if previous_timestamp is None
            else pose.timestamp_ns - previous_timestamp
        )
        output.append(PredictorPoseCommitView(
            pose.quaternion_xyzw,
            delta,
            pose.value_valid,
            pose.arithmetic_valid,
        ))
        previous_timestamp = pose.timestamp_ns
    return tuple(output)


def _checked_fallback_attempt(
    value: object,
    where: str,
    visible_pose_ids: Tuple[int, ...],
) -> FallbackAttempt:
    if type(value) is not FallbackAttempt:
        raise PredictorFrameworkError("%s returned the wrong attempt type" % where)
    attempt = value
    if any(pose_id not in visible_pose_ids for pose_id in attempt.used_pose_ids):
        raise PredictorFrameworkError("%s used a causally invisible pose" % where)
    return attempt


def _make_decision(
    event: EventEnvelope,
    model: CandidateModel,
    model_id: str,
    configuration_sha256: str,
    state: PredictorStateVersion,
    candidate_state: CandidateStateView,
    predictor_view: PredictorEventView,
    context: FallbackContext,
    hooks: FallbackHooks,
) -> Tuple[PredictorDecision, Optional[CandidateAttempt]]:
    visible_ids = tuple(pose.pose_id for pose in context.visible_poses)
    current = _checked_fallback_attempt(
        hooks.current_cav(context), "current-CAV hook", visible_ids
    )
    candidate = None  # type: Optional[CandidateAttempt]
    trace = []

    if current.output is not None:
        if current.output.quaternion_xyzw is None:
            raise PredictorFrameworkError(
                "current-CAV success must provide exact orientation"
            )
        candidate_value = model.predict(predictor_view, candidate_state)
        if type(candidate_value) is not CandidateAttempt:
            raise PredictorFrameworkError("candidate returned the wrong attempt type")
        candidate = candidate_value
        if candidate.output is not None:
            if any(slot >= len(context.visible_poses) for slot in candidate.used_pose_slots):
                raise PredictorFrameworkError("candidate used an invisible pose slot")
            used_ids = tuple(
                context.visible_poses[slot].pose_id
                for slot in candidate.used_pose_slots
            )
            route = DecisionRoute.CANDIDATE
            output = candidate.output
        else:
            trace.append("candidate:%s" % candidate.failure_reason)
            used_ids = current.used_pose_ids
            route = DecisionRoute.CURRENT_CAV
            output = current.output
    else:
        trace.append("current_cav:%s" % current.failure_reason)
        age = context.latest_pose_age_ns
        if age is not None and age <= hooks.fresh_zoh_max_age_ns:
            zoh = _checked_fallback_attempt(
                hooks.fresh_zoh(context), "fresh-ZOH hook", visible_ids
            )
        else:
            zoh = FallbackAttempt.failure("missing_or_stale_pose")
        if zoh.output is not None:
            latest = context.visible_poses[-1]
            if (
                zoh.output.quaternion_xyzw != latest.quaternion_xyzw
                or zoh.used_pose_ids != (latest.pose_id,)
            ):
                raise PredictorFrameworkError(
                    "fresh-ZOH hook differs from the latest visible pose"
                )
            used_ids = zoh.used_pose_ids
            route = DecisionRoute.FRESH_ZOH
            output = zoh.output
        else:
            trace.append("fresh_zoh:%s" % zoh.failure_reason)
            sensor = _checked_fallback_attempt(
                hooks.sensor_fixed(context), "sensor-fixed hook", visible_ids
            )
            if sensor.output is None:
                raise PredictorFrameworkError("sensor-fixed fallback failed")
            if sensor.output.quaternion_xyzw is not None or sensor.used_pose_ids:
                raise PredictorFrameworkError("sensor-fixed fallback is not raw bypass")
            used_ids = ()
            route = DecisionRoute.SENSOR_FIXED
            output = sensor.output

    decision = PredictorDecision(
        event.event_id,
        event.timestamp_ns,
        event.occurrence_cycle,
        event.decision_cycle,
        event.is_query,
        model_id,
        configuration_sha256,
        state.version_id,
        state.state_sha256,
        route,
        candidate is not None,
        route is DecisionRoute.CANDIDATE,
        output,
        used_ids,
        tuple(trace),
    )
    return decision, candidate


def run_candidate_neutral_predictor(
    model: CandidateModel,
    events: Sequence[EventEnvelope],
    poses: Sequence[PoseEnvelope],
    fallback_hooks: FallbackHooks,
) -> PredictorRunResult:
    """Run one score-blind candidate without dropping or reordering events."""

    if not isinstance(model, CandidateModel):
        raise PredictorFrameworkError("model must implement CandidateModel")
    model_id = _nonempty_text(model.model_id, "model ID")
    configuration_sha256 = _sha256(
        model.configuration_sha256, "candidate configuration digest"
    )
    event_values = tuple(events)
    pose_values = tuple(poses)
    if any(type(event) is not EventEnvelope for event in event_values):
        raise PredictorFrameworkError("event stream contains a non-envelope value")
    if any(type(pose) is not PoseEnvelope for pose in pose_values):
        raise PredictorFrameworkError("pose stream contains a non-envelope value")
    _validate_input_order(event_values, pose_values)
    if type(fallback_hooks) is not FallbackHooks:
        raise PredictorFrameworkError("fallback hooks have the wrong type")

    initial_payload = model.initial_state_payload()
    if type(initial_payload) is not bytes:
        raise PredictorFrameworkError("initial state payload must be immutable bytes")
    state = PredictorStateVersion(
        model_id, 0, 0, None, "initial_state", initial_payload
    )
    states = [state]
    decisions = []
    committed_poses = []
    cycle_receipts = []

    events_by_cycle = {}  # type: dict
    poses_by_cycle = {}  # type: dict
    for event in event_values:
        events_by_cycle.setdefault(event.decision_cycle, []).append(event)
    for pose in pose_values:
        poses_by_cycle.setdefault(pose.commit_cycle, []).append(pose)
    cycles = sorted(set(events_by_cycle) | set(poses_by_cycle))

    for cycle in cycles:
        if state.effective_cycle > cycle:
            raise PredictorFrameworkError("state became visible before its effective cycle")
        cycle_events = tuple(events_by_cycle.get(cycle, ()))
        cycle_poses = tuple(poses_by_cycle.get(cycle, ()))
        candidate_state = CandidateStateView(state.payload)
        cluster_views = []
        index = 0
        while index < len(cycle_events):
            timestamp = cycle_events[index].timestamp_ns
            stop = index + 1
            while (
                stop < len(cycle_events)
                and cycle_events[stop].timestamp_ns == timestamp
            ):
                stop += 1
            cluster = cycle_events[index:stop]
            predictor_views = []
            candidate_attempts = []
            for event in cluster:
                visible = tuple(
                    pose
                    for pose in committed_poses
                    if pose.commit_cycle < event.decision_cycle
                    and pose.timestamp_ns <= event.timestamp_ns
                )
                predictor_view = _predictor_event_view(event, visible)
                decision, candidate = _make_decision(
                    event,
                    model,
                    model_id,
                    configuration_sha256,
                    state,
                    candidate_state,
                    predictor_view,
                    FallbackContext(event, visible),
                    fallback_hooks,
                )
                decisions.append(decision)
                predictor_views.append(predictor_view)
                candidate_attempts.append(candidate)
            cluster_views.append(EventClusterCommitView(
                tuple(predictor_views), tuple(candidate_attempts)
            ))
            index = stop

        observations = CycleCommitView(
            _pose_commit_views(cycle_poses, tuple(committed_poses)),
            tuple(cluster_views),
        )
        prior_state = state
        update = model.commit_cycle(observations, candidate_state)
        if update is not None:
            if type(update) is not StateUpdate:
                raise PredictorFrameworkError("candidate returned the wrong state-update type")
            state = PredictorStateVersion(
                model_id,
                state.version_id + 1,
                cycle + 1,
                state.state_sha256,
                update.reason,
                update.payload,
            )
            states.append(state)
        next_state = state if state is not prior_state else None
        cycle_receipts.append(CycleStateReceipt(
            cycle,
            prior_state.version_id,
            prior_state.state_sha256,
            tuple(pose.pose_id for pose in cycle_poses),
            tuple(
                tuple(event.event_id for event in cycle_events[index:stop])
                for index, stop in _cluster_ranges(cycle_events)
            ),
            None if next_state is None else next_state.version_id,
            None if next_state is None else next_state.state_sha256,
            None if next_state is None else next_state.effective_cycle,
        ))
        committed_poses.extend(cycle_poses)

    decision_values = tuple(decisions)
    observed_ids = tuple(decision.event_id for decision in decision_values)
    expected_ids = tuple(event.event_id for event in event_values)
    if observed_ids != expected_ids:
        raise PredictorFrameworkError("ordered event conservation differs")
    observed_query_ids = tuple(
        decision.event_id for decision in decision_values if decision.is_query
    )
    expected_query_ids = tuple(event.event_id for event in event_values if event.is_query)
    if observed_query_ids != expected_query_ids:
        raise PredictorFrameworkError("ordered query population Q differs")
    digest = canonical_sha256(
        [decision.to_mapping() for decision in decision_values]
    )
    receipt_values = tuple(cycle_receipts)
    receipt_digest = canonical_sha256(
        [receipt.to_mapping() for receipt in receipt_values]
    )
    return PredictorRunResult(
        model_id,
        configuration_sha256,
        expected_ids,
        expected_query_ids,
        decision_values,
        tuple(states),
        receipt_values,
        digest,
        receipt_digest,
    )


def _cluster_ranges(
    events: Tuple[EventEnvelope, ...]
) -> Tuple[Tuple[int, int], ...]:
    ranges = []
    index = 0
    while index < len(events):
        stop = index + 1
        while stop < len(events) and events[stop].timestamp_ns == events[index].timestamp_ns:
            stop += 1
        ranges.append((index, stop))
        index = stop
    return tuple(ranges)


def verify_predictor_run_integrity(
    result: PredictorRunResult,
    events: Sequence[EventEnvelope],
    poses: Sequence[PoseEnvelope],
) -> str:
    """Verify append-only decisions, Q identity, and the immutable state chain."""

    if type(result) is not PredictorRunResult:
        raise PredictorFrameworkError("run result has the wrong type")
    expected_events = tuple(events)
    expected_poses = tuple(poses)
    _validate_input_order(expected_events, expected_poses)
    expected_ids = tuple(event.event_id for event in expected_events)
    expected_query_ids = tuple(event.event_id for event in expected_events if event.is_query)
    if result.ordered_event_ids != expected_ids:
        raise PredictorFrameworkError("run event identity differs")
    if result.ordered_query_event_ids != expected_query_ids:
        raise PredictorFrameworkError("run query identity differs")
    if tuple(decision.event_id for decision in result.decisions) != expected_ids:
        raise PredictorFrameworkError("decision order differs")
    if tuple(decision.event_id for decision in result.query_decisions) != expected_query_ids:
        raise PredictorFrameworkError("decision Q differs")
    prior = None  # type: Optional[PredictorStateVersion]
    for index, state in enumerate(result.state_versions):
        if state.version_id != index:
            raise PredictorFrameworkError("state version order differs")
        if prior is None:
            if state.parent_state_sha256 is not None:
                raise PredictorFrameworkError("initial state has a parent")
        elif (
            state.parent_state_sha256 != prior.state_sha256
            or state.effective_cycle <= prior.effective_cycle
        ):
            raise PredictorFrameworkError("immutable state chain differs")
        reconstructed = PredictorStateVersion(
            state.model_id,
            state.version_id,
            state.effective_cycle,
            state.parent_state_sha256,
            state.transition_reason,
            state.payload,
        )
        if reconstructed.state_sha256 != state.state_sha256:
            raise PredictorFrameworkError("state content digest differs")
        prior = state
    states_by_version = {
        state.version_id: state for state in result.state_versions
    }
    for decision in result.decisions:
        state = states_by_version.get(decision.state_version_id)
        if (
            state is None
            or decision.state_sha256 != state.state_sha256
            or state.effective_cycle > decision.decision_cycle
        ):
            raise PredictorFrameworkError("decision state reference differs")
        reconstructed = PredictorDecision(
            decision.event_id,
            decision.event_timestamp_ns,
            decision.occurrence_cycle,
            decision.decision_cycle,
            decision.is_query,
            decision.model_id,
            decision.configuration_sha256,
            decision.state_version_id,
            decision.state_sha256,
            decision.route,
            decision.candidate_attempted,
            decision.candidate_used,
            decision.output,
            decision.used_pose_ids,
            decision.fallback_trace,
        )
        if reconstructed.decision_sha256 != decision.decision_sha256:
            raise PredictorFrameworkError("decision content digest differs")
    events_by_cycle = {}  # type: dict
    poses_by_cycle = {}  # type: dict
    for event in expected_events:
        events_by_cycle.setdefault(event.decision_cycle, []).append(event)
    for pose in expected_poses:
        poses_by_cycle.setdefault(pose.commit_cycle, []).append(pose)
    expected_cycles = tuple(sorted(set(events_by_cycle) | set(poses_by_cycle)))
    if tuple(receipt.observation_cycle for receipt in result.cycle_state_receipts) != expected_cycles:
        raise PredictorFrameworkError("cycle receipt population differs")
    referenced_next_versions = tuple(
        receipt.next_state_version_id
        for receipt in result.cycle_state_receipts
        if receipt.next_state_version_id is not None
    )
    if referenced_next_versions != tuple(range(1, len(result.state_versions))):
        raise PredictorFrameworkError("cycle receipts do not cover the state chain")
    for receipt in result.cycle_state_receipts:
        cycle_events = tuple(events_by_cycle.get(receipt.observation_cycle, ()))
        expected_clusters = tuple(
            tuple(event.event_id for event in cycle_events[start:stop])
            for start, stop in _cluster_ranges(cycle_events)
        )
        if receipt.event_cluster_ids != expected_clusters:
            raise PredictorFrameworkError("cycle receipt event clusters differ")
        if receipt.pose_commit_ids != tuple(
            pose.pose_id for pose in poses_by_cycle.get(receipt.observation_cycle, ())
        ):
            raise PredictorFrameworkError("cycle receipt pose commits differ")
        prior_state = states_by_version.get(receipt.prior_state_version_id)
        visible_states = tuple(
            state
            for state in result.state_versions
            if state.effective_cycle <= receipt.observation_cycle
        )
        latest_visible_state = visible_states[-1] if visible_states else None
        if (
            prior_state is None
            or prior_state is not latest_visible_state
            or prior_state.state_sha256 != receipt.prior_state_sha256
        ):
            raise PredictorFrameworkError("cycle receipt prior state differs")
        if any(
            decision.decision_cycle == receipt.observation_cycle
            and (
                decision.state_version_id != prior_state.version_id
                or decision.state_sha256 != prior_state.state_sha256
            )
            for decision in result.decisions
        ):
            raise PredictorFrameworkError("cycle decisions used the wrong state")
        if receipt.next_state_version_id is not None:
            next_state = states_by_version.get(receipt.next_state_version_id)
            if (
                next_state is None
                or next_state.state_sha256 != receipt.next_state_sha256
                or next_state.parent_state_sha256 != receipt.prior_state_sha256
                or next_state.effective_cycle != receipt.observation_cycle + 1
            ):
                raise PredictorFrameworkError("cycle receipt next state differs")
        reconstructed_receipt = CycleStateReceipt(
            receipt.observation_cycle,
            receipt.prior_state_version_id,
            receipt.prior_state_sha256,
            receipt.pose_commit_ids,
            receipt.event_cluster_ids,
            receipt.next_state_version_id,
            receipt.next_state_sha256,
            receipt.next_state_effective_cycle,
        )
        if reconstructed_receipt.receipt_sha256 != receipt.receipt_sha256:
            raise PredictorFrameworkError("cycle receipt content digest differs")
    expected_digest = canonical_sha256(
        [decision.to_mapping() for decision in result.decisions]
    )
    if result.decision_records_sha256 != expected_digest:
        raise PredictorFrameworkError("decision record digest differs")
    expected_receipt_digest = canonical_sha256(
        [receipt.to_mapping() for receipt in result.cycle_state_receipts]
    )
    if result.cycle_state_receipts_sha256 != expected_receipt_digest:
        raise PredictorFrameworkError("cycle receipt aggregate digest differs")
    return expected_digest


__all__ = [
    "CandidateAttempt",
    "CandidateModel",
    "CandidateStateView",
    "CycleStateReceipt",
    "CycleCommitView",
    "DecisionRoute",
    "EventClusterCommitView",
    "EventEnvelope",
    "FallbackAttempt",
    "FallbackContext",
    "FallbackHooks",
    "PoseEnvelope",
    "PredictionOutput",
    "PredictorDecision",
    "PredictorEventView",
    "PredictorFrameworkError",
    "PredictorPoseCommitView",
    "PredictorPoseView",
    "PredictorRunResult",
    "PredictorStateVersion",
    "StateUpdate",
    "run_candidate_neutral_predictor",
    "verify_predictor_run_integrity",
]
