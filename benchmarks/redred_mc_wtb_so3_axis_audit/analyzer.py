"""Score-free SO(3) axis/motion diagnostics for timestamped orientations.

The module deliberately accepts only timestamps and xyzw quaternions.  It has
no campaign, arm, quality, reference-bank, or decision input.  All motion is
computed intrinsically on SO(3), using principal (shortest-arc) relative
rotations rather than Euler-angle subtraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import List, Optional, Sequence, Tuple


QuaternionXYZW = Tuple[float, float, float, float]
Vector3 = Tuple[float, float, float]

_NANOSECONDS_PER_SECOND = 1_000_000_000.0
_ROTATION_ZERO_EPSILON = 1.0e-15
_EIGEN_RELATIVE_TOLERANCE = 1.0e-12


class SO3AxisAuditError(ValueError):
    """The pose stream or analyzer configuration is invalid."""


class RotationFrame(str, Enum):
    """Coordinates used for each relative rotation vector.

    ``BODY`` reports ``q_before^-1 * q_after`` in the earlier sensor frame.
    ``WORLD`` reports ``q_after * q_before^-1`` in the world frame.  Input
    quaternions therefore follow the active sensor-to-world convention.
    """

    BODY = "body"
    WORLD = "world"


def _finite_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SO3AxisAuditError("%s must be a finite number" % where)
    result = float(value)
    if not math.isfinite(result):
        raise SO3AxisAuditError("%s must be a finite number" % where)
    return result


def _timestamp(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SO3AxisAuditError("%s must be a non-negative integer" % where)
    return value


def _quaternion(value: Sequence[float], where: str) -> QuaternionXYZW:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise SO3AxisAuditError("%s must contain exactly four xyzw components" % where)
    raw = tuple(
        _finite_number(component, "%s[%d]" % (where, index))
        for index, component in enumerate(value)
    )
    norm_squared = math.fsum(component * component for component in raw)
    if not math.isfinite(norm_squared) or norm_squared <= 0.0:
        raise SO3AxisAuditError("%s must have nonzero finite norm" % where)
    norm = math.sqrt(norm_squared)
    normalized = tuple(component / norm for component in raw)

    # SO(3) is projective: q and -q are the same orientation.  Choosing the
    # sign from the largest component is deterministic, including w == 0.
    pivot = max(range(4), key=lambda index: (abs(normalized[index]), -index))
    if normalized[pivot] < 0.0:
        normalized = tuple(-component for component in normalized)
    return normalized  # type: ignore[return-value]


def _conjugate(value: QuaternionXYZW) -> QuaternionXYZW:
    return (-value[0], -value[1], -value[2], value[3])


def _multiply(left: QuaternionXYZW, right: QuaternionXYZW) -> QuaternionXYZW:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _frame(value: object) -> RotationFrame:
    if isinstance(value, RotationFrame):
        return value
    try:
        return RotationFrame(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise SO3AxisAuditError("frame must be RotationFrame.BODY or WORLD")


def _relative_quaternion(
    before: QuaternionXYZW,
    after: QuaternionXYZW,
    frame: RotationFrame,
) -> QuaternionXYZW:
    # Align the pair before multiplication so an antipodal representation can
    # never turn a short physical step into a 2*pi quaternion path.
    if math.fsum(a * b for a, b in zip(before, after)) < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
    if frame is RotationFrame.BODY:
        delta = _multiply(_conjugate(before), after)
    else:
        delta = _multiply(after, _conjugate(before))
    norm_squared = math.fsum(component * component for component in delta)
    if not math.isfinite(norm_squared) or norm_squared <= 0.0:
        raise SO3AxisAuditError("relative quaternion must have nonzero finite norm")
    normalized = tuple(component / math.sqrt(norm_squared) for component in delta)
    # Pair alignment makes w non-negative and therefore selects the principal
    # SO(3) logarithm.  Do not apply the general largest-component sign rule
    # again here: doing so could turn (axis, angle=90 deg) back into its
    # equivalent quaternion with angle=270 deg.  Exact half turns use a
    # deterministic axis sign because their scalar component is zero.
    if normalized[3] < 0.0:
        normalized = tuple(-component for component in normalized)
    if abs(normalized[3]) <= _ROTATION_ZERO_EPSILON:
        pivot = max(range(3), key=lambda index: (abs(normalized[index]), -index))
        if normalized[pivot] < 0.0:
            normalized = tuple(-component for component in normalized)
    return normalized  # type: ignore[return-value]


def _rotation_vector(delta: QuaternionXYZW) -> Tuple[Vector3, float, Optional[Vector3]]:
    x, y, z, w = delta
    vector_norm = math.sqrt(math.fsum(component * component for component in (x, y, z)))
    if vector_norm <= _ROTATION_ZERO_EPSILON:
        return (0.0, 0.0, 0.0), 0.0, None
    angle = 2.0 * math.atan2(vector_norm, max(0.0, w))
    # Canonical projective normalization above keeps the principal angle in
    # [0, pi].  Clamp only roundoff immediately above pi.
    angle = min(math.pi, angle)
    axis = (x / vector_norm, y / vector_norm, z / vector_norm)
    return tuple(angle * component for component in axis), angle, axis  # type: ignore[return-value]


def relative_rotation_vector(
    before_quaternion_xyzw: Sequence[float],
    after_quaternion_xyzw: Sequence[float],
    *,
    frame: RotationFrame = RotationFrame.BODY,
) -> Vector3:
    """Return the principal SO(3) logarithm from ``before`` to ``after``.

    The result is a rotation vector in radians.  Its norm is in ``[0, pi]``;
    its direction is the rotation axis in the requested coordinate frame.
    Quaternion scale and antipodal sign do not affect the result.
    """

    selected_frame = _frame(frame)
    before = _quaternion(before_quaternion_xyzw, "before_quaternion_xyzw")
    after = _quaternion(after_quaternion_xyzw, "after_quaternion_xyzw")
    vector, _, _ = _rotation_vector(
        _relative_quaternion(before, after, selected_frame)
    )
    return vector


@dataclass(frozen=True)
class PoseSample:
    """One timestamped active sensor-to-world orientation."""

    timestamp_ns: int
    quaternion_xyzw: QuaternionXYZW

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_ns", _timestamp(self.timestamp_ns, "timestamp_ns"))
        object.__setattr__(
            self,
            "quaternion_xyzw",
            _quaternion(self.quaternion_xyzw, "quaternion_xyzw"),
        )


@dataclass(frozen=True)
class RotationStep:
    """Intrinsic motion over one adjacent pose interval."""

    start_timestamp_ns: int
    end_timestamp_ns: int
    duration_ns: int
    rotation_vector_rad: Vector3
    angle_rad: float
    axis_xyz: Optional[Vector3]
    angular_velocity_xyz_rad_s: Vector3
    angular_speed_rad_s: float
    stationary: bool


@dataclass(frozen=True)
class AxisMotionAnalysis:
    """Aggregate path, endpoint, speed, axis, and reversal diagnostics."""

    frame: RotationFrame
    sample_count: int
    interval_count: int
    elapsed_ns: int
    steps: Tuple[RotationStep, ...]
    stationary_interval_count: int
    moving_interval_count: int
    total_path_angle_rad: float
    net_rotation_vector_rad: Vector3
    net_angle_rad: float
    net_axis_xyz: Optional[Vector3]
    mean_angular_speed_rad_s: float
    rms_angular_speed_rad_s: float
    peak_angular_speed_rad_s: float
    dominant_axis_xyz: Optional[Vector3]
    axis_coherence: Optional[float]
    signed_dominant_rotation_rad: float
    positive_dominant_rotation_rad: float
    negative_dominant_rotation_rad: float
    direction_reversal_count: int
    xyz_absolute_rotation_rad: Vector3


def _symmetric_eigensystem(
    matrix: Sequence[Sequence[float]],
) -> Tuple[Tuple[float, Vector3], ...]:
    """Return descending eigenpairs of a real symmetric 3x3 matrix."""

    values = [list(row) for row in matrix]
    vectors = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    scale = max(1.0, sum(abs(values[index][index]) for index in range(3)))
    for _ in range(32):
        row, column = max(
            ((0, 1), (0, 2), (1, 2)),
            key=lambda pair: abs(values[pair[0]][pair[1]]),
        )
        off_diagonal = values[row][column]
        if abs(off_diagonal) <= 1.0e-15 * scale:
            break
        tau = (values[column][column] - values[row][row]) / (2.0 * off_diagonal)
        tangent = math.copysign(
            1.0 / (abs(tau) + math.sqrt(1.0 + tau * tau)), tau
        )
        cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
        sine = tangent * cosine

        old_rr = values[row][row]
        old_cc = values[column][column]
        values[row][row] = old_rr - tangent * off_diagonal
        values[column][column] = old_cc + tangent * off_diagonal
        values[row][column] = values[column][row] = 0.0
        for other in range(3):
            if other in (row, column):
                continue
            old_ro = values[row][other]
            old_co = values[column][other]
            values[row][other] = values[other][row] = cosine * old_ro - sine * old_co
            values[column][other] = values[other][column] = sine * old_ro + cosine * old_co
        for other in range(3):
            old_or = vectors[other][row]
            old_oc = vectors[other][column]
            vectors[other][row] = cosine * old_or - sine * old_oc
            vectors[other][column] = sine * old_or + cosine * old_oc

    eigenpairs = []
    for index in range(3):
        vector = tuple(vectors[row][index] for row in range(3))
        norm = math.sqrt(math.fsum(component * component for component in vector))
        eigenpairs.append(
            (values[index][index], tuple(component / norm for component in vector))
        )
    eigenpairs.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(eigenpairs)  # type: ignore[return-value]


def _dominant_axis(
    steps: Sequence[RotationStep],
) -> Tuple[Optional[Vector3], Optional[float]]:
    moving = tuple(step for step in steps if not step.stationary and step.axis_xyz is not None)
    total_weight = math.fsum(step.angle_rad for step in moving)
    if total_weight <= 0.0:
        return None, None
    tensor = [[0.0 for _ in range(3)] for _ in range(3)]
    for step in moving:
        assert step.axis_xyz is not None
        for row in range(3):
            for column in range(3):
                tensor[row][column] += (
                    step.angle_rad * step.axis_xyz[row] * step.axis_xyz[column]
                )
    eigenpairs = _symmetric_eigensystem(tensor)
    largest, axis = eigenpairs[0]
    coherence = min(1.0, max(0.0, largest / total_weight))
    if largest - eigenpairs[1][0] <= _EIGEN_RELATIVE_TOLERANCE * total_weight:
        return None, coherence

    resultant = tuple(
        math.fsum(step.rotation_vector_rad[index] for step in moving)
        for index in range(3)
    )
    alignment = math.fsum(left * right for left, right in zip(axis, resultant))
    if abs(alignment) > _ROTATION_ZERO_EPSILON:
        if alignment < 0.0:
            axis = tuple(-component for component in axis)  # type: ignore[assignment]
    else:
        pivot = max(range(3), key=lambda index: (abs(axis[index]), -index))
        if axis[pivot] < 0.0:
            axis = tuple(-component for component in axis)  # type: ignore[assignment]
    return axis, coherence


def analyze_axis_motion(
    samples: Sequence[PoseSample],
    *,
    frame: RotationFrame = RotationFrame.BODY,
    stationary_threshold_rad: float = 1.0e-9,
) -> AxisMotionAnalysis:
    """Analyze adjacent pose motion without consuming any performance score.

    The dominant axis is an undirected, path-angle-weighted principal axis.
    ``axis_coherence`` is its largest tensor eigenvalue divided by total moving
    path angle: 1.0 means perfectly axial motion.  If the two largest
    eigenvalues tie, there is no unique dominant axis and the field is ``None``.
    Directional totals and reversals are evaluated only after orienting that
    axis deterministically.
    """

    selected_frame = _frame(frame)
    threshold = _finite_number(stationary_threshold_rad, "stationary_threshold_rad")
    if threshold < 0.0 or threshold > math.pi:
        raise SO3AxisAuditError("stationary_threshold_rad must lie in [0, pi]")
    source = tuple(samples)
    if not source:
        raise SO3AxisAuditError("samples must contain at least one PoseSample")
    if any(not isinstance(sample, PoseSample) for sample in source):
        raise SO3AxisAuditError("samples must contain only PoseSample values")
    if any(left.timestamp_ns >= right.timestamp_ns for left, right in zip(source, source[1:])):
        raise SO3AxisAuditError("sample timestamps must be strictly increasing")

    steps: List[RotationStep] = []
    for before, after in zip(source, source[1:]):
        duration_ns = after.timestamp_ns - before.timestamp_ns
        delta = _relative_quaternion(
            before.quaternion_xyzw, after.quaternion_xyzw, selected_frame
        )
        vector, angle, axis = _rotation_vector(delta)
        seconds = float(duration_ns) / _NANOSECONDS_PER_SECOND
        velocity = tuple(component / seconds for component in vector)
        steps.append(
            RotationStep(
                before.timestamp_ns,
                after.timestamp_ns,
                duration_ns,
                vector,
                angle,
                axis,
                velocity,  # type: ignore[arg-type]
                angle / seconds,
                angle <= threshold,
            )
        )

    step_tuple = tuple(steps)
    elapsed_ns = source[-1].timestamp_ns - source[0].timestamp_ns
    total_angle = math.fsum(step.angle_rad for step in step_tuple)
    net_vector, net_angle, net_axis = _rotation_vector(
        _relative_quaternion(
            source[0].quaternion_xyzw,
            source[-1].quaternion_xyzw,
            selected_frame,
        )
    )
    if elapsed_ns:
        elapsed_seconds = float(elapsed_ns) / _NANOSECONDS_PER_SECOND
        mean_speed = total_angle / elapsed_seconds
        rms_speed = math.sqrt(
            math.fsum(
                step.angular_speed_rad_s
                * step.angular_speed_rad_s
                * (float(step.duration_ns) / _NANOSECONDS_PER_SECOND)
                for step in step_tuple
            )
            / elapsed_seconds
        )
    else:
        mean_speed = 0.0
        rms_speed = 0.0
    peak_speed = max((step.angular_speed_rad_s for step in step_tuple), default=0.0)
    dominant_axis, coherence = _dominant_axis(step_tuple)

    signed_rotation = 0.0
    positive_rotation = 0.0
    negative_rotation = 0.0
    reversals = 0
    previous_sign = 0
    if dominant_axis is not None:
        projections = tuple(
            math.fsum(
                component * direction
                for component, direction in zip(step.rotation_vector_rad, dominant_axis)
            )
            for step in step_tuple
            if not step.stationary
        )
        signed_rotation = math.fsum(projections)
        positive_rotation = math.fsum(value for value in projections if value > threshold)
        negative_rotation = math.fsum(-value for value in projections if value < -threshold)
        for value in projections:
            sign = 1 if value > threshold else -1 if value < -threshold else 0
            if sign:
                if previous_sign and sign != previous_sign:
                    reversals += 1
                previous_sign = sign

    xyz_absolute = tuple(
        math.fsum(abs(step.rotation_vector_rad[index]) for step in step_tuple)
        for index in range(3)
    )
    stationary_count = sum(1 for step in step_tuple if step.stationary)
    return AxisMotionAnalysis(
        selected_frame,
        len(source),
        len(step_tuple),
        elapsed_ns,
        step_tuple,
        stationary_count,
        len(step_tuple) - stationary_count,
        total_angle,
        net_vector,
        net_angle,
        net_axis,
        mean_speed,
        rms_speed,
        peak_speed,
        dominant_axis,
        coherence,
        signed_rotation,
        positive_rotation,
        negative_rotation,
        reversals,
        xyz_absolute,  # type: ignore[arg-type]
    )
