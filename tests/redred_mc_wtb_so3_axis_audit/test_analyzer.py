from __future__ import annotations

import math
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit import (
    PoseSample,
    RotationFrame,
    SO3AxisAuditError,
    analyze_axis_motion,
    relative_rotation_vector,
)


IDENTITY = (0.0, 0.0, 0.0, 1.0)


def axis_rotation(axis, degrees):
    norm = math.sqrt(sum(component * component for component in axis))
    unit = tuple(component / norm for component in axis)
    half_angle = math.radians(degrees) / 2.0
    sine = math.sin(half_angle)
    return tuple(component * sine for component in unit) + (math.cos(half_angle),)


def multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def assert_vector(test, actual, expected, places=12):
    for observed, wanted in zip(actual, expected):
        test.assertAlmostEqual(observed, wanted, places=places)


class RelativeRotationTests(unittest.TestCase):
    def test_principal_log_is_normalized_and_antipodal_invariant(self):
        rotation = axis_rotation((0.0, 0.0, 1.0), 270.0)
        scaled_antipodal = tuple(-7.0 * component for component in rotation)
        expected = (0.0, 0.0, -math.pi / 2.0)
        assert_vector(self, relative_rotation_vector(IDENTITY, rotation), expected)
        assert_vector(
            self,
            relative_rotation_vector(IDENTITY, scaled_antipodal),
            expected,
        )

    def test_body_and_world_frame_semantics_are_distinct(self):
        before = axis_rotation((0.0, 0.0, 1.0), 90.0)
        world_x_step = axis_rotation((1.0, 0.0, 0.0), 30.0)
        after = multiply(world_x_step, before)
        world = relative_rotation_vector(before, after, frame=RotationFrame.WORLD)
        body = relative_rotation_vector(before, after, frame=RotationFrame.BODY)
        assert_vector(self, world, (math.pi / 6.0, 0.0, 0.0))
        assert_vector(self, body, (0.0, -math.pi / 6.0, 0.0))

    def test_exact_half_turn_has_deterministic_projective_axis(self):
        half_turn = axis_rotation((-1.0, 0.0, 0.0), 180.0)
        antipodal = tuple(-component for component in half_turn)
        expected = (math.pi, 0.0, 0.0)
        assert_vector(self, relative_rotation_vector(IDENTITY, half_turn), expected)
        assert_vector(self, relative_rotation_vector(IDENTITY, antipodal), expected)


class AxisMotionAnalysisTests(unittest.TestCase):
    def test_axial_back_and_forth_motion_reports_path_and_reversal(self):
        poses = tuple(
            PoseSample(index * 1_000_000_000, axis_rotation((0, 0, 1), degrees))
            for index, degrees in enumerate((0.0, 10.0, 30.0, 20.0, 0.0))
        )
        result = analyze_axis_motion(poses, frame=RotationFrame.WORLD)

        self.assertEqual(result.sample_count, 5)
        self.assertEqual(result.interval_count, 4)
        self.assertEqual(result.moving_interval_count, 4)
        self.assertAlmostEqual(result.total_path_angle_rad, math.radians(60.0))
        self.assertAlmostEqual(result.net_angle_rad, 0.0)
        assert_vector(self, result.dominant_axis_xyz, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(result.axis_coherence, 1.0)
        self.assertAlmostEqual(result.positive_dominant_rotation_rad, math.radians(30.0))
        self.assertAlmostEqual(result.negative_dominant_rotation_rad, math.radians(30.0))
        self.assertAlmostEqual(result.signed_dominant_rotation_rad, 0.0)
        self.assertEqual(result.direction_reversal_count, 1)

    def test_speed_statistics_are_time_weighted_and_stationary_is_explicit(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1_000_000_000, axis_rotation((1, 0, 0), 10.0)),
            PoseSample(3_000_000_000, axis_rotation((1, 0, 0), 30.0)),
            PoseSample(4_000_000_000, axis_rotation((1, 0, 0), 30.0)),
        )
        result = analyze_axis_motion(poses)
        ten_degrees = math.radians(10.0)
        self.assertEqual(result.stationary_interval_count, 1)
        self.assertEqual(result.moving_interval_count, 2)
        self.assertAlmostEqual(result.mean_angular_speed_rad_s, 30.0 * math.pi / 180.0 / 4.0)
        self.assertAlmostEqual(result.rms_angular_speed_rad_s, math.sqrt(0.75) * ten_degrees)
        self.assertAlmostEqual(result.peak_angular_speed_rad_s, ten_degrees)
        self.assertTrue(result.steps[-1].stationary)
        self.assertIsNone(result.steps[-1].axis_xyz)

    def test_equal_orthogonal_motion_has_no_unique_dominant_axis(self):
        x_step = axis_rotation((1, 0, 0), 20.0)
        y_step = axis_rotation((0, 1, 0), 20.0)
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1, x_step),
            PoseSample(2, multiply(x_step, y_step)),
        )
        result = analyze_axis_motion(poses, frame=RotationFrame.BODY)
        self.assertIsNone(result.dominant_axis_xyz)
        self.assertAlmostEqual(result.axis_coherence, 0.5)
        self.assertEqual(result.direction_reversal_count, 0)

    def test_noncommuting_path_sum_is_not_mislabeled_as_net_rotation(self):
        x_step = axis_rotation((1, 0, 0), 90.0)
        y_step = axis_rotation((0, 1, 0), 90.0)
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1, x_step),
            PoseSample(2, multiply(x_step, y_step)),
        )
        result = analyze_axis_motion(poses)
        self.assertAlmostEqual(result.total_path_angle_rad, math.pi)
        self.assertAlmostEqual(result.net_angle_rad, 2.0 * math.pi / 3.0)
        self.assertNotAlmostEqual(result.net_angle_rad, result.total_path_angle_rad)

    def test_single_sample_is_a_valid_zero_motion_audit(self):
        result = analyze_axis_motion((PoseSample(17, IDENTITY),))
        self.assertEqual(result.interval_count, 0)
        self.assertEqual(result.elapsed_ns, 0)
        self.assertEqual(result.total_path_angle_rad, 0.0)
        self.assertIsNone(result.dominant_axis_xyz)
        self.assertIsNone(result.axis_coherence)


class ContractTests(unittest.TestCase):
    def test_invalid_pose_and_stream_inputs_fail_closed(self):
        with self.assertRaisesRegex(SO3AxisAuditError, "nonzero"):
            PoseSample(0, (0.0, 0.0, 0.0, 0.0))
        with self.assertRaisesRegex(SO3AxisAuditError, "strictly increasing"):
            analyze_axis_motion(
                (PoseSample(1, IDENTITY), PoseSample(1, IDENTITY))
            )
        with self.assertRaisesRegex(SO3AxisAuditError, "at least one"):
            analyze_axis_motion(())
        with self.assertRaisesRegex(SO3AxisAuditError, "frame"):
            analyze_axis_motion((PoseSample(0, IDENTITY),), frame="camera")
        with self.assertRaisesRegex(SO3AxisAuditError, r"\[0, pi\]"):
            analyze_axis_motion(
                (PoseSample(0, IDENTITY),), stationary_threshold_rad=-1.0
            )


if __name__ == "__main__":
    unittest.main()
