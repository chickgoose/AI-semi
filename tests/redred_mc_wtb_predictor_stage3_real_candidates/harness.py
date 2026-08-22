"""Shared analytic-stream harness for the actual RG3, DSPB, and SO3-PLL APIs.

This module is intentionally test-only.  It translates one score-free ledger
into each candidate's native public API and normalizes receipts for independent
cross-candidate invariants.  Candidate geometry is never reimplemented here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample,
    RecoveryMode,
    recover_causal_cav,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import (
    DSPBModel,
    DecisionMode as DSPBDecisionMode,
    EventRecord as DSPBEvent,
    SuppliedPose as DSPBPose,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import recover_rg3_cav
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import (
    SO3PLLMode,
    SO3PLLModel,
)


Quaternion = Tuple[float, float, float, float]
CANDIDATE_NAMES = ("RG3", "DSPB", "SO3_PLL")


class IntegrationViolation(AssertionError):
    """The common stream or a normalized candidate receipt is inconsistent."""


@dataclass(frozen=True)
class CommonPose:
    pose_id: int
    measurement_timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Quaternion
    valid: bool = True


@dataclass(frozen=True)
class CommonEvent:
    event_id: int
    timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int


@dataclass(frozen=True)
class EventCluster:
    events: Tuple[CommonEvent, ...]

    def __post_init__(self) -> None:
        if not self.events:
            raise IntegrationViolation("event cluster must not be empty")
        edge = (
            self.events[0].timestamp_ns,
            self.events[0].occurrence_cycle,
            self.events[0].decision_cycle,
        )
        if any(
            (event.timestamp_ns, event.occurrence_cycle, event.decision_cycle) != edge
            for event in self.events
        ):
            raise IntegrationViolation("equal-timestamp cluster edges differ")


@dataclass(frozen=True)
class AnalyticStream:
    poses: Tuple[CommonPose, ...]
    clusters: Tuple[EventCluster, ...]


@dataclass(frozen=True)
class NormalizedDecision:
    candidate_name: str
    event_id: int
    timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int
    mode: str
    candidate_used: bool
    quaternion_xyzw: Optional[Quaternion]
    used_pose_ids: Tuple[int, ...]
    used_commit_cycles: Tuple[int, ...]
    state_version: Optional[int]
    reason: str


@dataclass(frozen=True)
class NormalizedPoseReceipt:
    candidate_name: str
    pose_id: int
    commit_cycle: int
    accepted: bool
    effective_cycle: Optional[int]
    source_state_version: Optional[int]
    published_state_version: Optional[int]
    reason: str
    native_sha256: str


@dataclass(frozen=True)
class CandidateRun:
    candidate_name: str
    decisions: Tuple[NormalizedDecision, ...]
    pose_receipts: Tuple[NormalizedPoseReceipt, ...]
    replay_sha256: str


def z_rotation_degrees(degrees: float) -> Quaternion:
    half = math.radians(float(degrees)) * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))


def quaternion_equivalent(left: Optional[Quaternion], right: Optional[Quaternion]) -> bool:
    if left is None or right is None:
        return left is right
    dot = math.fsum(a * b for a, b in zip(left, right))
    return abs(abs(dot) - 1.0) <= 1.0e-10


def _sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _mode_from_recovery(mode: RecoveryMode) -> str:
    if mode is RecoveryMode.CAV:
        return "CAV"
    if mode is RecoveryMode.ZOH:
        return "ZOH"
    return "BYPASS"


def _validate_stream(stream: AnalyticStream) -> None:
    if type(stream) is not AnalyticStream:
        raise IntegrationViolation("stream has the wrong type")
    if len({pose.pose_id for pose in stream.poses}) != len(stream.poses):
        raise IntegrationViolation("pose identity repeats")
    if len({pose.commit_cycle for pose in stream.poses}) != len(stream.poses):
        raise IntegrationViolation("test stream permits one pose commit per cycle")
    if any(
        right.measurement_timestamp_ns <= left.measurement_timestamp_ns
        or right.commit_cycle <= left.commit_cycle
        for left, right in zip(stream.poses, stream.poses[1:])
    ):
        raise IntegrationViolation("pose measurement and commit order must increase")
    events = tuple(event for cluster in stream.clusters for event in cluster.events)
    if not events:
        raise IntegrationViolation("stream must contain events")
    if len({event.event_id for event in events}) != len(events):
        raise IntegrationViolation("event identity repeats")
    if any(event.occurrence_cycle >= event.decision_cycle for event in events):
        raise IntegrationViolation("event occurrence must precede decision")
    if any(
        (right.decision_cycle, right.timestamp_ns)
        < (left.decision_cycle, left.timestamp_ns)
        for left, right in zip(events, events[1:])
    ):
        raise IntegrationViolation("event stream order moves backwards")


def ordered_events(stream: AnalyticStream) -> Tuple[CommonEvent, ...]:
    return tuple(event for cluster in stream.clusters for event in cluster.events)


def reference_fallback(stream: AnalyticStream, event: CommonEvent):
    samples = tuple(
        PoseSample(
            pose.measurement_timestamp_ns,
            pose.commit_cycle,
            pose.quaternion_xyzw,
        )
        for pose in stream.poses
        if pose.valid
    )
    return recover_causal_cav(samples, event.timestamp_ns, event.decision_cycle)


def assert_identity_order_exact_once(
    stream: AnalyticStream, decisions: Sequence[NormalizedDecision]
) -> None:
    expected = tuple(event.event_id for event in ordered_events(stream))
    actual = tuple(decision.event_id for decision in decisions)
    if len(actual) != len(expected):
        raise IntegrationViolation("event/decision cardinality differs")
    if len(set(actual)) != len(actual):
        raise IntegrationViolation("decision identity is not exact-once")
    if actual != expected:
        raise IntegrationViolation("decision identity or order differs")


def _resolve_pose_ids(
    stream: AnalyticStream,
    timestamps: Sequence[int],
    cycles: Sequence[int],
) -> Tuple[int, ...]:
    resolved = []
    for timestamp, cycle in zip(timestamps, cycles):
        match = next(
            (
                pose
                for pose in stream.poses
                if pose.valid
                and pose.measurement_timestamp_ns == timestamp
                and pose.commit_cycle == cycle
            ),
            None,
        )
        if match is None:
            raise IntegrationViolation("candidate cited an unknown pose")
        resolved.append(match.pose_id)
    return tuple(resolved)


def _finish_run(
    candidate_name: str,
    stream: AnalyticStream,
    decisions: Sequence[NormalizedDecision],
    receipts: Sequence[NormalizedPoseReceipt],
) -> CandidateRun:
    assert_identity_order_exact_once(stream, decisions)
    for decision in decisions:
        if any(cycle >= decision.decision_cycle for cycle in decision.used_commit_cycles):
            raise IntegrationViolation("same/future-edge pose reached a decision")
        if decision.quaternion_xyzw is not None:
            norm = math.sqrt(math.fsum(value * value for value in decision.quaternion_xyzw))
            if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
                raise IntegrationViolation("candidate emitted a non-unit quaternion")
    body = {
        "candidate_name": candidate_name,
        "decisions": [asdict(decision) for decision in decisions],
        "pose_receipts": [asdict(receipt) for receipt in receipts],
    }
    return CandidateRun(
        candidate_name,
        tuple(decisions),
        tuple(receipts),
        _sha256(body),
    )


def _run_rg3(stream: AnalyticStream) -> CandidateRun:
    samples = []  # type: List[PoseSample]
    decisions = []  # type: List[NormalizedDecision]
    receipts = []  # type: List[NormalizedPoseReceipt]
    poses_by_cycle = {pose.commit_cycle: pose for pose in stream.poses}
    clusters_by_cycle = {}  # type: Dict[int, List[EventCluster]]
    for cluster in stream.clusters:
        clusters_by_cycle.setdefault(cluster.events[0].decision_cycle, []).append(cluster)
    for cycle in sorted(set(poses_by_cycle) | set(clusters_by_cycle)):
        pose = poses_by_cycle.get(cycle)
        if pose is not None:
            if pose.valid:
                samples.append(PoseSample(
                    pose.measurement_timestamp_ns,
                    pose.commit_cycle,
                    pose.quaternion_xyzw,
                ))
            receipts.append(NormalizedPoseReceipt(
                "RG3",
                pose.pose_id,
                pose.commit_cycle,
                pose.valid,
                pose.commit_cycle + 1 if pose.valid else None,
                None,
                None,
                "visibility_only" if pose.valid else "invalid_pose_ignored",
                _sha256(asdict(pose)),
            ))
        for cluster in clusters_by_cycle.get(cycle, ()):
            for event in cluster.events:
                native = recover_rg3_cav(samples, event.timestamp_ns, event.decision_cycle)
                baseline = native.baseline_decision
                mode = "CANDIDATE" if native.candidate_used else _mode_from_recovery(baseline.mode)
                ids = _resolve_pose_ids(
                    stream,
                    native.used_measurement_timestamps_ns,
                    native.used_commit_cycles,
                )
                decisions.append(NormalizedDecision(
                    "RG3",
                    event.event_id,
                    event.timestamp_ns,
                    event.occurrence_cycle,
                    event.decision_cycle,
                    mode,
                    native.candidate_used,
                    native.quaternion_xyzw,
                    ids,
                    native.used_commit_cycles,
                    None,
                    native.reason,
                ))
    return _finish_run("RG3", stream, decisions, receipts)


def _run_dspb(stream: AnalyticStream) -> CandidateRun:
    model = DSPBModel()
    decisions = []  # type: List[NormalizedDecision]
    receipts = []  # type: List[NormalizedPoseReceipt]
    poses_by_cycle = {pose.commit_cycle: pose for pose in stream.poses}
    clusters_by_cycle = {}  # type: Dict[int, List[EventCluster]]
    for cluster in stream.clusters:
        clusters_by_cycle.setdefault(cluster.events[0].decision_cycle, []).append(cluster)
    for cycle in sorted(set(poses_by_cycle) | set(clusters_by_cycle)):
        pose = poses_by_cycle.get(cycle)
        if pose is not None:
            native_receipt = model.commit_pose(DSPBPose(
                pose.pose_id,
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                pose.quaternion_xyzw,
                value_valid=pose.valid,
                arithmetic_valid=pose.valid,
            ))
            function_faults = tuple(sorted({
                function.invalid_reason
                for function in model.pending_state.expert_functions
                if not function.valid and function.invalid_reason is not None
            }))
            receipt_reason = native_receipt.next_lock_reason
            if function_faults:
                receipt_reason += "|" + "|".join(function_faults)
            receipts.append(NormalizedPoseReceipt(
                "DSPB",
                pose.pose_id,
                pose.commit_cycle,
                pose.valid,
                native_receipt.next_effective_cycle,
                native_receipt.prior_state_version,
                native_receipt.next_state_version,
                receipt_reason,
                native_receipt.receipt_sha256,
            ))
        for cluster in clusters_by_cycle.get(cycle, ()):
            native_events = tuple(DSPBEvent(
                event.event_id,
                event.timestamp_ns,
                event.occurrence_cycle,
                event.decision_cycle,
            ) for event in cluster.events)
            for event, native in zip(cluster.events, model.predict_event_cluster(native_events)):
                if native.mode is DSPBDecisionMode.DSPB:
                    mode = "CANDIDATE"
                elif native.mode is DSPBDecisionMode.CURRENT_CAV:
                    mode = "CAV"
                elif native.mode is DSPBDecisionMode.ZOH:
                    mode = "ZOH"
                else:
                    mode = "BYPASS"
                decisions.append(NormalizedDecision(
                    "DSPB",
                    event.event_id,
                    event.timestamp_ns,
                    event.occurrence_cycle,
                    event.decision_cycle,
                    mode,
                    native.candidate_used,
                    native.output_quaternion_xyzw,
                    native.used_pose_ids,
                    native.used_pose_commit_cycles,
                    native.state_version,
                    native.fallback_reason or "candidate_selected",
                ))
    return _finish_run("DSPB", stream, decisions, receipts)


def _run_pll(stream: AnalyticStream) -> CandidateRun:
    model = SO3PLLModel()
    decisions = []  # type: List[NormalizedDecision]
    receipts = []  # type: List[NormalizedPoseReceipt]
    poses_by_cycle = {pose.commit_cycle: pose for pose in stream.poses}
    pose_by_id = {pose.pose_id: pose for pose in stream.poses}
    clusters_by_cycle = {}  # type: Dict[int, List[EventCluster]]
    for cluster in stream.clusters:
        clusters_by_cycle.setdefault(cluster.events[0].decision_cycle, []).append(cluster)
    for cycle in sorted(set(poses_by_cycle) | set(clusters_by_cycle)):
        pose = poses_by_cycle.get(cycle)
        if pose is not None:
            native_receipt = model.commit_pose(
                pose.pose_id,
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                pose.quaternion_xyzw,
                valid=pose.valid,
            )
            receipts.append(NormalizedPoseReceipt(
                "SO3_PLL",
                pose.pose_id,
                pose.commit_cycle,
                native_receipt.accepted,
                native_receipt.effective_cycle,
                native_receipt.source_state_version,
                native_receipt.published_state_version,
                native_receipt.fault_reason or native_receipt.update_kind,
                _sha256(asdict(native_receipt)),
            ))
        for cluster in clusters_by_cycle.get(cycle, ()):
            for event in cluster.events:
                native = model.predict(event.timestamp_ns, event.decision_cycle)
                if native.mode is SO3PLLMode.PLL:
                    mode = "CANDIDATE"
                    used_ids = () if native.anchor_pose_id is None else (native.anchor_pose_id,)
                    used_cycles = tuple(pose_by_id[item].commit_cycle for item in used_ids)
                else:
                    mode = {
                        SO3PLLMode.CAV: "CAV",
                        SO3PLLMode.ZOH: "ZOH",
                        SO3PLLMode.BYPASS: "BYPASS",
                    }[native.mode]
                    fallback = native.fallback_decision
                    if fallback is None:
                        raise IntegrationViolation("PLL fallback receipt is missing")
                    used_ids = _resolve_pose_ids(
                        stream,
                        fallback.used_measurement_timestamps_ns,
                        fallback.used_commit_cycles,
                    )
                    used_cycles = fallback.used_commit_cycles
                decisions.append(NormalizedDecision(
                    "SO3_PLL",
                    event.event_id,
                    event.timestamp_ns,
                    event.occurrence_cycle,
                    event.decision_cycle,
                    mode,
                    native.candidate_used,
                    native.quaternion_xyzw,
                    used_ids,
                    used_cycles,
                    native.state_version,
                    native.reason,
                ))
    return _finish_run("SO3_PLL", stream, decisions, receipts)


def run_all_candidates(stream: AnalyticStream) -> Mapping[str, CandidateRun]:
    """Replay one analytic ledger through all three actual public APIs."""

    _validate_stream(stream)
    return {
        "RG3": _run_rg3(stream),
        "DSPB": _run_dspb(stream),
        "SO3_PLL": _run_pll(stream),
    }


__all__ = (
    "AnalyticStream",
    "CANDIDATE_NAMES",
    "CandidateRun",
    "CommonEvent",
    "CommonPose",
    "EventCluster",
    "IntegrationViolation",
    "NormalizedDecision",
    "NormalizedPoseReceipt",
    "assert_identity_order_exact_once",
    "ordered_events",
    "quaternion_equivalent",
    "reference_fallback",
    "run_all_candidates",
    "z_rotation_degrees",
)
