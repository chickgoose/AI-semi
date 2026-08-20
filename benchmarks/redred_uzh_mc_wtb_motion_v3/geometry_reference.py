"""Independent common-reference geometry for MC-WTB metric V3.

No production MC-WTB geometry is imported.  The conventions are exact integer
nanosecond timestamps, active camera-to-world ``T_WC`` poses in xyzw order,
shortest-arc SLERP, OpenCV pinhole+radtan raw coordinates, and a current-to-
reference ray rotation ``R_WC0.T @ R_WCt``.  Translation is retained but not
used without an authoritative depth/plane model.

Use one :class:`CommonReferenceGeometry` for both queries and historical
anchors so that a metric never compares different camera frames.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Final, Iterable, Sequence

Vector3 = tuple[float, float, float]
QuaternionXYZW = tuple[float, float, float, float]
Matrix3 = tuple[tuple[float, float, float], ...]

CONTINUOUS_EXTENT: Final = "continuous_extent"
NEAREST_PIXEL_SUPPORT: Final = "nearest_pixel_support"
FOV_POLICIES: Final = frozenset({CONTINUOUS_EXTENT, NEAREST_PIXEL_SUPPORT})
IN_FOV: Final = "in_fov"
OUTSIDE_FOV: Final = "outside_fov"
BEHIND_REFERENCE: Final = "behind_reference"
INVALID_GEOMETRY: Final = "invalid_geometry"

_SLERP_LINEAR_THRESHOLD: Final = 0.9995
_INVERSE_MAX_ITERATIONS: Final = 50
_INVERSE_TOLERANCE: Final = 2.0e-15
_MIN_DETERMINANT: Final = 1.0e-18


class GeometryReferenceError(ValueError):
    """Input or numerical result violates the reference contract."""


def _integer_ns(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeometryReferenceError(f"{where} must be an integer nanosecond value")
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryReferenceError(f"{where} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise GeometryReferenceError(f"{where} must be finite")
    return result


def _vector3(value: Sequence[float], where: str) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise GeometryReferenceError(f"{where} must contain three numbers")
    return tuple(_finite(value[i], f"{where}[{i}]") for i in range(3))  # type: ignore[return-value]


def _quaternion(value: Sequence[float], where: str) -> QuaternionXYZW:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise GeometryReferenceError(f"{where} must contain four xyzw numbers")
    return tuple(_finite(value[i], f"{where}[{i}]") for i in range(4))  # type: ignore[return-value]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _normalize_vector(value: Sequence[float], where: str) -> Vector3:
    vector = _vector3(value, where)
    norm2 = _dot(vector, vector)
    if not math.isfinite(norm2) or norm2 <= 0.0:
        raise GeometryReferenceError(f"{where} must have nonzero finite norm")
    scale = 1.0 / math.sqrt(norm2)
    return tuple(component * scale for component in vector)  # type: ignore[return-value]


def normalize_quaternion_xyzw(value: Sequence[float]) -> QuaternionXYZW:
    quaternion = _quaternion(value, "quaternion_xyzw")
    norm2 = _dot(quaternion, quaternion)
    if not math.isfinite(norm2) or norm2 <= 0.0:
        raise GeometryReferenceError("quaternion_xyzw must have nonzero finite norm")
    scale = 1.0 / math.sqrt(norm2)
    return tuple(component * scale for component in quaternion)  # type: ignore[return-value]


def quaternion_xyzw_to_rotation_t_wc(value: Sequence[float]) -> Matrix3:
    """Convert xyzw to active camera-to-world ``R_WC``."""

    x, y, z, w = normalize_quaternion_xyzw(value)
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def transpose(matrix: Matrix3) -> Matrix3:
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def matmul(left: Matrix3, right: Matrix3) -> Matrix3:
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def matvec(matrix: Matrix3, vector: Sequence[float]) -> Vector3:
    checked = _vector3(vector, "vector")
    return tuple(sum(matrix[row][k] * checked[k] for k in range(3)) for row in range(3))  # type: ignore[return-value]


def shortest_arc_slerp_xyzw(
    before_xyzw: Sequence[float], after_xyzw: Sequence[float], alpha: float
) -> QuaternionXYZW:
    """Normalized shortest-arc SLERP with deterministic NLERP near unity."""

    fraction = _finite(alpha, "alpha")
    if not 0.0 <= fraction <= 1.0:
        raise GeometryReferenceError("alpha must be in [0,1]")
    before = normalize_quaternion_xyzw(before_xyzw)
    after = normalize_quaternion_xyzw(after_xyzw)
    cosine = _dot(before, after)
    if cosine < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
        cosine = -cosine
    cosine = min(1.0, max(-1.0, cosine))
    if cosine > _SLERP_LINEAR_THRESHOLD:
        return normalize_quaternion_xyzw(
            tuple((1.0 - fraction) * before[i] + fraction * after[i] for i in range(4))
        )
    theta = math.acos(cosine)
    sine = math.sin(theta)
    if not math.isfinite(sine) or sine <= 0.0:
        raise GeometryReferenceError("degenerate SLERP arc")
    before_weight = math.sin((1.0 - fraction) * theta) / sine
    after_weight = math.sin(fraction * theta) / sine
    return normalize_quaternion_xyzw(
        tuple(before_weight * before[i] + after_weight * after[i] for i in range(4))
    )


@dataclass(frozen=True, slots=True)
class TimedPoseTWC:
    timestamp_ns: int
    quaternion_xyzw: QuaternionXYZW
    translation_world: Vector3 = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_ns", _integer_ns(self.timestamp_ns, "timestamp_ns"))
        object.__setattr__(self, "quaternion_xyzw", normalize_quaternion_xyzw(self.quaternion_xyzw))
        object.__setattr__(self, "translation_world", _vector3(self.translation_world, "translation_world"))


@dataclass(frozen=True, slots=True)
class InterpolatedPoseTWC:
    timestamp_ns: int
    quaternion_xyzw: QuaternionXYZW
    translation_world: Vector3
    before_timestamp_ns: int
    after_timestamp_ns: int
    numerator_ns: int
    denominator_ns: int


class PoseSeries:
    """Strictly increasing pose samples with exact-ns bracket provenance."""

    __slots__ = ("_poses", "_times")

    def __init__(self, poses: Iterable[TimedPoseTWC]) -> None:
        checked = tuple(poses)
        if not checked:
            raise GeometryReferenceError("pose series must not be empty")
        if any(not isinstance(pose, TimedPoseTWC) for pose in checked):
            raise GeometryReferenceError("pose series entries must be TimedPoseTWC")
        times = tuple(pose.timestamp_ns for pose in checked)
        if any(right <= left for left, right in zip(times, times[1:])):
            raise GeometryReferenceError("pose timestamps must be strictly increasing")
        self._poses, self._times = checked, times

    @property
    def poses(self) -> tuple[TimedPoseTWC, ...]:
        return self._poses

    def at(self, timestamp_ns: int) -> InterpolatedPoseTWC:
        timestamp = _integer_ns(timestamp_ns, "pose lookup timestamp_ns")
        index = bisect.bisect_left(self._times, timestamp)
        if index < len(self._times) and self._times[index] == timestamp:
            pose = self._poses[index]
            return InterpolatedPoseTWC(timestamp, pose.quaternion_xyzw, pose.translation_world, timestamp, timestamp, 0, 0)
        if index == 0 or index == len(self._times):
            raise GeometryReferenceError("pose lookup lies outside the closed pose support")
        before, after = self._poses[index - 1], self._poses[index]
        numerator = timestamp - before.timestamp_ns
        denominator = after.timestamp_ns - before.timestamp_ns
        alpha = numerator / denominator
        translation = tuple(
            before.translation_world[axis]
            + alpha * (after.translation_world[axis] - before.translation_world[axis])
            for axis in range(3)
        )
        return InterpolatedPoseTWC(
            timestamp,
            shortest_arc_slerp_xyzw(before.quaternion_xyzw, after.quaternion_xyzw, alpha),
            translation,  # type: ignore[arg-type]
            before.timestamp_ns,
            after.timestamp_ns,
            numerator,
            denominator,
        )


@dataclass(frozen=True, slots=True)
class RadtanCalibration:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    k1: float
    k2: float
    p1: float
    p2: float
    k3: float

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width <= 0:
            raise GeometryReferenceError("width must be a positive integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height <= 0:
            raise GeometryReferenceError("height must be a positive integer")
        for name in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise GeometryReferenceError("fx and fy must be positive")


def distort_normalized(
    x_undistorted: float, y_undistorted: float, calibration: RadtanCalibration
) -> tuple[float, float]:
    """Apply OpenCV radtan in ``k1,k2,p1,p2,k3`` order."""

    x, y = _finite(x_undistorted, "x_undistorted"), _finite(y_undistorted, "y_undistorted")
    r2 = x * x + y * y
    radial = 1.0 + calibration.k1 * r2 + calibration.k2 * r2 * r2 + calibration.k3 * r2 * r2 * r2
    delta_x = 2.0 * calibration.p1 * x * y + calibration.p2 * (r2 + 2.0 * x * x)
    delta_y = calibration.p1 * (r2 + 2.0 * y * y) + 2.0 * calibration.p2 * x * y
    result = (x * radial + delta_x, y * radial + delta_y)
    if not all(math.isfinite(component) for component in result):
        raise GeometryReferenceError("radtan forward model produced non-finite output")
    return result


def undistort_normalized(
    x_distorted: float, y_distorted: float, calibration: RadtanCalibration
) -> tuple[float, float]:
    """Invert radtan with a bounded independent Newton solve."""

    target_x, target_y = _finite(x_distorted, "x_distorted"), _finite(y_distorted, "y_distorted")
    x, y = target_x, target_y
    for _ in range(_INVERSE_MAX_ITERATIONS):
        projected_x, projected_y = distort_normalized(x, y, calibration)
        residual_x, residual_y = projected_x - target_x, projected_y - target_y
        r2 = x * x + y * y
        radial = 1.0 + calibration.k1 * r2 + calibration.k2 * r2 * r2 + calibration.k3 * r2 * r2 * r2
        gradient = calibration.k1 + 2.0 * calibration.k2 * r2 + 3.0 * calibration.k3 * r2 * r2
        dr_dx, dr_dy = 2.0 * x * gradient, 2.0 * y * gradient
        j00 = radial + x * dr_dx + 2.0 * calibration.p1 * y + 6.0 * calibration.p2 * x
        j01 = x * dr_dy + 2.0 * calibration.p1 * x + 2.0 * calibration.p2 * y
        j10 = y * dr_dx + 2.0 * calibration.p1 * x + 2.0 * calibration.p2 * y
        j11 = radial + y * dr_dy + 6.0 * calibration.p1 * y + 2.0 * calibration.p2 * x
        determinant = j00 * j11 - j01 * j10
        if not math.isfinite(determinant) or abs(determinant) < _MIN_DETERMINANT:
            raise GeometryReferenceError("radtan inverse Jacobian is singular")
        step_x = (j11 * residual_x - j01 * residual_y) / determinant
        step_y = (-j10 * residual_x + j00 * residual_y) / determinant
        x, y = x - step_x, y - step_y
        if not math.isfinite(x) or not math.isfinite(y):
            raise GeometryReferenceError("radtan inverse produced non-finite output")
        if max(abs(step_x), abs(step_y), abs(residual_x), abs(residual_y)) <= _INVERSE_TOLERANCE:
            return x, y
    raise GeometryReferenceError("radtan inverse did not converge")


@dataclass(frozen=True, slots=True)
class EventObservation:
    event_id: int
    timestamp_ns: int
    x: float
    y: float
    polarity: int

    def __post_init__(self) -> None:
        if isinstance(self.event_id, bool) or not isinstance(self.event_id, int):
            raise GeometryReferenceError("event_id must be an integer")
        object.__setattr__(self, "timestamp_ns", _integer_ns(self.timestamp_ns, "event timestamp_ns"))
        object.__setattr__(self, "x", _finite(self.x, "event x"))
        object.__setattr__(self, "y", _finite(self.y, "event y"))
        if isinstance(self.polarity, bool) or self.polarity not in (0, 1):
            raise GeometryReferenceError("polarity must be source encoding 0 or 1")


@dataclass(frozen=True, slots=True)
class FOVDecision:
    policy: str
    in_fov: bool
    continuous_extent: bool
    nearest_pixel_supported: bool
    rounded_x: int
    rounded_y: int


def round_nearest_pixel(value: float) -> int:
    return math.floor(_finite(value, "pixel coordinate") + 0.5)


def classify_fov(x: float, y: float, calibration: RadtanCalibration, policy: str) -> FOVDecision:
    """Apply ``[0,W)`` continuous or ``[-.5,W-.5)`` pixel-support bounds."""

    if policy not in FOV_POLICIES:
        raise GeometryReferenceError(f"unknown FOV policy: {policy!r}")
    checked_x, checked_y = _finite(x, "projected x"), _finite(y, "projected y")
    rounded_x, rounded_y = round_nearest_pixel(checked_x), round_nearest_pixel(checked_y)
    continuous = 0.0 <= checked_x < calibration.width and 0.0 <= checked_y < calibration.height
    supported = (
        -0.5 <= checked_x < calibration.width - 0.5
        and -0.5 <= checked_y < calibration.height - 0.5
    )
    selected = continuous if policy == CONTINUOUS_EXTENT else supported
    return FOVDecision(policy, selected, continuous, supported, rounded_x, rounded_y)


@dataclass(frozen=True, slots=True)
class ReferenceWarp:
    event_id: int
    timestamp_ns: int
    reference_timestamp_ns: int
    source_x: float
    source_y: float
    polarity: int
    status: str
    reference_ray: Vector3 | None
    reference_x: float | None
    reference_y: float | None
    fov: FOVDecision | None
    pose_before_timestamp_ns: int
    pose_after_timestamp_ns: int
    pose_numerator_ns: int
    pose_denominator_ns: int


class CommonReferenceGeometry:
    """Prepared pose/calibration context shared by event and anchor cohorts."""

    __slots__ = ("calibration", "poses", "reference_timestamp_ns", "fov_policy", "_reference_inverse")

    def __init__(
        self,
        poses: PoseSeries | Iterable[TimedPoseTWC],
        calibration: RadtanCalibration,
        reference_timestamp_ns: int,
        fov_policy: str,
    ) -> None:
        self.poses = poses if isinstance(poses, PoseSeries) else PoseSeries(poses)
        if not isinstance(calibration, RadtanCalibration):
            raise GeometryReferenceError("calibration must be RadtanCalibration")
        if fov_policy not in FOV_POLICIES:
            raise GeometryReferenceError(f"unknown FOV policy: {fov_policy!r}")
        self.calibration = calibration
        self.reference_timestamp_ns = _integer_ns(reference_timestamp_ns, "reference_timestamp_ns")
        self.fov_policy = fov_policy
        reference = self.poses.at(self.reference_timestamp_ns)
        self._reference_inverse = transpose(quaternion_xyzw_to_rotation_t_wc(reference.quaternion_xyzw))

    def warp(self, event: EventObservation) -> ReferenceWarp:
        if not isinstance(event, EventObservation):
            raise GeometryReferenceError("event must be EventObservation")
        occurrence = self.poses.at(event.timestamp_ns)
        xd = (event.x - self.calibration.cx) / self.calibration.fx
        yd = (event.y - self.calibration.cy) / self.calibration.fy
        try:
            xu, yu = undistort_normalized(xd, yd, self.calibration)
            current_ray = _normalize_vector((xu, yu, 1.0), "current camera ray")
            current_to_reference = matmul(
                self._reference_inverse,
                quaternion_xyzw_to_rotation_t_wc(occurrence.quaternion_xyzw),
            )
            reference_ray = _normalize_vector(matvec(current_to_reference, current_ray), "reference ray")
        except GeometryReferenceError:
            return self._result(event, occurrence, INVALID_GEOMETRY)
        if reference_ray[2] <= 0.0:
            return self._result(event, occurrence, BEHIND_REFERENCE, reference_ray=reference_ray)
        try:
            xd_ref, yd_ref = distort_normalized(
                reference_ray[0] / reference_ray[2], reference_ray[1] / reference_ray[2], self.calibration
            )
            x = self.calibration.fx * xd_ref + self.calibration.cx
            y = self.calibration.fy * yd_ref + self.calibration.cy
            fov = classify_fov(x, y, self.calibration, self.fov_policy)
        except GeometryReferenceError:
            return self._result(event, occurrence, INVALID_GEOMETRY, reference_ray=reference_ray)
        return self._result(
            event, occurrence, IN_FOV if fov.in_fov else OUTSIDE_FOV, reference_ray, x, y, fov
        )

    def warp_many(self, events: Iterable[EventObservation]) -> tuple[ReferenceWarp, ...]:
        """Warp without sorting, dropping or deduplicating occurrences."""

        return tuple(self.warp(event) for event in events)

    def _result(
        self,
        event: EventObservation,
        pose: InterpolatedPoseTWC,
        status: str,
        reference_ray: Vector3 | None = None,
        reference_x: float | None = None,
        reference_y: float | None = None,
        fov: FOVDecision | None = None,
    ) -> ReferenceWarp:
        return ReferenceWarp(
            event.event_id,
            event.timestamp_ns,
            self.reference_timestamp_ns,
            event.x,
            event.y,
            event.polarity,
            status,
            reference_ray,
            reference_x,
            reference_y,
            fov,
            pose.before_timestamp_ns,
            pose.after_timestamp_ns,
            pose.numerator_ns,
            pose.denominator_ns,
        )


def warp_events_to_common_reference(
    events: Iterable[EventObservation],
    poses: PoseSeries | Iterable[TimedPoseTWC],
    calibration: RadtanCalibration,
    reference_timestamp_ns: int,
    fov_policy: str,
) -> tuple[ReferenceWarp, ...]:
    """Convenience entry point for query or anchor cohorts."""

    return CommonReferenceGeometry(
        poses, calibration, reference_timestamp_ns, fov_policy
    ).warp_many(events)
