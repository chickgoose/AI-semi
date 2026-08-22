"""Strictly causal model-only DSPB predictor candidate.

The delayed supplied-pose-residual predictor bank contains exactly four
experts.  It has no event-loss, selector, label, codec, or external-data
interface.  Expert credit is updated only when a new authoritative supplied
pose commits, by scoring immutable expert functions published before that
pose existed.  The resulting winner is visible only on the next cycle.

Quaternions are active sensor-to-world rotations in ``xyzw`` order.  All
expert rates are right-trivialized body-frame vectors at their anchor pose.
This is a software research model; it makes no RTL or PPA claim.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import math
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample as RecoveryPoseSample,
    RecoveryMode,
    extrapolate_constant_angular_velocity,
    normalize_quaternion_xyzw,
    recover_causal_cav,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.analyzer import (
    RotationFrame,
    SO3AxisAuditError,
    relative_rotation_vector,
)


QuaternionXYZW = Tuple[float, float, float, float]
Vector3 = Tuple[float, float, float]

E0 = "E0_CURRENT_CAV"
E1 = "E1_EWMA_RATE"
E2 = "E2_BOUNDED_RG3"
E3 = "E3_AXIS_COHERENT_SIGNED_SPEED"
EXPERT_IDS = (E0, E1, E2, E3)

_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_UINT64_MAX = (1 << 64) - 1
_ZERO_VECTOR = (0.0, 0.0, 0.0)


class DSPBError(ValueError):
    """A DSPB input or numerical invariant failed closed."""


class DecisionMode(str, Enum):
    """Actual geometry source used for an event."""

    DSPB = "dspb_candidate"
    CURRENT_CAV = "exact_current_cav"
    ZOH = "fresh_zoh"
    BYPASS = "sensor_fixed_bypass"


def _exact_dataclass(value: object, expected: type, where: str) -> None:
    if type(value) is not expected:
        raise DSPBError("%s must have exact %s type" % (where, expected.__name__))
    if tuple(vars(value)) != tuple(field.name for field in fields(expected)):
        raise DSPBError("%s dataclass fields differ from the frozen schema" % where)


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
        raise DSPBError("%s must be an integer in [%d,%d]" % (where, minimum, maximum))
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DSPBError("%s must be finite" % where)
    result = float(value)
    if not math.isfinite(result):
        raise DSPBError("%s must be finite" % where)
    return result


def _vector(value: Sequence[float], where: str) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise DSPBError("%s must contain three components" % where)
    return (
        _finite(value[0], "%s[0]" % where),
        _finite(value[1], "%s[1]" % where),
        _finite(value[2], "%s[2]" % where),
    )


def _canonical_quaternion(value: Sequence[float]) -> QuaternionXYZW:
    try:
        normalized = normalize_quaternion_xyzw(value)
    except GeometryError as exc:
        raise DSPBError(str(exc)) from exc
    pivot = max(range(4), key=lambda index: (abs(normalized[index]), -index))
    if normalized[pivot] < 0.0:
        normalized = tuple(-component for component in normalized)
    return normalized  # type: ignore[return-value]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right))


def _norm(value: Sequence[float]) -> float:
    result = math.sqrt(math.fsum(component * component for component in value))
    if not math.isfinite(result):
        raise DSPBError("vector norm is non-finite")
    return result


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


def _relative_delta(
    before: QuaternionXYZW, after: QuaternionXYZW
) -> QuaternionXYZW:
    aligned = after
    if _dot(before, aligned) < 0.0:
        aligned = tuple(-component for component in aligned)  # type: ignore[assignment]
    delta = normalize_quaternion_xyzw(_multiply(_conjugate(before), aligned))
    if delta[3] < 0.0:
        delta = tuple(-component for component in delta)  # type: ignore[assignment]
    return delta


def _rotate_vector(rotation: QuaternionXYZW, value: Vector3) -> Vector3:
    x, y, z, w = normalize_quaternion_xyzw(rotation)
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


def _transport_to_next_body(delta: QuaternionXYZW, value: Vector3) -> Vector3:
    return _rotate_vector(_conjugate(delta), value)


def _rotation_vector(before: QuaternionXYZW, after: QuaternionXYZW) -> Vector3:
    try:
        return relative_rotation_vector(before, after, frame=RotationFrame.BODY)
    except SO3AxisAuditError as exc:
        raise DSPBError(str(exc)) from exc


def _quaternion_exp(rotation_vector: Vector3) -> QuaternionXYZW:
    angle = _norm(rotation_vector)
    if angle <= 1.0e-15:
        return (0.0, 0.0, 0.0, 1.0)
    half = 0.5 * angle
    scale = math.sin(half) / angle
    return (
        rotation_vector[0] * scale,
        rotation_vector[1] * scale,
        rotation_vector[2] * scale,
        math.cos(half),
    )


def _advance(anchor: QuaternionXYZW, rotation_vector: Vector3) -> QuaternionXYZW:
    return _canonical_quaternion(_multiply(anchor, _quaternion_exp(rotation_vector)))


def _angular_error(left: QuaternionXYZW, right: QuaternionXYZW) -> float:
    return _norm(_rotation_vector(left, right))


def _canonical_sha256(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DSPBError("receipt is not canonical JSON") from exc
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class DSPBConfig:
    """The single frozen A4 research profile; changed values are a new candidate."""

    candidate_id: str = "DSPB-A4-E0E1E2E3-V1"
    max_horizon_ns: int = 5_000_000
    zoh_max_age_ns: int = 1_000_000
    ewma_rate_alpha: float = 0.25
    credit_ewma_alpha: float = 0.25
    minimum_credit_samples: int = 2
    credit_tie_tolerance_rad: float = 1.0e-12
    winner_switch_margin_rad: float = 1.0e-4
    disagreement_probe_ns: int = 5_000_000
    maximum_expert_disagreement_rad: float = 0.5
    maximum_rate_rad_s: float = 100.0
    maximum_rg3_acceleration_rad_s2: float = 10_000.0
    maximum_cadence_ratio: float = 2.0
    rg3_minimum_direction_cosine: float = 0.0
    rg3_maximum_prior_residual_rad: float = 0.25
    axis_minimum_coherence: float = 0.90
    minimum_signed_speed_rad_s: float = 1.0e-9
    near_pi_margin_rad: float = 1.0e-6

    def __post_init__(self) -> None:
        _exact_dataclass(self, DSPBConfig, "config")
        if self != DSPBConfig._frozen_values():
            raise DSPBError("DSPB A4 profile is frozen; changed parameters need a new ID")

    @classmethod
    def _frozen_values(cls) -> "DSPBConfig":
        instance = object.__new__(cls)
        for field in fields(cls):
            object.__setattr__(instance, field.name, field.default)
        return instance

    def to_mapping(self) -> Mapping[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_mapping())


@dataclass(frozen=True)
class SuppliedPose:
    pose_id: int
    measurement_timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: QuaternionXYZW
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        _exact_dataclass(self, SuppliedPose, "pose")
        object.__setattr__(self, "pose_id", _integer(self.pose_id, "pose_id"))
        object.__setattr__(
            self,
            "measurement_timestamp_ns",
            _integer(self.measurement_timestamp_ns, "pose timestamp"),
        )
        object.__setattr__(
            self,
            "commit_cycle",
            _integer(self.commit_cycle, "pose commit cycle", _INT64_MIN, _INT64_MAX),
        )
        object.__setattr__(
            self, "quaternion_xyzw", _canonical_quaternion(self.quaternion_xyzw)
        )
        if type(self.value_valid) is not bool or type(self.arithmetic_valid) is not bool:
            raise DSPBError("pose validity flags must be exact bools")

    @property
    def valid(self) -> bool:
        return self.value_valid and self.arithmetic_valid


@dataclass(frozen=True)
class EventRecord:
    """Receipt identity plus the only event timing consumed by the model."""

    event_id: int
    occurrence_timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int

    def __post_init__(self) -> None:
        _exact_dataclass(self, EventRecord, "event")
        object.__setattr__(self, "event_id", _integer(self.event_id, "event_id"))
        object.__setattr__(
            self,
            "occurrence_timestamp_ns",
            _integer(self.occurrence_timestamp_ns, "event occurrence timestamp"),
        )
        object.__setattr__(
            self,
            "occurrence_cycle",
            _integer(self.occurrence_cycle, "event occurrence cycle", _INT64_MIN, _INT64_MAX),
        )
        object.__setattr__(
            self,
            "decision_cycle",
            _integer(self.decision_cycle, "event decision cycle", _INT64_MIN, _INT64_MAX),
        )
        if self.occurrence_cycle >= self.decision_cycle:
            raise DSPBError("event occurrence_cycle must be strictly before decision_cycle")


@dataclass(frozen=True)
class CreditState:
    expert_id: str
    sample_count: int
    ewma_error_rad: Optional[float]

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "expert_id": self.expert_id,
            "sample_count": self.sample_count,
            "ewma_error_rad": self.ewma_error_rad,
        }


@dataclass(frozen=True)
class ForecastResult:
    valid: bool
    quaternion_xyzw: Optional[QuaternionXYZW]
    reason: str


@dataclass(frozen=True)
class ExpertFunction:
    """An immutable pose-prediction function published for one epoch."""

    expert_id: str
    state_version: int
    anchor_pose_id: int
    anchor_timestamp_ns: int
    anchor_commit_cycle: int
    anchor_quaternion_xyzw: QuaternionXYZW
    source_pose_ids: Tuple[int, ...]
    source_timestamps_ns: Tuple[int, ...]
    source_commit_cycles: Tuple[int, ...]
    parent_state_version: Optional[int]
    rate_body_rad_s: Vector3
    acceleration_body_rad_s2: Vector3
    previous_quaternion_xyzw: Optional[QuaternionXYZW]
    previous_interval_ns: Optional[int]
    valid: bool
    invalid_reason: Optional[str]

    def forecast(self, target_timestamp_ns: int) -> ForecastResult:
        target = _integer(target_timestamp_ns, "forecast target timestamp")
        if not self.valid:
            return ForecastResult(False, None, self.invalid_reason or "expert_invalid")
        if target < self.anchor_timestamp_ns:
            return ForecastResult(False, None, "target_before_anchor")
        horizon_ns = target - self.anchor_timestamp_ns
        if horizon_ns > 5_000_000:
            return ForecastResult(False, None, "horizon_exceeds_frozen_limit")
        try:
            if self.expert_id == E0:
                if self.previous_quaternion_xyzw is None or self.previous_interval_ns is None:
                    return ForecastResult(False, None, "e0_missing_previous_pose")
                if horizon_ns > self.previous_interval_ns:
                    return ForecastResult(False, None, "e0_horizon_exceeds_previous_interval")
                quaternion = extrapolate_constant_angular_velocity(
                    self.previous_quaternion_xyzw,
                    self.anchor_quaternion_xyzw,
                    self.previous_interval_ns,
                    horizon_ns,
                )
                return ForecastResult(True, _canonical_quaternion(quaternion), "ok")
            seconds = float(horizon_ns) * 1.0e-9
            vector = tuple(
                self.rate_body_rad_s[index] * seconds
                + 0.5 * self.acceleration_body_rad_s2[index] * seconds * seconds
                for index in range(3)
            )
            if _norm(vector) >= math.pi - 1.0e-6:
                return ForecastResult(False, None, "forecast_near_pi")
            return ForecastResult(
                True,
                _advance(self.anchor_quaternion_xyzw, vector),
                "ok",
            )
        except (DSPBError, GeometryError, OverflowError, ValueError):
            return ForecastResult(False, None, "forecast_arithmetic_failure")


@dataclass(frozen=True)
class EpochState:
    state_version: int
    effective_cycle: int
    expert_functions: Tuple[ExpertFunction, ...]
    credits: Tuple[CreditState, ...]
    selected_expert_id: Optional[str]
    lock_reason: str

    def function(self, expert_id: str) -> Optional[ExpertFunction]:
        for function in self.expert_functions:
            if function.expert_id == expert_id:
                return function
        return None


@dataclass(frozen=True)
class PoseForecastScore:
    expert_id: str
    forecast_state_version: Optional[int]
    source_pose_ids: Tuple[int, ...]
    target_timestamp_ns: int
    pose_commit_cycle: int
    forecast_valid: bool
    forecast_quaternion_xyzw: Optional[QuaternionXYZW]
    angular_error_rad: Optional[float]
    reason: str

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "expert_id": self.expert_id,
            "forecast_state_version": self.forecast_state_version,
            "source_pose_ids": list(self.source_pose_ids),
            "target_timestamp_ns": self.target_timestamp_ns,
            "pose_commit_cycle": self.pose_commit_cycle,
            "forecast_valid": self.forecast_valid,
            "forecast_quaternion_xyzw": (
                list(self.forecast_quaternion_xyzw)
                if self.forecast_quaternion_xyzw is not None
                else None
            ),
            "angular_error_rad": self.angular_error_rad,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PoseCommitReceipt:
    candidate_id: str
    config_sha256: str
    pose_id: int
    measurement_timestamp_ns: int
    commit_cycle: int
    prior_state_version: int
    next_state_version: int
    next_effective_cycle: int
    scored_forecasts: Tuple[PoseForecastScore, ...]
    next_credits: Tuple[CreditState, ...]
    next_selected_expert_id: Optional[str]
    next_lock_reason: str
    receipt_sha256: str

    def to_mapping(self, include_digest: bool = True) -> Mapping[str, object]:
        result = {
            "candidate_id": self.candidate_id,
            "config_sha256": self.config_sha256,
            "pose_id": self.pose_id,
            "measurement_timestamp_ns": self.measurement_timestamp_ns,
            "commit_cycle": self.commit_cycle,
            "prior_state_version": self.prior_state_version,
            "next_state_version": self.next_state_version,
            "next_effective_cycle": self.next_effective_cycle,
            "scored_forecasts": [score.to_mapping() for score in self.scored_forecasts],
            "next_credits": [credit.to_mapping() for credit in self.next_credits],
            "next_selected_expert_id": self.next_selected_expert_id,
            "next_lock_reason": self.next_lock_reason,
        }  # type: Dict[str, object]
        if include_digest:
            result["receipt_sha256"] = self.receipt_sha256
        return result


@dataclass(frozen=True)
class EventDecision:
    candidate_id: str
    config_sha256: str
    event_id: int
    occurrence_timestamp_ns: int
    occurrence_cycle: int
    decision_cycle: int
    state_version: int
    selected_expert_id: Optional[str]
    geometry_expert_id: Optional[str]
    mode: DecisionMode
    candidate_used: bool
    output_quaternion_xyzw: Optional[QuaternionXYZW]
    used_pose_ids: Tuple[int, ...]
    used_pose_timestamps_ns: Tuple[int, ...]
    used_pose_commit_cycles: Tuple[int, ...]
    fallback_reason: Optional[str]
    decision_sha256: str

    def to_mapping(self, include_digest: bool = True) -> Mapping[str, object]:
        result = {
            "candidate_id": self.candidate_id,
            "config_sha256": self.config_sha256,
            "event_id": self.event_id,
            "occurrence_timestamp_ns": self.occurrence_timestamp_ns,
            "occurrence_cycle": self.occurrence_cycle,
            "decision_cycle": self.decision_cycle,
            "state_version": self.state_version,
            "selected_expert_id": self.selected_expert_id,
            "geometry_expert_id": self.geometry_expert_id,
            "mode": self.mode.value,
            "candidate_used": self.candidate_used,
            "output_quaternion_xyzw": (
                list(self.output_quaternion_xyzw)
                if self.output_quaternion_xyzw is not None
                else None
            ),
            "used_pose_ids": list(self.used_pose_ids),
            "used_pose_timestamps_ns": list(self.used_pose_timestamps_ns),
            "used_pose_commit_cycles": list(self.used_pose_commit_cycles),
            "fallback_reason": self.fallback_reason,
        }  # type: Dict[str, object]
        if include_digest:
            result["decision_sha256"] = self.decision_sha256
        return result


def _invalid_function(
    expert_id: str, version: int, pose: SuppliedPose, reason: str
) -> ExpertFunction:
    return ExpertFunction(
        expert_id,
        version,
        pose.pose_id,
        pose.measurement_timestamp_ns,
        pose.commit_cycle,
        pose.quaternion_xyzw,
        (pose.pose_id,),
        (pose.measurement_timestamp_ns,),
        (pose.commit_cycle,),
        None,
        _ZERO_VECTOR,
        _ZERO_VECTOR,
        None,
        None,
        False,
        reason,
    )


def _function(
    expert_id: str,
    version: int,
    poses: Sequence[SuppliedPose],
    rate: Vector3,
    acceleration: Vector3 = _ZERO_VECTOR,
    previous_quaternion: Optional[QuaternionXYZW] = None,
    previous_interval_ns: Optional[int] = None,
    parent_state_version: Optional[int] = None,
) -> ExpertFunction:
    anchor = poses[-1]
    return ExpertFunction(
        expert_id,
        version,
        anchor.pose_id,
        anchor.measurement_timestamp_ns,
        anchor.commit_cycle,
        anchor.quaternion_xyzw,
        tuple(pose.pose_id for pose in poses),
        tuple(pose.measurement_timestamp_ns for pose in poses),
        tuple(pose.commit_cycle for pose in poses),
        parent_state_version,
        _vector(rate, "expert rate"),
        _vector(acceleration, "expert acceleration"),
        previous_quaternion,
        previous_interval_ns,
        True,
        None,
    )


class DSPBModel:
    """Stateful four-expert DSPB with delayed pose-only selection."""

    def __init__(self, config: Optional[DSPBConfig] = None) -> None:
        self.config = DSPBConfig() if config is None else config
        _exact_dataclass(self.config, DSPBConfig, "config")
        credits = tuple(CreditState(expert_id, 0, None) for expert_id in EXPERT_IDS)
        self._published = EpochState(0, _INT64_MIN, (), credits, None, "bank_untrained")
        self._pending = None  # type: Optional[EpochState]
        self._valid_poses = ()  # type: Tuple[SuppliedPose, ...]
        self._pose_receipts = ()  # type: Tuple[PoseCommitReceipt, ...]
        self._event_decisions = ()  # type: Tuple[EventDecision, ...]
        self._seen_pose_ids = set()  # type: set
        self._seen_event_ids = set()  # type: set
        self._last_pose_commit_cycle = None  # type: Optional[int]
        self._last_stream_cycle = None  # type: Optional[int]
        self._last_event_timestamp_ns = None  # type: Optional[int]

    @property
    def expert_ids(self) -> Tuple[str, ...]:
        return EXPERT_IDS

    @property
    def published_state(self) -> EpochState:
        return self._published

    @property
    def pending_state(self) -> Optional[EpochState]:
        return self._pending

    @property
    def pose_receipts(self) -> Tuple[PoseCommitReceipt, ...]:
        return self._pose_receipts

    @property
    def event_decisions(self) -> Tuple[EventDecision, ...]:
        return self._event_decisions

    def _advance_stream(self, cycle: int) -> None:
        if self._last_stream_cycle is not None and cycle < self._last_stream_cycle:
            raise DSPBError("stream decision/commit cycles must not move backwards")
        self._last_stream_cycle = cycle
        if self._pending is not None and self._pending.effective_cycle <= cycle:
            self._published = self._pending
            self._pending = None

    def _validate_credits(
        self, credits: Sequence[CreditState]
    ) -> Optional[Tuple[CreditState, ...]]:
        source = tuple(credits)
        if tuple(credit.expert_id for credit in source) != EXPERT_IDS:
            return None
        for credit in source:
            if (
                isinstance(credit.sample_count, bool)
                or not isinstance(credit.sample_count, int)
                or credit.sample_count < 0
            ):
                return None
            if credit.sample_count == 0:
                if credit.ewma_error_rad is not None:
                    return None
            elif (
                credit.ewma_error_rad is None
                or not math.isfinite(credit.ewma_error_rad)
                or credit.ewma_error_rad < 0.0
            ):
                return None
        return source

    def _score_prior_functions(self, pose: SuppliedPose) -> Tuple[PoseForecastScore, ...]:
        functions = {item.expert_id: item for item in self._published.expert_functions}
        scores = []
        for expert_id in EXPERT_IDS:
            function = functions.get(expert_id)
            if function is None:
                scores.append(PoseForecastScore(
                    expert_id,
                    None,
                    (),
                    pose.measurement_timestamp_ns,
                    pose.commit_cycle,
                    False,
                    None,
                    None,
                    "expert_not_yet_published",
                ))
                continue
            if any(cycle >= pose.commit_cycle for cycle in function.source_commit_cycles):
                raise DSPBError("pre-pose forecast contains a same/future-edge pose")
            if any(
                timestamp > pose.measurement_timestamp_ns
                for timestamp in function.source_timestamps_ns
            ):
                raise DSPBError("pre-pose forecast contains a future measurement")
            forecast = function.forecast(pose.measurement_timestamp_ns)
            if not forecast.valid or forecast.quaternion_xyzw is None:
                scores.append(PoseForecastScore(
                    expert_id,
                    function.state_version,
                    function.source_pose_ids,
                    pose.measurement_timestamp_ns,
                    pose.commit_cycle,
                    False,
                    None,
                    None,
                    forecast.reason,
                ))
                continue
            error = _angular_error(forecast.quaternion_xyzw, pose.quaternion_xyzw)
            scores.append(PoseForecastScore(
                expert_id,
                function.state_version,
                function.source_pose_ids,
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                True,
                forecast.quaternion_xyzw,
                error,
                "scored_before_pose_update",
            ))
        return tuple(scores)

    def _updated_credits(
        self,
        scores: Sequence[PoseForecastScore],
    ) -> Tuple[Tuple[CreditState, ...], bool]:
        valid = self._validate_credits(self._published.credits)
        if valid is None:
            return tuple(CreditState(expert_id, 0, None) for expert_id in EXPERT_IDS), True
        by_id = {credit.expert_id: credit for credit in valid}
        score_by_id = {score.expert_id: score for score in scores}
        result = []
        for expert_id in EXPERT_IDS:
            prior = by_id[expert_id]
            score = score_by_id[expert_id]
            if not score.forecast_valid:
                result.append(prior)
                continue
            error = score.angular_error_rad
            if error is None or not math.isfinite(error) or error < 0.0:
                return tuple(CreditState(item, 0, None) for item in EXPERT_IDS), True
            if prior.sample_count == 0:
                updated = error
            else:
                assert prior.ewma_error_rad is not None
                alpha = self.config.credit_ewma_alpha
                updated = alpha * error + (1.0 - alpha) * prior.ewma_error_rad
            if not math.isfinite(updated):
                return tuple(CreditState(item, 0, None) for item in EXPERT_IDS), True
            result.append(CreditState(expert_id, prior.sample_count + 1, updated))
        return tuple(result), False

    def _interval_rate(
        self, before: SuppliedPose, after: SuppliedPose
    ) -> Tuple[Vector3, QuaternionXYZW, int]:
        interval_ns = after.measurement_timestamp_ns - before.measurement_timestamp_ns
        if interval_ns <= 0:
            raise DSPBError("valid pose timestamps must be strictly increasing")
        delta = _relative_delta(before.quaternion_xyzw, after.quaternion_xyzw)
        vector = _rotation_vector(before.quaternion_xyzw, after.quaternion_xyzw)
        if _norm(vector) >= math.pi - self.config.near_pi_margin_rad:
            raise DSPBError("pose step is within the frozen near-pi margin")
        scale = 1.0e9 / float(interval_ns)
        rate = tuple(component * scale for component in vector)
        if _norm(rate) > self.config.maximum_rate_rad_s:
            raise DSPBError("pose rate exceeds the frozen bound")
        return _vector(rate, "interval rate"), delta, interval_ns

    def _build_functions(
        self,
        version: int,
        scores: Sequence[PoseForecastScore],
    ) -> Tuple[ExpertFunction, ...]:
        history = self._valid_poses
        anchor = history[-1]
        if len(history) < 2:
            return tuple(
                _invalid_function(expert_id, version, anchor, "insufficient_pose_history")
                for expert_id in EXPERT_IDS
            )

        previous, latest = history[-2:]
        try:
            latest_rate_previous, latest_delta, latest_interval = self._interval_rate(
                previous, latest
            )
            latest_rate = _transport_to_next_body(latest_delta, latest_rate_previous)
        except DSPBError as exc:
            return tuple(
                _invalid_function(expert_id, version, anchor, str(exc))
                for expert_id in EXPERT_IDS
            )

        e0 = _function(
            E0,
            version,
            (previous, latest),
            latest_rate,
            previous_quaternion=previous.quaternion_xyzw,
            previous_interval_ns=latest_interval,
        )

        prior_e1 = self._published.function(E1)
        if (
            prior_e1 is not None
            and prior_e1.valid
            and prior_e1.anchor_pose_id == previous.pose_id
        ):
            transported_ewma = _transport_to_next_body(
                latest_delta, prior_e1.rate_body_rad_s
            )
            alpha = self.config.ewma_rate_alpha
            ewma_rate = tuple(
                alpha * latest_rate[index]
                + (1.0 - alpha) * transported_ewma[index]
                for index in range(3)
            )
            e1_parent = prior_e1.state_version
        else:
            ewma_rate = latest_rate
            e1_parent = None
        e1 = _function(
            E1,
            version,
            history,
            _vector(ewma_rate, "EWMA rate"),
            parent_state_version=e1_parent,
        )

        if len(history) < 3:
            e2 = _invalid_function(E2, version, anchor, "insufficient_pose_history")
            e3 = _invalid_function(E3, version, anchor, "insufficient_pose_history")
            return (e0, e1, e2, e3)

        first, middle, latest = history[-3:]
        try:
            rate01_frame0, delta01, interval01 = self._interval_rate(first, middle)
            rate12_frame1 = latest_rate_previous
            delta12 = latest_delta
            interval12 = latest_interval
            rate01_frame1 = _transport_to_next_body(delta01, rate01_frame0)
        except DSPBError as exc:
            e2 = _invalid_function(E2, version, anchor, str(exc))
            e3 = _invalid_function(E3, version, anchor, str(exc))
            return (e0, e1, e2, e3)

        try:
            cadence_ratio = float(max(interval01, interval12)) / float(
                min(interval01, interval12)
            )
            if cadence_ratio > self.config.maximum_cadence_ratio:
                raise DSPBError("RG3 cadence ratio exceeds the frozen bound")
            norm01 = _norm(rate01_frame1)
            norm12 = _norm(rate12_frame1)
            if norm01 <= self.config.minimum_signed_speed_rad_s or norm12 <= self.config.minimum_signed_speed_rad_s:
                raise DSPBError("RG3 direction is unavailable at zero speed")
            direction_cosine = _dot(rate01_frame1, rate12_frame1) / (norm01 * norm12)
            if direction_cosine < self.config.rg3_minimum_direction_cosine:
                raise DSPBError("RG3 direction gate failed")
            e0_score = next(score for score in scores if score.expert_id == E0)
            if (
                not e0_score.forecast_valid
                or e0_score.angular_error_rad is None
                or e0_score.angular_error_rad > self.config.rg3_maximum_prior_residual_rad
            ):
                raise DSPBError("RG3 past-pose residual gate failed")
            average_seconds = 0.5 * float(interval01 + interval12) * 1.0e-9
            acceleration_frame1 = tuple(
                (rate12_frame1[index] - rate01_frame1[index]) / average_seconds
                for index in range(3)
            )
            if _norm(acceleration_frame1) > self.config.maximum_rg3_acceleration_rad_s2:
                raise DSPBError("RG3 acceleration exceeds the frozen bound")
            rate_frame2 = _transport_to_next_body(delta12, rate12_frame1)
            acceleration_frame2 = _transport_to_next_body(delta12, acceleration_frame1)
            e2 = _function(
                E2,
                version,
                (first, middle, latest),
                rate_frame2,
                acceleration_frame2,
            )
        except (DSPBError, StopIteration) as exc:
            e2 = _invalid_function(E2, version, anchor, str(exc))

        try:
            # Use only the two completed past intervals.  No external axis label
            # or retrospective motion class is represented.
            rate01_frame1 = _transport_to_next_body(delta01, rate01_frame0)
            dominant01 = max(range(3), key=lambda index: (abs(rate01_frame1[index]), -index))
            dominant12 = max(range(3), key=lambda index: (abs(rate12_frame1[index]), -index))
            if dominant01 != dominant12:
                raise DSPBError("E3 dominant axis is not coherent")
            axis = dominant12
            speed01 = _norm(rate01_frame1)
            speed12 = _norm(rate12_frame1)
            if speed01 <= self.config.minimum_signed_speed_rad_s or speed12 <= self.config.minimum_signed_speed_rad_s:
                raise DSPBError("E3 signed speed is unavailable")
            if (
                abs(rate01_frame1[axis]) / speed01 < self.config.axis_minimum_coherence
                or abs(rate12_frame1[axis]) / speed12 < self.config.axis_minimum_coherence
            ):
                raise DSPBError("E3 axis coherence gate failed")
            if rate01_frame1[axis] * rate12_frame1[axis] <= 0.0:
                raise DSPBError("E3 signed direction gate failed")
            signed_speed = 0.5 * (
                rate01_frame1[axis] + rate12_frame1[axis]
            )
            if abs(signed_speed) <= self.config.minimum_signed_speed_rad_s:
                raise DSPBError("E3 signed speed cancelled")
            axis_rate_frame1 = tuple(
                signed_speed if index == axis else 0.0 for index in range(3)
            )
            axis_rate_frame2 = _transport_to_next_body(delta12, axis_rate_frame1)
            e3 = _function(
                E3,
                version,
                (first, middle, latest),
                _vector(axis_rate_frame2, "E3 rate"),
            )
        except DSPBError as exc:
            e3 = _invalid_function(E3, version, anchor, str(exc))

        return (e0, e1, e2, e3)

    def _select_winner(
        self,
        functions: Sequence[ExpertFunction],
        credits: Sequence[CreditState],
    ) -> Tuple[Optional[str], str]:
        validated = self._validate_credits(credits)
        if validated is None:
            return None, "credit_corruption"
        if any(
            credit.sample_count < self.config.minimum_credit_samples
            for credit in validated
        ):
            return None, "bank_untrained"
        values = {credit.expert_id: credit.ewma_error_rad for credit in validated}
        if any(value is None for value in values.values()):
            return None, "credit_corruption"
        minimum = min(value for value in values.values() if value is not None)
        tied = tuple(
            expert_id
            for expert_id in EXPERT_IDS
            if abs(float(values[expert_id]) - minimum)
            <= self.config.credit_tie_tolerance_rad
        )
        if len(tied) != 1:
            return None, "winner_tie"
        winner = tied[0]
        by_id = {function.expert_id: function for function in functions}
        winner_function = by_id.get(winner)
        if winner_function is None or not winner_function.valid:
            reason = "missing" if winner_function is None else winner_function.invalid_reason
            return None, "invalid_winner:%s:%s" % (winner, reason)

        previous = self._published.selected_expert_id
        if previous is not None and previous != winner:
            previous_function = by_id.get(previous)
            previous_credit = values.get(previous)
            if (
                previous_function is not None
                and previous_function.valid
                and previous_credit is not None
                and float(previous_credit)
                <= minimum + self.config.winner_switch_margin_rad
            ):
                winner = previous

        forecasts = []
        for expert_id in EXPERT_IDS:
            function = by_id.get(expert_id)
            if function is None or not function.valid:
                continue
            result = function.forecast(
                function.anchor_timestamp_ns + self.config.disagreement_probe_ns
            )
            if result.valid and result.quaternion_xyzw is not None:
                forecasts.append((expert_id, result.quaternion_xyzw))
        for left_index in range(len(forecasts)):
            for right_index in range(left_index + 1, len(forecasts)):
                if (
                    _angular_error(
                        forecasts[left_index][1], forecasts[right_index][1]
                    )
                    > self.config.maximum_expert_disagreement_rad
                ):
                    return None, "excessive_expert_disagreement"
        return winner, "locked_pose_residual_winner"

    def commit_pose(self, pose: SuppliedPose) -> PoseCommitReceipt:
        """Commit a pose and schedule all learning for the following cycle."""

        _exact_dataclass(pose, SuppliedPose, "pose")
        if pose.pose_id in self._seen_pose_ids:
            raise DSPBError("pose_id must be exact-once")
        if (
            self._last_pose_commit_cycle is not None
            and pose.commit_cycle <= self._last_pose_commit_cycle
        ):
            raise DSPBError("pose commit cycles must be strictly increasing")
        if pose.commit_cycle == _INT64_MAX:
            raise DSPBError("pose effective cycle overflows signed 64 bits")
        self._advance_stream(pose.commit_cycle)
        prior = self._published
        version = prior.state_version + 1

        if pose.valid:
            if (
                self._valid_poses
                and pose.measurement_timestamp_ns
                <= self._valid_poses[-1].measurement_timestamp_ns
            ):
                raise DSPBError("valid pose measurement timestamps must increase")
            scores = self._score_prior_functions(pose)
            credits, corrupted = self._updated_credits(scores)
            self._valid_poses = self._valid_poses + (pose,)
            functions = self._build_functions(version, scores)
            if corrupted:
                selected, lock_reason = None, "credit_corruption"
            else:
                selected, lock_reason = self._select_winner(functions, credits)
        else:
            scores = tuple(PoseForecastScore(
                expert_id,
                prior.function(expert_id).state_version
                if prior.function(expert_id) is not None
                else None,
                prior.function(expert_id).source_pose_ids
                if prior.function(expert_id) is not None
                else (),
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                False,
                None,
                None,
                "invalid_supplied_pose_no_credit_update",
            ) for expert_id in EXPERT_IDS)
            credits = prior.credits
            functions = prior.expert_functions
            selected, lock_reason = None, "invalid_supplied_pose"

        next_state = EpochState(
            version,
            pose.commit_cycle + 1,
            tuple(functions),
            tuple(credits),
            selected,
            lock_reason,
        )
        self._pending = next_state
        unsigned = {
            "candidate_id": self.config.candidate_id,
            "config_sha256": self.config.sha256,
            "pose_id": pose.pose_id,
            "measurement_timestamp_ns": pose.measurement_timestamp_ns,
            "commit_cycle": pose.commit_cycle,
            "prior_state_version": prior.state_version,
            "next_state_version": version,
            "next_effective_cycle": pose.commit_cycle + 1,
            "scored_forecasts": [score.to_mapping() for score in scores],
            "next_credits": [credit.to_mapping() for credit in credits],
            "next_selected_expert_id": selected,
            "next_lock_reason": lock_reason,
        }
        receipt = PoseCommitReceipt(
            self.config.candidate_id,
            self.config.sha256,
            pose.pose_id,
            pose.measurement_timestamp_ns,
            pose.commit_cycle,
            prior.state_version,
            version,
            pose.commit_cycle + 1,
            tuple(scores),
            tuple(credits),
            selected,
            lock_reason,
            _canonical_sha256(unsigned),
        )
        self._seen_pose_ids.add(pose.pose_id)
        self._last_pose_commit_cycle = pose.commit_cycle
        self._pose_receipts = self._pose_receipts + (receipt,)
        return receipt

    def _baseline_decision(
        self, event: EventRecord
    ) -> Tuple[
        DecisionMode,
        Optional[QuaternionXYZW],
        Tuple[int, ...],
        Tuple[int, ...],
        Tuple[int, ...],
        str,
    ]:
        samples = tuple(
            RecoveryPoseSample(
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                pose.quaternion_xyzw,
            )
            for pose in self._valid_poses
        )
        try:
            recovery = recover_causal_cav(
                samples,
                event.occurrence_timestamp_ns,
                event.decision_cycle,
                max_horizon_ns=self.config.max_horizon_ns,
                zoh_max_age_ns=self.config.zoh_max_age_ns,
            )
        except GeometryError as exc:
            raise DSPBError("frozen CAV fallback failed: %s" % exc) from exc
        selected = []
        for timestamp, cycle in zip(
            recovery.used_measurement_timestamps_ns, recovery.used_commit_cycles
        ):
            match = next(
                (
                    pose
                    for pose in self._valid_poses
                    if pose.measurement_timestamp_ns == timestamp
                    and pose.commit_cycle == cycle
                ),
                None,
            )
            if match is None:
                raise DSPBError("frozen CAV receipt could not resolve a used pose")
            selected.append(match)
        if recovery.mode is RecoveryMode.CAV:
            mode = DecisionMode.CURRENT_CAV
        elif recovery.mode is RecoveryMode.ZOH:
            mode = DecisionMode.ZOH
        else:
            mode = DecisionMode.BYPASS
        return (
            mode,
            recovery.quaternion_xyzw,
            tuple(pose.pose_id for pose in selected),
            tuple(pose.measurement_timestamp_ns for pose in selected),
            tuple(pose.commit_cycle for pose in selected),
            recovery.reason,
        )

    def _predict_from_state(self, event: EventRecord, state: EpochState) -> EventDecision:
        selected = state.selected_expert_id
        function = state.function(selected) if selected is not None else None
        candidate_result = None  # type: Optional[ForecastResult]
        fallback_cause = state.lock_reason
        if selected in (E1, E2, E3) and function is not None:
            if any(cycle >= event.decision_cycle for cycle in function.source_commit_cycles):
                raise DSPBError("published expert contains a same/future-edge pose")
            if any(
                timestamp > event.occurrence_timestamp_ns
                for timestamp in function.source_timestamps_ns
            ):
                fallback_cause = "selected_expert_measurement_after_event"
            else:
                candidate_result = function.forecast(event.occurrence_timestamp_ns)
                if not candidate_result.valid:
                    fallback_cause = "selected_expert_%s" % candidate_result.reason
        elif selected == E0:
            fallback_cause = "selected_e0_baseline"
        elif selected is not None:
            raise DSPBError("published winner is not one of the exact four experts")

        if (
            selected in (E1, E2, E3)
            and function is not None
            and candidate_result is not None
            and candidate_result.valid
            and candidate_result.quaternion_xyzw is not None
        ):
            mode = DecisionMode.DSPB
            output = candidate_result.quaternion_xyzw
            used_ids = function.source_pose_ids
            used_timestamps = function.source_timestamps_ns
            used_cycles = function.source_commit_cycles
            geometry_expert = selected
            candidate_used = True
            fallback_reason = None
        else:
            (
                mode,
                output,
                used_ids,
                used_timestamps,
                used_cycles,
                baseline_reason,
            ) = self._baseline_decision(event)
            geometry_expert = E0 if mode is DecisionMode.CURRENT_CAV else None
            candidate_used = False
            fallback_reason = "%s:%s" % (fallback_cause, baseline_reason)

        unsigned = {
            "candidate_id": self.config.candidate_id,
            "config_sha256": self.config.sha256,
            "event_id": event.event_id,
            "occurrence_timestamp_ns": event.occurrence_timestamp_ns,
            "occurrence_cycle": event.occurrence_cycle,
            "decision_cycle": event.decision_cycle,
            "state_version": state.state_version,
            "selected_expert_id": selected,
            "geometry_expert_id": geometry_expert,
            "mode": mode.value,
            "candidate_used": candidate_used,
            "output_quaternion_xyzw": list(output) if output is not None else None,
            "used_pose_ids": list(used_ids),
            "used_pose_timestamps_ns": list(used_timestamps),
            "used_pose_commit_cycles": list(used_cycles),
            "fallback_reason": fallback_reason,
        }
        return EventDecision(
            self.config.candidate_id,
            self.config.sha256,
            event.event_id,
            event.occurrence_timestamp_ns,
            event.occurrence_cycle,
            event.decision_cycle,
            state.state_version,
            selected,
            geometry_expert,
            mode,
            candidate_used,
            output,
            used_ids,
            used_timestamps,
            used_cycles,
            fallback_reason,
            _canonical_sha256(unsigned),
        )

    def predict_event_cluster(
        self, events: Sequence[EventRecord]
    ) -> Tuple[EventDecision, ...]:
        """Predict one equal-time cluster atomically from one state version."""

        source = tuple(events)
        if not source:
            raise DSPBError("event cluster must not be empty")
        for event in source:
            _exact_dataclass(event, EventRecord, "event")
        edge = (
            source[0].occurrence_timestamp_ns,
            source[0].occurrence_cycle,
            source[0].decision_cycle,
        )
        if any(
            (
                event.occurrence_timestamp_ns,
                event.occurrence_cycle,
                event.decision_cycle,
            )
            != edge
            for event in source
        ):
            raise DSPBError("equal-time cluster members must share timestamp and edges")
        identifiers = tuple(event.event_id for event in source)
        if len(set(identifiers)) != len(identifiers):
            raise DSPBError("event_id is duplicated within the cluster")
        if any(identifier in self._seen_event_ids for identifier in identifiers):
            raise DSPBError("event_id must be exact-once")
        if (
            self._last_event_timestamp_ns is not None
            and source[0].occurrence_timestamp_ns < self._last_event_timestamp_ns
        ):
            raise DSPBError("event occurrence timestamps must not move backwards")
        self._advance_stream(source[0].decision_cycle)
        state = self._published
        decisions = tuple(self._predict_from_state(event, state) for event in source)
        if len({decision.state_version for decision in decisions}) != 1:
            raise DSPBError("equal-time cluster observed multiple state versions")
        self._seen_event_ids.update(identifiers)
        self._last_event_timestamp_ns = source[0].occurrence_timestamp_ns
        self._event_decisions = self._event_decisions + decisions
        return decisions

    def predict_event(self, event: EventRecord) -> EventDecision:
        return self.predict_event_cluster((event,))[0]


__all__ = (
    "DSPBConfig",
    "DSPBError",
    "DSPBModel",
    "CreditState",
    "DecisionMode",
    "E0",
    "E1",
    "E2",
    "E3",
    "EXPERT_IDS",
    "EpochState",
    "EventDecision",
    "EventRecord",
    "ExpertFunction",
    "ForecastResult",
    "PoseCommitReceipt",
    "PoseForecastScore",
    "SuppliedPose",
)
