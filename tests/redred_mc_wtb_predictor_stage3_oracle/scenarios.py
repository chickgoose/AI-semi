"""Deterministic, wholly synthetic motion and transport scenarios."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Sequence, Set, Tuple

from oracle import quaternion_from_rotation_vector
from protocol import EventRecord, PoseRecord, Quaternion


CYCLE_NS = 100_000
AngleLaw = Callable[[int], float]


@dataclass(frozen=True)
class SyntheticScenario:
    name: str
    poses: Tuple[PoseRecord, ...]
    events: Tuple[EventRecord, ...]
    truth_at: Callable[[int], Quaternion]


def z_axis_quaternion(angle_rad: float) -> Quaternion:
    return quaternion_from_rotation_vector((0.0, 0.0, angle_rad))


def stationary(angle_rad: float = 0.0) -> AngleLaw:
    return lambda timestamp_ns: angle_rad


def constant_rate(rate_rad_s: float, initial_angle_rad: float = 0.0) -> AngleLaw:
    return lambda timestamp_ns: initial_angle_rad + rate_rad_s * timestamp_ns * 1.0e-9


def constant_acceleration(
    acceleration_rad_s2: float,
    initial_rate_rad_s: float = 0.0,
    initial_angle_rad: float = 0.0,
) -> AngleLaw:
    def law(timestamp_ns: int) -> float:
        seconds = timestamp_ns * 1.0e-9
        return initial_angle_rad + initial_rate_rad_s * seconds + 0.5 * acceleration_rad_s2 * seconds * seconds

    return law


def stop(rate_rad_s: float, stop_timestamp_ns: int) -> AngleLaw:
    def law(timestamp_ns: int) -> float:
        moving_ns = min(timestamp_ns, stop_timestamp_ns)
        return rate_rad_s * moving_ns * 1.0e-9

    return law


def reversal(rate_rad_s: float, reversal_timestamp_ns: int) -> AngleLaw:
    def law(timestamp_ns: int) -> float:
        if timestamp_ns <= reversal_timestamp_ns:
            return rate_rad_s * timestamp_ns * 1.0e-9
        before = rate_rad_s * reversal_timestamp_ns * 1.0e-9
        after = rate_rad_s * (timestamp_ns - reversal_timestamp_ns) * 1.0e-9
        return before - after

    return law


def make_motion_scenario(
    name: str,
    pose_timestamps_ns: Sequence[int],
    event_timestamps_ns: Sequence[int],
    angle_law: AngleLaw,
    pose_commit_delay_cycles: int = 1,
    event_decision_delay_cycles: int = 1,
    pose_delay_overrides: Optional[Dict[int, int]] = None,
    dropped_pose_indices: Iterable[int] = (),
    invalid_pose_indices: Iterable[int] = (),
) -> SyntheticScenario:
    delay_overrides = pose_delay_overrides or {}
    dropped: Set[int] = set(dropped_pose_indices)
    invalid: Set[int] = set(invalid_pose_indices)
    poses = []
    for index, timestamp_ns in enumerate(pose_timestamps_ns):
        if index in dropped:
            continue
        measurement_cycle = timestamp_ns // CYCLE_NS
        delay = delay_overrides.get(index, pose_commit_delay_cycles)
        poses.append(
            PoseRecord(
                pose_id="p%d" % index,
                measurement_timestamp_ns=timestamp_ns,
                commit_cycle=measurement_cycle + delay,
                quaternion_xyzw=z_axis_quaternion(angle_law(timestamp_ns)),
                valid=index not in invalid,
            )
        )

    events = []
    for index, timestamp_ns in enumerate(event_timestamps_ns):
        occurrence_cycle = timestamp_ns // CYCLE_NS
        events.append(
            EventRecord(
                event_id="e%d" % index,
                timestamp_ns=timestamp_ns,
                occurrence_cycle=occurrence_cycle,
                decision_cycle=occurrence_cycle + event_decision_delay_cycles,
                x=index % 32,
                y=(index * 3) % 24,
                polarity=1 if index % 2 == 0 else -1,
            )
        )

    def truth_at(timestamp_ns: int) -> Quaternion:
        return z_axis_quaternion(angle_law(timestamp_ns))

    return SyntheticScenario(name, tuple(poses), tuple(events), truth_at)


def near_pi_pair() -> Tuple[PoseRecord, PoseRecord]:
    return (
        PoseRecord("p0", 0, 1, z_axis_quaternion(0.0)),
        PoseRecord("p1", 1_000_000, 11, z_axis_quaternion(math.pi)),
    )
