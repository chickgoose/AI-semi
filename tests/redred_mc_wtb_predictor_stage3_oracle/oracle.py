"""Independent Stage-3 synthetic correctness and causality oracle."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from protocol import (
    AdapterDecision,
    CandidateAdapter,
    CandidateNumericError,
    CausalView,
    EventRecord,
    FallbackDecision,
    ForecastReceipt,
    PoseFeedback,
    PoseRecord,
    PredictorEvent,
    Quaternion,
)


CAV_MAX_AGE_NS = 5_000_000
ZOH_MAX_AGE_NS = 1_000_000
NEAR_PI_EPSILON_RAD = 1.0e-6
UNIT_TOLERANCE = 1.0e-7


class OracleViolation(AssertionError):
    """A scenario or candidate violated the causal adapter contract."""


@dataclass(frozen=True)
class DecisionRecord:
    event_id: str
    mode: str
    quaternion_xyzw: Optional[Quaternion]
    used_pose_ids: Tuple[str, ...]
    source_state_version: int
    decision_cycle: int
    state_effective_cycle: int
    fallback_reason: str
    baseline_reason: str


@dataclass(frozen=True)
class EventAudit:
    event_id: str
    timestamp_ns: int
    decision_cycle: int
    visible_pose_ids: Tuple[str, ...]
    state_version: int
    state_effective_cycle: int


@dataclass(frozen=True)
class FeedbackAudit:
    pose_id: str
    pose_commit_cycle: int
    source_state_version: int
    forecast_generation_cycle: int
    forecast_target_timestamp_ns: int
    effective_cycle: Optional[int]
    published_state_version: Optional[int]
    updated: bool
    reason: str


@dataclass(frozen=True)
class RunReceipt:
    candidate_id: str
    decisions: Tuple[DecisionRecord, ...]
    event_audits: Tuple[EventAudit, ...]
    feedback_audits: Tuple[FeedbackAudit, ...]
    decision_digests: Tuple[str, ...]


@dataclass(frozen=True)
class _PendingPublication:
    payload: Any
    state_version: int
    effective_cycle: int


def _finite_quaternion(q: Sequence[float]) -> bool:
    return len(q) == 4 and all(math.isfinite(float(value)) for value in q)


def normalize_quaternion(q: Sequence[float]) -> Quaternion:
    if not _finite_quaternion(q):
        raise ValueError("quaternion is not finite")
    norm = math.sqrt(sum(float(value) * float(value) for value in q))
    if norm <= 1.0e-15:
        raise ValueError("quaternion norm is zero")
    return tuple(float(value) / norm for value in q)  # type: ignore[return-value]


def _require_unit_quaternion(q: Sequence[float]) -> Quaternion:
    if len(q) != 4:
        raise OracleViolation("candidate quaternion does not have four components")
    try:
        values = tuple(float(value) for value in q)
    except (OverflowError, TypeError, ValueError) as error:
        raise CandidateNumericError("candidate quaternion conversion failed") from error
    if not all(math.isfinite(value) for value in values):
        raise CandidateNumericError("candidate quaternion is not finite")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 1.0e-15:
        raise CandidateNumericError("candidate quaternion norm failed")
    if abs(norm - 1.0) > UNIT_TOLERANCE:
        raise OracleViolation("candidate quaternion is not unit length")
    return values  # type: ignore[return-value]


def quaternion_conjugate(q: Quaternion) -> Quaternion:
    return (-q[0], -q[1], -q[2], q[3])


def quaternion_multiply(left: Quaternion, right: Quaternion) -> Quaternion:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def quaternion_from_rotation_vector(vector: Sequence[float]) -> Quaternion:
    vx, vy, vz = (float(value) for value in vector)
    angle = math.sqrt(vx * vx + vy * vy + vz * vz)
    if angle < 1.0e-15:
        return normalize_quaternion((0.5 * vx, 0.5 * vy, 0.5 * vz, 1.0))
    scale = math.sin(0.5 * angle) / angle
    return normalize_quaternion((vx * scale, vy * scale, vz * scale, math.cos(0.5 * angle)))


def quaternion_rotation_vector(q: Quaternion) -> Tuple[float, float, float]:
    qn = normalize_quaternion(q)
    if qn[3] < 0.0:
        qn = tuple(-value for value in qn)  # type: ignore[assignment]
    vector_norm = math.sqrt(qn[0] * qn[0] + qn[1] * qn[1] + qn[2] * qn[2])
    if vector_norm < 1.0e-15:
        return (2.0 * qn[0], 2.0 * qn[1], 2.0 * qn[2])
    angle = 2.0 * math.atan2(vector_norm, qn[3])
    scale = angle / vector_norm
    return (qn[0] * scale, qn[1] * scale, qn[2] * scale)


def rotation_distance(left: Quaternion, right: Quaternion) -> float:
    relative = quaternion_multiply(quaternion_conjugate(normalize_quaternion(left)), normalize_quaternion(right))
    return math.sqrt(sum(value * value for value in quaternion_rotation_vector(relative)))


def _valid_pose(pose: PoseRecord) -> bool:
    if not pose.valid:
        return False
    try:
        normalize_quaternion(pose.quaternion_xyzw)
    except ValueError:
        return False
    return True


def reference_fallback(event: EventRecord, visible_poses: Sequence[PoseRecord]) -> FallbackDecision:
    """Frozen CAV -> fresh ZOH -> RAW reference, independent of a candidate."""

    eligible = [
        pose
        for pose in visible_poses
        if _valid_pose(pose) and pose.measurement_timestamp_ns <= event.timestamp_ns
    ]
    eligible.sort(key=lambda pose: (pose.measurement_timestamp_ns, pose.commit_cycle, pose.pose_id))
    if not eligible:
        return FallbackDecision("RAW", None, (), "NO_VALID_POSE")

    latest = eligible[-1]
    age_ns = event.timestamp_ns - latest.measurement_timestamp_ns
    cav_reason = "INSUFFICIENT_HISTORY"
    if len(eligible) >= 2:
        previous = eligible[-2]
        interval_ns = latest.measurement_timestamp_ns - previous.measurement_timestamp_ns
        if interval_ns <= 0:
            cav_reason = "NONPOSITIVE_POSE_INTERVAL"
        elif age_ns > min(CAV_MAX_AGE_NS, interval_ns):
            cav_reason = "CAV_STALE"
        else:
            q0 = normalize_quaternion(previous.quaternion_xyzw)
            q1 = normalize_quaternion(latest.quaternion_xyzw)
            if sum(a * b for a, b in zip(q0, q1)) < 0.0:
                q1 = tuple(-value for value in q1)  # type: ignore[assignment]
            relative = quaternion_multiply(quaternion_conjugate(q0), q1)
            step = quaternion_rotation_vector(relative)
            step_angle = math.sqrt(sum(value * value for value in step))
            if math.pi - step_angle <= NEAR_PI_EPSILON_RAD:
                cav_reason = "CAV_NEAR_PI"
            else:
                scale = float(age_ns) / float(interval_ns)
                delta = quaternion_from_rotation_vector(tuple(value * scale for value in step))
                prediction = normalize_quaternion(quaternion_multiply(q1, delta))
                return FallbackDecision(
                    "CAV",
                    prediction,
                    (previous.pose_id, latest.pose_id),
                    "NONE",
                )

    if age_ns <= ZOH_MAX_AGE_NS:
        return FallbackDecision(
            "ZOH",
            normalize_quaternion(latest.quaternion_xyzw),
            (latest.pose_id,),
            cav_reason,
        )
    return FallbackDecision("RAW", None, (), cav_reason)


def fallback_equivalent(decision: DecisionRecord, fallback: FallbackDecision) -> bool:
    return (
        decision.mode == fallback.mode
        and decision.quaternion_xyzw == fallback.quaternion_xyzw
        and decision.used_pose_ids == fallback.used_pose_ids
    )


def decision_digest(decision: DecisionRecord) -> str:
    payload = json.dumps(asdict(decision), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_identity_order_exact_once(
    events: Sequence[EventRecord], decisions: Sequence[DecisionRecord]
) -> None:
    expected = [event.event_id for event in events]
    actual = [decision.event_id for decision in decisions]
    if len(actual) != len(expected):
        raise OracleViolation("decision cardinality differs from event cardinality")
    if len(set(actual)) != len(actual):
        raise OracleViolation("an event was decided more than once")
    if actual != expected:
        raise OracleViolation("event identity or order changed")


def _validate_scenario(poses: Sequence[PoseRecord], events: Sequence[EventRecord]) -> None:
    if len({pose.pose_id for pose in poses}) != len(poses):
        raise OracleViolation("duplicate pose_id")
    if len({pose.commit_cycle for pose in poses}) != len(poses):
        raise OracleViolation("synthetic oracle requires at most one pose commit per cycle")
    if len({event.event_id for event in events}) != len(events):
        raise OracleViolation("duplicate event_id")
    prior_key: Optional[Tuple[int, int]] = None
    timestamp_cycles: Dict[int, int] = {}
    for event in events:
        if event.occurrence_cycle >= event.decision_cycle:
            raise OracleViolation("event must occur before its immutable decision edge")
        key = (event.decision_cycle, event.timestamp_ns)
        if prior_key is not None and key < prior_key:
            raise OracleViolation("events are not in decision/timestamp order")
        prior_key = key
        known_cycle = timestamp_cycles.setdefault(event.timestamp_ns, event.decision_cycle)
        if known_cycle != event.decision_cycle:
            raise OracleViolation("equal-timestamp cluster spans multiple decision edges")


def _state_copy(state: Any) -> Tuple[Any, Any]:
    before = copy.deepcopy(state)
    supplied = copy.deepcopy(state)
    return before, supplied


def _assert_unmutated(before: Any, supplied: Any, operation: str) -> None:
    if supplied != before:
        raise OracleViolation("candidate mutated its %s input state" % operation)


class OracleHarness:
    """Cycle-accurate synthetic harness with immutable causal receipts."""

    _NUMERIC_FAILURES = (CandidateNumericError, ArithmeticError, ValueError)

    def run(
        self,
        adapter: CandidateAdapter,
        poses: Sequence[PoseRecord],
        events: Sequence[EventRecord],
    ) -> RunReceipt:
        poses = tuple(poses)
        events = tuple(events)
        _validate_scenario(poses, events)

        events_by_cycle: Dict[int, List[EventRecord]] = {}
        for event in events:
            events_by_cycle.setdefault(event.decision_cycle, []).append(event)
        poses_by_cycle = {pose.commit_cycle: pose for pose in poses}
        cycles = set(events_by_cycle)
        cycles.update(poses_by_cycle)
        cycles.update(cycle + 1 for cycle in poses_by_cycle)

        state_payload = copy.deepcopy(adapter.initial_state())
        state_version = 0
        state_effective_cycle = 0
        pending: Dict[int, _PendingPublication] = {}
        decisions: List[DecisionRecord] = []
        sealed_digests: List[str] = []
        event_audits: List[EventAudit] = []
        feedback_audits: List[FeedbackAudit] = []

        for cycle in sorted(cycles):
            publication = pending.pop(cycle, None)
            if publication is not None:
                state_payload = copy.deepcopy(publication.payload)
                state_version = publication.state_version
                state_effective_cycle = publication.effective_cycle

            # All decisions on the edge consume this one pre-feedback snapshot.
            edge_payload = copy.deepcopy(state_payload)
            edge_version = state_version
            edge_effective_cycle = state_effective_cycle
            for event in events_by_cycle.get(cycle, ()):
                visible = tuple(
                    pose
                    for pose in poses
                    if pose.commit_cycle < cycle
                    and pose.measurement_timestamp_ns <= event.timestamp_ns
                )
                view = CausalView(
                    decision_cycle=cycle,
                    visible_poses=visible,
                    state_version=edge_version,
                    state_effective_cycle=edge_effective_cycle,
                )
                fallback = reference_fallback(event, visible)
                predictor_event = PredictorEvent(
                    timestamp_ns=event.timestamp_ns,
                    x=event.x,
                    y=event.y,
                    polarity=event.polarity,
                )
                before, supplied = _state_copy(edge_payload)
                numeric_failure = ""
                try:
                    response = adapter.decide(supplied, predictor_event, view, fallback)
                    _assert_unmutated(before, supplied, "decision")
                except self._NUMERIC_FAILURES as error:
                    response = AdapterDecision(True, None, (), edge_version, "NUMERIC_FAILURE")
                    numeric_failure = type(error).__name__

                if response.source_state_version != edge_version:
                    raise OracleViolation("candidate cited a non-visible state version")
                visible_valid_ids = {pose.pose_id for pose in visible if _valid_pose(pose)}
                if not set(response.used_pose_ids).issubset(visible_valid_ids):
                    raise OracleViolation("candidate cited an invisible or invalid pose")

                if response.use_fallback:
                    decision = DecisionRecord(
                        event_id=event.event_id,
                        mode=fallback.mode,
                        quaternion_xyzw=fallback.quaternion_xyzw,
                        used_pose_ids=fallback.used_pose_ids,
                        source_state_version=edge_version,
                        decision_cycle=cycle,
                        state_effective_cycle=edge_effective_cycle,
                        fallback_reason=numeric_failure or response.reason or fallback.reason,
                        baseline_reason=fallback.reason,
                    )
                else:
                    if response.quaternion_xyzw is None:
                        raise OracleViolation("candidate prediction omitted its quaternion")
                    try:
                        candidate_q = _require_unit_quaternion(response.quaternion_xyzw)
                    except self._NUMERIC_FAILURES as error:
                        decision = DecisionRecord(
                            event_id=event.event_id,
                            mode=fallback.mode,
                            quaternion_xyzw=fallback.quaternion_xyzw,
                            used_pose_ids=fallback.used_pose_ids,
                            source_state_version=edge_version,
                            decision_cycle=cycle,
                            state_effective_cycle=edge_effective_cycle,
                            fallback_reason=type(error).__name__,
                            baseline_reason=fallback.reason,
                        )
                    else:
                        decision = DecisionRecord(
                            event_id=event.event_id,
                            mode="CANDIDATE",
                            quaternion_xyzw=candidate_q,
                            used_pose_ids=tuple(response.used_pose_ids),
                            source_state_version=edge_version,
                            decision_cycle=cycle,
                            state_effective_cycle=edge_effective_cycle,
                            fallback_reason="",
                            baseline_reason=fallback.reason,
                        )
                decisions.append(decision)
                sealed_digests.append(decision_digest(decision))
                event_audits.append(
                    EventAudit(
                        event_id=event.event_id,
                        timestamp_ns=event.timestamp_ns,
                        decision_cycle=cycle,
                        visible_pose_ids=tuple(pose.pose_id for pose in visible),
                        state_version=edge_version,
                        state_effective_cycle=edge_effective_cycle,
                    )
                )

            # A pose on this edge is intentionally processed after event sealing.
            pose = poses_by_cycle.get(cycle)
            if pose is None:
                continue
            if not _valid_pose(pose):
                feedback_audits.append(
                    FeedbackAudit(
                        pose_id=pose.pose_id,
                        pose_commit_cycle=cycle,
                        source_state_version=state_version,
                        forecast_generation_cycle=state_effective_cycle,
                        forecast_target_timestamp_ns=pose.measurement_timestamp_ns,
                        effective_cycle=None,
                        published_state_version=None,
                        updated=False,
                        reason="INVALID_POSE",
                    )
                )
                continue

            prior_visible = tuple(
                prior
                for prior in poses
                if prior.commit_cycle < cycle
                and prior.measurement_timestamp_ns <= pose.measurement_timestamp_ns
                and _valid_pose(prior)
            )
            before_forecast, forecast_state = _state_copy(state_payload)
            try:
                forecast_q = adapter.forecast_pose(
                    forecast_state, pose.measurement_timestamp_ns, prior_visible
                )
                _assert_unmutated(before_forecast, forecast_state, "forecast")
                if forecast_q is not None:
                    forecast_q = _require_unit_quaternion(forecast_q)
                forecast = ForecastReceipt(
                    source_state_version=state_version,
                    generation_cycle=state_effective_cycle,
                    target_timestamp_ns=pose.measurement_timestamp_ns,
                    quaternion_xyzw=forecast_q,
                )
                before_update, update_state = _state_copy(state_payload)
                new_payload = adapter.accept_pose(update_state, PoseFeedback(pose, forecast))
                _assert_unmutated(before_update, update_state, "pose-update")
            except self._NUMERIC_FAILURES as error:
                feedback_audits.append(
                    FeedbackAudit(
                        pose_id=pose.pose_id,
                        pose_commit_cycle=cycle,
                        source_state_version=state_version,
                        forecast_generation_cycle=state_effective_cycle,
                        forecast_target_timestamp_ns=pose.measurement_timestamp_ns,
                        effective_cycle=None,
                        published_state_version=None,
                        updated=False,
                        reason="NUMERIC_FAILURE:%s" % type(error).__name__,
                    )
                )
                continue

            effective_cycle = cycle + 1
            new_version = state_version + 1
            if effective_cycle in pending:
                raise OracleViolation("more than one state publication on an edge")
            pending[effective_cycle] = _PendingPublication(
                payload=copy.deepcopy(new_payload),
                state_version=new_version,
                effective_cycle=effective_cycle,
            )
            feedback_audits.append(
                FeedbackAudit(
                    pose_id=pose.pose_id,
                    pose_commit_cycle=cycle,
                    source_state_version=state_version,
                    forecast_generation_cycle=state_effective_cycle,
                    forecast_target_timestamp_ns=pose.measurement_timestamp_ns,
                    effective_cycle=effective_cycle,
                    published_state_version=new_version,
                    updated=True,
                    reason="NONE",
                )
            )

        validate_identity_order_exact_once(events, decisions)
        digests = tuple(sealed_digests)
        if digests != tuple(decision_digest(decision) for decision in decisions):
            raise OracleViolation("sealed decision mutated after publication")
        return RunReceipt(
            candidate_id=adapter.candidate_id,
            decisions=tuple(decisions),
            event_audits=tuple(event_audits),
            feedback_audits=tuple(feedback_audits),
            decision_digests=digests,
        )
