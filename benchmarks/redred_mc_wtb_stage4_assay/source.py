"""Python-3.8-safe, hash-gated UZH source parsing and geometry inputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re
from typing import BinaryIO, Iterator, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference.development import (
    CALIB_SHA256,
    EVENTS_LINE_COUNT,
    EVENTS_SHA256,
    EVENTS_SIZE_BYTES,
    GROUNDTRUTH_SHA256,
)


class SourceInputError(ValueError):
    """A source identity, syntax, or geometry-input invariant failed."""


_EVENT_LINE = re.compile(rb"([0-9]+\.[0-9]{9}) ([0-9]+) ([0-9]+) ([01])\n\Z")
_INVERSE_MAX_ITERATIONS = 50
_INVERSE_TOLERANCE = 2.0e-15
_MIN_DETERMINANT = 1.0e-18
_SLERP_LINEAR_THRESHOLD = 0.9995


@dataclass(frozen=True)
class SourcePins:
    events_sha256: str
    groundtruth_sha256: str
    calibration_sha256: str
    events_size_bytes: int
    events_line_count: int

    def __post_init__(self) -> None:
        for name in ("events_sha256", "groundtruth_sha256", "calibration_sha256"):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise SourceInputError("%s must be a lowercase SHA-256" % name)
        for name in ("events_size_bytes", "events_line_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SourceInputError("%s must be a positive integer" % name)


OFFICIAL_SOURCE_PINS = SourcePins(
    events_sha256=EVENTS_SHA256,
    groundtruth_sha256=GROUNDTRUTH_SHA256,
    calibration_sha256=CALIB_SHA256,
    events_size_bytes=EVENTS_SIZE_BYTES,
    events_line_count=EVENTS_LINE_COUNT,
)


@dataclass(frozen=True)
class ValidatedSources:
    events_path: Path
    groundtruth_path: Path
    calibration_path: Path
    calibration_bytes: bytes
    calibration_sha256: str
    pins: SourcePins


@dataclass(frozen=True)
class Calibration:
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
        if self.width <= 0 or self.height <= 0:
            raise SourceInputError("calibration dimensions must be positive")
        for name in ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"):
            if not math.isfinite(getattr(self, name)):
                raise SourceInputError("calibration values must be finite")
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise SourceInputError("calibration focal lengths must be positive")


@dataclass(frozen=True)
class PoseSample:
    pose_id: int
    timestamp_ns: int
    quaternion_xyzw: Tuple[float, float, float, float]


@dataclass(frozen=True)
class EventSample:
    event_id: int
    timestamp_ns: int
    x: int
    y: int
    polarity: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SourceInputError("cannot read source: %s" % path.name) from exc
    return digest.hexdigest()


def validate_sources(dataset_dir: Path, pins: SourcePins = OFFICIAL_SOURCE_PINS) -> ValidatedSources:
    """Hash every source before any parser consumes its contents."""

    if not isinstance(pins, SourcePins):
        raise SourceInputError("pins must be SourcePins")
    root = Path(dataset_dir)
    events = root / "events.txt"
    groundtruth = root / "groundtruth.txt"
    calibration = root / "calib.txt"
    for path in (events, groundtruth, calibration):
        if not path.is_file():
            raise SourceInputError("missing source: %s" % path.name)
    try:
        calibration_bytes = calibration.read_bytes()
    except OSError as exc:
        raise SourceInputError("cannot read source: calib.txt") from exc
    calibration_sha256 = hashlib.sha256(calibration_bytes).hexdigest()
    actual = {
        "events.txt": _sha256(events),
        "groundtruth.txt": _sha256(groundtruth),
        "calib.txt": calibration_sha256,
    }
    expected = {
        "events.txt": pins.events_sha256,
        "groundtruth.txt": pins.groundtruth_sha256,
        "calib.txt": pins.calibration_sha256,
    }
    for name in ("events.txt", "groundtruth.txt", "calib.txt"):
        if actual[name] != expected[name]:
            raise SourceInputError("source hash mismatch: %s" % name)
    if events.stat().st_size != pins.events_size_bytes:
        raise SourceInputError("events.txt size differs from its source pin")
    return ValidatedSources(
        events,
        groundtruth,
        calibration,
        calibration_bytes,
        calibration_sha256,
        pins,
    )


def _seconds_text_to_ns(text: str, where: str) -> int:
    try:
        value = Decimal(text) * Decimal(1_000_000_000)
    except InvalidOperation as exc:
        raise SourceInputError("%s timestamp is invalid" % where) from exc
    integral = value.to_integral_value()
    if value != integral:
        raise SourceInputError("%s timestamp is not an integer nanosecond" % where)
    timestamp = int(integral)
    if timestamp < 0:
        raise SourceInputError("%s timestamp is negative" % where)
    return timestamp


def parse_calibration_bytes(payload: bytes) -> Calibration:
    if type(payload) is not bytes:
        raise SourceInputError("calibration payload must be immutable bytes")
    try:
        tokens = payload.decode("ascii").split()
        values = tuple(float(token) for token in tokens)
    except (UnicodeError, ValueError) as exc:
        raise SourceInputError("cannot parse calib.txt") from exc
    if len(values) != 9:
        raise SourceInputError("calib.txt must contain exactly nine values")
    return Calibration(240, 180, *values)


def load_calibration(path: Path) -> Calibration:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise SourceInputError("cannot parse calib.txt") from exc
    return parse_calibration_bytes(payload)


def _normalize_quaternion(values: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(values) != 4 or not all(math.isfinite(float(value)) for value in values):
        raise SourceInputError("quaternion must contain four finite values")
    norm = math.sqrt(math.fsum(float(value) * float(value) for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise SourceInputError("quaternion norm must be positive")
    return tuple(float(value) / norm for value in values)  # type: ignore[return-value]


def canonicalize_quaternion(values: Sequence[float]) -> Tuple[float, float, float, float]:
    """Normalize and apply the frozen largest-component-positive sign rule."""

    normalized = _normalize_quaternion(values)
    largest = max(range(4), key=lambda index: (abs(normalized[index]), -index))
    if normalized[largest] < 0.0:
        return tuple(-value for value in normalized)  # type: ignore[return-value]
    return normalized


def load_pose_samples(path: Path) -> Tuple[PoseSample, ...]:
    poses = []
    previous_timestamp = None  # type: Optional[int]
    try:
        with Path(path).open("r", encoding="ascii", newline="") as stream:
            for line_number, line in enumerate(stream, 1):
                fields = line.rstrip("\n").split(" ")
                if len(fields) != 8 or any(field == "" for field in fields):
                    raise SourceInputError(
                        "groundtruth line %d is not canonical" % line_number
                    )
                timestamp_ns = _seconds_text_to_ns(
                    fields[0], "groundtruth line %d" % line_number
                )
                numeric = tuple(float(field) for field in fields[1:])
                if not all(math.isfinite(value) for value in numeric):
                    raise SourceInputError(
                        "groundtruth line %d is non-finite" % line_number
                    )
                if previous_timestamp is not None and timestamp_ns <= previous_timestamp:
                    raise SourceInputError("pose timestamps must be strictly increasing")
                previous_timestamp = timestamp_ns
                poses.append(
                    PoseSample(
                        line_number - 1,
                        timestamp_ns,
                        canonicalize_quaternion(
                            (numeric[3], numeric[4], numeric[5], numeric[6])
                        ),
                    )
                )
    except SourceInputError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise SourceInputError("cannot parse groundtruth.txt") from exc
    if not poses:
        raise SourceInputError("groundtruth.txt is empty")
    return tuple(poses)


def iter_event_samples(stream: BinaryIO) -> Iterator[EventSample]:
    previous_timestamp = None  # type: Optional[int]
    for event_id, raw in enumerate(stream):
        match = _EVENT_LINE.fullmatch(raw)
        if match is None:
            raise SourceInputError("event line %d is not canonical" % (event_id + 1))
        timestamp_ns = _seconds_text_to_ns(
            match.group(1).decode("ascii"), "event line %d" % (event_id + 1)
        )
        if previous_timestamp is not None and timestamp_ns < previous_timestamp:
            raise SourceInputError("event timestamps are not nondecreasing")
        previous_timestamp = timestamp_ns
        yield EventSample(
            event_id,
            timestamp_ns,
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
        )


def _distort_normalized(x: float, y: float, calibration: Calibration) -> Tuple[float, float]:
    r2 = x * x + y * y
    radial = (
        1.0
        + calibration.k1 * r2
        + calibration.k2 * r2 * r2
        + calibration.k3 * r2 * r2 * r2
    )
    delta_x = 2.0 * calibration.p1 * x * y + calibration.p2 * (r2 + 2.0 * x * x)
    delta_y = calibration.p1 * (r2 + 2.0 * y * y) + 2.0 * calibration.p2 * x * y
    result = (x * radial + delta_x, y * radial + delta_y)
    if not all(math.isfinite(value) for value in result):
        raise SourceInputError("radtan forward model produced non-finite output")
    return result


def _undistort_normalized(
    x_distorted: float, y_distorted: float, calibration: Calibration
) -> Tuple[float, float]:
    target_x, target_y = float(x_distorted), float(y_distorted)
    x, y = target_x, target_y
    for _ in range(_INVERSE_MAX_ITERATIONS):
        projected_x, projected_y = _distort_normalized(x, y, calibration)
        residual_x, residual_y = projected_x - target_x, projected_y - target_y
        r2 = x * x + y * y
        radial = (
            1.0
            + calibration.k1 * r2
            + calibration.k2 * r2 * r2
            + calibration.k3 * r2 * r2 * r2
        )
        gradient = (
            calibration.k1
            + 2.0 * calibration.k2 * r2
            + 3.0 * calibration.k3 * r2 * r2
        )
        dr_dx, dr_dy = 2.0 * x * gradient, 2.0 * y * gradient
        j00 = radial + x * dr_dx + 2.0 * calibration.p1 * y + 6.0 * calibration.p2 * x
        j01 = x * dr_dy + 2.0 * calibration.p1 * x + 2.0 * calibration.p2 * y
        j10 = y * dr_dx + 2.0 * calibration.p1 * x + 2.0 * calibration.p2 * y
        j11 = radial + y * dr_dy + 6.0 * calibration.p1 * y + 2.0 * calibration.p2 * x
        determinant = j00 * j11 - j01 * j10
        if not math.isfinite(determinant) or abs(determinant) < _MIN_DETERMINANT:
            raise SourceInputError("radtan inverse Jacobian is singular")
        step_x = (j11 * residual_x - j01 * residual_y) / determinant
        step_y = (-j10 * residual_x + j00 * residual_y) / determinant
        x, y = x - step_x, y - step_y
        if not math.isfinite(x) or not math.isfinite(y):
            raise SourceInputError("radtan inverse produced non-finite output")
        if max(abs(step_x), abs(step_y), abs(residual_x), abs(residual_y)) <= _INVERSE_TOLERANCE:
            return x, y
    raise SourceInputError("radtan inverse did not converge")


def sensor_ray(event: EventSample, calibration: Calibration) -> Tuple[float, float, float]:
    xd = (event.x - calibration.cx) / calibration.fx
    yd = (event.y - calibration.cy) / calibration.fy
    xu, yu = _undistort_normalized(xd, yd, calibration)
    norm = math.sqrt(xu * xu + yu * yu + 1.0)
    if not math.isfinite(norm) or norm <= 0.0:
        raise SourceInputError("sensor ray norm is invalid")
    return xu / norm, yu / norm, 1.0 / norm


def shortest_arc_slerp(
    before: Sequence[float], after: Sequence[float], numerator: int, denominator: int
) -> Tuple[float, float, float, float]:
    """Frozen endpoint canonicalization, shortest-arc sign rule, and normalization."""

    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, int)
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
        or numerator < 0
        or numerator > denominator
    ):
        raise SourceInputError("SLERP fraction is invalid")
    left = canonicalize_quaternion(before)
    right = canonicalize_quaternion(after)
    dot = math.fsum(a * b for a, b in zip(left, right))
    if dot < 0.0:
        right = tuple(-value for value in right)
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    alpha = numerator / denominator
    if dot > _SLERP_LINEAR_THRESHOLD:
        mixed = tuple((1.0 - alpha) * left[i] + alpha * right[i] for i in range(4))
        return _normalize_quaternion(mixed)
    theta = math.acos(dot)
    sine = math.sin(theta)
    if not math.isfinite(sine) or sine <= 0.0:
        raise SourceInputError("SLERP arc is degenerate")
    left_weight = math.sin((1.0 - alpha) * theta) / sine
    right_weight = math.sin(alpha * theta) / sine
    return _normalize_quaternion(
        tuple(left_weight * left[i] + right_weight * right[i] for i in range(4))
    )
