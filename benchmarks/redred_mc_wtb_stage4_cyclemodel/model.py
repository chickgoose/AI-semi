"""Normative, score-free Stage-4 two-lane cycle model.

The model operates only on event identity/timing and hash-bound pose packet
metadata.  It deliberately has no quality, loss, reference-bank, or scorer
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


CLOCK_PERIOD_PS = 6_500
PICOSECONDS_PER_NANOSECOND = 1_000
RAW_INGRESS_LANES = 6
INGRESS_STAGING_ENTRIES = 6
EVENT_LANES = 2
TRANSFORM_PIPELINE_CYCLES = 1
BUFFER_ENTRIES = 1_024
EVENT_RECORD_BITS = 102
CAUSAL_POSE_INDEX_BITS = 14
POSE_PACKET_BITS = 192
ZOH_MAX_AGE_NS = 1_000_000
CAV_MAX_HORIZON_NS = 5_000_000
DELAYED_DEADLINE_NS = 6_000_000
ORACLE_CADENCE_NS = 1_000_000
DELAYED_DEADLINE_CYCLES = (
    DELAYED_DEADLINE_NS * PICOSECONDS_PER_NANOSECOND + CLOCK_PERIOD_PS - 1
) // CLOCK_PERIOD_PS
DATASET_POSE_ARRIVAL_ASSUMPTION = "arrival_equals_recorded_timestamp"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CycleModelError(ValueError):
    """An input or a frozen cycle-model invariant failed."""


class Arm(str, Enum):
    ZOH_FRESHNESS = "zoh_freshness"
    DELAYED_EXACT = "delayed_exact"
    CAUSAL_CAV = "causal_cav"
    ORACLE_1KHZ = "oracle_resampled_groundtruth_1khz"


class PoseSource(str, Enum):
    DATASET = "dataset"
    ORACLE_1KHZ = "oracle_resampled_groundtruth_1khz"


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CycleModelError("%s must be a non-negative integer" % where)
    return value


def _nonempty_text(value: Any, where: str) -> str:
    if type(value) is not str or not value:
        raise CycleModelError("%s must be a non-empty string" % where)
    return value


def _sha256(value: Any, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CycleModelError("%s must be a lowercase SHA-256" % where)
    return value


def ceil_div(numerator: int, denominator: int) -> int:
    """Return exact mathematical ceiling division for non-negative integers."""

    numerator = _nonnegative_int(numerator, "numerator")
    denominator = _nonnegative_int(denominator, "denominator")
    if denominator == 0:
        raise CycleModelError("denominator must be positive")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder else 0)


def timestamp_to_cycle(timestamp_ns: int, window_start_ns: int) -> int:
    """Map an integer timestamp with the frozen 6.5 ns ceiling rule."""

    timestamp_ns = _nonnegative_int(timestamp_ns, "timestamp_ns")
    window_start_ns = _nonnegative_int(window_start_ns, "window_start_ns")
    if timestamp_ns < window_start_ns:
        raise CycleModelError("timestamp precedes window_start_ns")
    return ceil_div(
        (timestamp_ns - window_start_ns) * PICOSECONDS_PER_NANOSECOND,
        CLOCK_PERIOD_PS,
    )


@dataclass(frozen=True)
class Event:
    event_id: int
    timestamp_ns: int
    transform_guard_valid: bool = True

    def __post_init__(self) -> None:
        _nonnegative_int(self.event_id, "event_id")
        _nonnegative_int(self.timestamp_ns, "event timestamp_ns")
        if type(self.transform_guard_valid) is not bool:
            raise CycleModelError("transform_guard_valid must be bool")


@dataclass(frozen=True)
class PosePacket:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    source: PoseSource
    pose_sha256: str
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        _nonnegative_int(self.pose_id, "pose_id")
        _nonnegative_int(self.timestamp_ns, "pose timestamp_ns")
        _nonnegative_int(self.commit_cycle, "pose commit_cycle")
        if not isinstance(self.source, PoseSource):
            raise CycleModelError("pose source must be PoseSource")
        _sha256(self.pose_sha256, "pose_sha256")
        if type(self.value_valid) is not bool:
            raise CycleModelError("pose value_valid must be bool")
        if type(self.arithmetic_valid) is not bool:
            raise CycleModelError("pose arithmetic_valid must be bool")

    @classmethod
    def dataset(
        cls,
        pose_id: int,
        timestamp_ns: int,
        window_start_ns: int,
        pose_sha256: str,
        value_valid: bool = True,
        arithmetic_valid: bool = True,
    ) -> "PosePacket":
        return cls(
            pose_id=pose_id,
            timestamp_ns=timestamp_ns,
            commit_cycle=timestamp_to_cycle(timestamp_ns, window_start_ns),
            source=PoseSource.DATASET,
            pose_sha256=pose_sha256,
            value_valid=value_valid,
            arithmetic_valid=arithmetic_valid,
        )

    @classmethod
    def oracle_1khz(
        cls,
        pose_id: int,
        timestamp_ns: int,
        window_start_ns: int,
        pose_sha256: str,
        value_valid: bool = True,
        arithmetic_valid: bool = True,
    ) -> "PosePacket":
        return cls(
            pose_id=pose_id,
            timestamp_ns=timestamp_ns,
            commit_cycle=timestamp_to_cycle(timestamp_ns, window_start_ns) + 1,
            source=PoseSource.ORACLE_1KHZ,
            pose_sha256=pose_sha256,
            value_valid=value_valid,
            arithmetic_valid=arithmetic_valid,
        )


@dataclass(frozen=True)
class DecisionRecord:
    window_id: str
    event_id: int
    event_timestamp_ns: int
    arm: str
    occurrence_cycle: int
    retire_cycle: int
    occurrence_pose_ids: Tuple[int, ...]
    occurrence_pose_timestamps_ns: Tuple[int, ...]
    occurrence_pose_commit_cycles: Tuple[int, ...]
    occurrence_pose_sha256: Tuple[str, ...]
    used_pose_ids: Tuple[int, ...]
    used_pose_timestamps_ns: Tuple[int, ...]
    used_pose_commit_cycles: Tuple[int, ...]
    used_pose_sha256: Tuple[str, ...]
    intentional_future_pose_use: bool
    pose_age_ns: Optional[int]
    disposition: str
    disposition_reason: str
    queue_cycles: int

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "event_timestamp_ns": self.event_timestamp_ns,
            "arm": self.arm,
            "occurrence_cycle": self.occurrence_cycle,
            "retire_cycle": self.retire_cycle,
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
            "intentional_future_pose_use": self.intentional_future_pose_use,
            "pose_age_ns": self.pose_age_ns,
            "disposition": self.disposition,
            "disposition_reason": self.disposition_reason,
            "queue_cycles": self.queue_cycles,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class CycleReceipt:
    window_id: str
    event_id: int
    arm: str
    occurrence_cycle: int
    admission_cycle: int
    admission_lane: int
    launch_cycle: Optional[int]
    launch_lane: Optional[int]
    retire_cycle: int
    retire_lane: int
    fifo_occupancy_before_admission: int
    fifo_occupancy_after_admission: int
    fifo_occupancy_before_retire: int
    fifo_occupancy_after_retire: int
    disposition: str
    disposition_reason: str
    decision_record_sha256: str

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "event_id": self.event_id,
            "arm": self.arm,
            "occurrence_cycle": self.occurrence_cycle,
            "admission_cycle": self.admission_cycle,
            "admission_lane": self.admission_lane,
            "launch_cycle": self.launch_cycle,
            "launch_lane": self.launch_lane,
            "retire_cycle": self.retire_cycle,
            "retire_lane": self.retire_lane,
            "fifo_occupancy_before_admission": self.fifo_occupancy_before_admission,
            "fifo_occupancy_after_admission": self.fifo_occupancy_after_admission,
            "fifo_occupancy_before_retire": self.fifo_occupancy_before_retire,
            "fifo_occupancy_after_retire": self.fifo_occupancy_after_retire,
            "disposition": self.disposition,
            "disposition_reason": self.disposition_reason,
            "decision_record_sha256": self.decision_record_sha256,
        }

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class SimulationResult:
    window_id: str
    arm: Arm
    records: Tuple[DecisionRecord, ...]
    decision_records_sha256: str
    cycle_receipts: Tuple[CycleReceipt, ...]
    cycle_receipts_sha256: str
    common_serializer_cycles: Tuple[int, ...]
    always_bypass_retire_cycles: Tuple[int, ...]
    policy_added_latency_cycles: Tuple[int, ...]
    peak_ingress_staging_occupancy: int
    peak_buffer_occupancy: int
    raw_ingress_lanes: int
    ingress_staging_entries: int
    buffer_entries: int
    event_record_bits: int
    causal_pose_index_bits_in_event_record: int
    pose_packet_bits: int
    event_lanes: int
    transform_pipeline_cycles: int
    dataset_pose_arrival_assumption: str
    arm_disposition_label: str


@dataclass
class _EventState:
    event: Event
    occurrence_cycle: int
    occurrence_snapshot: Tuple[PosePacket, ...]
    accept_cycle: Optional[int] = None
    admission_lane: Optional[int] = None
    deadline_cycle: Optional[int] = None
    inflight: bool = False
    launch_cycle: Optional[int] = None
    launch_lane: Optional[int] = None
    retire_lane: Optional[int] = None
    fifo_occupancy_before_admission: Optional[int] = None
    fifo_occupancy_after_admission: Optional[int] = None
    fifo_occupancy_before_retire: Optional[int] = None
    fifo_occupancy_after_retire: Optional[int] = None
    selected: Tuple[PosePacket, ...] = ()
    disposition: Optional[str] = None
    reason: Optional[str] = None


def _canonical_sha256(value: Any) -> str:
    encoded = (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_and_prepare(
    window_start_ns: int,
    arm: Arm,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
) -> Tuple[List[_EventState], Tuple[PosePacket, ...]]:
    _nonnegative_int(window_start_ns, "window_start_ns")
    if not isinstance(arm, Arm):
        raise CycleModelError("arm must be Arm")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise CycleModelError("events must be an ordered sequence")
    if isinstance(poses, (str, bytes)) or not isinstance(poses, Sequence):
        raise CycleModelError("poses must be an ordered sequence")
    if any(not isinstance(event, Event) for event in events):
        raise CycleModelError("events must contain Event values")
    if any(not isinstance(pose, PosePacket) for pose in poses):
        raise CycleModelError("poses must contain PosePacket values")

    event_values = tuple(events)
    pose_values = tuple(poses)
    if any(
        right.event_id <= left.event_id
        for left, right in zip(event_values, event_values[1:])
    ):
        raise CycleModelError("event IDs must be strictly increasing")
    if any(
        right.timestamp_ns < left.timestamp_ns
        for left, right in zip(event_values, event_values[1:])
    ):
        raise CycleModelError("event timestamps must be nondecreasing")
    if any(
        right.pose_id <= left.pose_id
        for left, right in zip(pose_values, pose_values[1:])
    ):
        raise CycleModelError("pose IDs must be strictly increasing")
    if any(
        right.timestamp_ns <= left.timestamp_ns
        for left, right in zip(pose_values, pose_values[1:])
    ):
        raise CycleModelError("pose timestamps must be strictly increasing")

    expected_source = (
        PoseSource.ORACLE_1KHZ
        if arm is Arm.ORACLE_1KHZ
        else PoseSource.DATASET
    )
    for pose in pose_values:
        if pose.source is not expected_source:
            raise CycleModelError("pose source does not match the selected arm")
        timestamp_cycle = timestamp_to_cycle(pose.timestamp_ns, window_start_ns)
        expected_commit = timestamp_cycle + (
            1 if pose.source is PoseSource.ORACLE_1KHZ else 0
        )
        if pose.commit_cycle != expected_commit:
            raise CycleModelError("pose commit cycle violates delivery timing")
        if (
            pose.source is PoseSource.ORACLE_1KHZ
            and pose.timestamp_ns % ORACLE_CADENCE_NS != 0
        ):
            raise CycleModelError("oracle pose timestamp violates global 1 kHz phase")

    occurrence_cycles = [
        timestamp_to_cycle(event.timestamp_ns, window_start_ns)
        for event in event_values
    ]
    cycle_groups = {}  # type: Dict[int, List[Event]]
    for event, cycle in zip(event_values, occurrence_cycles):
        cycle_groups.setdefault(cycle, []).append(event)
    if any(len(group) > RAW_INGRESS_LANES for group in cycle_groups.values()):
        raise CycleModelError("more than six source records map to one occurrence cycle")

    prepared = []  # type: List[_EventState]
    for event, occurrence_cycle in zip(event_values, occurrence_cycles):
        visible = tuple(
            pose
            for pose in pose_values
            if pose.commit_cycle < occurrence_cycle
            and pose.timestamp_ns <= event.timestamp_ns
        )
        prepared.append(
            _EventState(
                event=event,
                occurrence_cycle=occurrence_cycle,
                occurrence_snapshot=visible[-2:],
                deadline_cycle=(
                    occurrence_cycle + DELAYED_DEADLINE_CYCLES
                    if arm is Arm.DELAYED_EXACT
                    else None
                ),
            )
        )
    return prepared, pose_values


def _capture_occurrences(
    states: List[_EventState],
    next_event: int,
    cycle: int,
    staging: List[_EventState],
) -> Tuple[int, int]:
    if next_event < len(states) and states[next_event].occurrence_cycle < cycle:
        raise CycleModelError("an occurrence was skipped before ingress capture")
    captured = []  # type: List[_EventState]
    while (
        next_event < len(states)
        and states[next_event].occurrence_cycle == cycle
    ):
        captured.append(states[next_event])
        next_event += 1
    if len(captured) > RAW_INGRESS_LANES:
        raise CycleModelError("raw ingress exceeded six lanes")
    if len(staging) + len(captured) > INGRESS_STAGING_ENTRIES:
        raise CycleModelError("six-entry ingress staging overflow")
    staging.extend(captured)
    return next_event, len(staging)


def _provenance(
    packets: Tuple[PosePacket, ...]
) -> Tuple[Tuple[int, ...], Tuple[int, ...], Tuple[int, ...], Tuple[str, ...]]:
    return (
        tuple(packet.pose_id for packet in packets),
        tuple(packet.timestamp_ns for packet in packets),
        tuple(packet.commit_cycle for packet in packets),
        tuple(packet.pose_sha256 for packet in packets),
    )


def _make_record(
    window_id: str,
    arm: Arm,
    state: _EventState,
    retire_cycle: int,
    selected: Tuple[PosePacket, ...],
    disposition: str,
    reason: str,
    queue_cycles: int,
) -> DecisionRecord:
    occurrence = _provenance(state.occurrence_snapshot)
    used = _provenance(selected)
    pose_age = (
        state.event.timestamp_ns - selected[-1].timestamp_ns if selected else None
    )
    return DecisionRecord(
        window_id=window_id,
        event_id=state.event.event_id,
        event_timestamp_ns=state.event.timestamp_ns,
        arm=arm.value,
        occurrence_cycle=state.occurrence_cycle,
        retire_cycle=retire_cycle,
        occurrence_pose_ids=occurrence[0],
        occurrence_pose_timestamps_ns=occurrence[1],
        occurrence_pose_commit_cycles=occurrence[2],
        occurrence_pose_sha256=occurrence[3],
        used_pose_ids=used[0],
        used_pose_timestamps_ns=used[1],
        used_pose_commit_cycles=used[2],
        used_pose_sha256=used[3],
        intentional_future_pose_use=any(
            packet.timestamp_ns > state.event.timestamp_ns for packet in selected
        ),
        pose_age_ns=pose_age,
        disposition=disposition,
        disposition_reason=reason,
        queue_cycles=queue_cycles,
    )


def _select_causal(
    arm: Arm, state: _EventState
) -> Tuple[Tuple[PosePacket, ...], str, str]:
    snapshot = state.occurrence_snapshot
    if not snapshot:
        return (), "raw_bypass", "no_occurrence_pose"
    latest = snapshot[-1]
    age = state.event.timestamp_ns - latest.timestamp_ns
    if arm is Arm.CAUSAL_CAV and len(snapshot) == 2:
        previous = snapshot[0]
        interval = latest.timestamp_ns - previous.timestamp_ns
        horizon = min(CAV_MAX_HORIZON_NS, interval)
        if (
            age <= horizon
            and previous.value_valid
            and latest.value_valid
            and previous.arithmetic_valid
            and latest.arithmetic_valid
            and state.event.transform_guard_valid
        ):
            return snapshot, "corrected_world_ray", "causal_cav"
    if latest.value_valid and latest.arithmetic_valid and age <= ZOH_MAX_AGE_NS:
        reason = (
            "fresh_zoh_fallback"
            if arm is Arm.CAUSAL_CAV
            else "oracle_fresh_zoh"
            if arm is Arm.ORACLE_1KHZ
            else "fresh_zoh"
        )
        return (latest,), "corrected_world_ray", reason
    if not latest.value_valid or not latest.arithmetic_valid:
        return (latest,), "raw_bypass", "invalid_pose"
    return (latest,), "raw_bypass", "stale_pose"


def _run_causal(
    window_id: str,
    arm: Arm,
    states: List[_EventState],
) -> Tuple[List[DecisionRecord], int, int]:
    if not states:
        return [], 0, 0
    records = []  # type: List[DecisionRecord]
    staging = []  # type: List[_EventState]
    inflight = []  # type: List[_EventState]
    next_event = 0
    peak_staging = 0
    cycle = states[0].occurrence_cycle
    while len(records) < len(states):
        if inflight:
            for retire_lane, state in enumerate(inflight):
                if state.launch_cycle is None or state.disposition is None or state.reason is None:
                    raise CycleModelError("internal transform state is incomplete")
                state.retire_lane = retire_lane
                state.fifo_occupancy_before_retire = 0
                state.fifo_occupancy_after_retire = 0
                records.append(
                    _make_record(
                        window_id,
                        arm,
                        state,
                        cycle,
                        state.selected,
                        state.disposition,
                        state.reason,
                        0,
                    )
                )
            inflight = []

        next_event, staging_occupancy = _capture_occurrences(
            states, next_event, cycle, staging
        )
        peak_staging = max(peak_staging, staging_occupancy)
        admitted = staging[:EVENT_LANES]
        del staging[: len(admitted)]
        for admission_lane, state in enumerate(admitted):
            state.accept_cycle = cycle
            state.admission_lane = admission_lane
            state.fifo_occupancy_before_admission = 0
            state.fifo_occupancy_after_admission = 0
            selected, disposition, reason = _select_causal(arm, state)
            state.selected = selected
            state.disposition = disposition
            state.reason = reason
            state.launch_cycle = cycle
            state.launch_lane = admission_lane
        inflight = admitted

        if len(records) == len(states):
            break
        if inflight or staging:
            cycle += 1
        elif next_event < len(states):
            cycle = states[next_event].occurrence_cycle
        else:
            raise CycleModelError("causal simulation stopped before exact retirement")
    return records, 0, peak_staging


def _first_right_pose(
    state: _EventState, poses: Tuple[PosePacket, ...]
) -> Optional[PosePacket]:
    for pose in poses:
        if pose.timestamp_ns > state.event.timestamp_ns:
            return pose
    return None


def _delayed_status(
    state: _EventState,
    poses: Tuple[PosePacket, ...],
    cycle: int,
) -> Tuple[str, Tuple[PosePacket, ...], str]:
    left = state.occurrence_snapshot[-1:] if state.occurrence_snapshot else ()
    if not left:
        return "raw", (), "missing_left_pose"
    right = _first_right_pose(state, poses)
    if right is not None and right.commit_cycle < cycle:
        selected = (left[0], right)
        if (
            left[0].value_valid
            and right.value_valid
            and left[0].arithmetic_valid
            and right.arithmetic_valid
            and state.event.transform_guard_valid
        ):
            return "correct", selected, "bracket_interpolation"
        return "raw", left, "invalid_bracket"
    if state.deadline_cycle is None:
        raise CycleModelError("delayed event has no deadline")
    if cycle >= state.deadline_cycle:
        return "raw", left, "deadline_timeout"
    return "wait", left, "waiting_for_right_bracket"


def _pop_raw_head(
    queue: List[_EventState],
    records: List[DecisionRecord],
    window_id: str,
    cycle: int,
    selected: Tuple[PosePacket, ...],
    reason: str,
    retire_lane: int,
) -> None:
    occupancy_before = len(queue)
    state = queue.pop(0)
    if state.accept_cycle is None:
        raise CycleModelError("delayed FIFO head was never admitted")
    state.retire_lane = retire_lane
    state.fifo_occupancy_before_retire = occupancy_before
    state.fifo_occupancy_after_retire = len(queue)
    records.append(
        _make_record(
            window_id,
            Arm.DELAYED_EXACT,
            state,
            cycle,
            selected,
            "raw_bypass",
            reason,
            cycle - state.accept_cycle,
        )
    )


def _run_delayed(
    window_id: str,
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
) -> Tuple[List[DecisionRecord], int, int]:
    if not states:
        return [], 0, 0
    records = []  # type: List[DecisionRecord]
    queue = []  # type: List[_EventState]
    staging = []  # type: List[_EventState]
    inflight = []  # type: List[_EventState]
    next_event = 0
    peak = 0
    peak_staging = 0
    cycle = states[0].occurrence_cycle

    while len(records) < len(states):
        retirements = 0
        if inflight:
            for expected in inflight:
                if not queue or queue[0] is not expected:
                    raise CycleModelError("transform pipeline reordered the FIFO")
                occupancy_before = len(queue)
                state = queue.pop(0)
                if state.launch_cycle is None:
                    raise CycleModelError("launched delayed event has no cycle")
                if state.accept_cycle is None:
                    raise CycleModelError("launched delayed event was never admitted")
                state.retire_lane = retirements
                state.fifo_occupancy_before_retire = occupancy_before
                state.fifo_occupancy_after_retire = len(queue)
                records.append(
                    _make_record(
                        window_id,
                        Arm.DELAYED_EXACT,
                        state,
                        cycle,
                        state.selected,
                        "corrected_world_ray",
                        "bracket_interpolation",
                        state.launch_cycle - state.accept_cycle,
                    )
                )
                retirements += 1
            inflight = []

        while queue and retirements < EVENT_LANES:
            status, selected, reason = _delayed_status(queue[0], poses, cycle)
            if status != "raw":
                break
            _pop_raw_head(
                queue,
                records,
                window_id,
                cycle,
                selected,
                reason,
                retirements,
            )
            retirements += 1

        next_event, staging_occupancy = _capture_occurrences(
            states, next_event, cycle, staging
        )
        peak_staging = max(peak_staging, staging_occupancy)
        incoming = staging[:EVENT_LANES]
        needed = max(0, len(queue) + len(incoming) - BUFFER_ENTRIES)
        if needed > EVENT_LANES - retirements:
            raise CycleModelError("full-pressure retirement exceeded two lanes")
        for _ in range(needed):
            if not queue or queue[0].inflight:
                raise CycleModelError("full-pressure bypass cannot select the oldest head")
            left = queue[0].occurrence_snapshot[-1:] if queue[0].occurrence_snapshot else ()
            _pop_raw_head(
                queue,
                records,
                window_id,
                cycle,
                left,
                "full_pressure_oldest_bypass",
                retirements,
            )
            retirements += 1

        del staging[: len(incoming)]
        for admission_lane, state in enumerate(incoming):
            state.accept_cycle = cycle
            state.admission_lane = admission_lane
            state.fifo_occupancy_before_admission = len(queue)
            queue.append(state)
            state.fifo_occupancy_after_admission = len(queue)
        if len(queue) > BUFFER_ENTRIES:
            raise CycleModelError("delayed FIFO exceeded 1,024 entries")
        peak = max(peak, len(queue))

        launch = []  # type: List[_EventState]
        for launch_lane, state in enumerate(queue[:EVENT_LANES]):
            if state.inflight:
                break
            status, selected, reason = _delayed_status(state, poses, cycle)
            if status != "correct":
                break
            state.inflight = True
            state.launch_cycle = cycle
            state.launch_lane = launch_lane
            state.selected = selected
            state.disposition = "corrected_world_ray"
            state.reason = reason
            launch.append(state)
        inflight = launch

        if len(records) == len(states):
            break

        force_next_cycle = bool(inflight or staging or incoming)
        if retirements == EVENT_LANES and queue:
            force_next_cycle = True
        if force_next_cycle:
            cycle += 1
            continue

        candidates = []  # type: List[int]
        if next_event < len(states):
            candidates.append(states[next_event].occurrence_cycle)
        if queue:
            head = queue[0]
            if head.deadline_cycle is None:
                raise CycleModelError("queued delayed event has no deadline")
            candidates.append(head.deadline_cycle)
            right = _first_right_pose(head, poses)
            if right is not None:
                candidates.append(right.commit_cycle + 1)
        future = [candidate for candidate in candidates if candidate > cycle]
        if not future:
            cycle += 1
        else:
            cycle = min(future)
    return records, peak, peak_staging


def _validate_conservation(
    states: List[_EventState], records: List[DecisionRecord]
) -> None:
    expected = [state.event.event_id for state in states]
    actual = [record.event_id for record in records]
    if actual != expected or len(set(actual)) != len(actual):
        raise CycleModelError("exact-once ordered retirement failed")
    if any(
        right.retire_cycle < left.retire_cycle
        for left, right in zip(records, records[1:])
    ):
        raise CycleModelError("retirement cycles moved backwards")
    if any(record.retire_cycle < record.occurrence_cycle for record in records):
        raise CycleModelError("an event retired before occurrence")


def _validate_delayed_dispositions(
    states: List[_EventState],
    poses: Tuple[PosePacket, ...],
    records: List[DecisionRecord],
) -> None:
    raw_reasons = {
        "deadline_timeout",
        "full_pressure_oldest_bypass",
        "invalid_bracket",
        "missing_left_pose",
    }
    for state, record in zip(states, records):
        if record.arm != Arm.DELAYED_EXACT.value:
            raise CycleModelError("delayed record lost its diagnostic arm identity")
        if record.disposition == "corrected_world_ray":
            left = state.occurrence_snapshot[-1:] if state.occurrence_snapshot else ()
            right = _first_right_pose(state, poses)
            expected = left + ((right,) if right is not None else ())
            if (
                len(expected) != 2
                or record.used_pose_ids != tuple(pose.pose_id for pose in expected)
                or record.used_pose_timestamps_ns[0] > state.event.timestamp_ns
                or record.used_pose_timestamps_ns[1] <= state.event.timestamp_ns
                or not record.intentional_future_pose_use
                or record.disposition_reason != "bracket_interpolation"
            ):
                raise CycleModelError(
                    "corrected delayed record lacks the first strict right bracket"
                )
        else:
            if record.disposition_reason not in raw_reasons:
                raise CycleModelError("delayed raw bypass lacks an explicit reason")
            if record.intentional_future_pose_use or any(
                timestamp > record.event_timestamp_ns
                for timestamp in record.used_pose_timestamps_ns
            ):
                raise CycleModelError("delayed raw bypass recorded a future used pose")


def _make_cycle_receipts(
    window_id: str,
    arm: Arm,
    states: List[_EventState],
    records: List[DecisionRecord],
) -> Tuple[CycleReceipt, ...]:
    receipts = []  # type: List[CycleReceipt]
    for state, record in zip(states, records):
        required = (
            state.accept_cycle,
            state.admission_lane,
            state.retire_lane,
            state.fifo_occupancy_before_admission,
            state.fifo_occupancy_after_admission,
            state.fifo_occupancy_before_retire,
            state.fifo_occupancy_after_retire,
        )
        if any(value is None for value in required):
            raise CycleModelError("cycle receipt scheduling metadata is incomplete")
        if (state.launch_cycle is None) != (state.launch_lane is None):
            raise CycleModelError("cycle receipt launch cycle/lane pairing differs")
        if not 0 <= state.admission_lane < EVENT_LANES:
            raise CycleModelError("admission lane is outside the two-lane service")
        if not 0 <= state.retire_lane < EVENT_LANES:
            raise CycleModelError("retire lane is outside the two-lane service")
        if state.launch_lane is not None and not 0 <= state.launch_lane < EVENT_LANES:
            raise CycleModelError("launch lane is outside the two-lane pipeline")
        receipts.append(
            CycleReceipt(
                window_id=window_id,
                event_id=state.event.event_id,
                arm=arm.value,
                occurrence_cycle=state.occurrence_cycle,
                admission_cycle=state.accept_cycle,
                admission_lane=state.admission_lane,
                launch_cycle=state.launch_cycle,
                launch_lane=state.launch_lane,
                retire_cycle=record.retire_cycle,
                retire_lane=state.retire_lane,
                fifo_occupancy_before_admission=state.fifo_occupancy_before_admission,
                fifo_occupancy_after_admission=state.fifo_occupancy_after_admission,
                fifo_occupancy_before_retire=state.fifo_occupancy_before_retire,
                fifo_occupancy_after_retire=state.fifo_occupancy_after_retire,
                disposition=record.disposition,
                disposition_reason=record.disposition_reason,
                decision_record_sha256=record.canonical_sha256(),
            )
        )
    return tuple(receipts)


def run_cycle_model(
    *,
    window_id: str,
    window_start_ns: int,
    arm: Arm,
    events: Sequence[Event],
    poses: Sequence[PosePacket],
) -> SimulationResult:
    """Simulate one arm/window and return exact-once score-free decisions."""

    _nonempty_text(window_id, "window_id")
    states, checked_poses = _validate_and_prepare(
        window_start_ns, arm, events, poses
    )
    if arm is Arm.DELAYED_EXACT:
        records, peak, peak_staging = _run_delayed(
            window_id, states, checked_poses
        )
    else:
        records, peak, peak_staging = _run_causal(window_id, arm, states)
    _validate_conservation(states, records)
    if arm is Arm.DELAYED_EXACT:
        _validate_delayed_dispositions(states, checked_poses, records)
    cycle_receipts = _make_cycle_receipts(window_id, arm, states, records)
    serializer_cycles = []  # type: List[int]
    baseline_retire_cycles = []  # type: List[int]
    policy_added_cycles = []  # type: List[int]
    for state, record in zip(states, records):
        if state.accept_cycle is None:
            raise CycleModelError("retired event has no serializer exit cycle")
        serializer_cycle_count = state.accept_cycle - state.occurrence_cycle
        baseline_retire_cycle = state.accept_cycle + TRANSFORM_PIPELINE_CYCLES
        serializer_cycles.append(serializer_cycle_count)
        baseline_retire_cycles.append(baseline_retire_cycle)
        policy_added_cycles.append(record.retire_cycle - baseline_retire_cycle)
    mapping = [record.to_mapping() for record in records]
    label = (
        "DIAGNOSTIC_UPPER_BOUND"
        if arm is Arm.DELAYED_EXACT
        else "INTERFACE_VALUE_ONLY"
        if arm is Arm.ORACLE_1KHZ
        else "CAUSAL_COMPARISON_ARM"
    )
    return SimulationResult(
        window_id=window_id,
        arm=arm,
        records=tuple(records),
        decision_records_sha256=_canonical_sha256(mapping),
        cycle_receipts=cycle_receipts,
        cycle_receipts_sha256=_canonical_sha256(
            [receipt.to_mapping() for receipt in cycle_receipts]
        ),
        common_serializer_cycles=tuple(serializer_cycles),
        always_bypass_retire_cycles=tuple(baseline_retire_cycles),
        policy_added_latency_cycles=tuple(policy_added_cycles),
        peak_ingress_staging_occupancy=peak_staging,
        peak_buffer_occupancy=peak,
        raw_ingress_lanes=RAW_INGRESS_LANES,
        ingress_staging_entries=INGRESS_STAGING_ENTRIES,
        buffer_entries=BUFFER_ENTRIES,
        event_record_bits=EVENT_RECORD_BITS,
        causal_pose_index_bits_in_event_record=CAUSAL_POSE_INDEX_BITS,
        pose_packet_bits=POSE_PACKET_BITS,
        event_lanes=EVENT_LANES,
        transform_pipeline_cycles=TRANSFORM_PIPELINE_CYCLES,
        dataset_pose_arrival_assumption=DATASET_POSE_ARRIVAL_ASSUMPTION,
        arm_disposition_label=label,
    )
