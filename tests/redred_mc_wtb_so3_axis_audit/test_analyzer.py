from __future__ import annotations

import math
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_so3_axis_audit import analyzer as analyzer_module
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
    def test_axial_back_and_forth_reports_sampled_step_reversal(self):
        poses = tuple(
            PoseSample(index * 1_000_000_000, axis_rotation((0, 0, 1), degrees))
            for index, degrees in enumerate((0.0, 10.0, 30.0, 20.0, 0.0))
        )
        result = analyze_axis_motion(
            poses,
            frame=RotationFrame.WORLD,
            maximum_physical_angular_speed_rad_s=math.radians(60.0),
        )

        self.assertEqual(result.sample_count, 5)
        self.assertEqual(result.interval_count, 4)
        self.assertEqual(result.moving_interval_count, 4)
        self.assertAlmostEqual(result.total_path_angle_rad, math.radians(60.0))
        self.assertAlmostEqual(result.net_angle_rad, 0.0)
        assert_vector(self, result.dominant_axis_xyz, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(result.axis_coherence, 1.0)
        self.assertEqual(result.dominant_axis_status, "UNIQUE")
        self.assertIsNone(result.dominant_axis_unavailable_reason)
        self.assertTrue(result.directional_metrics_available)
        self.assertIsNone(result.directional_metrics_unavailable_reason)
        self.assertTrue(all(step.directional_valid for step in result.steps))
        self.assertAlmostEqual(result.positive_dominant_rotation_rad, math.radians(30.0))
        self.assertAlmostEqual(result.negative_dominant_rotation_rad, math.radians(30.0))
        self.assertAlmostEqual(result.signed_dominant_rotation_rad, 0.0)
        self.assertEqual(result.sampled_step_direction_reversal_count, 1)

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
        self.assertEqual(result.dominant_axis_status, "NON_UNIQUE_EIGENGAP")
        self.assertEqual(
            result.dominant_axis_unavailable_reason,
            "NON_UNIQUE_EIGENGAP",
        )
        self.assertFalse(result.directional_metrics_available)
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "DOMINANT_AXIS_NON_UNIQUE_EIGENGAP",
        )
        self.assertIsNone(result.sampled_step_direction_reversal_count)

    def test_small_tensor_off_diagonal_is_not_discarded_by_unit_scale_floor(self):
        base = 5.0e-4
        diagonal_gap = 2.0e-15
        off_diagonal = 9.0e-16
        expected_angle = 0.5 * math.atan2(2.0 * off_diagonal, diagonal_gap)
        eigen_gap_half = 0.5 * math.sqrt(
            diagonal_gap * diagonal_gap + 4.0 * off_diagonal * off_diagonal
        )
        mean = base + diagonal_gap / 2.0
        first_weight = mean + eigen_gap_half
        second_weight = mean - eigen_gap_half
        first_axis = (math.cos(expected_angle), math.sin(expected_angle), 0.0)
        second_axis = (-math.sin(expected_angle), math.cos(expected_angle), 0.0)
        first_step = axis_rotation(first_axis, math.degrees(first_weight))
        second_step = axis_rotation(second_axis, math.degrees(second_weight))
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1, first_step),
            PoseSample(2, multiply(second_step, first_step)),
        )

        result = analyze_axis_motion(
            poses,
            frame=RotationFrame.WORLD,
            stationary_threshold_rad=0.0,
        )

        self.assertIsNotNone(result.dominant_axis_xyz)
        assert_vector(self, result.dominant_axis_xyz, first_axis, places=4)
        self.assertEqual(result.dominant_axis_status, "UNIQUE")
        self.assertIsNone(result.dominant_axis_unavailable_reason)

    def test_uncertified_eigenpair_residual_makes_axis_unavailable(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1, axis_rotation((1, 0, 0), 10.0)),
        )
        uncertified = (
            (1.0, (1.0, 0.0, 0.0), 1.0),
            (0.0, (0.0, 1.0, 0.0), 1.0),
            (0.0, (0.0, 0.0, 1.0), 1.0),
        )
        with mock.patch.object(
            analyzer_module,
            "_symmetric_eigensystem",
            return_value=(uncertified, "CONVERGED"),
        ):
            result = analyze_axis_motion(poses)
        self.assertIsNone(result.dominant_axis_xyz)
        self.assertEqual(result.dominant_axis_status, "EIGEN_RESIDUAL_FAILED")
        self.assertEqual(
            result.dominant_axis_unavailable_reason,
            "EIGEN_RESIDUAL_FAILED",
        )
        self.assertFalse(result.directional_metrics_available)
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "DOMINANT_AXIS_EIGEN_RESIDUAL_FAILED",
        )

    def test_nonconverged_eigensolver_does_not_masquerade_as_mixed_motion(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1, axis_rotation((1, 0, 0), 10.0)),
        )
        with mock.patch.object(
            analyzer_module,
            "_EIGEN_MAX_ITERATIONS",
            0,
        ):
            result = analyze_axis_motion(poses)
        self.assertIsNone(result.dominant_axis_xyz)
        self.assertIsNone(result.axis_coherence)
        self.assertEqual(
            result.dominant_axis_status,
            "EIGENSOLVER_NONCONVERGED",
        )
        self.assertEqual(
            result.dominant_axis_unavailable_reason,
            "EIGENSOLVER_NONCONVERGED",
        )
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "DOMINANT_AXIS_EIGENSOLVER_NONCONVERGED",
        )

    def test_half_turn_direction_and_reversal_are_unavailable(self):
        poses = tuple(
            PoseSample(index, axis_rotation((0, 0, 1), degrees))
            for index, degrees in enumerate((0.0, 180.0, 360.0))
        )
        result = analyze_axis_motion(
            poses,
            frame=RotationFrame.WORLD,
            stationary_threshold_rad=0.0,
            maximum_physical_angular_speed_rad_s=math.pi,
        )

        assert_vector(self, result.dominant_axis_xyz, (0.0, 0.0, 1.0))
        self.assertFalse(result.directional_metrics_available)
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "STEP_AT_OR_NEAR_PI",
        )
        self.assertTrue(all(not step.directional_valid for step in result.steps))
        self.assertIsNone(result.signed_dominant_rotation_rad)
        self.assertIsNone(result.positive_dominant_rotation_rad)
        self.assertIsNone(result.negative_dominant_rotation_rad)
        self.assertIsNone(result.sampled_step_direction_reversal_count)

    def test_direction_requires_bound_and_cadence_proof(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(10_000_000_000, axis_rotation((0, 0, 1), 10.0)),
        )
        missing = analyze_axis_motion(poses, frame=RotationFrame.WORLD)
        self.assertEqual(
            missing.directional_metrics_unavailable_reason,
            "PHYSICAL_SPEED_BOUND_NOT_PROVIDED",
        )
        sparse = analyze_axis_motion(
            poses,
            frame=RotationFrame.WORLD,
            maximum_physical_angular_speed_rad_s=math.radians(30.0),
        )
        self.assertEqual(
            sparse.directional_metrics_unavailable_reason,
            "CADENCE_DOES_NOT_PROVE_SUB_PI_STEP",
        )
        self.assertFalse(sparse.steps[0].directional_valid)

    def test_stationary_looking_gap_still_requires_cadence_proof(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(10_000_000_000, IDENTITY),
            PoseSample(11_000_000_000, axis_rotation((0, 0, 1), 10.0)),
        )
        result = analyze_axis_motion(
            poses,
            frame=RotationFrame.WORLD,
            maximum_physical_angular_speed_rad_s=math.radians(30.0),
        )
        assert_vector(self, result.dominant_axis_xyz, (0.0, 0.0, 1.0))
        self.assertTrue(result.steps[0].stationary)
        self.assertFalse(result.steps[0].directional_valid)
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "CADENCE_DOES_NOT_PROVE_SUB_PI_STEP",
        )

    def test_observed_step_must_not_exceed_physical_speed_bound(self):
        poses = (
            PoseSample(0, IDENTITY),
            PoseSample(1_000_000_000, axis_rotation((1, 0, 0), 30.0)),
        )
        result = analyze_axis_motion(
            poses,
            maximum_physical_angular_speed_rad_s=math.radians(20.0),
        )
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "OBSERVED_STEP_EXCEEDS_PHYSICAL_SPEED_BOUND",
        )
        self.assertFalse(result.steps[0].directional_valid)

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
        self.assertEqual(result.dominant_axis_status, "NO_MOVING_INTERVALS")
        self.assertEqual(
            result.dominant_axis_unavailable_reason,
            "NO_MOVING_INTERVALS",
        )
        self.assertEqual(
            result.directional_metrics_unavailable_reason,
            "DOMINANT_AXIS_NO_MOVING_INTERVALS",
        )


class ContractTests(unittest.TestCase):
    def test_dominant_axis_status_vocabulary_is_exact_and_frozen(self):
        self.assertIsInstance(analyzer_module.DOMINANT_AXIS_STATUSES, frozenset)
        self.assertEqual(
            analyzer_module.DOMINANT_AXIS_STATUSES,
            frozenset((
                "NO_MOVING_INTERVALS",
                "NON_UNIQUE_EIGENGAP",
                "EIGENSOLVER_NONCONVERGED",
                "EIGEN_RESIDUAL_FAILED",
                "UNIQUE",
            )),
        )

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
        with self.assertRaisesRegex(SO3AxisAuditError, "must be positive"):
            analyze_axis_motion(
                (PoseSample(0, IDENTITY),),
                maximum_physical_angular_speed_rad_s=0.0,
            )
        with self.assertRaisesRegex(SO3AxisAuditError, r"\(0, pi\)"):
            analyze_axis_motion(
                (PoseSample(0, IDENTITY),), directional_pi_margin_rad=0.0
            )


if __name__ == "__main__":
    unittest.main()
