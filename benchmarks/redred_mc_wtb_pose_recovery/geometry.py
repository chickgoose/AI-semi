"""Score-free quaternion geometry for MC-WTB Stage-4 pose recovery.

The causal API distinguishes a pose's measurement timestamp from the clock
cycle on which it commits.  A pose committed on an event's cycle is not yet
visible to that event.  Counterfactual resampling is a separate, explicitly
offline upstream-interface utility and never participates in causal CAV
selection.
"""

import bisect
import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence, Tuple


QuaternionXYZW = Tuple[float, float, float, float]

SLERP_LINEAR_DOT_THRESHOLD = 0.9995
DEFAULT_CAV_MAX_HORIZON_NS = 5_000_000
DEFAULT_ZOH_MAX_AGE_NS = 1_000_000
DEFAULT_RESAMPLE_CADENCE_NS = 1_000_000
DEFAULT_CLOCK_PERIOD_PS = 6_500


class GeometryError(ValueError):
    """Raised when pose geometry or its timing contract is invalid."""


class RecoveryMode(Enum):
    """Score-independent causal pose selection result."""

    CAV = "causal_cav"
    ZOH = "zoh_fallback"
    BYPASS = "sensor_fixed_bypass"


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GeometryError("%s must be an integer >= %d" % (name, minimum))
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError("%s must be a finite number" % name)
    result = float(value)
    if not math.isfinite(result):
        raise GeometryError("%s must be a finite number" % name)
    return result


def _quaternion(value: Sequence[float], name: str) -> QuaternionXYZW:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise GeometryError("%s must contain exactly four xyzw components" % name)
    return (
        _finite(value[0], "%s[0]" % name),
        _finite(value[1], "%s[1]" % name),
        _finite(value[2], "%s[2]" % name),
        _finite(value[3], "%s[3]" % name),
    )


def _dot(left: QuaternionXYZW, right: QuaternionXYZW) -> float:
    return math.fsum(left[index] * right[index] for index in range(4))


def normalize_quaternion_xyzw(value: Sequence[float]) -> QuaternionXYZW:
    """Return the unit xyzw quaternion, rejecting zero or non-finite input."""

    quaternion = _quaternion(value, "quaternion_xyzw")
    norm_squared = _dot(quaternion, quaternion)
    if not math.isfinite(norm_squared) or norm_squared <= 0.0:
        raise GeometryError("quaternion_xyzw must have nonzero finite norm")
    norm = math.sqrt(norm_squared)
    result = tuple(component / norm for component in quaternion)
    if not all(math.isfinite(component) for component in result):
        raise GeometryError("quaternion normalization produced a non-finite value")
    return result  # type: ignore[return-value]


def _canonicalize_projective(value: QuaternionXYZW) -> QuaternionXYZW:
    """Choose one deterministic representative of the q/-q rotation."""

    pivot = max(range(4), key=lambda index: (abs(value[index]), -index))
    if value[pivot] < 0.0:
        return tuple(-component for component in value)  # type: ignore[return-value]
    return value


def _align_shortest_arc(
    before: QuaternionXYZW, after: QuaternionXYZW
) -> Tuple[QuaternionXYZW, float]:
    cosine = _dot(before, after)
    if cosine < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
        cosine = -cosine
    return after, min(1.0, max(-1.0, cosine))


def shortest_arc_slerp_xyzw(
    before_xyzw: Sequence[float],
    after_xyzw: Sequence[float],
    alpha: float,
) -> QuaternionXYZW:
    """Interpolate two unit orientations along their shortest quaternion arc."""

    fraction = _finite(alpha, "alpha")
    if not 0.0 <= fraction <= 1.0:
        raise GeometryError("alpha must lie in the closed interval [0, 1]")
    before = _canonicalize_projective(normalize_quaternion_xyzw(before_xyzw))
    after = _canonicalize_projective(normalize_quaternion_xyzw(after_xyzw))
    after, cosine = _align_shortest_arc(before, after)

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
    return normalize_quaternion_xyzw(tuple(
        before_weight * before[index] + after_weight * after[index]
        for index in range(4)
    ))


def _conjugate(value: QuaternionXYZW) -> QuaternionXYZW:
    return (-value[0], -value[1], -value[2], value[3])


def _multiply(
    left: QuaternionXYZW, right: QuaternionXYZW
) -> QuaternionXYZW:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def extrapolate_constant_angular_velocity(
    previous_xyzw: Sequence[float],
    latest_xyzw: Sequence[float],
    previous_interval_ns: int,
    age_ns: int,
) -> QuaternionXYZW:
    """Continue the exact shortest-arc angular velocity beyond ``latest``.

    Quaternion exponentiation, rather than extrapolated NLERP, preserves
    constant angular speed.  ``age_ns`` is the prediction horizon after the
    latest measurement.
    """

    interval = _integer(previous_interval_ns, "previous_interval_ns", 1)
    horizon = _integer(age_ns, "age_ns")
    previous = _canonicalize_projective(normalize_quaternion_xyzw(previous_xyzw))
    latest = _canonicalize_projective(normalize_quaternion_xyzw(latest_xyzw))
    latest, _ = _align_shortest_arc(previous, latest)

    delta = normalize_quaternion_xyzw(_multiply(_conjugate(previous), latest))
    vector_norm = math.sqrt(math.fsum(component * component for component in delta[:3]))
    if vector_norm <= 1.0e-15:
        return latest

    half_angle = math.atan2(vector_norm, min(1.0, max(-1.0, delta[3])))
    scaled_half_angle = half_angle * (float(horizon) / float(interval))
    scale = math.sin(scaled_half_angle) / vector_norm
    step = (
        delta[0] * scale,
        delta[1] * scale,
        delta[2] * scale,
        math.cos(scaled_half_angle),
    )
    return normalize_quaternion_xyzw(_multiply(latest, step))


@dataclass(frozen=True)
class PoseSample:
    """One orientation measurement and the cycle on which it commits."""

    measurement_timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: QuaternionXYZW

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "measurement_timestamp_ns",
            _integer(self.measurement_timestamp_ns, "measurement_timestamp_ns"),
        )
        object.__setattr__(self, "commit_cycle", _integer(self.commit_cycle, "commit_cycle"))
        object.__setattr__(
            self, "quaternion_xyzw", normalize_quaternion_xyzw(self.quaternion_xyzw)
        )

    @property
    def availability_cycle(self) -> int:
        """Alias emphasizing that commit makes the measurement available."""

        return self.commit_cycle


@dataclass(frozen=True)
class BracketInterpolation:
    quaternion_xyzw: QuaternionXYZW
    event_timestamp_ns: int
    decision_cycle: int
    left_measurement_timestamp_ns: int
    right_measurement_timestamp_ns: int
    left_commit_cycle: int
    right_commit_cycle: int
    alpha: float


@dataclass(frozen=True)
class RecoveryDecision:
    mode: RecoveryMode
    quaternion_xyzw: Optional[QuaternionXYZW]
    event_timestamp_ns: int
    event_cycle: int
    used_measurement_timestamps_ns: Tuple[int, ...]
    used_commit_cycles: Tuple[int, ...]
    age_ns: Optional[int]
    previous_interval_ns: Optional[int]
    horizon_limit_ns: Optional[int]
    reason: str


def _validate_series(samples: Sequence[PoseSample]) -> Tuple[PoseSample, ...]:
    source = tuple(samples)
    if any(not isinstance(sample, PoseSample) for sample in source):
        raise GeometryError("pose series must contain only PoseSample values")
    timestamps = tuple(sample.measurement_timestamp_ns for sample in source)
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise GeometryError("pose measurement timestamps must be strictly increasing")
    return source


def interpolate_committed_bracket(
    before: PoseSample,
    after: PoseSample,
    event_timestamp_ns: int,
    decision_cycle: int,
) -> BracketInterpolation:
    """Interpolate only after both sides of a strict-right bracket commit."""

    if not isinstance(before, PoseSample) or not isinstance(after, PoseSample):
        raise GeometryError("bracket endpoints must be PoseSample values")
    timestamp = _integer(event_timestamp_ns, "event_timestamp_ns")
    cycle = _integer(decision_cycle, "decision_cycle")
    if not before.measurement_timestamp_ns <= timestamp < after.measurement_timestamp_ns:
        raise GeometryError("bracket must satisfy left <= event < right")
    if before.commit_cycle >= cycle or after.commit_cycle >= cycle:
        raise GeometryError("both bracket poses must commit before the decision cycle")
    numerator = timestamp - before.measurement_timestamp_ns
    denominator = after.measurement_timestamp_ns - before.measurement_timestamp_ns
    alpha = float(numerator) / float(denominator)
    return BracketInterpolation(
        shortest_arc_slerp_xyzw(
            before.quaternion_xyzw, after.quaternion_xyzw, alpha
        ),
        timestamp,
        cycle,
        before.measurement_timestamp_ns,
        after.measurement_timestamp_ns,
        before.commit_cycle,
        after.commit_cycle,
        alpha,
    )


def recover_causal_cav(
    samples: Sequence[PoseSample],
    event_timestamp_ns: int,
    event_cycle: int,
    max_horizon_ns: int = DEFAULT_CAV_MAX_HORIZON_NS,
    zoh_max_age_ns: int = DEFAULT_ZOH_MAX_AGE_NS,
) -> RecoveryDecision:
    """Select CAV, fresh ZOH, or bypass using committed past poses only."""

    source = _validate_series(samples)
    timestamp = _integer(event_timestamp_ns, "event_timestamp_ns")
    cycle = _integer(event_cycle, "event_cycle")
    maximum_horizon = _integer(max_horizon_ns, "max_horizon_ns")
    maximum_zoh_age = _integer(zoh_max_age_ns, "zoh_max_age_ns")

    available = tuple(
        sample for sample in source
        if sample.commit_cycle < cycle
        and sample.measurement_timestamp_ns <= timestamp
    )
    if not available:
        return RecoveryDecision(
            RecoveryMode.BYPASS, None, timestamp, cycle, (), (), None, None,
            None, "no_committed_past_pose",
        )

    latest = available[-1]
    age = timestamp - latest.measurement_timestamp_ns
    if len(available) >= 2:
        previous = available[-2]
        interval = latest.measurement_timestamp_ns - previous.measurement_timestamp_ns
        horizon_limit = min(maximum_horizon, interval)
        if age <= horizon_limit:
            predicted = extrapolate_constant_angular_velocity(
                previous.quaternion_xyzw,
                latest.quaternion_xyzw,
                interval,
                age,
            )
            return RecoveryDecision(
                RecoveryMode.CAV,
                predicted,
                timestamp,
                cycle,
                (previous.measurement_timestamp_ns, latest.measurement_timestamp_ns),
                (previous.commit_cycle, latest.commit_cycle),
                age,
                interval,
                horizon_limit,
                "guarded_constant_angular_velocity",
            )

    if age <= maximum_zoh_age:
        return RecoveryDecision(
            RecoveryMode.ZOH,
            latest.quaternion_xyzw,
            timestamp,
            cycle,
            (latest.measurement_timestamp_ns,),
            (latest.commit_cycle,),
            age,
            None,
            None,
            "fresh_latest_pose",
        )
    return RecoveryDecision(
        RecoveryMode.BYPASS,
        None,
        timestamp,
        cycle,
        (latest.measurement_timestamp_ns,),
        (latest.commit_cycle,),
        age,
        None,
        None,
        "pose_too_old_for_cav_or_zoh",
    )


def _cycle_at_or_after_timestamp(
    timestamp_ns: int, origin_timestamp_ns: int, clock_period_ps: int
) -> int:
    if timestamp_ns < origin_timestamp_ns:
        raise GeometryError("resample timestamp precedes the cycle origin")
    delta_ps = (timestamp_ns - origin_timestamp_ns) * 1_000
    return (delta_ps + clock_period_ps - 1) // clock_period_ps


def _truth_orientation_at(
    source: Tuple[PoseSample, ...], timestamps: Tuple[int, ...], timestamp_ns: int
) -> QuaternionXYZW:
    right = bisect.bisect_left(timestamps, timestamp_ns)
    if right < len(source) and timestamps[right] == timestamp_ns:
        return source[right].quaternion_xyzw
    left = right - 1
    if left < 0 or right >= len(source):
        raise GeometryError("counterfactual resample lacks a closed truth bracket")
    numerator = timestamp_ns - timestamps[left]
    denominator = timestamps[right] - timestamps[left]
    return shortest_arc_slerp_xyzw(
        source[left].quaternion_xyzw,
        source[right].quaternion_xyzw,
        float(numerator) / float(denominator),
    )


def resample_counterfactual_1khz(
    truth_samples: Sequence[PoseSample],
    start_timestamp_ns: int,
    end_timestamp_ns: int,
    cycle_origin_timestamp_ns: int,
    commit_delay_cycles: int = 1,
    clock_period_ps: int = DEFAULT_CLOCK_PERIOD_PS,
) -> Tuple[PoseSample, ...]:
    """Create the deterministic counterfactual upstream 1 kHz pose stream.

    Truth bracket availability is intentionally not used: this function models
    a separately supplied upstream interface, not causal recovery from the
    original runtime pose packets.  Every emitted packet carries its newly
    modeled commit cycle.
    """

    source = _validate_series(truth_samples)
    if not source:
        raise GeometryError("truth pose series must not be empty")
    start = _integer(start_timestamp_ns, "start_timestamp_ns")
    end = _integer(end_timestamp_ns, "end_timestamp_ns")
    origin = _integer(cycle_origin_timestamp_ns, "cycle_origin_timestamp_ns")
    delay = _integer(commit_delay_cycles, "commit_delay_cycles")
    period_ps = _integer(clock_period_ps, "clock_period_ps", 1)
    if end <= start:
        raise GeometryError("resample interval must be non-empty")

    timestamps = tuple(sample.measurement_timestamp_ns for sample in source)
    result = []
    timestamp = start
    while timestamp < end:
        effective_cycle = _cycle_at_or_after_timestamp(timestamp, origin, period_ps)
        result.append(PoseSample(
            timestamp,
            effective_cycle + delay,
            _truth_orientation_at(source, timestamps, timestamp),
        ))
        timestamp += DEFAULT_RESAMPLE_CADENCE_NS
    return tuple(result)
