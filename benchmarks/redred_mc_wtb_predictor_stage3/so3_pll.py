"""Strictly causal supplied-pose-residual SO(3) PLL candidate.

This model consumes only authoritative pose commits and event timestamp/cycle
coordinates.  It never consumes events, event loss, scorer output, labels, or
future poses.  Input quaternions are active sensor-to-world ``R_WC`` rotations
in xyzw order.  The oscillator stores a right-trivialized angular velocity in
the body frame at an authoritative pose's *measurement timestamp*::

    q_forecast(t) = q_anchor * Exp(omega_body * (t - t_anchor))

Every valid pose update first forecasts an immutable pre-pose state to the new
pose's measurement timestamp.  Its shortest-arc residual may update only a new
state version whose effective cycle is one later than the pose commit cycle.
Consequently, an event decided on the commit edge still observes the old state.

The candidate is model-only.  When it is unlocked or a prediction guard fails,
it delegates verbatim to the frozen current CAV recovery implementation, which
provides the exact CAV -> fresh ZOH -> sensor-fixed fallback chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample as RecoveryPoseSample,
    RecoveryDecision,
    normalize_quaternion_xyzw,
    recover_causal_cav,
)


QuaternionXYZW = Tuple[float, float, float, float]
Vector3 = Tuple[float, float, float]

_NANOSECONDS_PER_SECOND = 1_000_000_000.0
_VECTOR_EPSILON = 1.0e-15
_UINT64_MAX = (1 << 64) - 1
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
CANDIDATE_FAMILY = "SO3_PLL_A5_V1"


class SO3PLLError(ValueError):
    """Raised for an invalid model API call or frozen configuration."""


class SO3PLLMode(str, Enum):
    """Geometry source selected for one event decision."""

    PLL = "so3_pll"
    CAV = "causal_cav"
    ZOH = "zoh_fallback"
    BYPASS = "sensor_fixed_bypass"


def _integer(
    value: object,
    where: str,
    minimum: int = 0,
    maximum: int = _UINT64_MAX,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise SO3PLLError(
            "%s must be an integer in [%d,%d]" % (where, minimum, maximum)
        )
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SO3PLLError("%s must be a finite number" % where)
    result = float(value)
    if not math.isfinite(result):
        raise SO3PLLError("%s must be a finite number" % where)
    return result


def _vector3(value: Sequence[float], where: str) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise SO3PLLError("%s must contain exactly three components" % where)
    return (
        _finite(value[0], "%s[0]" % where),
        _finite(value[1], "%s[1]" % where),
        _finite(value[2], "%s[2]" % where),
    )


def _norm(value: Vector3) -> float:
    result = math.sqrt(math.fsum(component * component for component in value))
    if not math.isfinite(result):
        raise SO3PLLError("vector norm is non-finite")
    return result


def _scale(value: Vector3, factor: float) -> Vector3:
    return tuple(component * factor for component in value)  # type: ignore[return-value]


def _add(*values: Vector3) -> Vector3:
    return tuple(
        math.fsum(value[index] for value in values) for index in range(3)
    )  # type: ignore[return-value]


def _dot3(left: Vector3, right: Vector3) -> float:
    return math.fsum(a * b for a, b in zip(left, right))


def _canonical_quaternion(value: Sequence[float]) -> QuaternionXYZW:
    try:
        normalized = normalize_quaternion_xyzw(value)
    except GeometryError as error:
        raise SO3PLLError(str(error))
    pivot = max(range(4), key=lambda index: (abs(normalized[index]), -index))
    if normalized[pivot] < 0.0:
        normalized = tuple(-component for component in normalized)
    return normalized  # type: ignore[return-value]


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


def _relative_shortest_arc(
    before: QuaternionXYZW, after: QuaternionXYZW
) -> QuaternionXYZW:
    if math.fsum(a * b for a, b in zip(before, after)) < 0.0:
        after = tuple(-component for component in after)  # type: ignore[assignment]
    delta = normalize_quaternion_xyzw(_multiply(_conjugate(before), after))
    if delta[3] < 0.0:
        delta = tuple(-component for component in delta)
    if abs(delta[3]) <= _VECTOR_EPSILON:
        pivot = max(range(3), key=lambda index: (abs(delta[index]), -index))
        if delta[pivot] < 0.0:
            delta = tuple(-component for component in delta)
    return delta  # type: ignore[return-value]


def _rotation_vector(delta: QuaternionXYZW) -> Tuple[Vector3, float]:
    vector = (delta[0], delta[1], delta[2])
    vector_norm = _norm(vector)
    if vector_norm <= _VECTOR_EPSILON:
        return (0.0, 0.0, 0.0), 0.0
    angle = min(
        math.pi,
        2.0 * math.atan2(vector_norm, max(0.0, min(1.0, delta[3]))),
    )
    result = _scale(vector, angle / vector_norm)
    return result, angle


def _quaternion_exp(rotation_vector_rad: Vector3) -> QuaternionXYZW:
    angle = _norm(rotation_vector_rad)
    if angle <= _VECTOR_EPSILON:
        return (0.0, 0.0, 0.0, 1.0)
    half_angle = 0.5 * angle
    scale = math.sin(half_angle) / angle
    return normalize_quaternion_xyzw((
        rotation_vector_rad[0] * scale,
        rotation_vector_rad[1] * scale,
        rotation_vector_rad[2] * scale,
        math.cos(half_angle),
    ))


def _rotate_vector(quaternion: QuaternionXYZW, value: Vector3) -> Vector3:
    """Actively rotate ``value`` by a unit quaternion."""

    x, y, z, w = quaternion
    vx, vy, vz = value
    return (
        (1.0 - 2.0 * (y * y + z * z)) * vx
        + 2.0 * (x * y - z * w) * vy
        + 2.0 * (x * z + y * w) * vz,
        2.0 * (x * y + z * w) * vx
        + (1.0 - 2.0 * (x * x + z * z)) * vy
        + 2.0 * (y * z - x * w) * vz,
        2.0 * (x * z - y * w) * vx
        + 2.0 * (y * z + x * w) * vy
        + (1.0 - 2.0 * (x * x + y * y)) * vz,
    )


def _forecast(
    anchor_quaternion_xyzw: QuaternionXYZW,
    angular_velocity_body_rad_s: Vector3,
    elapsed_ns: int,
) -> QuaternionXYZW:
    elapsed = _integer(elapsed_ns, "elapsed_ns")
    elapsed_seconds = float(elapsed) / _NANOSECONDS_PER_SECOND
    step = _quaternion_exp(_scale(angular_velocity_body_rad_s, elapsed_seconds))
    return _canonical_quaternion(_multiply(anchor_quaternion_xyzw, step))


@dataclass(frozen=True)
class SO3PLLConfig:
    """Frozen, outcome-independent candidate parameters.

    The gains operate on residual angular rate.  A bound violation is never
    silently clipped: it publishes an unlocked reset state and records the
    exact fault, so subsequent events take the frozen fallback chain.
    """

    proportional_gain: float = 0.25
    integral_gain: float = 0.02
    lock_residual_max_rad: float = math.radians(2.0)
    phase_jump_max_rad: float = math.radians(30.0)
    near_pi_margin_rad: float = 1.0e-6
    max_gap_ns: int = 20_000_000
    max_prediction_horizon_ns: int = 5_000_000
    cav_max_horizon_ns: int = 5_000_000
    zoh_max_age_ns: int = 1_000_000
    max_proportional_correction_rad_s: float = math.radians(2_000.0)
    max_integral_correction_rad_s: float = math.radians(500.0)
    max_angular_rate_rad_s: float = math.radians(4_000.0)
    lock_count: int = 2
    limit_cycle_min_residual_rad: float = math.radians(0.05)
    limit_cycle_cosine_max: float = -0.95

    @property
    def candidate_id(self) -> str:
        """Return an identity that changes with every material numeric knob."""

        numeric = (
            self.proportional_gain,
            self.integral_gain,
            self.lock_residual_max_rad,
            self.phase_jump_max_rad,
            self.near_pi_margin_rad,
            self.max_proportional_correction_rad_s,
            self.max_integral_correction_rad_s,
            self.max_angular_rate_rad_s,
            self.limit_cycle_min_residual_rad,
            self.limit_cycle_cosine_max,
        )
        integers = (
            self.max_gap_ns,
            self.max_prediction_horizon_ns,
            self.cav_max_horizon_ns,
            self.zoh_max_age_ns,
            self.lock_count,
        )
        return "%s:%s:%s" % (
            CANDIDATE_FAMILY,
            ",".join(format(value, ".17g") for value in numeric),
            ",".join(str(value) for value in integers),
        )

    def __post_init__(self) -> None:
        for name in (
            "proportional_gain",
            "integral_gain",
            "lock_residual_max_rad",
            "phase_jump_max_rad",
            "near_pi_margin_rad",
            "max_proportional_correction_rad_s",
            "max_integral_correction_rad_s",
            "max_angular_rate_rad_s",
            "limit_cycle_min_residual_rad",
            "limit_cycle_cosine_max",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        for name in (
            "max_gap_ns",
            "max_prediction_horizon_ns",
            "cav_max_horizon_ns",
            "zoh_max_age_ns",
        ):
            object.__setattr__(self, name, _integer(getattr(self, name), name))
        object.__setattr__(
            self, "lock_count", _integer(self.lock_count, "lock_count", 1, 10)
        )

        if not 0.0 <= self.proportional_gain <= 1.0:
            raise SO3PLLError("proportional_gain must lie in [0,1]")
        if not 0.0 <= self.integral_gain <= 1.0:
            raise SO3PLLError("integral_gain must lie in [0,1]")
        if not 0.0 <= self.lock_residual_max_rad < self.phase_jump_max_rad:
            raise SO3PLLError(
                "lock_residual_max_rad must be non-negative and below phase_jump_max_rad"
            )
        if not 0.0 < self.phase_jump_max_rad < math.pi:
            raise SO3PLLError("phase_jump_max_rad must lie in (0,pi)")
        if not 0.0 < self.near_pi_margin_rad < math.pi:
            raise SO3PLLError("near_pi_margin_rad must lie in (0,pi)")
        if self.phase_jump_max_rad >= math.pi - self.near_pi_margin_rad:
            raise SO3PLLError("phase-jump guard must precede the near-pi guard")
        if self.max_gap_ns <= 0 or self.max_prediction_horizon_ns <= 0:
            raise SO3PLLError("gap and prediction horizons must be positive")
        if self.cav_max_horizon_ns <= 0:
            raise SO3PLLError("cav_max_horizon_ns must be positive")
        if (
            self.max_proportional_correction_rad_s <= 0.0
            or self.max_integral_correction_rad_s <= 0.0
            or self.max_angular_rate_rad_s <= 0.0
        ):
            raise SO3PLLError("rate and correction bounds must be positive")
        if self.limit_cycle_min_residual_rad < 0.0:
            raise SO3PLLError("limit_cycle_min_residual_rad must be non-negative")
        if not -1.0 <= self.limit_cycle_cosine_max < 0.0:
            raise SO3PLLError("limit_cycle_cosine_max must lie in [-1,0)")


@dataclass(frozen=True)
class ForecastState:
    """One immutable oscillator state published after a pose commit."""

    state_version: int
    effective_cycle: int
    source_commit_cycle: int
    anchor_pose_id: int
    anchor_measurement_timestamp_ns: int
    anchor_quaternion_xyzw: QuaternionXYZW
    angular_velocity_body_rad_s: Vector3
    integral_correction_body_rad_s: Vector3
    previous_interval_ns: Optional[int]
    lock_streak: int
    locked: bool
    previous_residual_body_rad: Optional[Vector3]
    status: str


@dataclass(frozen=True)
class PoseUpdateReceipt:
    """Causal evidence for one supplied pose commit attempt."""

    pose_id: int
    measurement_timestamp_ns: int
    commit_cycle: int
    accepted: bool
    effective_cycle: Optional[int]
    source_state_version: Optional[int]
    forecast_generation_cycle: Optional[int]
    published_state_version: Optional[int]
    forecast_quaternion_xyzw: Optional[QuaternionXYZW]
    residual_body_rad: Optional[Vector3]
    residual_norm_rad: Optional[float]
    locked_before: bool
    locked_after: bool
    lock_streak_after: int
    update_kind: str
    fault_reason: Optional[str]


@dataclass(frozen=True)
class SO3PLLDecision:
    """One append-only event-time prediction result."""

    mode: SO3PLLMode
    quaternion_xyzw: Optional[QuaternionXYZW]
    event_timestamp_ns: int
    decision_cycle: int
    candidate_used: bool
    state_version: Optional[int]
    anchor_pose_id: Optional[int]
    age_ns: Optional[int]
    reason: str
    fallback_decision: Optional[RecoveryDecision]


class SO3PLLModel:
    """State-versioned model-only SO(3) supplied-pose PLL."""

    def __init__(self, config: SO3PLLConfig = SO3PLLConfig()) -> None:
        if not isinstance(config, SO3PLLConfig):
            raise SO3PLLError("config must be an SO3PLLConfig")
        self._config = config
        self._poses = []  # type: list[RecoveryPoseSample]
        self._pose_ids = []  # type: list[int]
        self._states = []  # type: list[ForecastState]
        self._receipts = []  # type: list[PoseUpdateReceipt]

    @property
    def config(self) -> SO3PLLConfig:
        return self._config

    @property
    def pose_history(self) -> Tuple[RecoveryPoseSample, ...]:
        return tuple(self._poses)

    @property
    def state_versions(self) -> Tuple[ForecastState, ...]:
        return tuple(self._states)

    @property
    def update_receipts(self) -> Tuple[PoseUpdateReceipt, ...]:
        return tuple(self._receipts)

    @property
    def current_state(self) -> Optional[ForecastState]:
        return self._states[-1] if self._states else None

    @property
    def locked(self) -> bool:
        state = self.current_state
        return bool(state is not None and state.locked)

    def reset(self) -> None:
        """Reset all recording-local pose and feedback state."""

        self._poses.clear()
        self._pose_ids.clear()
        self._states.clear()
        self._receipts.clear()

    def _invalid_receipt(
        self,
        pose_id: int,
        timestamp_ns: int,
        commit_cycle: int,
        reason: str,
    ) -> PoseUpdateReceipt:
        current = self.current_state
        receipt = PoseUpdateReceipt(
            pose_id,
            timestamp_ns,
            commit_cycle,
            False,
            None,
            current.state_version if current is not None else None,
            current.source_commit_cycle if current is not None else None,
            None,
            None,
            None,
            None,
            bool(current is not None and current.locked),
            bool(current is not None and current.locked),
            current.lock_streak if current is not None else 0,
            "ignored_invalid_pose",
            reason,
        )
        self._receipts.append(receipt)
        return receipt

    def _new_state(
        self,
        pose_id: int,
        sample: RecoveryPoseSample,
        angular_velocity: Vector3,
        integral: Vector3,
        previous_interval_ns: Optional[int],
        lock_streak: int,
        locked: bool,
        previous_residual: Optional[Vector3],
        status: str,
    ) -> ForecastState:
        return ForecastState(
            len(self._states),
            sample.commit_cycle + 1,
            sample.commit_cycle,
            pose_id,
            sample.measurement_timestamp_ns,
            _canonical_quaternion(sample.quaternion_xyzw),
            _vector3(angular_velocity, "angular_velocity_body_rad_s"),
            _vector3(integral, "integral_correction_body_rad_s"),
            previous_interval_ns,
            lock_streak,
            locked,
            (
                None
                if previous_residual is None
                else _vector3(previous_residual, "previous_residual_body_rad")
            ),
            status,
        )

    def _publish_fault(
        self,
        pose_id: int,
        sample: RecoveryPoseSample,
        source: Optional[ForecastState],
        forecast: Optional[QuaternionXYZW],
        residual: Optional[Vector3],
        residual_norm: Optional[float],
        reason: str,
    ) -> PoseUpdateReceipt:
        state = self._new_state(
            pose_id,
            sample,
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
            None,
            0,
            False,
            None,
            "unlocked_%s" % reason,
        )
        self._poses.append(sample)
        self._pose_ids.append(pose_id)
        self._states.append(state)
        receipt = PoseUpdateReceipt(
            pose_id,
            sample.measurement_timestamp_ns,
            sample.commit_cycle,
            True,
            state.effective_cycle,
            source.state_version if source is not None else None,
            source.source_commit_cycle if source is not None else None,
            state.state_version,
            forecast,
            residual,
            residual_norm,
            bool(source is not None and source.locked),
            False,
            0,
            "unlock_reset",
            reason,
        )
        self._receipts.append(receipt)
        return receipt

    def commit_pose(
        self,
        pose_id: int,
        measurement_timestamp_ns: int,
        commit_cycle: int,
        quaternion_xyzw: Sequence[float],
        *,
        valid: bool = True,
    ) -> PoseUpdateReceipt:
        """Commit one supplied pose without exposing its update on that edge.

        Invalid poses are recorded as ignored receipts and do not alter pose or
        loop state.  Valid timing and IDs must increase strictly.  A valid pose
        that trips a PLL guard remains authoritative for the exact fallback,
        while the newly published oscillator state is reset and unlocked.
        """

        identifier = _integer(pose_id, "pose_id")
        timestamp = _integer(measurement_timestamp_ns, "measurement_timestamp_ns")
        cycle = _integer(commit_cycle, "commit_cycle", _INT64_MIN, _INT64_MAX)
        if not isinstance(valid, bool):
            raise SO3PLLError("valid must be a bool")
        if not valid:
            return self._invalid_receipt(
                identifier, timestamp, cycle, "pose_validity_false"
            )
        if cycle == _INT64_MAX:
            return self._invalid_receipt(
                identifier, timestamp, cycle, "effective_cycle_overflow"
            )
        try:
            quaternion = _canonical_quaternion(quaternion_xyzw)
        except (SO3PLLError, GeometryError, OverflowError, ValueError):
            return self._invalid_receipt(
                identifier, timestamp, cycle, "invalid_quaternion"
            )

        if self._poses:
            if identifier <= self._pose_ids[-1]:
                return self._invalid_receipt(
                    identifier, timestamp, cycle, "nonmonotonic_pose_id"
                )
            if timestamp <= self._poses[-1].measurement_timestamp_ns:
                return self._invalid_receipt(
                    identifier, timestamp, cycle, "nonmonotonic_measurement_timestamp"
                )
            if cycle <= self._poses[-1].commit_cycle:
                return self._invalid_receipt(
                    identifier, timestamp, cycle, "nonmonotonic_commit_cycle"
                )

        sample = RecoveryPoseSample(timestamp, cycle, quaternion)
        source = self.current_state
        if source is None:
            state = self._new_state(
                identifier,
                sample,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                None,
                0,
                False,
                None,
                "initialized_unlocked",
            )
            self._poses.append(sample)
            self._pose_ids.append(identifier)
            self._states.append(state)
            receipt = PoseUpdateReceipt(
                identifier,
                timestamp,
                cycle,
                True,
                state.effective_cycle,
                None,
                None,
                state.state_version,
                None,
                None,
                None,
                False,
                False,
                0,
                "initialize",
                None,
            )
            self._receipts.append(receipt)
            return receipt

        gap_ns = timestamp - source.anchor_measurement_timestamp_ns
        if gap_ns <= 0:
            return self._invalid_receipt(
                identifier, timestamp, cycle, "nonpositive_state_gap"
            )
        if gap_ns > self._config.max_gap_ns:
            return self._publish_fault(
                identifier, sample, source, None, None, None, "pose_gap"
            )

        try:
            forecast = _forecast(
                source.anchor_quaternion_xyzw,
                source.angular_velocity_body_rad_s,
                gap_ns,
            )
            residual_quaternion = _relative_shortest_arc(forecast, quaternion)
            residual_predicted_body, residual_norm = _rotation_vector(
                residual_quaternion
            )
        except (SO3PLLError, GeometryError, OverflowError, ValueError):
            return self._publish_fault(
                identifier,
                sample,
                source,
                None,
                None,
                None,
                "normalization_failure",
            )

        if residual_norm >= math.pi - self._config.near_pi_margin_rad:
            return self._publish_fault(
                identifier,
                sample,
                source,
                forecast,
                residual_predicted_body,
                residual_norm,
                "near_pi_residual",
            )
        if residual_norm > self._config.phase_jump_max_rad:
            return self._publish_fault(
                identifier,
                sample,
                source,
                forecast,
                residual_predicted_body,
                residual_norm,
                "phase_jump",
            )

        # q_error maps the measured body frame into the predicted body frame.
        # Its inverse therefore transports stored body vectors to the newly
        # authoritative measured frame before any correction is published.
        transport = _conjugate(residual_quaternion)
        residual_body = _rotate_vector(transport, residual_predicted_body)
        transported_rate = _rotate_vector(
            transport, source.angular_velocity_body_rad_s
        )
        transported_integral = _rotate_vector(
            transport, source.integral_correction_body_rad_s
        )

        if source.previous_interval_ns is None:
            # Acquisition uses exactly the observed pre-pose forecast residual
            # over its measurement-time interval; no event or outcome enters.
            angular_velocity = _scale(
                residual_body,
                _NANOSECONDS_PER_SECOND / float(gap_ns),
            )
            if _norm(angular_velocity) > self._config.max_angular_rate_rad_s:
                return self._publish_fault(
                    identifier,
                    sample,
                    source,
                    forecast,
                    residual_body,
                    residual_norm,
                    "angular_rate_saturation",
                )
            streak = 1 if residual_norm <= self._config.phase_jump_max_rad else 0
            locked = streak >= self._config.lock_count
            integral = (0.0, 0.0, 0.0)
            update_kind = "bootstrap_rate"
        else:
            previous_residual = source.previous_residual_body_rad
            if previous_residual is not None:
                previous_norm = _norm(previous_residual)
                if (
                    residual_norm >= self._config.limit_cycle_min_residual_rad
                    and previous_norm >= self._config.limit_cycle_min_residual_rad
                ):
                    cosine = _dot3(previous_residual, residual_body) / (
                        previous_norm * residual_norm
                    )
                    ratio = max(previous_norm, residual_norm) / min(
                        previous_norm, residual_norm
                    )
                    if cosine <= self._config.limit_cycle_cosine_max and ratio <= 2.0:
                        return self._publish_fault(
                            identifier,
                            sample,
                            source,
                            forecast,
                            residual_body,
                            residual_norm,
                            "limit_cycle",
                        )

            residual_rate = _scale(
                residual_body,
                _NANOSECONDS_PER_SECOND / float(gap_ns),
            )
            proportional = _scale(
                residual_rate, self._config.proportional_gain
            )
            integral = _add(
                transported_integral,
                _scale(residual_rate, self._config.integral_gain),
            )
            if (
                _norm(proportional)
                > self._config.max_proportional_correction_rad_s
            ):
                return self._publish_fault(
                    identifier,
                    sample,
                    source,
                    forecast,
                    residual_body,
                    residual_norm,
                    "proportional_saturation",
                )
            if _norm(integral) > self._config.max_integral_correction_rad_s:
                return self._publish_fault(
                    identifier,
                    sample,
                    source,
                    forecast,
                    residual_body,
                    residual_norm,
                    "integral_saturation",
                )
            angular_velocity = _add(transported_rate, proportional, integral)
            if _norm(angular_velocity) > self._config.max_angular_rate_rad_s:
                return self._publish_fault(
                    identifier,
                    sample,
                    source,
                    forecast,
                    residual_body,
                    residual_norm,
                    "angular_rate_saturation",
                )

            if source.locked:
                locked = True
                streak = max(source.lock_streak, self._config.lock_count)
            elif residual_norm <= self._config.lock_residual_max_rad:
                streak = source.lock_streak + 1
                locked = streak >= self._config.lock_count
            else:
                streak = 0
                locked = False
            update_kind = "bounded_pi_update"

        state = self._new_state(
            identifier,
            sample,
            angular_velocity,
            integral,
            gap_ns,
            streak,
            locked,
            residual_body,
            "locked" if locked else "acquiring",
        )
        self._poses.append(sample)
        self._pose_ids.append(identifier)
        self._states.append(state)
        receipt = PoseUpdateReceipt(
            identifier,
            timestamp,
            cycle,
            True,
            state.effective_cycle,
            source.state_version,
            source.source_commit_cycle,
            state.state_version,
            forecast,
            residual_body,
            residual_norm,
            source.locked,
            state.locked,
            state.lock_streak,
            update_kind,
            None,
        )
        self._receipts.append(receipt)
        return receipt

    def _state_visible_at(self, decision_cycle: int) -> Optional[ForecastState]:
        for state in reversed(self._states):
            if state.effective_cycle <= decision_cycle:
                return state
        return None

    def _fallback(
        self,
        timestamp_ns: int,
        decision_cycle: int,
        state: Optional[ForecastState],
        candidate_reason: str,
    ) -> SO3PLLDecision:
        recovery = recover_causal_cav(
            self._poses,
            timestamp_ns,
            decision_cycle,
            self._config.cav_max_horizon_ns,
            self._config.zoh_max_age_ns,
        )
        mode = SO3PLLMode(recovery.mode.value)
        return SO3PLLDecision(
            mode,
            recovery.quaternion_xyzw,
            timestamp_ns,
            decision_cycle,
            False,
            state.state_version if state is not None else None,
            state.anchor_pose_id if state is not None else None,
            recovery.age_ns,
            "%s:%s" % (candidate_reason, recovery.reason),
            recovery,
        )

    def predict(
        self, event_timestamp_ns: int, decision_cycle: int
    ) -> SO3PLLDecision:
        """Predict orientation for one event without updating model state."""

        timestamp = _integer(event_timestamp_ns, "event_timestamp_ns")
        cycle = _integer(decision_cycle, "decision_cycle")
        state = self._state_visible_at(cycle)
        if state is None:
            return self._fallback(timestamp, cycle, None, "no_visible_pll_state")
        if not state.locked:
            return self._fallback(timestamp, cycle, state, "pll_unlocked")
        if timestamp < state.anchor_measurement_timestamp_ns:
            return self._fallback(timestamp, cycle, state, "event_precedes_anchor")
        age_ns = timestamp - state.anchor_measurement_timestamp_ns
        horizon = self._config.max_prediction_horizon_ns
        if state.previous_interval_ns is not None:
            horizon = min(horizon, state.previous_interval_ns)
        if age_ns > horizon:
            return self._fallback(timestamp, cycle, state, "pll_horizon_exceeded")
        try:
            quaternion = _forecast(
                state.anchor_quaternion_xyzw,
                state.angular_velocity_body_rad_s,
                age_ns,
            )
        except (SO3PLLError, GeometryError, OverflowError, ValueError):
            return self._fallback(timestamp, cycle, state, "pll_numeric_failure")
        return SO3PLLDecision(
            SO3PLLMode.PLL,
            quaternion,
            timestamp,
            cycle,
            True,
            state.state_version,
            state.anchor_pose_id,
            age_ns,
            "locked_measurement_time_forecast",
            None,
        )


__all__ = (
    "CANDIDATE_FAMILY",
    "ForecastState",
    "PoseUpdateReceipt",
    "QuaternionXYZW",
    "SO3PLLConfig",
    "SO3PLLDecision",
    "SO3PLLError",
    "SO3PLLMode",
    "SO3PLLModel",
    "Vector3",
)
