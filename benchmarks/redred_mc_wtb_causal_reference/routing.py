"""Exact-once epoch routing contract for the motion-qualified control plane.

This is the executable integration boundary between the qualifier decision and
future sparse/tile datapaths.  It deliberately records every accepted source
event.  Out-of-FOV and invalid geometry are dispositions, never filters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import math
from typing import Any, Callable, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_motion_qualification import MotionDecision, Route

# Kept byte-identical to the preserved geometry status vocabulary without
# importing its Python >=3.10 dataclass implementation.  The route/ledger core
# therefore remains executable on the Python 3.8 physical server.
BEHIND_REFERENCE = "behind_reference"
IN_FOV = "in_fov"
INVALID_GEOMETRY = "invalid_geometry"
OUTSIDE_FOV = "outside_fov"


class EpochRouteError(ValueError):
    """The exact-once epoch contract was violated."""


class Disposition(str, Enum):
    SENSOR_FIXED_EVENT = "sensor_fixed_event"
    MC_CORRECT_SPARSE_EVENT = "mc_correct_sparse_event"
    MC_WTB_TILE_MEMBER = "mc_wtb_tile_member"
    RAW_ESCAPE_GEOMETRIC_OOF = "raw_escape_geometric_oof"
    RAW_BYPASS_INVALID_GEOMETRY = "raw_bypass_invalid_geometry"


@dataclass(frozen=True)
class SourceEvent:
    event_id: int
    timestamp_ns: int
    x: int
    y: int
    polarity: int

    def __post_init__(self) -> None:
        for name in ("event_id", "timestamp_ns", "x", "y", "polarity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise EpochRouteError("%s must be an integer" % name)
        if self.event_id < 0 or self.timestamp_ns < 0 or self.x < 0 or self.y < 0:
            raise EpochRouteError("event fields must be non-negative")
        if self.polarity not in (0, 1):
            raise EpochRouteError("polarity must be zero or one")


@dataclass(frozen=True)
class RoutedEvent:
    source: SourceEvent
    epoch_id: int
    requested_route: Route
    disposition: Disposition
    reference_x: Optional[float]
    reference_y: Optional[float]
    reference_ray: Optional[Tuple[float, float, float]]
    geometry_status: str
    tile_x: Optional[int]
    tile_y: Optional[int]


@dataclass(frozen=True)
class EpochReceipt:
    epoch_id: int
    route: Route
    start_timestamp_ns: int
    end_timestamp_ns: int
    accepted_events: int
    output_dispositions: int
    ordered_event_ids: Tuple[int, ...]
    ordered_identity_sha256: str
    disposition_counts: Tuple[Tuple[str, int], ...]
    dropped_events: int
    duplicate_events: int
    reordered_events: int
    routing_complete: bool
    transport_drain_claimed: bool


def _identity_line(event: SourceEvent) -> bytes:
    return ("%d,%d,%d,%d,%d\n" % (
        event.event_id, event.timestamp_ns, event.x, event.y, event.polarity
    )).encode("ascii")


class EpochRouter:
    """Route one closed epoch without changing source order or denominator."""

    def __init__(self, tile_width: int = 8, tile_height: int = 8) -> None:
        if (
            isinstance(tile_width, bool) or not isinstance(tile_width, int)
            or isinstance(tile_height, bool) or not isinstance(tile_height, int)
            or tile_width <= 0 or tile_height <= 0
        ):
            raise EpochRouteError("tile dimensions must be positive")
        self.tile_width = tile_width
        self.tile_height = tile_height
        self._seen_ids = set()  # type: set[int]
        self._last_closed_epoch = None  # type: Optional[int]
        self._last_end_timestamp = None  # type: Optional[int]

    def route_epoch(
        self,
        decision: MotionDecision,
        events: Sequence[SourceEvent],
        expected_event_ids: Sequence[int],
        start_timestamp_ns: int,
        end_timestamp_ns: int,
        warp_provider: Callable[[SourceEvent], Any],
    ) -> Tuple[Tuple[RoutedEvent, ...], EpochReceipt]:
        if not isinstance(decision, MotionDecision):
            raise EpochRouteError("decision must be MotionDecision")
        expected_class_route = {
            0: Route.SENSOR_FIXED_BYPASS,
            1: Route.SENSOR_FIXED_BYPASS,
            2: Route.MC_CORRECT_SPARSE,
            3: Route.MC_WTB_TILE,
        }
        if expected_class_route.get(int(decision.motion_class)) is not decision.route:
            raise EpochRouteError("motion class and route are inconsistent")
        if decision.warp_enable != (decision.route is not Route.SENSOR_FIXED_BYPASS):
            raise EpochRouteError("warp enable and route are inconsistent")
        if decision.tile_enable != (decision.route is Route.MC_WTB_TILE):
            raise EpochRouteError("tile enable and route are inconsistent")
        if decision.safe_bypass != (decision.route is Route.SENSOR_FIXED_BYPASS):
            raise EpochRouteError("safe bypass and route are inconsistent")
        if self._last_closed_epoch is not None and decision.epoch_id <= self._last_closed_epoch:
            raise EpochRouteError("epoch_id must be strictly increasing after drain")
        if (
            isinstance(start_timestamp_ns, bool) or not isinstance(start_timestamp_ns, int)
            or isinstance(end_timestamp_ns, bool) or not isinstance(end_timestamp_ns, int)
            or start_timestamp_ns < 0 or end_timestamp_ns <= start_timestamp_ns
        ):
            raise EpochRouteError("epoch must be a non-empty half-open interval")
        if self._last_end_timestamp is not None and start_timestamp_ns < self._last_end_timestamp:
            raise EpochRouteError("epoch intervals overlap or regress")
        expected_ids = tuple(expected_event_ids)
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in expected_ids):
            raise EpochRouteError("expected event IDs must be non-negative integers")
        if len(set(expected_ids)) != len(expected_ids):
            raise EpochRouteError("expected event IDs contain duplicates")
        local_ids = set()
        previous_timestamp = None  # type: Optional[int]
        routed = []  # type: list[RoutedEvent]

        for source in events:
            if not isinstance(source, SourceEvent):
                raise EpochRouteError("events must contain SourceEvent values")
            if not start_timestamp_ns <= source.timestamp_ns < end_timestamp_ns:
                raise EpochRouteError("event timestamp lies outside epoch")
            if source.event_id in local_ids or source.event_id in self._seen_ids:
                raise EpochRouteError("duplicate or cross-epoch event ID")
            if previous_timestamp is not None and source.timestamp_ns < previous_timestamp:
                raise EpochRouteError("timestamps are not nondecreasing")
            local_ids.add(source.event_id)
            previous_timestamp = source.timestamp_ns

            if decision.route is Route.SENSOR_FIXED_BYPASS:
                routed.append(RoutedEvent(
                    source, decision.epoch_id, decision.route,
                    Disposition.SENSOR_FIXED_EVENT, float(source.x), float(source.y),
                    None, "bypass", None, None,
                ))
                continue

            warp = warp_provider(source)
            required = ("event_id", "timestamp_ns", "polarity", "status", "reference_x", "reference_y", "reference_ray")
            if any(not hasattr(warp, name) for name in required):
                raise EpochRouteError("warp provider returned an incomplete record")
            if warp.event_id != source.event_id:
                raise EpochRouteError("warp provider changed event identity")
            if warp.timestamp_ns != source.timestamp_ns or warp.polarity != source.polarity:
                raise EpochRouteError("warp provider changed event semantics")
            if not hasattr(warp, "source_x") or not hasattr(warp, "source_y") or not hasattr(warp, "reference_timestamp_ns"):
                raise EpochRouteError("warp provider omitted source/reference binding")
            if warp.source_x != float(source.x) or warp.source_y != float(source.y):
                raise EpochRouteError("warp provider changed source coordinates")
            if warp.reference_timestamp_ns != start_timestamp_ns:
                raise EpochRouteError("warp provider changed epoch reference timestamp")
            if warp.status in (OUTSIDE_FOV, BEHIND_REFERENCE):
                disposition = Disposition.RAW_ESCAPE_GEOMETRIC_OOF
                rx, ry, ray, tx, ty = float(source.x), float(source.y), warp.reference_ray, None, None
            elif warp.status == INVALID_GEOMETRY:
                disposition = Disposition.RAW_BYPASS_INVALID_GEOMETRY
                rx, ry, ray, tx, ty = float(source.x), float(source.y), None, None, None
            elif warp.status == IN_FOV:
                if warp.reference_x is None or warp.reference_y is None or warp.reference_ray is None:
                    raise EpochRouteError("in-FOV warp lacks reference coordinates")
                rx, ry, ray = warp.reference_x, warp.reference_y, warp.reference_ray
                if not math.isfinite(float(rx)) or not math.isfinite(float(ry)) or rx < 0 or ry < 0:
                    raise EpochRouteError("in-FOV warp coordinates are invalid")
                if decision.route is Route.MC_CORRECT_SPARSE:
                    disposition = Disposition.MC_CORRECT_SPARSE_EVENT
                    tx, ty = None, None
                else:
                    disposition = Disposition.MC_WTB_TILE_MEMBER
                    tx = int(rx) // self.tile_width
                    ty = int(ry) // self.tile_height
            else:
                raise EpochRouteError("warp provider returned an unknown status")
            routed.append(RoutedEvent(
                source, decision.epoch_id, decision.route, disposition,
                rx, ry, ray, warp.status, tx, ty,
            ))

        input_ids = tuple(item.event_id for item in events)
        output_ids = tuple(item.source.event_id for item in routed)
        if input_ids != expected_ids:
            raise EpochRouteError("input IDs differ from the frozen ordered ledger")
        if input_ids != output_ids or len(routed) != len(events):
            raise EpochRouteError("event conservation failed")
        digest = hashlib.sha256()
        for item in routed:
            digest.update(_identity_line(item.source))
        counts = tuple(
            (item.value, sum(row.disposition is item for row in routed))
            for item in Disposition
        )
        receipt = EpochReceipt(
            epoch_id=decision.epoch_id,
            route=decision.route,
            start_timestamp_ns=start_timestamp_ns,
            end_timestamp_ns=end_timestamp_ns,
            accepted_events=len(events),
            output_dispositions=len(routed),
            ordered_event_ids=output_ids,
            ordered_identity_sha256=digest.hexdigest(),
            disposition_counts=counts,
            dropped_events=0,
            duplicate_events=0,
            reordered_events=0,
            routing_complete=True,
            transport_drain_claimed=False,
        )
        self._seen_ids.update(local_ids)
        self._last_closed_epoch = decision.epoch_id
        self._last_end_timestamp = end_timestamp_ns
        return tuple(routed), receipt
