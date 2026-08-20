"""Pure geometry primitives for the UZH DAVIS MC-WTB data path.

The UZH ground-truth pose is ``T_WC``: it maps camera-frame points into
the motion-capture world frame.  MC-WTB uses a fixed reference camera
``C0`` instead of that motion-capture frame.  This module therefore derives
both relative rotations explicitly:

``R_Ct_C0 = R_WCt.T @ R_WC0`` (reference to current; world-to-sensor), and
``R_C0_Ct = R_WC0.T @ R_WCt`` (current sensor to reference).

Translation is retained in :class:`RelativeGeometry`, but the pixel warp is
deliberately rotation-only.  The module performs no I/O and makes no codec,
transport, reconstruction, or MC-WTB benefit claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Sequence


Vector2 = tuple[float, float]
Vector3 = tuple[float, float, float]
QuaternionXYZW = tuple[float, float, float, float]
Matrix3 = tuple[tuple[float, float, float], ...]

IN_FOV: Final = "in_fov"
OUTSIDE_REFERENCE_IMAGE: Final = "outside_reference_image"
BEHIND_REFERENCE: Final = "behind_reference"
INVALID_DISTORTION: Final = "invalid_distortion"

WARP_STATUSES: Final = frozenset(
    {IN_FOV, OUTSIDE_REFERENCE_IMAGE, BEHIND_REFERENCE, INVALID_DISTORTION}
)
RAW_ESCAPE_STATUSES: Final = frozenset(
    {OUTSIDE_REFERENCE_IMAGE, BEHIND_REFERENCE}
)

SLERP_LINEAR_DOT_THRESHOLD: Final = 0.9995
RADTAN_INVERSE_MAX_ITERATIONS: Final = 50
RADTAN_INVERSE_STEP_TOLERANCE: Final = 1.0e-15
RADTAN_MIN_RADIAL_MAGNITUDE: Final = 1.0e-15


class GeometryError(ValueError):
    """Raised when a caller violates the geometry API contract."""


def _finite_number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{where} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise GeometryError(f"{where} must be a finite number")
    return result


def _positive_integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GeometryError(f"{where} must be a positive integer")
    return value


def _vector3(value: Sequence[float], where: str) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise GeometryError(f"{where} must contain exactly three numbers")
    return (
        _finite_number(value[0], f"{where}[0]"),
        _finite_number(value[1], f"{where}[1]"),
        _finite_number(value[2], f"{where}[2]"),
    )


def _quaternion(value: Sequence[float], where: str) -> QuaternionXYZW:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise GeometryError(f"{where} must contain exactly four xyzw numbers")
    return (
        _finite_number(value[0], f"{where}[0]"),
        _finite_number(value[1], f"{where}[1]"),
        _finite_number(value[2], f"{where}[2]"),
        _finite_number(value[3], f"{where}[3]"),
    )


def _matrix3(value: Sequence[Sequence[float]], where: str) -> Matrix3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise GeometryError(f"{where} must be a 3x3 matrix")
    rows: list[tuple[float, float, float]] = []
    for row_index, row in enumerate(value):
        rows.append(_vector3(row, f"{where}[{row_index}]"))
    return tuple(rows)


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _add(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return tuple(left[index] - right[index] for index in range(3))  # type: ignore[return-value]


def _scale(vector: Vector3, scalar: float) -> Vector3:
    return tuple(component * scalar for component in vector)  # type: ignore[return-value]


def transpose(matrix: Sequence[Sequence[float]]) -> Matrix3:
    """Return the transpose of a finite 3x3 matrix."""

    checked = _matrix3(matrix, "matrix")
    return tuple(
        tuple(checked[column][row] for column in range(3))
        for row in range(3)
    )


def matmul(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> Matrix3:
    """Multiply two finite 3x3 matrices in row-major order."""

    a = _matrix3(left, "left")
    b = _matrix3(right, "right")
    return tuple(
        tuple(sum(a[row][index] * b[index][column] for index in range(3))
              for column in range(3))
        for row in range(3)
    )


def matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> Vector3:
    """Multiply a finite row-major 3x3 matrix by a finite 3-vector."""

    checked_matrix = _matrix3(matrix, "matrix")
    checked_vector = _vector3(vector, "vector")
    return tuple(
        sum(checked_matrix[row][index] * checked_vector[index] for index in range(3))
        for row in range(3)
    )  # type: ignore[return-value]


def normalize_quaternion_xyzw(
    quaternion_xyzw: Sequence[float],
) -> QuaternionXYZW:
    """Normalize a finite, nonzero quaternion stored as ``(x,y,z,w)``."""

    quaternion = _quaternion(quaternion_xyzw, "quaternion_xyzw")
    norm_squared = _dot(quaternion, quaternion)
    if not math.isfinite(norm_squared) or norm_squared <= 0.0:
        raise GeometryError("quaternion_xyzw must have nonzero finite norm")
    norm = math.sqrt(norm_squared)
    normalized = tuple(component / norm for component in quaternion)
    if not all(math.isfinite(component) for component in normalized):
        raise GeometryError("quaternion normalization produced a non-finite value")
    return normalized  # type: ignore[return-value]


def quaternion_xyzw_to_world_camera_matrix(
    quaternion_xyzw: Sequence[float],
) -> Matrix3:
    """Convert normalized xyzw orientation into active camera-to-world ``R_WC``."""

    x, y, z, w = normalize_quaternion_xyzw(quaternion_xyzw)
    return (
        (
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
        ),
        (
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
        ),
        (
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
        ),
    )


def slerp_xyzw(
    before_xyzw: Sequence[float],
    after_xyzw: Sequence[float],
    alpha: float,
) -> QuaternionXYZW:
    """Shortest-arc unit-quaternion SLERP with deterministic NLERP fallback."""

    fraction = _finite_number(alpha, "alpha")
    if not 0.0 <= fraction <= 1.0:
        raise GeometryError("alpha must lie in the closed interval [0,1]")
    before = normalize_quaternion_xyzw(before_xyzw)
    after = normalize_quaternion_xyzw(after_xyzw)
    cosine = _dot(before, after)
    if cosine < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
        cosine = -cosine
    cosine = min(1.0, max(-1.0, cosine))

    if cosine > SLERP_LINEAR_DOT_THRESHOLD:
        blended = tuple(
            (1.0 - fraction) * before[index] + fraction * after[index]
            for index in range(4)
        )
        return normalize_quaternion_xyzw(blended)

    theta = math.acos(cosine)
    sine = math.sin(theta)
    if not math.isfinite(sine) or sine <= 0.0:
        raise GeometryError("SLERP encountered a degenerate quaternion arc")
    before_weight = math.sin((1.0 - fraction) * theta) / sine
    after_weight = math.sin(fraction * theta) / sine
    result = tuple(
        before_weight * before[index] + after_weight * after[index]
        for index in range(4)
    )
    return normalize_quaternion_xyzw(result)


@dataclass(frozen=True, slots=True)
class RadtanCalibration:
    """Raw DAVIS lattice and OpenCV radial-tangential calibration."""

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
        object.__setattr__(self, "width", _positive_integer(self.width, "width"))
        object.__setattr__(self, "height", _positive_integer(self.height, "height"))
        for name in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"):
            object.__setattr__(self, name, _finite_number(getattr(self, name), name))
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise GeometryError("fx and fy must be positive")


@dataclass(frozen=True, slots=True)
class WorldCameraPose:
    """UZH ``T_WC`` components, with translation preserved in world metres."""

    translation_world: Vector3
    quaternion_xyzw: QuaternionXYZW

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "translation_world", _vector3(self.translation_world, "translation_world")
        )
        object.__setattr__(
            self,
            "quaternion_xyzw",
            normalize_quaternion_xyzw(self.quaternion_xyzw),
        )

    @property
    def rotation_world_camera(self) -> Matrix3:
        """Active ``R_WC`` mapping camera-frame vectors into world."""

        return quaternion_xyzw_to_world_camera_matrix(self.quaternion_xyzw)


@dataclass(frozen=True, slots=True)
class TimedWorldCameraPose:
    """A ``T_WC`` pose associated with one exact integer timestamp."""

    timestamp_ns: int
    pose: WorldCameraPose

    def __post_init__(self) -> None:
        if isinstance(self.timestamp_ns, bool) or not isinstance(self.timestamp_ns, int):
            raise GeometryError("timestamp_ns must be an integer")
        if not isinstance(self.pose, WorldCameraPose):
            raise GeometryError("pose must be a WorldCameraPose")


def interpolate_world_camera_pose(
    before: TimedWorldCameraPose,
    after: TimedWorldCameraPose,
    timestamp_ns: int,
) -> TimedWorldCameraPose:
    """Linearly interpolate translation and shortest-arc SLERP orientation."""

    if not isinstance(before, TimedWorldCameraPose) or not isinstance(after, TimedWorldCameraPose):
        raise GeometryError("before and after must be TimedWorldCameraPose values")
    if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int):
        raise GeometryError("timestamp_ns must be an integer")
    if before.timestamp_ns >= after.timestamp_ns:
        raise GeometryError("pose interpolation requires strictly increasing source timestamps")
    if not before.timestamp_ns <= timestamp_ns <= after.timestamp_ns:
        raise GeometryError("timestamp_ns lies outside the closed pose bracket")
    alpha = (timestamp_ns - before.timestamp_ns) / (
        after.timestamp_ns - before.timestamp_ns
    )
    translation = _add(
        _scale(before.pose.translation_world, 1.0 - alpha),
        _scale(after.pose.translation_world, alpha),
    )
    quaternion = slerp_xyzw(
        before.pose.quaternion_xyzw, after.pose.quaternion_xyzw, alpha
    )
    return TimedWorldCameraPose(
        timestamp_ns=timestamp_ns,
        pose=WorldCameraPose(translation, quaternion),
    )


@dataclass(frozen=True, slots=True)
class RelativeGeometry:
    """Relative camera geometry with translation retained but not used by warp."""

    rotation_reference_to_current: Matrix3
    rotation_sensor_to_reference: Matrix3
    translation_current_in_reference: Vector3
    translation_reference_in_current: Vector3
    translation_applied_to_pixel_warp: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rotation_reference_to_current",
            _matrix3(self.rotation_reference_to_current, "rotation_reference_to_current"),
        )
        object.__setattr__(
            self,
            "rotation_sensor_to_reference",
            _matrix3(self.rotation_sensor_to_reference, "rotation_sensor_to_reference"),
        )
        object.__setattr__(
            self,
            "translation_current_in_reference",
            _vector3(self.translation_current_in_reference, "translation_current_in_reference"),
        )
        object.__setattr__(
            self,
            "translation_reference_in_current",
            _vector3(self.translation_reference_in_current, "translation_reference_in_current"),
        )
        if self.translation_applied_to_pixel_warp is not False:
            raise GeometryError("translation_applied_to_pixel_warp must remain False")

    @property
    def rotation_world_to_sensor(self) -> Matrix3:
        """MC-WTB name for ``R_Ct_C0`` (reference ray to current sensor)."""

        return self.rotation_reference_to_current


def relative_geometry(
    reference: WorldCameraPose, current: WorldCameraPose
) -> RelativeGeometry:
    """Derive both relative rotations and translations from two ``T_WC`` poses."""

    if not isinstance(reference, WorldCameraPose) or not isinstance(current, WorldCameraPose):
        raise GeometryError("reference and current must be WorldCameraPose values")
    rotation_world_reference = reference.rotation_world_camera
    rotation_world_current = current.rotation_world_camera
    rotation_reference_to_current = matmul(
        transpose(rotation_world_current), rotation_world_reference
    )
    rotation_sensor_to_reference = matmul(
        transpose(rotation_world_reference), rotation_world_current
    )
    delta_world = _subtract(
        current.translation_world, reference.translation_world
    )
    translation_current_in_reference = matvec(
        transpose(rotation_world_reference), delta_world
    )
    translation_reference_in_current = _scale(
        matvec(rotation_reference_to_current, translation_current_in_reference),
        -1.0,
    )
    return RelativeGeometry(
        rotation_reference_to_current=rotation_reference_to_current,
        rotation_sensor_to_reference=rotation_sensor_to_reference,
        translation_current_in_reference=translation_current_in_reference,
        translation_reference_in_current=translation_reference_in_current,
    )


def distort_normalized(
    x_undistorted: float,
    y_undistorted: float,
    calibration: RadtanCalibration,
) -> Vector2 | None:
    """Apply OpenCV radial-tangential distortion, returning ``None`` if invalid."""

    if not isinstance(calibration, RadtanCalibration):
        raise GeometryError("calibration must be a RadtanCalibration")
    try:
        x = _finite_number(x_undistorted, "x_undistorted")
        y = _finite_number(y_undistorted, "y_undistorted")
    except GeometryError:
        return None
    radius2 = x * x + y * y
    radial = (
        1.0
        + calibration.k1 * radius2
        + calibration.k2 * radius2 * radius2
        + calibration.k3 * radius2 * radius2 * radius2
    )
    delta_x = (
        2.0 * calibration.p1 * x * y
        + calibration.p2 * (radius2 + 2.0 * x * x)
    )
    delta_y = (
        calibration.p1 * (radius2 + 2.0 * y * y)
        + 2.0 * calibration.p2 * x * y
    )
    distorted = (x * radial + delta_x, y * radial + delta_y)
    return distorted if all(math.isfinite(value) for value in distorted) else None


@dataclass(frozen=True, slots=True)
class UndistortionResult:
    """Result of deterministic fixed-point inverse radial-tangential distortion."""

    status: str
    x_undistorted: float | None
    y_undistorted: float | None
    iterations: int

    def __post_init__(self) -> None:
        if self.status not in (IN_FOV, INVALID_DISTORTION):
            raise GeometryError("invalid undistortion status")
        if isinstance(self.iterations, bool) or not isinstance(self.iterations, int) or self.iterations < 0:
            raise GeometryError("iterations must be a non-negative integer")
        if self.status == IN_FOV:
            _finite_number(self.x_undistorted, "x_undistorted")
            _finite_number(self.y_undistorted, "y_undistorted")
        elif self.x_undistorted is not None or self.y_undistorted is not None:
            raise GeometryError("invalid distortion must not expose an undistorted point")


def undistort_normalized(
    x_distorted: float,
    y_distorted: float,
    calibration: RadtanCalibration,
) -> UndistortionResult:
    """Invert OpenCV radtan with fixed constants and an explicit invalid result."""

    if not isinstance(calibration, RadtanCalibration):
        raise GeometryError("calibration must be a RadtanCalibration")
    try:
        target_x = _finite_number(x_distorted, "x_distorted")
        target_y = _finite_number(y_distorted, "y_distorted")
    except GeometryError:
        return UndistortionResult(INVALID_DISTORTION, None, None, 0)
    x, y = target_x, target_y
    for iteration in range(1, RADTAN_INVERSE_MAX_ITERATIONS + 1):
        radius2 = x * x + y * y
        radial = (
            1.0
            + calibration.k1 * radius2
            + calibration.k2 * radius2 * radius2
            + calibration.k3 * radius2 * radius2 * radius2
        )
        if not math.isfinite(radial) or abs(radial) < RADTAN_MIN_RADIAL_MAGNITUDE:
            return UndistortionResult(INVALID_DISTORTION, None, None, iteration)
        delta_x = (
            2.0 * calibration.p1 * x * y
            + calibration.p2 * (radius2 + 2.0 * x * x)
        )
        delta_y = (
            calibration.p1 * (radius2 + 2.0 * y * y)
            + 2.0 * calibration.p2 * x * y
        )
        next_x = (target_x - delta_x) / radial
        next_y = (target_y - delta_y) / radial
        if not math.isfinite(next_x) or not math.isfinite(next_y):
            return UndistortionResult(INVALID_DISTORTION, None, None, iteration)
        step = max(abs(next_x - x), abs(next_y - y))
        x, y = next_x, next_y
        if step <= RADTAN_INVERSE_STEP_TOLERANCE:
            check = distort_normalized(x, y, calibration)
            if check is None:
                return UndistortionResult(INVALID_DISTORTION, None, None, iteration)
            residual = max(abs(check[0] - target_x), abs(check[1] - target_y))
            if not math.isfinite(residual) or residual > RADTAN_INVERSE_STEP_TOLERANCE:
                return UndistortionResult(INVALID_DISTORTION, None, None, iteration)
            return UndistortionResult(IN_FOV, x, y, iteration)
    return UndistortionResult(
        INVALID_DISTORTION, None, None, RADTAN_INVERSE_MAX_ITERATIONS
    )


def deterministic_pixel_round(value: float) -> int:
    """Round a non-negative continuous pixel coordinate via ``floor(v+0.5)``."""

    checked = _finite_number(value, "pixel coordinate")
    if checked < 0.0:
        raise GeometryError("pixel coordinate must be non-negative before rounding")
    return int(math.floor(checked + 0.5))


@dataclass(frozen=True, slots=True)
class WarpResult:
    """Rotation-only raw-reference warp result with disjoint status classes."""

    status: str
    x_reference: int | None = None
    y_reference: int | None = None
    x_reference_float: float | None = None
    y_reference_float: float | None = None
    ray_z: float | None = None
    distortion_iterations: int = 0

    def __post_init__(self) -> None:
        if self.status not in WARP_STATUSES:
            raise GeometryError(f"unknown warp status: {self.status!r}")
        if self.status == IN_FOV:
            if (
                isinstance(self.x_reference, bool)
                or not isinstance(self.x_reference, int)
                or isinstance(self.y_reference, bool)
                or not isinstance(self.y_reference, int)
            ):
                raise GeometryError("in_fov result requires integer reference pixels")
        elif self.x_reference is not None or self.y_reference is not None:
            raise GeometryError("non-in_fov result must not expose rounded pixels")
        if self.x_reference_float is not None:
            _finite_number(self.x_reference_float, "x_reference_float")
        if self.y_reference_float is not None:
            _finite_number(self.y_reference_float, "y_reference_float")
        if self.ray_z is not None:
            _finite_number(self.ray_z, "ray_z")
        if (
            isinstance(self.distortion_iterations, bool)
            or not isinstance(self.distortion_iterations, int)
            or self.distortion_iterations < 0
        ):
            raise GeometryError("distortion_iterations must be non-negative")

    @property
    def requires_raw_escape(self) -> bool:
        """Whether valid geometry placed the event outside the reference lattice."""

        return self.status in RAW_ESCAPE_STATUSES

    @property
    def geometry_invalid(self) -> bool:
        """Whether this result must be excluded from valid-geometry OOF counts."""

        return self.status == INVALID_DISTORTION


def warp_raw_sensor_to_reference(
    x_raw: float,
    y_raw: float,
    calibration: RadtanCalibration,
    geometry: RelativeGeometry,
) -> WarpResult:
    """Warp one raw DAVIS pixel to the fixed raw-reference camera lattice.

    The function undistorts the input, applies only
    ``geometry.rotation_sensor_to_reference``, and forward-distorts the result.
    Both retained relative translations are intentionally ignored.
    """

    if not isinstance(calibration, RadtanCalibration):
        raise GeometryError("calibration must be a RadtanCalibration")
    if not isinstance(geometry, RelativeGeometry):
        raise GeometryError("geometry must be a RelativeGeometry")
    x = _finite_number(x_raw, "x_raw")
    y = _finite_number(y_raw, "y_raw")
    if not (0.0 <= x <= calibration.width - 1 and 0.0 <= y <= calibration.height - 1):
        raise GeometryError("raw input pixel lies outside the calibrated sensor lattice")

    distorted_x = (x - calibration.cx) / calibration.fx
    distorted_y = (y - calibration.cy) / calibration.fy
    undistorted = undistort_normalized(distorted_x, distorted_y, calibration)
    if undistorted.status == INVALID_DISTORTION:
        return WarpResult(
            status=INVALID_DISTORTION,
            distortion_iterations=undistorted.iterations,
        )
    assert undistorted.x_undistorted is not None
    assert undistorted.y_undistorted is not None
    ray_reference = matvec(
        geometry.rotation_sensor_to_reference,
        (undistorted.x_undistorted, undistorted.y_undistorted, 1.0),
    )
    ray_z = ray_reference[2]
    if ray_z <= 0.0:
        return WarpResult(
            status=BEHIND_REFERENCE,
            ray_z=ray_z,
            distortion_iterations=undistorted.iterations,
        )

    projected = distort_normalized(
        ray_reference[0] / ray_z,
        ray_reference[1] / ray_z,
        calibration,
    )
    if projected is None:
        return WarpResult(
            status=INVALID_DISTORTION,
            ray_z=ray_z,
            distortion_iterations=undistorted.iterations,
        )
    reference_x = calibration.fx * projected[0] + calibration.cx
    reference_y = calibration.fy * projected[1] + calibration.cy
    if not math.isfinite(reference_x) or not math.isfinite(reference_y):
        return WarpResult(
            status=INVALID_DISTORTION,
            ray_z=ray_z,
            distortion_iterations=undistorted.iterations,
        )
    if not (
        0.0 <= reference_x <= calibration.width - 1
        and 0.0 <= reference_y <= calibration.height - 1
    ):
        return WarpResult(
            status=OUTSIDE_REFERENCE_IMAGE,
            x_reference_float=reference_x,
            y_reference_float=reference_y,
            ray_z=ray_z,
            distortion_iterations=undistorted.iterations,
        )
    return WarpResult(
        status=IN_FOV,
        x_reference=deterministic_pixel_round(reference_x),
        y_reference=deterministic_pixel_round(reference_y),
        x_reference_float=reference_x,
        y_reference_float=reference_y,
        ray_z=ray_z,
        distortion_iterations=undistorted.iterations,
    )


__all__ = [
    "BEHIND_REFERENCE",
    "GeometryError",
    "IN_FOV",
    "INVALID_DISTORTION",
    "Matrix3",
    "OUTSIDE_REFERENCE_IMAGE",
    "QuaternionXYZW",
    "RADTAN_INVERSE_MAX_ITERATIONS",
    "RADTAN_INVERSE_STEP_TOLERANCE",
    "RAW_ESCAPE_STATUSES",
    "RadtanCalibration",
    "RelativeGeometry",
    "SLERP_LINEAR_DOT_THRESHOLD",
    "TimedWorldCameraPose",
    "Vector2",
    "Vector3",
    "WARP_STATUSES",
    "WarpResult",
    "WorldCameraPose",
    "deterministic_pixel_round",
    "distort_normalized",
    "interpolate_world_camera_pose",
    "matmul",
    "matvec",
    "normalize_quaternion_xyzw",
    "quaternion_xyzw_to_world_camera_matrix",
    "relative_geometry",
    "slerp_xyzw",
    "transpose",
    "undistort_normalized",
    "warp_raw_sensor_to_reference",
]
