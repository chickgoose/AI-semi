from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.geometry_reference import (
    BEHIND_REFERENCE,
    CONTINUOUS_EXTENT,
    IN_FOV,
    NEAREST_PIXEL_SUPPORT,
    OUTSIDE_FOV,
    CommonReferenceGeometry,
    EventObservation,
    GeometryReferenceError,
    PoseSeries,
    RadtanCalibration,
    TimedPoseTWC,
    classify_fov,
    distort_normalized,
    matmul,
    quaternion_xyzw_to_rotation_t_wc,
    shortest_arc_slerp_xyzw,
    transpose,
    undistort_normalized,
    warp_events_to_common_reference,
)

SQRT_HALF = math.sqrt(0.5)
Q_IDENTITY = (0.0, 0.0, 0.0, 1.0)
Q_Z_90 = (0.0, 0.0, SQRT_HALF, SQRT_HALF)


def zero_calibration(width: int = 201, height: int = 201) -> RadtanCalibration:
    return RadtanCalibration(
        width, height, 10.0, 10.0, 100.0, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0
    )


def assert_matrix_close(
    case: unittest.TestCase,
    actual: tuple[tuple[float, ...], ...],
    expected: tuple[tuple[float, ...], ...],
) -> None:
    for row in range(3):
        for column in range(3):
            case.assertAlmostEqual(actual[row][column], expected[row][column], places=12)


class QuaternionAndPoseTests(unittest.TestCase):
    def test_xyzw_active_t_wc_and_wxyz_mutant(self) -> None:
        expected = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        assert_matrix_close(self, quaternion_xyzw_to_rotation_t_wc(Q_Z_90), expected)
        assert_matrix_close(
            self, quaternion_xyzw_to_rotation_t_wc(tuple(-v for v in Q_Z_90)), expected
        )

        # Deliberate wxyz-as-xyzw mutant must not satisfy the analytic fixture.
        mutant = quaternion_xyzw_to_rotation_t_wc(
            (Q_Z_90[3], Q_Z_90[0], Q_Z_90[1], Q_Z_90[2])
        )
        self.assertGreater(
            max(abs(mutant[r][c] - expected[r][c]) for r in range(3) for c in range(3)),
            0.5,
        )

    def test_shortest_arc_midpoint_antipode_and_zoh_mutant(self) -> None:
        expected = (0.0, 0.0, math.sin(math.pi / 8.0), math.cos(math.pi / 8.0))
        midpoint = shortest_arc_slerp_xyzw(Q_IDENTITY, Q_Z_90, 0.5)
        antipodal = shortest_arc_slerp_xyzw(
            Q_IDENTITY, tuple(-v for v in Q_Z_90), 0.5
        )
        for actual, wanted in zip(midpoint, expected):
            self.assertAlmostEqual(actual, wanted, places=12)
        for actual, wanted in zip(antipodal, expected):
            self.assertAlmostEqual(actual, wanted, places=12)
        self.assertGreater(abs(midpoint[2] - Q_IDENTITY[2]), 0.3)  # ZOH mutant.

    def test_exact_large_integer_ns_bracket_and_translation(self) -> None:
        base = 9_007_199_254_740_992  # 2**53; adjacent ns are not float-distinct.
        poses = PoseSeries(
            (
                TimedPoseTWC(base, Q_IDENTITY, (0.0, 0.0, 0.0)),
                TimedPoseTWC(base + 3, Q_Z_90, (3.0, 6.0, 9.0)),
            )
        )
        result = poses.at(base + 1)
        self.assertEqual(result.timestamp_ns, base + 1)
        self.assertEqual(
            (result.before_timestamp_ns, result.after_timestamp_ns), (base, base + 3)
        )
        self.assertEqual((result.numerator_ns, result.denominator_ns), (1, 3))
        self.assertEqual(result.translation_world, (1.0, 2.0, 3.0))

    def test_pose_series_fails_on_duplicate_range_and_bool(self) -> None:
        with self.assertRaisesRegex(GeometryReferenceError, "strictly increasing"):
            PoseSeries((TimedPoseTWC(1, Q_IDENTITY), TimedPoseTWC(1, Q_Z_90)))
        series = PoseSeries((TimedPoseTWC(1, Q_IDENTITY), TimedPoseTWC(3, Q_Z_90)))
        with self.assertRaisesRegex(GeometryReferenceError, "outside"):
            series.at(0)
        with self.assertRaisesRegex(GeometryReferenceError, "integer nanosecond"):
            series.at(True)


class RadtanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = RadtanCalibration(
            240,
            180,
            199.092366542,
            198.82882047,
            132.192071378,
            110.712660011,
            -0.368436311798,
            0.150947243557,
            -0.000296130534385,
            -0.000759431726241,
            0.0,
        )

    def test_analytic_radtan_and_inverse_roundtrip(self) -> None:
        x, y = 0.21, -0.17
        c = self.calibration
        r2 = x * x + y * y
        radial = 1.0 + c.k1 * r2 + c.k2 * r2 * r2 + c.k3 * r2 * r2 * r2
        expected = (
            x * radial + 2.0 * c.p1 * x * y + c.p2 * (r2 + 2.0 * x * x),
            y * radial + c.p1 * (r2 + 2.0 * y * y) + 2.0 * c.p2 * x * y,
        )
        distorted = distort_normalized(x, y, c)
        self.assertAlmostEqual(distorted[0], expected[0], places=15)
        self.assertAlmostEqual(distorted[1], expected[1], places=15)
        recovered = undistort_normalized(*distorted, c)
        self.assertAlmostEqual(recovered[0], x, places=13)
        self.assertAlmostEqual(recovered[1], y, places=13)

    def test_p1_p2_swap_mutant_is_detected(self) -> None:
        x, y = 0.31, -0.22
        correct = distort_normalized(x, y, self.calibration)
        c = self.calibration
        swapped = RadtanCalibration(
            c.width,
            c.height,
            c.fx,
            c.fy,
            c.cx,
            c.cy,
            c.k1,
            c.k2,
            c.p2,
            c.p1,
            c.k3,
        )
        mutant = distort_normalized(x, y, swapped)
        self.assertGreater(max(abs(a - b) for a, b in zip(correct, mutant)), 1.0e-5)


class FOVPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = zero_calibration(width=10, height=8)

    def test_explicit_continuous_and_nearest_pixel_boundaries(self) -> None:
        cases = (
            (-0.25, False, True, 0),
            (0.0, True, True, 0),
            (9.49, True, True, 9),
            (9.5, True, False, 10),
            (9.75, True, False, 10),
            (10.0, False, False, 10),
        )
        for x, continuous, supported, rounded in cases:
            with self.subTest(x=x):
                decision = classify_fov(x, 2.0, self.calibration, CONTINUOUS_EXTENT)
                self.assertEqual(decision.continuous_extent, continuous)
                self.assertEqual(decision.nearest_pixel_supported, supported)
                self.assertEqual(decision.rounded_x, rounded)
                self.assertEqual(decision.in_fov, continuous)
                pixel = classify_fov(x, 2.0, self.calibration, NEAREST_PIXEL_SUPPORT)
                self.assertEqual(pixel.in_fov, supported)

    def test_round_first_mutant_does_not_replace_continuous_extent(self) -> None:
        decision = classify_fov(-0.25, 2.0, self.calibration, CONTINUOUS_EXTENT)
        round_first_mutant = 0 <= decision.rounded_x < self.calibration.width
        self.assertFalse(decision.in_fov)
        self.assertTrue(round_first_mutant)

    def test_unknown_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(GeometryReferenceError, "unknown FOV"):
            classify_fov(1.0, 1.0, self.calibration, "legacy_closed_centres")


class CommonReferenceWarpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calibration = zero_calibration()
        self.poses = PoseSeries(
            (TimedPoseTWC(0, Q_IDENTITY), TimedPoseTWC(10, Q_Z_90))
        )

    def test_identity_preserves_coordinate_identity_time_and_polarity(self) -> None:
        result = CommonReferenceGeometry(
            self.poses, self.calibration, 0, CONTINUOUS_EXTENT
        ).warp(EventObservation(17, 0, 110.0, 100.0, 1))
        self.assertEqual(result.status, IN_FOV)
        self.assertAlmostEqual(result.reference_x, 110.0, places=12)
        self.assertAlmostEqual(result.reference_y, 100.0, places=12)
        self.assertEqual((result.event_id, result.timestamp_ns, result.polarity), (17, 0, 1))

    def test_anchor_and_query_for_same_world_ray_meet_in_c0(self) -> None:
        # Reference ray (1,0,1) is at (110,100).  A camera rotated +90 deg Z
        # observes that same world ray as camera ray (0,-1,1), pixel (100,90).
        anchor = EventObservation(1, 0, 110.0, 100.0, 0)
        query = EventObservation(2, 10, 100.0, 90.0, 0)
        results = warp_events_to_common_reference(
            (anchor, query), self.poses, self.calibration, 0, CONTINUOUS_EXTENT
        )
        self.assertEqual([result.event_id for result in results], [1, 2])
        for result in results:
            self.assertEqual(result.status, IN_FOV)
            self.assertAlmostEqual(result.reference_x, 110.0, places=11)
            self.assertAlmostEqual(result.reference_y, 100.0, places=11)

    def test_inverse_reference_mutant_is_detected(self) -> None:
        reference = quaternion_xyzw_to_rotation_t_wc(Q_IDENTITY)
        current = quaternion_xyzw_to_rotation_t_wc(Q_Z_90)
        correct = matmul(transpose(reference), current)
        mutant = matmul(transpose(current), reference)
        self.assertGreater(
            max(abs(correct[r][c] - mutant[r][c]) for r in range(3) for c in range(3)),
            1.0,
        )

    def test_warp_many_keeps_ties_order_duplicates_and_polarity(self) -> None:
        events = (
            EventObservation(9, 0, 100.0, 100.0, 1),
            EventObservation(9, 0, 100.0, 100.0, 1),
            EventObservation(8, 0, 100.0, 100.0, 0),
        )
        results = CommonReferenceGeometry(
            self.poses, self.calibration, 0, NEAREST_PIXEL_SUPPORT
        ).warp_many(events)
        self.assertEqual([result.event_id for result in results], [9, 9, 8])
        self.assertEqual([result.timestamp_ns for result in results], [0, 0, 0])
        self.assertEqual([result.polarity for result in results], [1, 1, 0])
        self.assertEqual(len(results), len(events))

    def test_behind_reference_is_not_outside_fov(self) -> None:
        geometry = CommonReferenceGeometry(
            PoseSeries(
                (TimedPoseTWC(0, Q_IDENTITY), TimedPoseTWC(1, (0.0, 1.0, 0.0, 0.0)))
            ),
            self.calibration,
            0,
            CONTINUOUS_EXTENT,
        )
        result = geometry.warp(EventObservation(1, 1, 100.0, 100.0, 0))
        self.assertEqual(result.status, BEHIND_REFERENCE)
        self.assertIsNone(result.reference_x)
        self.assertIsNone(result.fov)

    def test_fov_policy_changes_status_not_projected_coordinate(self) -> None:
        event = EventObservation(5, 0, 200.75, 100.0, 1)
        continuous = CommonReferenceGeometry(
            self.poses, self.calibration, 0, CONTINUOUS_EXTENT
        ).warp(event)
        pixel = CommonReferenceGeometry(
            self.poses, self.calibration, 0, NEAREST_PIXEL_SUPPORT
        ).warp(event)
        self.assertEqual(continuous.status, IN_FOV)
        self.assertEqual(pixel.status, OUTSIDE_FOV)
        self.assertAlmostEqual(continuous.reference_x, pixel.reference_x, places=12)
        self.assertEqual(continuous.fov.rounded_x, 201)


if __name__ == "__main__":
    unittest.main()
