"""Grid-phase-neutral focus metric for polarity-separated event clouds.

The metric is the normalized cross-event overlap of equal-mass, isotropic
Gaussian kernels.  The overlap is evaluated analytically on the continuous
plane, so neither pixel rounding nor a raster-grid origin can change it.  The
constant per-event self energy is reported but excluded from the score.

A :class:`PaddedCanvas` is still mandatory.  It is a fail-closed admission
envelope: every arm must contain the same event identities and every finite
coordinate must be inside the same declared padded canvas.  It is not a crop
of the Gaussian integral, and therefore cannot silently discard mass near a
sensor boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


METRIC_ID = "polarity-separated-analytic-gaussian-pair-overlap/v1"


class FocusMetricError(ValueError):
    """Raised when an input cannot satisfy the focus-metric contract."""


@dataclass(frozen=True)
class FocusSample:
    """One equal-mass event projected into the metric's coordinate frame."""

    event_id: int
    x: float
    y: float
    polarity: int


@dataclass(frozen=True)
class PaddedCanvas:
    """Fixed sensor canvas enlarged by the same padding on every side.

    Pixel centers occupy ``origin_x .. origin_x + width - 1`` and likewise
    for y.  Bounds are inclusive because they describe accepted continuous
    center coordinates, not array indices.
    """

    width: int
    height: int
    padding_px: float
    origin_x: float = 0.0
    origin_y: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, int) or self.width <= 0:
            raise FocusMetricError("canvas width must be a positive integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int) or self.height <= 0:
            raise FocusMetricError("canvas height must be a positive integer")
        for name, value in (
            ("padding_px", self.padding_px),
            ("origin_x", self.origin_x),
            ("origin_y", self.origin_y),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FocusMetricError(f"canvas {name} must be a finite number")
            if not math.isfinite(float(value)):
                raise FocusMetricError(f"canvas {name} must be a finite number")
        if self.padding_px < 0:
            raise FocusMetricError("canvas padding_px must be non-negative")

    @property
    def x_min(self) -> float:
        return float(self.origin_x) - float(self.padding_px)

    @property
    def x_max(self) -> float:
        return float(self.origin_x) + self.width - 1 + float(self.padding_px)

    @property
    def y_min(self) -> float:
        return float(self.origin_y) - float(self.padding_px)

    @property
    def y_max(self) -> float:
        return float(self.origin_y) + self.height - 1 + float(self.padding_px)

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


@dataclass(frozen=True)
class PolarityFocus:
    """Focus terms for one polarity channel."""

    event_count: int
    ordered_pair_count: int
    ordered_overlap_sum: float
    normalized_overlap: float


@dataclass(frozen=True)
class FocusResult:
    """A bounded focus score and auditable energy decomposition.

    ``score`` is in [0, 1].  Zero means no analytic overlap (up to floating
    underflow), and one means every same-polarity event is coincident.
    Opposite-polarity pairs never contribute.
    """

    metric_id: str
    sigma_px: float
    event_count: int
    total_mass: float
    self_energy: float
    cross_event_energy: float
    maximum_cross_event_energy: float
    raw_energy: float
    score: float
    polarity_0: PolarityFocus
    polarity_1: PolarityFocus


def _validated_samples(samples: Iterable[FocusSample], canvas: PaddedCanvas) -> list[FocusSample]:
    try:
        values = list(samples)
    except TypeError as error:
        raise FocusMetricError("samples must be iterable") from error
    if not values:
        raise FocusMetricError("focus requires at least one event")

    seen: set[int] = set()
    output: list[FocusSample] = []
    for ordinal, sample in enumerate(values):
        if not isinstance(sample, FocusSample):
            raise FocusMetricError(f"sample {ordinal} is not a FocusSample")
        if isinstance(sample.event_id, bool) or not isinstance(sample.event_id, int):
            raise FocusMetricError(f"sample {ordinal} event_id must be an integer")
        if sample.event_id in seen:
            raise FocusMetricError(f"duplicate event_id {sample.event_id}")
        seen.add(sample.event_id)
        if (
            isinstance(sample.polarity, bool)
            or not isinstance(sample.polarity, int)
            or sample.polarity not in (0, 1)
        ):
            raise FocusMetricError(f"sample {sample.event_id} polarity must be 0 or 1")
        if (
            isinstance(sample.x, bool)
            or isinstance(sample.y, bool)
            or not isinstance(sample.x, (int, float))
            or not isinstance(sample.y, (int, float))
        ):
            raise FocusMetricError(f"sample {sample.event_id} coordinates must be numbers")
        x, y = float(sample.x), float(sample.y)
        if not math.isfinite(x) or not math.isfinite(y):
            raise FocusMetricError(f"sample {sample.event_id} coordinates must be finite")
        if not canvas.contains(x, y):
            raise FocusMetricError(
                f"sample {sample.event_id} lies outside the fixed padded canvas"
            )
        output.append(FocusSample(sample.event_id, x, y, sample.polarity))

    # A canonical order makes the floating-point reduction independent of the
    # caller's record order as well as independent of any retire reordering.
    output.sort(key=lambda sample: sample.event_id)
    return output


def _polarity_focus(points: list[FocusSample], inverse_four_sigma_sq: float) -> PolarityFocus:
    count = len(points)
    pair_count = count * (count - 1)
    if pair_count == 0:
        return PolarityFocus(count, 0, 0.0, 0.0)

    unordered_terms: list[float] = []
    for left_index, left in enumerate(points[:-1]):
        for right in points[left_index + 1 :]:
            dx = left.x - right.x
            dy = left.y - right.y
            unordered_terms.append(math.exp(-(dx * dx + dy * dy) * inverse_four_sigma_sq))
    ordered_overlap = 2.0 * math.fsum(unordered_terms)
    normalized = ordered_overlap / pair_count
    # Protect the public invariant from a one-ulp overshoot at the all-equal
    # endpoint while retaining the analytic value everywhere else.
    normalized = min(1.0, max(0.0, normalized))
    return PolarityFocus(count, pair_count, ordered_overlap, normalized)


def compute_focus(
    samples: Iterable[FocusSample],
    *,
    sigma_px: float,
    canvas: PaddedCanvas,
    minimum_events_per_polarity: int = 2,
) -> FocusResult:
    """Compute polarity-separated analytic Gaussian pair-overlap focus.

    Every event has exactly unit mass.  ``minimum_events_per_polarity`` is a
    predeclared degeneracy gate and defaults to two, ensuring that both
    polarity channels have at least one same-polarity pair.  Lowering it below
    two is rejected rather than silently changing the score denominator.
    """

    if isinstance(sigma_px, bool) or not isinstance(sigma_px, (int, float)):
        raise FocusMetricError("sigma_px must be a finite positive number")
    sigma = float(sigma_px)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise FocusMetricError("sigma_px must be a finite positive number")
    if (
        isinstance(minimum_events_per_polarity, bool)
        or not isinstance(minimum_events_per_polarity, int)
        or minimum_events_per_polarity < 2
    ):
        raise FocusMetricError("minimum_events_per_polarity must be an integer >= 2")
    if not isinstance(canvas, PaddedCanvas):
        raise FocusMetricError("canvas must be a PaddedCanvas")

    values = _validated_samples(samples, canvas)
    channels = {
        polarity: [sample for sample in values if sample.polarity == polarity]
        for polarity in (0, 1)
    }
    for polarity, points in channels.items():
        if len(points) < minimum_events_per_polarity:
            raise FocusMetricError(
                f"polarity {polarity} has {len(points)} events; "
                f"requires at least {minimum_events_per_polarity}"
            )

    inverse_four_sigma_sq = 1.0 / (4.0 * sigma * sigma)
    per_polarity = {
        polarity: _polarity_focus(points, inverse_four_sigma_sq)
        for polarity, points in channels.items()
    }
    event_count = len(values)
    ordered_pairs = sum(value.ordered_pair_count for value in per_polarity.values())
    ordered_overlap = math.fsum(
        value.ordered_overlap_sum for value in per_polarity.values()
    )
    if ordered_pairs <= 0:
        # Kept as a second fail-closed guard even though the default per-channel
        # gate makes this unreachable.
        raise FocusMetricError("focus denominator has no same-polarity event pairs")

    kernel_self_energy = 1.0 / (4.0 * math.pi * sigma * sigma)
    self_energy = kernel_self_energy * event_count
    cross_energy = kernel_self_energy * ordered_overlap
    maximum_cross_energy = kernel_self_energy * ordered_pairs
    score = min(1.0, max(0.0, ordered_overlap / ordered_pairs))
    return FocusResult(
        metric_id=METRIC_ID,
        sigma_px=sigma,
        event_count=event_count,
        total_mass=float(event_count),
        self_energy=self_energy,
        cross_event_energy=cross_energy,
        maximum_cross_event_energy=maximum_cross_energy,
        raw_energy=self_energy + cross_energy,
        score=score,
        polarity_0=per_polarity[0],
        polarity_1=per_polarity[1],
    )


def compute_focus_by_arm(
    arms: Mapping[str, Iterable[FocusSample]],
    *,
    sigma_px: float,
    canvas: PaddedCanvas,
    minimum_events_per_polarity: int = 2,
) -> dict[str, FocusResult]:
    """Compute arm scores after enforcing equal identity, polarity, and mass.

    Coordinates may differ by arm.  Event-ID sets and the polarity attached to
    each ID must not.  Because :class:`FocusSample` has no weight field, this
    also fixes every admitted event to mass one and prevents arm-specific
    normalization, clipping, or duplication.
    """

    if not isinstance(arms, Mapping) or not arms:
        raise FocusMetricError("arms must be a non-empty mapping")
    if not isinstance(canvas, PaddedCanvas):
        raise FocusMetricError("canvas must be a PaddedCanvas")
    materialized: dict[str, list[FocusSample]] = {}
    identity: dict[int, int] | None = None
    for arm, samples in arms.items():
        if not isinstance(arm, str) or not arm:
            raise FocusMetricError("arm names must be non-empty strings")
        values = _validated_samples(samples, canvas)
        current = {sample.event_id: sample.polarity for sample in values}
        if identity is None:
            identity = current
        elif current != identity:
            raise FocusMetricError(f"arm {arm} does not have equal event IDs and polarities")
        materialized[arm] = values

    return {
        arm: compute_focus(
            samples,
            sigma_px=sigma_px,
            canvas=canvas,
            minimum_events_per_polarity=minimum_events_per_polarity,
        )
        for arm, samples in materialized.items()
    }


__all__ = [
    "METRIC_ID",
    "FocusMetricError",
    "FocusSample",
    "PaddedCanvas",
    "PolarityFocus",
    "FocusResult",
    "compute_focus",
    "compute_focus_by_arm",
]
