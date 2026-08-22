"""Candidate-neutral conversion from predictor decisions to sealed world rays.

This module is the wrapper-owned boundary between an always-on predictor and
the locked Stage-3 consumer.  It joins immutable neutral inputs, frozen
cycle-model occurrence decisions, and append-only predictor decisions.  It
does not execute a predictor or change an event route.

The serialized event shape is exactly the ``screen108`` candidate-output
shape.  A non-baseline quaternion is actively rotated from sensor to world.
An exact baseline fallback is authenticated here and serialized with
``world_ray=None`` so that the locked consumer reuses its independently
reconstructed current-CAV geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryMode,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import DecisionRecord

from .framework import (
    DecisionRoute,
    PredictorDecision,
    PredictorFrameworkError,
)


Ray = Tuple[float, float, float]
QuaternionXYZW = Tuple[float, float, float, float]
CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_output/v1"
CURRENT_CAV_MODEL_ID = "CURRENT_CAV"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CAV_MAX_HORIZON_NS = 5_000_000
_ZOH_MAX_AGE_NS = 1_000_000


class CandidateOutputError(ValueError):
    """A neutral-input, causal-edge, geometry, or receipt invariant failed."""


def _nonnegative_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CandidateOutputError("%s must be a non-negative integer" % where)
    return value


def _identifier(value: object, where: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise CandidateOutputError("%s is not a canonical identifier" % where)
    return value


def _nonempty_text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise CandidateOutputError("%s must be non-empty text" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CandidateOutputError("%s must be lowercase SHA-256" % where)
    return value


def _unit_ray(value: object, where: str) -> Ray:
    if type(value) is not tuple or len(value) != 3:  # type: ignore[arg-type]
        raise CandidateOutputError("%s must be an immutable three-tuple" % where)
    converted = []
    for component in value:  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise CandidateOutputError("%s must be finite" % where)
        number = float(component)
        if not math.isfinite(number):
            raise CandidateOutputError("%s must be finite" % where)
        converted.append(number)
    result = tuple(converted)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise CandidateOutputError("%s must have unit norm" % where)
    return result  # type: ignore[return-value]


def _ordered_pose_ids(value: object, where: str) -> Tuple[int, ...]:
    if type(value) is not tuple:
        raise CandidateOutputError("%s must be an immutable tuple" % where)
    result = tuple(_nonnegative_int(item, where) for item in value)  # type: ignore[union-attr]
    if result != tuple(sorted(set(result))):
        raise CandidateOutputError("%s must be unique and ordered" % where)
    return result


@dataclass(frozen=True)
class SealedEventReceipt:
    """One screen-compatible, append-only event geometry receipt."""

    event_id: int
    event_content_sha256: str
    decision_cycle: int
    model_id: str
    predictor_state_version: int
    used_pose_ids: Tuple[int, ...]
    candidate_used: bool
    fallback_reason: Optional[str]
    world_ray: Optional[Ray]
    decision_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "receipt event ID")
        _sha256(self.event_content_sha256, "receipt event digest")
        _nonnegative_int(self.decision_cycle, "receipt decision cycle")
        _identifier(self.model_id, "receipt model ID")
        _nonnegative_int(
            self.predictor_state_version, "receipt predictor state version"
        )
        _ordered_pose_ids(self.used_pose_ids, "receipt used pose IDs")
        if type(self.candidate_used) is not bool:
            raise CandidateOutputError("receipt candidate_used must be an exact bool")
        if self.candidate_used:
            if self.model_id == CURRENT_CAV_MODEL_ID:
                raise CandidateOutputError("candidate geometry names the baseline model")
            if not self.used_pose_ids:
                raise CandidateOutputError("candidate geometry has no used pose IDs")
            if self.fallback_reason is not None:
                raise CandidateOutputError("candidate geometry has a fallback reason")
            if self.world_ray is None:
                raise CandidateOutputError("candidate geometry has no world ray")
            object.__setattr__(
                self, "world_ray", _unit_ray(self.world_ray, "candidate world ray")
            )
        else:
            if self.model_id != CURRENT_CAV_MODEL_ID:
                raise CandidateOutputError("baseline fallback model identity differs")
            if type(self.fallback_reason) is not str or not self.fallback_reason:
                raise CandidateOutputError("baseline fallback reason is missing")
            if self.world_ray is not None:
                raise CandidateOutputError(
                    "baseline fallback must defer geometry to the locked consumer"
                )
        object.__setattr__(
            self, "decision_sha256", canonical_sha256(self.to_mapping(False))
        )

    def to_mapping(self, include_digest: bool = True) -> Mapping[str, object]:
        result = {
            "event_id": self.event_id,
            "event_content_sha256": self.event_content_sha256,
            "decision_cycle": self.decision_cycle,
            "model_id": self.model_id,
            "predictor_state_version": self.predictor_state_version,
            "used_pose_ids": list(self.used_pose_ids),
            "candidate_used": self.candidate_used,
            "fallback_reason": self.fallback_reason,
            "world_ray": None if self.world_ray is None else list(self.world_ray),
        }
        if include_digest:
            result = dict(result, decision_sha256=self.decision_sha256)
        return result


@dataclass(frozen=True)
class SealedWindowOutput:
    """One ordered neutral window and its event receipt seal."""

    window_id: str
    ordered_event_ids: Tuple[int, ...]
    ordered_query_event_ids: Tuple[int, ...]
    events: Tuple[SealedEventReceipt, ...]
    events_sha256: str = field(init=False)
    ordered_query_ids_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _nonempty_text(self.window_id, "window ID")
        if not self.events:
            raise CandidateOutputError("window event receipts must not be empty")
        if any(type(event) is not SealedEventReceipt for event in self.events):
            raise CandidateOutputError("window contains a non-receipt event")
        observed = tuple(event.event_id for event in self.events)
        if self.ordered_event_ids != observed:
            raise CandidateOutputError("window ordered event population differs")
        if len(set(observed)) != len(observed):
            raise CandidateOutputError("window event IDs repeat")
        if any(event_id not in observed for event_id in self.ordered_query_event_ids):
            raise CandidateOutputError("window ordered Q is not an event subsequence")
        positions = {event_id: index for index, event_id in enumerate(observed)}
        query_positions = tuple(
            positions[event_id] for event_id in self.ordered_query_event_ids
        )
        if query_positions != tuple(sorted(set(query_positions))):
            raise CandidateOutputError("window ordered Q differs")
        object.__setattr__(
            self,
            "events_sha256",
            canonical_sha256([event.to_mapping() for event in self.events]),
        )
        object.__setattr__(
            self,
            "ordered_query_ids_sha256",
            canonical_sha256(list(self.ordered_query_event_ids)),
        )

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "events": [event.to_mapping() for event in self.events],
            "events_sha256": self.events_sha256,
        }

    def to_unsealed_mapping(self) -> Mapping[str, object]:
        """Return the exact body accepted by ``screen108.seal_candidate_output``."""

        return {
            "window_id": self.window_id,
            "events": [event.to_mapping(False) for event in self.events],
        }


def _validate_neutral_event(value: object) -> NeutralEventInput:
    if type(value) is not NeutralEventInput:
        raise CandidateOutputError("neutral event has the wrong type")
    event = value
    _nonnegative_int(event.event_id, "neutral event ID")
    _nonnegative_int(event.timestamp_ns, "neutral event timestamp")
    if isinstance(event.polarity, bool) or event.polarity not in (0, 1):
        raise CandidateOutputError("neutral event polarity differs")
    if type(event.is_query) is not bool or type(event.transform_guard_valid) is not bool:
        raise CandidateOutputError("neutral event flags differ")
    _nonnegative_int(
        event.causal_pose_source_index, "neutral causal pose source index"
    )
    expected = canonical_event_content_sha256(
        event.event_id,
        event.timestamp_ns,
        event.polarity,
        event.is_query,
        event.sensor_ray,
        event.causal_pose_source_index,
        event.transform_guard_valid,
    )
    if event.event_content_sha256 != expected:
        raise CandidateOutputError("neutral event content digest differs")
    _unit_ray(event.sensor_ray, "neutral sensor ray")
    return event


def _validate_neutral_pose(value: object) -> NeutralPoseInput:
    if type(value) is not NeutralPoseInput:
        raise CandidateOutputError("neutral pose has the wrong type")
    pose = value
    _nonnegative_int(pose.pose_id, "neutral pose ID")
    _nonnegative_int(pose.timestamp_ns, "neutral pose timestamp")
    if isinstance(pose.commit_cycle, bool) or not isinstance(pose.commit_cycle, int):
        raise CandidateOutputError("neutral pose commit cycle differs")
    if type(pose.quaternion_xyzw) is not tuple or len(pose.quaternion_xyzw) != 4:
        raise CandidateOutputError("neutral pose quaternion shape differs")
    norm_squared = 0.0
    for component in pose.quaternion_xyzw:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise CandidateOutputError("neutral pose quaternion is non-finite")
        number = float(component)
        if not math.isfinite(number):
            raise CandidateOutputError("neutral pose quaternion is non-finite")
        norm_squared += number * number
    if not math.isfinite(norm_squared) or abs(math.sqrt(norm_squared) - 1.0) > 1.0e-9:
        raise CandidateOutputError("neutral pose quaternion is not unit length")
    if type(pose.value_valid) is not bool or type(pose.arithmetic_valid) is not bool:
        raise CandidateOutputError("neutral pose validity flags differ")
    expected = canonical_pose_value_sha256(
        pose.pose_id, pose.timestamp_ns, pose.quaternion_xyzw
    )
    if pose.pose_sha256 != expected:
        raise CandidateOutputError("neutral pose content digest differs")
    return pose


def _validate_predictor_decision(value: object) -> PredictorDecision:
    if type(value) is not PredictorDecision:
        raise CandidateOutputError("predictor decision has the wrong type")
    decision = value
    try:
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
    except PredictorFrameworkError as exc:
        raise CandidateOutputError("predictor decision fields differ") from exc
    if reconstructed.decision_sha256 != decision.decision_sha256:
        raise CandidateOutputError("predictor decision content digest differs")
    return decision


def _cycle_pose_rows(
    decision: DecisionRecord,
    prefix: str,
) -> Tuple[Tuple[int, int, int, str], ...]:
    identifiers = getattr(decision, "%s_pose_ids" % prefix)
    timestamps = getattr(decision, "%s_pose_timestamps_ns" % prefix)
    commits = getattr(decision, "%s_pose_commit_cycles" % prefix)
    digests = getattr(decision, "%s_pose_sha256" % prefix)
    if not all(type(value) is tuple for value in (identifiers, timestamps, commits, digests)):
        raise CandidateOutputError("cycle-model %s pose evidence is mutable" % prefix)
    if len({len(identifiers), len(timestamps), len(commits), len(digests)}) != 1:
        raise CandidateOutputError("cycle-model %s pose evidence cardinality differs" % prefix)
    if tuple(identifiers) != tuple(sorted(set(identifiers))):
        raise CandidateOutputError("cycle-model %s pose IDs differ" % prefix)
    return tuple(zip(identifiers, timestamps, commits, digests))


def _validate_cycle_decision(
    decision: object,
    window_id: str,
    event: NeutralEventInput,
    poses_by_id: Mapping[int, NeutralPoseInput],
) -> DecisionRecord:
    if type(decision) is not DecisionRecord:
        raise CandidateOutputError("cycle-model decision has the wrong type")
    value = decision
    if (
        value.window_id != window_id
        or value.event_id != event.event_id
        or value.event_timestamp_ns != event.timestamp_ns
    ):
        raise CandidateOutputError("cycle-model event identity differs")
    if value.arm != "causal_cav":
        raise CandidateOutputError("cycle-model decision is not current CAV")
    edge = _nonnegative_int(value.occurrence_cycle, "cycle-model occurrence edge")
    occurrence = _cycle_pose_rows(value, "occurrence")
    used = _cycle_pose_rows(value, "used")
    occurrence_ids = tuple(row[0] for row in occurrence)
    if any(row[0] not in occurrence_ids for row in used):
        raise CandidateOutputError("cycle-model used pose is not occurrence-visible")
    for pose_id, timestamp, commit, digest in occurrence:
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.timestamp_ns != timestamp
            or pose.commit_cycle != commit
            or pose.pose_sha256 != digest
            or commit >= edge
            or timestamp > event.timestamp_ns
        ):
            raise CandidateOutputError("cycle-model occurrence pose evidence differs")
    for pose_id, timestamp, commit, digest in used:
        pose = poses_by_id[pose_id]
        if (
            pose.timestamp_ns != timestamp
            or pose.commit_cycle != commit
            or pose.pose_sha256 != digest
        ):
            raise CandidateOutputError("cycle-model used pose evidence differs")

    visible = tuple(
        pose
        for pose in poses_by_id.values()
        if pose.commit_cycle < edge and pose.timestamp_ns <= event.timestamp_ns
    )
    expected_occurrence = visible[-2:]
    if occurrence_ids != tuple(pose.pose_id for pose in expected_occurrence):
        raise CandidateOutputError("cycle-model occurrence snapshot differs")
    if not expected_occurrence:
        expected_used = ()
        expected_disposition = "raw_bypass"
        expected_reason = "no_occurrence_pose"
    else:
        latest = expected_occurrence[-1]
        if event.causal_pose_source_index != latest.pose_id:
            raise CandidateOutputError("neutral causal pose source index differs")
        age_ns = event.timestamp_ns - latest.timestamp_ns
        cav_valid = False
        if len(expected_occurrence) == 2:
            previous = expected_occurrence[0]
            horizon_ns = min(
                _CAV_MAX_HORIZON_NS, latest.timestamp_ns - previous.timestamp_ns
            )
            cav_valid = (
                age_ns <= horizon_ns
                and previous.value_valid
                and previous.arithmetic_valid
                and latest.value_valid
                and latest.arithmetic_valid
                and event.transform_guard_valid
            )
        if cav_valid:
            expected_used = tuple(pose.pose_id for pose in expected_occurrence)
            expected_disposition = "corrected_world_ray"
            expected_reason = "causal_cav"
        elif (
            latest.value_valid
            and latest.arithmetic_valid
            and age_ns <= _ZOH_MAX_AGE_NS
        ):
            expected_used = (latest.pose_id,)
            expected_disposition = "corrected_world_ray"
            expected_reason = "fresh_zoh_fallback"
        elif not latest.value_valid or not latest.arithmetic_valid:
            expected_used = (latest.pose_id,)
            expected_disposition = "raw_bypass"
            expected_reason = "invalid_pose"
        else:
            expected_used = (latest.pose_id,)
            expected_disposition = "raw_bypass"
            expected_reason = "stale_pose"
    if (
        value.used_pose_ids != expected_used
        or value.disposition != expected_disposition
        or value.disposition_reason != expected_reason
    ):
        raise CandidateOutputError("cycle-model current-CAV route differs")
    expected_age = (
        None
        if not expected_used
        else event.timestamp_ns - poses_by_id[expected_used[-1]].timestamp_ns
    )
    if value.intentional_future_pose_use is not False or value.pose_age_ns != expected_age:
        raise CandidateOutputError("cycle-model causal pose summary differs")
    return value


def _exact_current_cav_quaternion(
    event: NeutralEventInput,
    edge: int,
    cycle_decision: DecisionRecord,
    poses_by_id: Mapping[int, NeutralPoseInput],
) -> QuaternionXYZW:
    if (
        cycle_decision.disposition != "corrected_world_ray"
        or cycle_decision.disposition_reason != "causal_cav"
    ):
        raise CandidateOutputError("cycle-model baseline is not current CAV")
    used_ids = cycle_decision.used_pose_ids
    if len(used_ids) != 2:
        raise CandidateOutputError("current CAV does not bind exactly two poses")
    samples = tuple(
        PoseSample(
            poses_by_id[pose_id].timestamp_ns,
            poses_by_id[pose_id].commit_cycle,
            poses_by_id[pose_id].quaternion_xyzw,
        )
        for pose_id in used_ids
    )
    try:
        recovery = recover_causal_cav(samples, event.timestamp_ns, edge)
    except GeometryError as exc:
        raise CandidateOutputError("cannot reconstruct exact current CAV") from exc
    if recovery.mode is not RecoveryMode.CAV or recovery.quaternion_xyzw is None:
        raise CandidateOutputError("frozen recovery disagrees with current CAV")
    return recovery.quaternion_xyzw


def _fallback_reason(decision: PredictorDecision) -> str:
    if not decision.fallback_trace:
        raise CandidateOutputError("baseline fallback lacks a causal trace")
    return "|".join(decision.fallback_trace)


def _event_receipt(
    event: NeutralEventInput,
    poses_by_id: Mapping[int, NeutralPoseInput],
    cycle_decision: DecisionRecord,
    predictor_decision: PredictorDecision,
) -> SealedEventReceipt:
    edge = cycle_decision.occurrence_cycle
    if (
        predictor_decision.event_id != event.event_id
        or predictor_decision.event_timestamp_ns != event.timestamp_ns
        or predictor_decision.is_query != event.is_query
    ):
        raise CandidateOutputError("predictor event identity or Q membership differs")
    if predictor_decision.occurrence_cycle >= predictor_decision.decision_cycle:
        raise CandidateOutputError("predictor occurrence record is not pre-decision")
    if predictor_decision.decision_cycle != edge:
        raise CandidateOutputError(
            "predictor decision edge differs from the cycle-model occurrence edge"
        )
    used_ids = _ordered_pose_ids(
        predictor_decision.used_pose_ids, "predictor used pose IDs"
    )
    occurrence_ids = set(cycle_decision.occurrence_pose_ids)
    if not set(used_ids).issubset(occurrence_ids):
        raise CandidateOutputError("predictor used a same-edge or future pose")
    for pose_id in used_ids:
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.commit_cycle >= edge
            or pose.timestamp_ns > event.timestamp_ns
            or not pose.value_valid
            or not pose.arithmetic_valid
        ):
            raise CandidateOutputError("predictor used an unavailable pose")

    if predictor_decision.route is DecisionRoute.CANDIDATE:
        # Reconstructing A here proves that candidate use occurred only where
        # the frozen current-CAV arm was valid on this exact occurrence edge.
        _exact_current_cav_quaternion(event, edge, cycle_decision, poses_by_id)
        quaternion = predictor_decision.output.quaternion_xyzw
        if quaternion is None:
            raise CandidateOutputError("candidate decision has no quaternion")
        try:
            ray = rotate_sensor_ray_to_world(quaternion, event.sensor_ray)
        except GeometryError as exc:
            raise CandidateOutputError("candidate world-ray rotation failed") from exc
        return SealedEventReceipt(
            event.event_id,
            event.event_content_sha256,
            edge,
            predictor_decision.model_id,
            predictor_decision.state_version_id,
            used_ids,
            True,
            None,
            _unit_ray(ray, "candidate world ray"),
        )

    expected_used = ()  # type: Tuple[int, ...]
    if predictor_decision.route is DecisionRoute.CURRENT_CAV:
        if not predictor_decision.candidate_attempted:
            raise CandidateOutputError("current-CAV fallback lacks candidate attempt")
        expected = _exact_current_cav_quaternion(
            event, edge, cycle_decision, poses_by_id
        )
        if predictor_decision.output.quaternion_xyzw != expected:
            raise CandidateOutputError("current-CAV fallback quaternion differs")
        expected_used = cycle_decision.used_pose_ids
    elif predictor_decision.route is DecisionRoute.FRESH_ZOH:
        if predictor_decision.candidate_attempted:
            raise CandidateOutputError("fresh-ZOH fallback followed a candidate attempt")
        if (
            cycle_decision.disposition != "corrected_world_ray"
            or cycle_decision.disposition_reason != "fresh_zoh_fallback"
            or len(cycle_decision.used_pose_ids) != 1
        ):
            raise CandidateOutputError("fresh-ZOH fallback differs from cycle model")
        expected_used = cycle_decision.used_pose_ids
        expected = poses_by_id[expected_used[0]].quaternion_xyzw
        if predictor_decision.output.quaternion_xyzw != expected:
            raise CandidateOutputError("fresh-ZOH fallback quaternion differs")
    elif predictor_decision.route is DecisionRoute.SENSOR_FIXED:
        if predictor_decision.candidate_attempted:
            raise CandidateOutputError("sensor-fixed fallback followed a candidate attempt")
        if cycle_decision.disposition != "raw_bypass":
            raise CandidateOutputError("sensor-fixed fallback differs from cycle model")
        if predictor_decision.output.quaternion_xyzw is not None:
            raise CandidateOutputError("sensor-fixed fallback supplied a quaternion")
    else:  # pragma: no cover - exact enum validation occurs in PredictorDecision
        raise CandidateOutputError("predictor route differs")
    if used_ids != expected_used:
        raise CandidateOutputError("baseline fallback used pose IDs differ")
    return SealedEventReceipt(
        event.event_id,
        event.event_content_sha256,
        edge,
        CURRENT_CAV_MODEL_ID,
        predictor_decision.state_version_id,
        used_ids,
        False,
        _fallback_reason(predictor_decision),
        None,
    )


def build_candidate_output_window(
    window_id: str,
    neutral_events: Sequence[NeutralEventInput],
    neutral_poses: Sequence[NeutralPoseInput],
    cycle_decisions: Sequence[DecisionRecord],
    predictor_decisions: Sequence[PredictorDecision],
) -> SealedWindowOutput:
    """Join one complete neutral window without filtering or reordering events."""

    identity = _nonempty_text(window_id, "window ID")
    events = tuple(_validate_neutral_event(value) for value in neutral_events)
    poses = tuple(_validate_neutral_pose(value) for value in neutral_poses)
    cycles = tuple(cycle_decisions)
    predictions = tuple(_validate_predictor_decision(value) for value in predictor_decisions)
    if not events or not poses:
        raise CandidateOutputError("neutral event and pose streams must not be empty")
    if len(events) != len(cycles) or len(events) != len(predictions):
        raise CandidateOutputError("candidate output changed event cardinality")
    event_ids = tuple(event.event_id for event in events)
    if len(set(event_ids)) != len(event_ids):
        raise CandidateOutputError("neutral event IDs repeat")
    if any(right.timestamp_ns < left.timestamp_ns for left, right in zip(events, events[1:])):
        raise CandidateOutputError("neutral event timestamps moved backwards")
    pose_ids = tuple(pose.pose_id for pose in poses)
    if pose_ids != tuple(sorted(set(pose_ids))):
        raise CandidateOutputError("neutral pose IDs are not unique and ordered")
    if any(right.timestamp_ns <= left.timestamp_ns for left, right in zip(poses, poses[1:])):
        raise CandidateOutputError("neutral pose timestamps are not strictly increasing")
    poses_by_id = {pose.pose_id: pose for pose in poses}

    receipts = []
    versions = {}
    prior_state_version = None  # type: Optional[int]
    prior_timestamp = None  # type: Optional[int]
    prior_state_sha256 = None  # type: Optional[str]
    prior_predictor_occurrence = None  # type: Optional[int]
    prior_decision_edge = None  # type: Optional[int]
    model_identity = None  # type: Optional[Tuple[str, str]]
    for event, cycle_value, prediction in zip(events, cycles, predictions):
        cycle = _validate_cycle_decision(cycle_value, identity, event, poses_by_id)
        if model_identity is None:
            model_identity = (prediction.model_id, prediction.configuration_sha256)
        elif model_identity != (prediction.model_id, prediction.configuration_sha256):
            raise CandidateOutputError("predictor model identity changed within a window")
        known_digest = versions.setdefault(
            prediction.state_version_id, prediction.state_sha256
        )
        if known_digest != prediction.state_sha256:
            raise CandidateOutputError("predictor state version changed content")
        if prior_state_version is not None and prediction.state_version_id < prior_state_version:
            raise CandidateOutputError("predictor state version moved backwards")
        if prior_decision_edge is not None and cycle.occurrence_cycle < prior_decision_edge:
            raise CandidateOutputError("cycle-model occurrence edge moved backwards")
        if prior_timestamp == event.timestamp_ns and (
            prediction.state_version_id != prior_state_version
            or prediction.state_sha256 != prior_state_sha256
            or prediction.occurrence_cycle != prior_predictor_occurrence
            or cycle.occurrence_cycle != prior_decision_edge
        ):
            raise CandidateOutputError(
                "equal-timestamp events changed predictor state or edge"
            )
        receipts.append(_event_receipt(event, poses_by_id, cycle, prediction))
        prior_state_version = prediction.state_version_id
        prior_state_sha256 = prediction.state_sha256
        prior_predictor_occurrence = prediction.occurrence_cycle
        prior_decision_edge = cycle.occurrence_cycle
        prior_timestamp = event.timestamp_ns

    query_ids = tuple(event.event_id for event in events if event.is_query)
    observed_query_ids = tuple(
        prediction.event_id for prediction in predictions if prediction.is_query
    )
    if observed_query_ids != query_ids:
        raise CandidateOutputError("ordered query population Q differs")
    return SealedWindowOutput(identity, event_ids, query_ids, tuple(receipts))


def seal_candidate_output_envelope(
    candidate_id: str,
    adapter_aggregate_sha256: str,
    neutral_input_sha256: str,
    candidate_executable_sha256: str,
    candidate_config_sha256: str,
    windows: Sequence[SealedWindowOutput],
) -> Mapping[str, object]:
    """Bind ordered sealed windows into the exact locked-screen envelope."""

    identifier = _identifier(candidate_id, "candidate ID")
    for value, where in (
        (adapter_aggregate_sha256, "adapter aggregate digest"),
        (neutral_input_sha256, "neutral input digest"),
        (candidate_executable_sha256, "candidate executable digest"),
        (candidate_config_sha256, "candidate config digest"),
    ):
        _sha256(value, where)
    values = tuple(windows)
    if not values or any(type(window) is not SealedWindowOutput for window in values):
        raise CandidateOutputError("candidate windows must be sealed window outputs")
    window_ids = tuple(window.window_id for window in values)
    if len(set(window_ids)) != len(window_ids):
        raise CandidateOutputError("candidate window IDs repeat")
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": identifier,
        "adapter_aggregate_sha256": adapter_aggregate_sha256,
        "neutral_input_sha256": neutral_input_sha256,
        "candidate_executable_sha256": candidate_executable_sha256,
        "candidate_config_sha256": candidate_config_sha256,
        "windows": [window.to_mapping() for window in values],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


__all__ = [
    "CANDIDATE_OUTPUT_SCHEMA",
    "CURRENT_CAV_MODEL_ID",
    "CandidateOutputError",
    "SealedEventReceipt",
    "SealedWindowOutput",
    "build_candidate_output_window",
    "seal_candidate_output_envelope",
]
