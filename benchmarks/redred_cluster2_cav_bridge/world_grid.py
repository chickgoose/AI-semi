"""Deterministic rotation-only world-ray to equirectangular-grid mapping.

This module is geometry only.  It neither invokes CAV nor imports a scorer.
The input ray is an active world-coordinate direction ``(+X, +Y, +Z)``.
Azimuth is measured about ``+Z`` from ``+X`` toward ``+Y`` and is
canonicalized to ``[-pi, pi)``.  Elevation is measured from the XY plane
toward ``+Z`` and lies in ``[-pi/2, pi/2]``.

The grid has ``width`` azimuth columns and ``height`` elevation rows.  Column
zero starts at the ``-pi`` seam and columns increase with azimuth.  Row zero
contains the north pole (``+Z``), rows increase toward the south pole
(``-Z``), and the flattened index is row-major: ``y * width + x``.  Because
azimuth is undefined at either exact pole, both poles canonically use ``x=0``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence, Tuple


Ray = Tuple[float, float, float]
UNIT_RAY_TOLERANCE = 1.0e-9
COORDINATE_CONVENTION = (
    "WORLD_XYZ_ACTIVE_RAY;azimuth=atan2(+Y,+X)_range[-pi,pi);"
    "elevation=atan2(+Z,hypot(X,Y))_range[-pi/2,pi/2];"
    "x=azimuth_bins_from_-pi_seam_increasing;"
    "y=elevation_bins_north_to_south;exact_poles_x=0;"
    "index=row_major_y_times_width_plus_x"
)


class WorldGridError(ValueError):
    """The ray, dimensions, or derived grid coordinate is invalid."""


def _positive_dimension(value: object, where: str) -> int:
    if type(value) is not int or value <= 0:
        raise WorldGridError("%s must be a positive integer" % where)
    return value


def _unit_world_ray(value: object) -> Ray:
    if type(value) not in (tuple, list) or len(value) != 3:  # type: ignore[arg-type]
        raise WorldGridError("world_ray must contain exactly three components")
    components = []
    for index, component in enumerate(value):  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise WorldGridError("world_ray[%d] must be a finite number" % index)
        number = float(component)
        if not math.isfinite(number):
            raise WorldGridError("world_ray[%d] must be a finite number" % index)
        components.append(number)
    ray = tuple(components)
    norm = math.sqrt(math.fsum(component * component for component in ray))
    if not math.isfinite(norm) or abs(norm - 1.0) > UNIT_RAY_TOLERANCE:
        raise WorldGridError("world_ray must be normalized within 1e-9")
    return ray  # type: ignore[return-value]


@dataclass(frozen=True)
class WorldGridCoordinate:
    """One validated cell in the explicit equirectangular convention."""

    x: int
    y: int
    index: int
    width: int
    height: int
    azimuth_rad: float
    elevation_rad: float
    coordinate_convention: str = COORDINATE_CONVENTION

    def __post_init__(self) -> None:
        width = _positive_dimension(self.width, "width")
        height = _positive_dimension(self.height, "height")
        if type(self.x) is not int or not 0 <= self.x < width:
            raise WorldGridError("x lies outside the world grid")
        if type(self.y) is not int or not 0 <= self.y < height:
            raise WorldGridError("y lies outside the world grid")
        if type(self.index) is not int or self.index != self.y * width + self.x:
            raise WorldGridError("index differs from row-major x/y coordinates")
        if (
            not isinstance(self.azimuth_rad, float)
            or not math.isfinite(self.azimuth_rad)
            or not -math.pi <= self.azimuth_rad < math.pi
        ):
            raise WorldGridError("azimuth lies outside [-pi, pi)")
        if (
            not isinstance(self.elevation_rad, float)
            or not math.isfinite(self.elevation_rad)
            or not -0.5 * math.pi <= self.elevation_rad <= 0.5 * math.pi
        ):
            raise WorldGridError("elevation lies outside [-pi/2, pi/2]")
        if self.coordinate_convention != COORDINATE_CONVENTION:
            raise WorldGridError("coordinate convention differs")


def quantize_world_ray(
    world_ray: Sequence[float], width: int, height: int
) -> WorldGridCoordinate:
    """Quantize one normalized world ray into a ``width`` by ``height`` grid.

    Bin ownership is half-open.  The azimuth seam belongs to column zero; the
    exact north and south poles belong to rows zero and ``height - 1`` and use
    canonical column zero.  No pose recovery, CAV decision, or scoring occurs.
    """

    checked_width = _positive_dimension(width, "width")
    checked_height = _positive_dimension(height, "height")
    x_world, y_world, z_world = _unit_world_ray(world_ray)
    horizontal = math.hypot(x_world, y_world)

    if horizontal == 0.0:
        azimuth = -math.pi
        x = 0
    else:
        azimuth = math.atan2(y_world, x_world)
        if azimuth >= math.pi:
            azimuth = -math.pi
        azimuth_fraction = (azimuth + math.pi) / (2.0 * math.pi)
        x = int(math.floor(azimuth_fraction * checked_width))
        # A value immediately below +pi can round to a unit fraction.  It is
        # still on the last half-open bin, not on the canonical seam.
        if x == checked_width:
            x = checked_width - 1

    elevation = math.atan2(z_world, horizontal)
    elevation_fraction = (0.5 * math.pi - elevation) / math.pi
    y = int(math.floor(elevation_fraction * checked_height))
    if y == checked_height:
        y = checked_height - 1

    if not (
        -math.pi <= azimuth < math.pi
        and -0.5 * math.pi <= elevation <= 0.5 * math.pi
        and 0 <= x < checked_width
        and 0 <= y < checked_height
    ):
        raise WorldGridError("derived spherical coordinate lies outside its range")

    return WorldGridCoordinate(
        x=x,
        y=y,
        index=y * checked_width + x,
        width=checked_width,
        height=checked_height,
        azimuth_rad=azimuth,
        elevation_rad=elevation,
    )


__all__ = [
    "COORDINATE_CONVENTION",
    "UNIT_RAY_TOLERANCE",
    "WorldGridCoordinate",
    "WorldGridError",
    "quantize_world_ray",
]
