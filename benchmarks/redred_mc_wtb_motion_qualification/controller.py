"""Deterministic motion qualification for the MC-WTB control plane.

The controller never looks at an accuracy score or a future event.  It consumes
one pose-derived, fixed-point displacement proxy at an epoch boundary and
chooses a path for the complete epoch.  UNRELIABLE and LOW always preserve the
sensor event through the bypass path; no class means drop or flush-discard.

Threshold values are deliberately supplied by the caller.  This module does
not freeze performance thresholds after the consumed metric-v3 holdout.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
from typing import Optional, Sequence


class MotionQualificationError(ValueError):
    """The motion-control contract was violated."""


class MotionClass(IntEnum):
    UNRELIABLE = 0
    LOW = 1
    MID = 2
    HIGH = 3


class Route(IntEnum):
    SENSOR_FIXED_BYPASS = 0
    MC_CORRECT_SPARSE = 1
    MC_WTB_TILE = 2


@dataclass(frozen=True)
class MotionQualificationConfig:
    """Fixed-point hysteresis thresholds, expressed in caller-defined Q units."""

    mid_to_low_q: int
    low_to_mid_q: int
    high_to_mid_q: int
    mid_to_high_q: int
    minimum_dwell_epochs: int

    def __post_init__(self) -> None:
        values = (
            self.mid_to_low_q,
            self.low_to_mid_q,
            self.high_to_mid_q,
            self.mid_to_high_q,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise MotionQualificationError("thresholds must be non-negative integers")
        if not (
            self.mid_to_low_q < self.low_to_mid_q
            <= self.high_to_mid_q < self.mid_to_high_q
        ):
            raise MotionQualificationError("hysteresis thresholds are not ordered")
        if type(self.minimum_dwell_epochs) is not int or self.minimum_dwell_epochs < 1:
            raise MotionQualificationError("minimum_dwell_epochs must be positive")


@dataclass(frozen=True)
class MotionEvidence:
    epoch_id: int
    displacement_q: int
    pose_reliable: bool

    def __post_init__(self) -> None:
        if type(self.epoch_id) is not int or self.epoch_id < 0:
            raise MotionQualificationError("epoch_id must be a non-negative integer")
        if type(self.displacement_q) is not int or self.displacement_q < 0:
            raise MotionQualificationError("displacement_q must be a non-negative integer")
        if type(self.pose_reliable) is not bool:
            raise MotionQualificationError("pose_reliable must be bool")


@dataclass(frozen=True)
class MotionDecision:
    epoch_id: int
    motion_class: MotionClass
    route: Route
    warp_enable: bool
    tile_enable: bool
    safe_bypass: bool
    class_changed: bool
    displacement_q: int


def _route(motion_class: MotionClass) -> Route:
    if motion_class in (MotionClass.UNRELIABLE, MotionClass.LOW):
        return Route.SENSOR_FIXED_BYPASS
    if motion_class is MotionClass.MID:
        return Route.MC_CORRECT_SPARSE
    return Route.MC_WTB_TILE


class MotionQualifier:
    """Stateful epoch controller with immediate fail-safe and dwell filtering."""

    __slots__ = ("config", "_motion_class", "_candidate", "_candidate_count", "_last_epoch")

    def __init__(self, config: MotionQualificationConfig) -> None:
        if not isinstance(config, MotionQualificationConfig):
            raise MotionQualificationError("config must be MotionQualificationConfig")
        self.config = config
        self._motion_class = MotionClass.UNRELIABLE
        self._candidate = MotionClass.UNRELIABLE
        self._candidate_count = 0
        self._last_epoch: Optional[int] = None

    @property
    def motion_class(self) -> MotionClass:
        return self._motion_class

    def _desired(self, displacement_q: int) -> MotionClass:
        cfg = self.config
        if self._motion_class is MotionClass.LOW:
            if displacement_q >= cfg.mid_to_high_q:
                return MotionClass.HIGH
            if displacement_q >= cfg.low_to_mid_q:
                return MotionClass.MID
            return MotionClass.LOW
        if self._motion_class is MotionClass.MID:
            if displacement_q <= cfg.mid_to_low_q:
                return MotionClass.LOW
            if displacement_q >= cfg.mid_to_high_q:
                return MotionClass.HIGH
            return MotionClass.MID
        if self._motion_class is MotionClass.HIGH:
            if displacement_q <= cfg.mid_to_low_q:
                return MotionClass.LOW
            if displacement_q <= cfg.high_to_mid_q:
                return MotionClass.MID
            return MotionClass.HIGH
        # There is no prior reliable state after reset or a pose fault.
        if displacement_q < cfg.low_to_mid_q:
            return MotionClass.LOW
        if displacement_q >= cfg.mid_to_high_q:
            return MotionClass.HIGH
        return MotionClass.MID

    def step(self, evidence: MotionEvidence) -> MotionDecision:
        if not isinstance(evidence, MotionEvidence):
            raise MotionQualificationError("evidence must be MotionEvidence")
        if self._last_epoch is not None and evidence.epoch_id <= self._last_epoch:
            raise MotionQualificationError("epoch_id must be strictly increasing")
        self._last_epoch = evidence.epoch_id
        previous = self._motion_class

        if not evidence.pose_reliable:
            # Safety faults bypass immediately; dwell applies only to enabling work.
            self._motion_class = MotionClass.UNRELIABLE
            self._candidate = MotionClass.UNRELIABLE
            self._candidate_count = 0
        else:
            desired = self._desired(evidence.displacement_q)
            if desired is self._motion_class:
                self._candidate = desired
                self._candidate_count = 0
            else:
                if desired is self._candidate:
                    self._candidate_count += 1
                else:
                    self._candidate = desired
                    self._candidate_count = 1
                if self._candidate_count >= self.config.minimum_dwell_epochs:
                    self._motion_class = desired
                    self._candidate_count = 0

        route = _route(self._motion_class)
        return MotionDecision(
            epoch_id=evidence.epoch_id,
            motion_class=self._motion_class,
            route=route,
            warp_enable=route is not Route.SENSOR_FIXED_BYPASS,
            tile_enable=route is Route.MC_WTB_TILE,
            safe_bypass=route is Route.SENSOR_FIXED_BYPASS,
            class_changed=self._motion_class is not previous,
            displacement_q=evidence.displacement_q,
        )


def rotation_displacement_proxy_q(
    reference_quaternion_xyzw: Sequence[float],
    occurrence_quaternion_xyzw: Sequence[float],
    focal_length_px: float,
    *,
    fractional_bits: int,
) -> int:
    """Return round(focal_px * relative_angle * 2**fractional_bits).

    This is a depth-independent rotation-magnitude proxy, not an event-score
    predictor and not a full per-pixel optical-flow bound.
    """

    if len(reference_quaternion_xyzw) != 4 or len(occurrence_quaternion_xyzw) != 4:
        raise MotionQualificationError("quaternions must contain four xyzw values")
    if type(fractional_bits) is not int or not 0 <= fractional_bits <= 24:
        raise MotionQualificationError("fractional_bits must be an integer in [0,24]")
    focal = float(focal_length_px)
    if not math.isfinite(focal) or focal <= 0.0:
        raise MotionQualificationError("focal_length_px must be positive and finite")

    quaternions = []
    for name, source in (
        ("reference", reference_quaternion_xyzw),
        ("occurrence", occurrence_quaternion_xyzw),
    ):
        values = tuple(float(value) for value in source)
        if not all(math.isfinite(value) for value in values):
            raise MotionQualificationError(f"{name} quaternion must be finite")
        norm = math.sqrt(math.fsum(value * value for value in values))
        if norm <= 0.0:
            raise MotionQualificationError(f"{name} quaternion must have nonzero norm")
        quaternions.append(tuple(value / norm for value in values))
    dot = abs(math.fsum(a * b for a, b in zip(*quaternions)))
    angle = 2.0 * math.acos(min(1.0, max(0.0, dot)))
    scaled = focal * angle * (1 << fractional_bits)
    if not math.isfinite(scaled):
        raise MotionQualificationError("displacement proxy is non-finite")
    return int(math.floor(scaled + 0.5))
