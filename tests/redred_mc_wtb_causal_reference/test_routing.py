from __future__ import annotations

import unittest
from types import SimpleNamespace

from benchmarks.redred_mc_wtb_causal_reference import (
    Disposition,
    EpochRouteError,
    EpochRouter,
    SourceEvent,
)
from benchmarks.redred_mc_wtb_motion_qualification import MotionClass, MotionDecision, Route
from benchmarks.redred_mc_wtb_causal_reference.routing import (
    BEHIND_REFERENCE, IN_FOV, INVALID_GEOMETRY, OUTSIDE_FOV,
)


def decision(epoch_id, route):
    motion_class = {
        Route.SENSOR_FIXED_BYPASS: MotionClass.LOW,
        Route.MC_CORRECT_SPARSE: MotionClass.MID,
        Route.MC_WTB_TILE: MotionClass.HIGH,
    }[route]
    return MotionDecision(
        epoch_id, motion_class, route, route is not Route.SENSOR_FIXED_BYPASS,
        route is Route.MC_WTB_TILE, route is Route.SENSOR_FIXED_BYPASS, True, 10,
    )


def warp(status, source):
    coordinate = status == IN_FOV
    return SimpleNamespace(
        event_id=source.event_id,
        timestamp_ns=source.timestamp_ns,
        polarity=source.polarity,
        status=status,
        source_x=float(source.x),
        source_y=float(source.y),
        reference_timestamp_ns=10 if source.timestamp_ns < 100 else 100,
        reference_ray=(0.0, 0.0, 1.0) if status != INVALID_GEOMETRY else None,
        reference_x=17.0 if coordinate else None,
        reference_y=25.0 if coordinate else None,
    )


class EpochRouterTests(unittest.TestCase):
    def test_all_routes_preserve_order_and_denominator(self) -> None:
        events = (SourceEvent(1, 10, 3, 4, 0), SourceEvent(2, 11, 5, 6, 1))
        for index, route in enumerate(Route):
            router = EpochRouter()
            rows, receipt = router.route_epoch(
                decision(index, route), events, (1, 2), 10, 12, lambda event: warp(IN_FOV, event)
            )
            self.assertEqual(tuple(row.source.event_id for row in rows), (1, 2))
            self.assertEqual(receipt.accepted_events, receipt.output_dispositions)
            self.assertEqual(receipt.dropped_events, 0)
            expected = {
                Route.SENSOR_FIXED_BYPASS: Disposition.SENSOR_FIXED_EVENT,
                Route.MC_CORRECT_SPARSE: Disposition.MC_CORRECT_SPARSE_EVENT,
                Route.MC_WTB_TILE: Disposition.MC_WTB_TILE_MEMBER,
            }[route]
            self.assertTrue(all(row.disposition is expected for row in rows))
            if route is Route.MC_WTB_TILE:
                self.assertEqual((rows[0].tile_x, rows[0].tile_y), (2, 3))

    def test_oof_and_invalid_are_explicit_not_filtered(self) -> None:
        events = tuple(SourceEvent(i, 100 + i, i, i, i % 2) for i in range(3))
        statuses = (OUTSIDE_FOV, BEHIND_REFERENCE, INVALID_GEOMETRY)
        rows, receipt = EpochRouter().route_epoch(
            decision(1, Route.MC_CORRECT_SPARSE), events, (0, 1, 2), 100, 104,
            lambda event: warp(statuses[event.event_id], event),
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].disposition, Disposition.RAW_ESCAPE_GEOMETRIC_OOF)
        self.assertEqual(rows[1].disposition, Disposition.RAW_ESCAPE_GEOMETRIC_OOF)
        self.assertEqual(rows[2].disposition, Disposition.RAW_BYPASS_INVALID_GEOMETRY)
        self.assertEqual(receipt.ordered_event_ids, (0, 1, 2))

    def test_half_open_boundary_duplicate_and_reorder_fail_closed(self) -> None:
        provider = lambda event: warp(IN_FOV, event)
        with self.assertRaisesRegex(EpochRouteError, "outside epoch"):
            EpochRouter().route_epoch(
                decision(0, Route.MC_CORRECT_SPARSE), (SourceEvent(1, 20, 0, 0, 0),), (1,),
                10, 20, provider,
            )
        with self.assertRaisesRegex(EpochRouteError, "frozen ordered ledger"):
            EpochRouter().route_epoch(
                decision(0, Route.MC_CORRECT_SPARSE),
                (SourceEvent(2, 10, 0, 0, 0), SourceEvent(1, 11, 0, 0, 0)),
                (1, 2), 10, 20, provider,
            )
        router = EpochRouter()
        router.route_epoch(
            decision(0, Route.SENSOR_FIXED_BYPASS), (SourceEvent(1, 10, 0, 0, 0),), (1,),
            10, 20, provider,
        )
        with self.assertRaisesRegex(EpochRouteError, "cross-epoch"):
            router.route_epoch(
                decision(1, Route.SENSOR_FIXED_BYPASS), (SourceEvent(1, 20, 0, 0, 0),), (1,),
                20, 30, provider,
            )

    def test_warp_cannot_change_identity(self) -> None:
        source = SourceEvent(1, 10, 0, 0, 0)
        with self.assertRaisesRegex(EpochRouteError, "identity"):
            EpochRouter().route_epoch(
                decision(0, Route.MC_CORRECT_SPARSE), (source,), (1,), 10, 20,
                lambda event: warp(IN_FOV, SourceEvent(2, 10, 0, 0, 0)),
            )

    def test_missing_expected_event_and_overlapping_epoch_fail_closed(self) -> None:
        provider = lambda event: warp(IN_FOV, event)
        with self.assertRaisesRegex(EpochRouteError, "frozen ordered ledger"):
            EpochRouter().route_epoch(
                decision(0, Route.MC_CORRECT_SPARSE), (SourceEvent(1, 10, 0, 0, 0),),
                (1, 2), 10, 20, provider,
            )
        router = EpochRouter()
        router.route_epoch(
            decision(0, Route.SENSOR_FIXED_BYPASS), (SourceEvent(9, 10, 0, 0, 0),),
            (9,), 10, 20, provider,
        )
        with self.assertRaisesRegex(EpochRouteError, "overlap"):
            router.route_epoch(
                decision(1, Route.SENSOR_FIXED_BYPASS), (SourceEvent(10, 19, 0, 0, 0),),
                (10,), 19, 21, provider,
            )


if __name__ == "__main__":
    unittest.main()
