"""Fail-closed, score-independent pose freshness gate.

Only metadata and pose-derived quantities available at the epoch-start decision
edge are accepted.  The arithmetic core is unsigned integer arithmetic with an
explicit 128-bit intermediate bound so this module is also an executable
reference for a later fixed-width implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Optional, Tuple


UINT64_MAX = (1 << 64) - 1
UINT128_MAX = (1 << 128) - 1
NANOSECONDS_PER_SECOND = 1_000_000_000
MICRORADIANS_PER_RADIAN = 1_000_000

_EVIDENCE_SCHEMA = "redred.mc_wtb.pose_freshness_evidence/v1"
_CONFIG_SCHEMA = "redred.mc_wtb.pose_freshness_config/v1"


class FreshnessContractError(ValueError):
    """Static configuration or typed metadata violates the API contract."""


class _ArithmeticOverflow(OverflowError):
    pass


class FreshnessProfile(str, Enum):
    AGE_ONLY = "age_only/v1"
    AGE_TIMES_RATE = "age_times_rate/v1"


class FreshnessAction(str, Enum):
    POSE_QUALIFIED = "pose_qualified"
    UNRELIABLE_SENSOR_FIXED_BYPASS = "unreliable_sensor_fixed_bypass"


class ReasonCode(str, Enum):
    DECISION_NOT_AT_EPOCH_START = "DECISION_NOT_AT_EPOCH_START"
    EPOCH_INTERVAL_INVALID = "EPOCH_INTERVAL_INVALID"
    CLOCK_ALIGNMENT_INVALID = "CLOCK_ALIGNMENT_INVALID"
    POSE_VALUE_INVALID = "POSE_VALUE_INVALID"
    PREVIOUS_POSE_NOT_PAST = "PREVIOUS_POSE_NOT_PAST"
    LATEST_POSE_FROM_FUTURE = "LATEST_POSE_FROM_FUTURE"
    POSE_TIMESTAMPS_NOT_STRICT = "POSE_TIMESTAMPS_NOT_STRICT"
    NON_IMMEDIATE_PREDECESSOR = "NON_IMMEDIATE_PREDECESSOR"
    LATEST_POSE_NOT_LATEST_AVAILABLE = "LATEST_POSE_NOT_LATEST_AVAILABLE"
    TIMEBASE_MISMATCH = "TIMEBASE_MISMATCH"
    TIMEBASE_HASH_MISMATCH = "TIMEBASE_HASH_MISMATCH"
    POSE_STREAM_MISMATCH = "POSE_STREAM_MISMATCH"
    CALIBRATION_ID_MISMATCH = "CALIBRATION_ID_MISMATCH"
    CALIBRATION_HASH_MISMATCH = "CALIBRATION_HASH_MISMATCH"
    PIXEL_GAIN_PROFILE_HASH_MISMATCH = "PIXEL_GAIN_PROFILE_HASH_MISMATCH"
    HARD_COVER_AGE_EXCEEDED = "HARD_COVER_AGE_EXCEEDED"
    RATE_SAMPLE_INTERVAL_TOO_LARGE = "RATE_SAMPLE_INTERVAL_TOO_LARGE"
    RATE_BOUND_UNAUTHORIZED = "RATE_BOUND_UNAUTHORIZED"
    PIXEL_ERROR_LIMIT_EXCEEDED = "PIXEL_ERROR_LIMIT_EXCEEDED"
    ARITHMETIC_OVERFLOW = "ARITHMETIC_OVERFLOW"


def _uint64(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreshnessContractError("%s must be an integer" % where)
    if value < 0 or value > UINT64_MAX:
        raise FreshnessContractError("%s must fit unsigned 64 bits" % where)
    return value


def _positive_uint64(value: Any, where: str) -> int:
    result = _uint64(value, where)
    if result == 0:
        raise FreshnessContractError("%s must be positive" % where)
    return result


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise FreshnessContractError("%s must be a non-empty string" % where)
    return value


def _digest(value: Any, where: str) -> str:
    result = _text(value, where)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise FreshnessContractError("%s must be a lowercase SHA-256" % where)
    return result


def _boolean(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise FreshnessContractError("%s must be bool" % where)
    return value


def ceil_div(numerator: int, denominator: int) -> int:
    """Return exact ceil(numerator/denominator) without addition overflow."""

    numerator = _uint64_or_uint128(numerator, "numerator")
    denominator = _positive_uint128(denominator, "denominator")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + (1 if remainder else 0)


def _uint64_or_uint128(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreshnessContractError("%s must be an integer" % where)
    if value < 0 or value > UINT128_MAX:
        raise FreshnessContractError("%s must fit unsigned 128 bits" % where)
    return value


def _positive_uint128(value: Any, where: str) -> int:
    result = _uint64_or_uint128(value, where)
    if result == 0:
        raise FreshnessContractError("%s must be positive" % where)
    return result


def _checked_product(*values: int) -> int:
    result = 1
    for value in values:
        if value < 0 or value > UINT64_MAX:
            raise _ArithmeticOverflow("operand is outside unsigned 64-bit range")
        if value and result > UINT128_MAX // value:
            raise _ArithmeticOverflow("unsigned 128-bit product overflow")
        result *= value
    return result


def _checked_add(left: int, right: int) -> int:
    if left < 0 or right < 0 or left > UINT64_MAX - right:
        raise _ArithmeticOverflow("unsigned 64-bit addition overflow")
    return left + right


def _canonical_digest(schema: str, value: Dict[str, Any]) -> str:
    payload = {"schema": schema, **value}
    encoded = (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PoseSampleMetadata:
    pose_id: str
    sequence: int
    timestamp_ns: int
    timebase_id: str
    pose_sha256: str
    value_valid: bool

    def __post_init__(self) -> None:
        _text(self.pose_id, "pose_id")
        _uint64(self.sequence, "pose sequence")
        _uint64(self.timestamp_ns, "pose timestamp_ns")
        _text(self.timebase_id, "pose timebase_id")
        _digest(self.pose_sha256, "pose_sha256")
        _boolean(self.value_valid, "pose value_valid")


@dataclass(frozen=True)
class PoseFreshnessConfig:
    profile: FreshnessProfile
    fractional_bits: int
    hard_max_cover_age_ns: int
    max_rate_sample_interval_ns: int
    max_pixel_error_q: int
    pixel_rate_floor_q_per_second: int
    static_error_margin_q: int
    rate_growth_num: int
    rate_growth_den: int
    pixel_gain_q_per_rad: int
    expected_timebase_id: str
    expected_timebase_sha256: str
    expected_pose_stream_id: str
    expected_calibration_id: str
    expected_calibration_sha256: str
    expected_pixel_gain_profile_sha256: str
    profile_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, FreshnessProfile):
            raise FreshnessContractError("profile must be FreshnessProfile")
        if (
            isinstance(self.fractional_bits, bool)
            or not isinstance(self.fractional_bits, int)
            or not 0 <= self.fractional_bits <= 30
        ):
            raise FreshnessContractError("fractional_bits must be an integer in [0,30]")
        _uint64(self.hard_max_cover_age_ns, "hard_max_cover_age_ns")
        _positive_uint64(self.max_rate_sample_interval_ns, "max_rate_sample_interval_ns")
        _uint64(self.max_pixel_error_q, "max_pixel_error_q")
        _uint64(self.pixel_rate_floor_q_per_second, "pixel_rate_floor_q_per_second")
        _uint64(self.static_error_margin_q, "static_error_margin_q")
        _positive_uint64(self.rate_growth_num, "rate_growth_num")
        _positive_uint64(self.rate_growth_den, "rate_growth_den")
        _positive_uint64(self.pixel_gain_q_per_rad, "pixel_gain_q_per_rad")
        _text(self.expected_timebase_id, "expected_timebase_id")
        _digest(self.expected_timebase_sha256, "expected_timebase_sha256")
        _text(self.expected_pose_stream_id, "expected_pose_stream_id")
        _text(self.expected_calibration_id, "expected_calibration_id")
        _digest(self.expected_calibration_sha256, "expected_calibration_sha256")
        _digest(
            self.expected_pixel_gain_profile_sha256,
            "expected_pixel_gain_profile_sha256",
        )
        _text(self.profile_id, "profile_id")


@dataclass(frozen=True)
class PoseEpochEvidence:
    epoch_id: int
    epoch_start_ns: int
    epoch_end_ns: int
    decision_timestamp_ns: int
    timebase_id: str
    timebase_sha256: str
    clock_alignment_valid: bool
    pose_stream_id: str
    pose_snapshot_id: str
    pose_snapshot_sha256: str
    previous_pose: PoseSampleMetadata
    latest_pose: PoseSampleMetadata
    latest_available_pose_id: str
    latest_available_pose_sequence: int
    poses_are_immediate_predecessors: bool
    relative_angle_upper_urad: int
    calibration_id: str
    calibration_sha256: str
    pixel_gain_profile_sha256: str
    rate_bound_assumption_authorized: bool

    def __post_init__(self) -> None:
        _uint64(self.epoch_id, "epoch_id")
        _uint64(self.epoch_start_ns, "epoch_start_ns")
        _uint64(self.epoch_end_ns, "epoch_end_ns")
        _uint64(self.decision_timestamp_ns, "decision_timestamp_ns")
        _text(self.timebase_id, "timebase_id")
        _digest(self.timebase_sha256, "timebase_sha256")
        _boolean(self.clock_alignment_valid, "clock_alignment_valid")
        _text(self.pose_stream_id, "pose_stream_id")
        _text(self.pose_snapshot_id, "pose_snapshot_id")
        _digest(self.pose_snapshot_sha256, "pose_snapshot_sha256")
        if not isinstance(self.previous_pose, PoseSampleMetadata):
            raise FreshnessContractError("previous_pose must be PoseSampleMetadata")
        if not isinstance(self.latest_pose, PoseSampleMetadata):
            raise FreshnessContractError("latest_pose must be PoseSampleMetadata")
        _text(self.latest_available_pose_id, "latest_available_pose_id")
        _uint64(self.latest_available_pose_sequence, "latest_available_pose_sequence")
        _boolean(
            self.poses_are_immediate_predecessors,
            "poses_are_immediate_predecessors",
        )
        _uint64(self.relative_angle_upper_urad, "relative_angle_upper_urad")
        _text(self.calibration_id, "calibration_id")
        _digest(self.calibration_sha256, "calibration_sha256")
        _digest(self.pixel_gain_profile_sha256, "pixel_gain_profile_sha256")
        _boolean(
            self.rate_bound_assumption_authorized,
            "rate_bound_assumption_authorized",
        )


@dataclass(frozen=True)
class FreshnessDecision:
    epoch_id: int
    profile: FreshnessProfile
    pose_reliable: bool
    action: FreshnessAction
    reason_codes: Tuple[str, ...]
    pose_age_at_start_ns: Optional[int]
    cover_age_ns: Optional[int]
    rate_sample_interval_ns: Optional[int]
    recent_displacement_q: Optional[int]
    recent_rate_error_q: Optional[int]
    rate_floor_error_q: Optional[int]
    total_pixel_error_q: Optional[int]
    evidence_sha256: str
    config_sha256: str


def _sample_value(sample: PoseSampleMetadata) -> Dict[str, Any]:
    return {
        "pose_id": sample.pose_id,
        "sequence": sample.sequence,
        "timestamp_ns": sample.timestamp_ns,
        "timebase_id": sample.timebase_id,
        "pose_sha256": sample.pose_sha256,
        "value_valid": sample.value_valid,
    }


def _evidence_value(evidence: PoseEpochEvidence) -> Dict[str, Any]:
    return {
        "epoch_id": evidence.epoch_id,
        "epoch_start_ns": evidence.epoch_start_ns,
        "epoch_end_ns": evidence.epoch_end_ns,
        "decision_timestamp_ns": evidence.decision_timestamp_ns,
        "timebase_id": evidence.timebase_id,
        "timebase_sha256": evidence.timebase_sha256,
        "clock_alignment_valid": evidence.clock_alignment_valid,
        "pose_stream_id": evidence.pose_stream_id,
        "pose_snapshot_id": evidence.pose_snapshot_id,
        "pose_snapshot_sha256": evidence.pose_snapshot_sha256,
        "previous_pose": _sample_value(evidence.previous_pose),
        "latest_pose": _sample_value(evidence.latest_pose),
        "latest_available_pose_id": evidence.latest_available_pose_id,
        "latest_available_pose_sequence": evidence.latest_available_pose_sequence,
        "poses_are_immediate_predecessors": evidence.poses_are_immediate_predecessors,
        "relative_angle_upper_urad": evidence.relative_angle_upper_urad,
        "calibration_id": evidence.calibration_id,
        "calibration_sha256": evidence.calibration_sha256,
        "pixel_gain_profile_sha256": evidence.pixel_gain_profile_sha256,
        "rate_bound_assumption_authorized": evidence.rate_bound_assumption_authorized,
    }


def _config_value(config: PoseFreshnessConfig) -> Dict[str, Any]:
    value = asdict(config)
    value["profile"] = config.profile.value
    return value


def evidence_digest(evidence: PoseEpochEvidence) -> str:
    if not isinstance(evidence, PoseEpochEvidence):
        raise FreshnessContractError("evidence must be PoseEpochEvidence")
    return _canonical_digest(_EVIDENCE_SCHEMA, _evidence_value(evidence))


def config_digest(config: PoseFreshnessConfig) -> str:
    if not isinstance(config, PoseFreshnessConfig):
        raise FreshnessContractError("config must be PoseFreshnessConfig")
    return _canonical_digest(_CONFIG_SCHEMA, _config_value(config))


def _append(reasons: list, reason: ReasonCode) -> None:
    if reason.value not in reasons:
        reasons.append(reason.value)


def qualify_pose_freshness(
    evidence: PoseEpochEvidence,
    config: PoseFreshnessConfig,
) -> FreshnessDecision:
    """Evaluate one epoch using only its epoch-start evidence snapshot."""

    if not isinstance(evidence, PoseEpochEvidence):
        raise FreshnessContractError("evidence must be PoseEpochEvidence")
    if not isinstance(config, PoseFreshnessConfig):
        raise FreshnessContractError("config must be PoseFreshnessConfig")

    reasons = []  # type: list
    previous = evidence.previous_pose
    latest = evidence.latest_pose

    if evidence.decision_timestamp_ns != evidence.epoch_start_ns:
        _append(reasons, ReasonCode.DECISION_NOT_AT_EPOCH_START)
    if evidence.epoch_end_ns <= evidence.epoch_start_ns:
        _append(reasons, ReasonCode.EPOCH_INTERVAL_INVALID)
    if not evidence.clock_alignment_valid:
        _append(reasons, ReasonCode.CLOCK_ALIGNMENT_INVALID)
    if not previous.value_valid or not latest.value_valid:
        _append(reasons, ReasonCode.POSE_VALUE_INVALID)
    if previous.timestamp_ns > evidence.epoch_start_ns:
        _append(reasons, ReasonCode.PREVIOUS_POSE_NOT_PAST)
    if latest.timestamp_ns > evidence.epoch_start_ns:
        _append(reasons, ReasonCode.LATEST_POSE_FROM_FUTURE)
    if latest.timestamp_ns <= previous.timestamp_ns:
        _append(reasons, ReasonCode.POSE_TIMESTAMPS_NOT_STRICT)
    if (
        not evidence.poses_are_immediate_predecessors
        or latest.sequence != previous.sequence + 1
    ):
        _append(reasons, ReasonCode.NON_IMMEDIATE_PREDECESSOR)
    if (
        latest.sequence != evidence.latest_available_pose_sequence
        or latest.pose_id != evidence.latest_available_pose_id
    ):
        _append(reasons, ReasonCode.LATEST_POSE_NOT_LATEST_AVAILABLE)
    if not (
        evidence.timebase_id == config.expected_timebase_id
        and previous.timebase_id == evidence.timebase_id
        and latest.timebase_id == evidence.timebase_id
    ):
        _append(reasons, ReasonCode.TIMEBASE_MISMATCH)
    if evidence.timebase_sha256 != config.expected_timebase_sha256:
        _append(reasons, ReasonCode.TIMEBASE_HASH_MISMATCH)
    if evidence.pose_stream_id != config.expected_pose_stream_id:
        _append(reasons, ReasonCode.POSE_STREAM_MISMATCH)
    if evidence.calibration_id != config.expected_calibration_id:
        _append(reasons, ReasonCode.CALIBRATION_ID_MISMATCH)
    if evidence.calibration_sha256 != config.expected_calibration_sha256:
        _append(reasons, ReasonCode.CALIBRATION_HASH_MISMATCH)
    if (
        evidence.pixel_gain_profile_sha256
        != config.expected_pixel_gain_profile_sha256
    ):
        _append(reasons, ReasonCode.PIXEL_GAIN_PROFILE_HASH_MISMATCH)

    pose_age_at_start = None  # type: Optional[int]
    cover_age = None  # type: Optional[int]
    rate_interval = None  # type: Optional[int]
    if latest.timestamp_ns <= evidence.epoch_start_ns:
        pose_age_at_start = evidence.epoch_start_ns - latest.timestamp_ns
    if latest.timestamp_ns <= evidence.epoch_end_ns:
        cover_age = evidence.epoch_end_ns - latest.timestamp_ns
        if cover_age > config.hard_max_cover_age_ns:
            _append(reasons, ReasonCode.HARD_COVER_AGE_EXCEEDED)
    if latest.timestamp_ns > previous.timestamp_ns:
        rate_interval = latest.timestamp_ns - previous.timestamp_ns

    recent_displacement = None  # type: Optional[int]
    recent_rate_error = None  # type: Optional[int]
    rate_floor_error = None  # type: Optional[int]
    total_pixel_error = None  # type: Optional[int]

    if config.profile is FreshnessProfile.AGE_TIMES_RATE:
        if rate_interval is not None and rate_interval > config.max_rate_sample_interval_ns:
            _append(reasons, ReasonCode.RATE_SAMPLE_INTERVAL_TOO_LARGE)
        if not evidence.rate_bound_assumption_authorized:
            _append(reasons, ReasonCode.RATE_BOUND_UNAUTHORIZED)
        if cover_age is not None and rate_interval is not None:
            try:
                recent_numerator = _checked_product(
                    config.pixel_gain_q_per_rad,
                    evidence.relative_angle_upper_urad,
                )
                recent_displacement = ceil_div(
                    recent_numerator,
                    MICRORADIANS_PER_RADIAN,
                )
                if recent_displacement > UINT64_MAX:
                    raise _ArithmeticOverflow("recent displacement exceeds unsigned 64 bits")
                rate_numerator = _checked_product(
                    config.rate_growth_num,
                    recent_displacement,
                    cover_age,
                )
                rate_denominator = _checked_product(
                    config.rate_growth_den,
                    rate_interval,
                )
                recent_rate_error = ceil_div(rate_numerator, rate_denominator)
                floor_numerator = _checked_product(
                    config.pixel_rate_floor_q_per_second,
                    cover_age,
                )
                rate_floor_error = ceil_div(floor_numerator, NANOSECONDS_PER_SECOND)
                if recent_rate_error > UINT64_MAX or rate_floor_error > UINT64_MAX:
                    raise _ArithmeticOverflow("pixel error exceeds unsigned 64 bits")
                total_pixel_error = _checked_add(
                    max(recent_rate_error, rate_floor_error),
                    config.static_error_margin_q,
                )
            except (_ArithmeticOverflow, FreshnessContractError):
                _append(reasons, ReasonCode.ARITHMETIC_OVERFLOW)
                recent_displacement = None
                recent_rate_error = None
                rate_floor_error = None
                total_pixel_error = None
            if (
                total_pixel_error is not None
                and total_pixel_error > config.max_pixel_error_q
            ):
                _append(reasons, ReasonCode.PIXEL_ERROR_LIMIT_EXCEEDED)

    reliable = not reasons
    return FreshnessDecision(
        epoch_id=evidence.epoch_id,
        profile=config.profile,
        pose_reliable=reliable,
        action=(
            FreshnessAction.POSE_QUALIFIED
            if reliable
            else FreshnessAction.UNRELIABLE_SENSOR_FIXED_BYPASS
        ),
        reason_codes=tuple(reasons),
        pose_age_at_start_ns=pose_age_at_start,
        cover_age_ns=cover_age,
        rate_sample_interval_ns=rate_interval,
        recent_displacement_q=recent_displacement,
        recent_rate_error_q=recent_rate_error,
        rate_floor_error_q=rate_floor_error,
        total_pixel_error_q=total_pixel_error,
        evidence_sha256=evidence_digest(evidence),
        config_sha256=config_digest(config),
    )
