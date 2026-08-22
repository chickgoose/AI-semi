"""Model-only residual-gated three-pose constant-acceleration predictor.

This module accepts only timestamped, committed orientation samples and one
event timestamp/cycle.  It has no selector, label, result, scorer, dataset, or
event-filter input.  Every valid call first obtains the frozen two-pose causal
CAV decision.  RG3 is attempted only when that baseline selected CAV; every
RG3 gate failure returns the baseline quaternion/mode/provenance unchanged.

The active sensor-to-world convention is the Stage-12 convention.  For three
visible poses R0, R1, R2 it computes::

    R01 = R0.T * R1                  R12 = R1.T * R2
    w01 = Log(R01) / dt01            w12 = Log(R12) / dt12
    w01_1 = R01.T * w01
    a_1 = (w12 - w01_1) / (0.5 * (dt01 + dt12))
    w12_2 = R12.T * w12              a_2 = R12.T * a_1
    R(event) = R2 * Exp(w12_2*h + 0.5*a_2*h*h)

All policy values below identify one untuned Stage-3 candidate.  Changing any
of them defines a different candidate; callers cannot supply replacements.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryDecision,
    RecoveryMode,
    normalize_quaternion_xyzw,
    recover_causal_cav,
)


QuaternionXYZW = Tuple[float, float, float, float]
Vector3 = Tuple[float, float, float]

_NS_PER_SECOND = 1_000_000_000.0
_VECTOR_EPSILON = 1.0e-15


class RG3Error(ValueError):
    """The frozen RG3 policy or an RG3 result invariant is invalid."""


@dataclass(frozen=True)
class RG3Policy:
    """Exact, non-adaptive validity policy for this candidate identity."""

    candidate_id: str
    maximum_pose_interval_ns: int
    near_pi_margin_rad: float
    maximum_rate_change_ratio: float
    minimum_direction_cosine: float
    maximum_acceleration_contribution_ratio: float

    def __post_init__(self) -> None:
        if type(self.candidate_id) is not str or not self.candidate_id:
            raise RG3Error("candidate_id must be nonempty text")
        if (
            isinstance(self.maximum_pose_interval_ns, bool)
            or not isinstance(self.maximum_pose_interval_ns, int)
            or self.maximum_pose_interval_ns < 1
        ):
            raise RG3Error("maximum_pose_interval_ns must be a positive integer")
        for name in (
            "near_pi_margin_rad",
            "maximum_rate_change_ratio",
            "minimum_direction_cosine",
            "maximum_acceleration_contribution_ratio",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RG3Error("%s must be finite" % name)
            if not math.isfinite(float(value)):
                raise RG3Error("%s must be finite" % name)
        if not 0.0 < self.near_pi_margin_rad < math.pi:
            raise RG3Error("near_pi_margin_rad must lie in (0, pi)")
        if not 0.0 < self.maximum_rate_change_ratio <= 1.0:
            raise RG3Error("maximum_rate_change_ratio must lie in (0, 1]")
        if not -1.0 <= self.minimum_direction_cosine <= 1.0:
            raise RG3Error("minimum_direction_cosine must lie in [-1, 1]")
        if not 0.0 < self.maximum_acceleration_contribution_ratio <= 1.0:
            raise RG3Error(
                "maximum_acceleration_contribution_ratio must lie in (0, 1]"
            )


RG3_POLICY = RG3Policy(
    candidate_id=(
        "redred.mc_wtb_predictor_stage3.rg3_cav/"
        "body_transport3_cadence10ms_nearpi1em6_"
        "residual0p5_dircos0_accel0p25/v1"
    ),
    maximum_pose_interval_ns=10_000_000,
    near_pi_margin_rad=1.0e-6,
    maximum_rate_change_ratio=0.5,
    minimum_direction_cosine=0.0,
    maximum_acceleration_contribution_ratio=0.25,
)


@dataclass(frozen=True)
class RG3Decision:
    """One always-on candidate decision and its independently frozen baseline."""

    candidate_id: str
    candidate_used: bool
    quaternion_xyzw: Optional[QuaternionXYZW]
    event_timestamp_ns: int
    event_cycle: int
    used_measurement_timestamps_ns: Tuple[int, ...]
    used_commit_cycles: Tuple[int, ...]
    age_ns: Optional[int]
    angular_velocity_xyz_rad_s: Optional[Vector3]
    angular_acceleration_xyz_rad_s2: Optional[Vector3]
    prediction_rotation_vector_rad: Optional[Vector3]
    reason: str
    baseline_decision: RecoveryDecision


class _GateFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _dot3(left: Vector3, right: Vector3) -> float:
    return math.fsum(left[index] * right[index] for index in range(3))


def _norm3(value: Vector3) -> float:
    result = math.sqrt(math.fsum(component * component for component in value))
    if not math.isfinite(result):
        raise _GateFailure("nonfinite_geometry")
    return result


def _add3(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _subtract3(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _scale3(value: Vector3, scale: float) -> Vector3:
    result = tuple(component * scale for component in value)
    if not all(math.isfinite(component) for component in result):
        raise _GateFailure("nonfinite_geometry")
    return result  # type: ignore[return-value]


def _dot4(left: QuaternionXYZW, right: QuaternionXYZW) -> float:
    return math.fsum(left[index] * right[index] for index in range(4))


def _canonicalize(value: QuaternionXYZW) -> QuaternionXYZW:
    pivot = max(range(4), key=lambda index: (abs(value[index]), -index))
    if value[pivot] < 0.0:
        return tuple(-component for component in value)  # type: ignore[return-value]
    return value


def _conjugate(value: QuaternionXYZW) -> QuaternionXYZW:
    return (-value[0], -value[1], -value[2], value[3])


def _multiply(left: QuaternionXYZW, right: QuaternionXYZW) -> QuaternionXYZW:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    result = (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )
    if not all(math.isfinite(component) for component in result):
        raise _GateFailure("nonfinite_geometry")
    return result


def _relative_log(
    before_xyzw: QuaternionXYZW, after_xyzw: QuaternionXYZW
) -> Tuple[QuaternionXYZW, Vector3, float]:
    before = _canonicalize(normalize_quaternion_xyzw(before_xyzw))
    after = _canonicalize(normalize_quaternion_xyzw(after_xyzw))
    if _dot4(before, after) < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
    relative = normalize_quaternion_xyzw(_multiply(_conjugate(before), after))
    if relative[3] < 0.0:
        relative = tuple(-component for component in relative)  # type: ignore[assignment]
    if abs(relative[3]) <= _VECTOR_EPSILON:
        pivot = max(range(3), key=lambda index: (abs(relative[index]), -index))
        if relative[pivot] < 0.0:
            relative = tuple(-component for component in relative)  # type: ignore[assignment]
    vector_norm = math.sqrt(math.fsum(component * component for component in relative[:3]))
    if not math.isfinite(vector_norm):
        raise _GateFailure("nonfinite_geometry")
    if vector_norm <= _VECTOR_EPSILON:
        return relative, (0.0, 0.0, 0.0), 0.0
    angle = min(math.pi, 2.0 * math.atan2(vector_norm, max(0.0, relative[3])))
    vector = tuple(angle * component / vector_norm for component in relative[:3])
    if not all(math.isfinite(component) for component in vector):
        raise _GateFailure("nonfinite_geometry")
    return relative, vector, angle  # type: ignore[return-value]


def _rotate_vector(quaternion_xyzw: QuaternionXYZW, vector: Vector3) -> Vector3:
    unit = normalize_quaternion_xyzw(quaternion_xyzw)
    embedded = (vector[0], vector[1], vector[2], 0.0)
    rotated = _multiply(_multiply(unit, embedded), _conjugate(unit))
    result = rotated[:3]
    if not all(math.isfinite(component) for component in result):
        raise _GateFailure("nonfinite_geometry")
    return result  # type: ignore[return-value]


def _exp_rotation_vector(vector: Vector3) -> QuaternionXYZW:
    angle = _norm3(vector)
    if angle <= _VECTOR_EPSILON:
        return (0.0, 0.0, 0.0, 1.0)
    half = 0.5 * angle
    scale = math.sin(half) / angle
    result = (
        vector[0] * scale,
        vector[1] * scale,
        vector[2] * scale,
        math.cos(half),
    )
    return normalize_quaternion_xyzw(result)


def _fallback(baseline: RecoveryDecision, reason: str) -> RG3Decision:
    return RG3Decision(
        RG3_POLICY.candidate_id,
        False,
        baseline.quaternion_xyzw,
        baseline.event_timestamp_ns,
        baseline.event_cycle,
        baseline.used_measurement_timestamps_ns,
        baseline.used_commit_cycles,
        baseline.age_ns,
        None,
        None,
        None,
        reason,
        baseline,
    )


def _predict_rg3(
    visible: Sequence[PoseSample], baseline: RecoveryDecision
) -> RG3Decision:
    if len(visible) < 3:
        raise _GateFailure("insufficient_visible_pose_history")
    pose0, pose1, pose2 = visible[-3:]
    dt01_ns = pose1.measurement_timestamp_ns - pose0.measurement_timestamp_ns
    dt12_ns = pose2.measurement_timestamp_ns - pose1.measurement_timestamp_ns
    if (
        dt01_ns < 1
        or dt12_ns < 1
        or dt01_ns > RG3_POLICY.maximum_pose_interval_ns
        or dt12_ns > RG3_POLICY.maximum_pose_interval_ns
    ):
        raise _GateFailure("pose_cadence_out_of_bounds")

    relative01, log01, angle01 = _relative_log(
        pose0.quaternion_xyzw, pose1.quaternion_xyzw
    )
    relative12, log12, angle12 = _relative_log(
        pose1.quaternion_xyzw, pose2.quaternion_xyzw
    )
    largest_unambiguous = math.pi - RG3_POLICY.near_pi_margin_rad
    if angle01 >= largest_unambiguous or angle12 >= largest_unambiguous:
        raise _GateFailure("near_pi_pose_step")

    dt01_s = float(dt01_ns) / _NS_PER_SECOND
    dt12_s = float(dt12_ns) / _NS_PER_SECOND
    velocity01_frame0 = _scale3(log01, 1.0 / dt01_s)
    velocity12_frame1 = _scale3(log12, 1.0 / dt12_s)
    velocity01_frame1 = _rotate_vector(_conjugate(relative01), velocity01_frame0)
    speed01 = _norm3(velocity01_frame1)
    speed12 = _norm3(velocity12_frame1)
    if speed01 <= _VECTOR_EPSILON or speed12 <= _VECTOR_EPSILON:
        raise _GateFailure("stationary_pose_step")
    direction_cosine = _dot3(velocity01_frame1, velocity12_frame1) / (
        speed01 * speed12
    )
    if not math.isfinite(direction_cosine):
        raise _GateFailure("nonfinite_geometry")
    direction_cosine = min(1.0, max(-1.0, direction_cosine))
    if direction_cosine < RG3_POLICY.minimum_direction_cosine:
        raise _GateFailure("direction_gate")

    residual_frame1 = _subtract3(velocity12_frame1, velocity01_frame1)
    residual_norm = _norm3(residual_frame1)
    if residual_norm > RG3_POLICY.maximum_rate_change_ratio * max(speed01, speed12):
        raise _GateFailure("rate_change_gate")
    midpoint_separation_s = 0.5 * (dt01_s + dt12_s)
    acceleration_frame1 = _scale3(residual_frame1, 1.0 / midpoint_separation_s)

    velocity_frame2 = _rotate_vector(_conjugate(relative12), velocity12_frame1)
    acceleration_frame2 = _rotate_vector(_conjugate(relative12), acceleration_frame1)
    if baseline.age_ns is None:
        raise _GateFailure("baseline_age_missing")
    horizon_s = float(baseline.age_ns) / _NS_PER_SECOND
    velocity_rotation = _scale3(velocity_frame2, horizon_s)
    acceleration_rotation = _scale3(
        acceleration_frame2, 0.5 * horizon_s * horizon_s
    )
    velocity_rotation_norm = _norm3(velocity_rotation)
    acceleration_rotation_norm = _norm3(acceleration_rotation)
    if (
        velocity_rotation_norm > _VECTOR_EPSILON
        and acceleration_rotation_norm
        > RG3_POLICY.maximum_acceleration_contribution_ratio
        * velocity_rotation_norm
    ):
        raise _GateFailure("acceleration_horizon_gate")
    prediction_rotation = _add3(velocity_rotation, acceleration_rotation)
    if _norm3(prediction_rotation) >= largest_unambiguous:
        raise _GateFailure("near_pi_prediction")

    latest = _canonicalize(normalize_quaternion_xyzw(pose2.quaternion_xyzw))
    predicted = normalize_quaternion_xyzw(
        _multiply(latest, _exp_rotation_vector(prediction_rotation))
    )
    return RG3Decision(
        RG3_POLICY.candidate_id,
        True,
        predicted,
        baseline.event_timestamp_ns,
        baseline.event_cycle,
        tuple(
            pose.measurement_timestamp_ns for pose in (pose0, pose1, pose2)
        ),
        tuple(pose.commit_cycle for pose in (pose0, pose1, pose2)),
        baseline.age_ns,
        velocity_frame2,
        acceleration_frame2,
        prediction_rotation,
        "rg3_constant_acceleration",
        baseline,
    )


def recover_rg3_cav(
    samples: Sequence[PoseSample], event_timestamp_ns: int, event_cycle: int
) -> RG3Decision:
    """Attempt frozen RG3 for one event, otherwise return exact frozen CAV.

    The input surface intentionally contains no event identity, selector field,
    label, score, result, or caller-selected threshold.  Pose visibility is
    strict: ``commit_cycle < event_cycle`` and measurement time no later than
    the event timestamp.  A same-edge or future-measurement pose is invisible.
    """

    source = tuple(samples)
    baseline = recover_causal_cav(source, event_timestamp_ns, event_cycle)
    if baseline.mode is not RecoveryMode.CAV:
        return _fallback(baseline, "baseline_%s" % baseline.mode.value)
    visible = tuple(
        pose
        for pose in source
        if pose.commit_cycle < baseline.event_cycle
        and pose.measurement_timestamp_ns <= baseline.event_timestamp_ns
    )
    try:
        return _predict_rg3(visible, baseline)
    except _GateFailure as error:
        return _fallback(baseline, error.reason)
    except (ArithmeticError, GeometryError, ValueError):
        return _fallback(baseline, "invalid_rg3_arithmetic")


__all__ = (
    "RG3Decision",
    "RG3Error",
    "RG3Policy",
    "RG3_POLICY",
    "recover_rg3_cav",
)
