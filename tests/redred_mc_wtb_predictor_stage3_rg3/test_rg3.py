from __future__ import annotations

import inspect
import math
import unittest

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample,
    RecoveryMode,
    normalize_quaternion_xyzw,
    recover_causal_cav,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import (
    RG3_POLICY,
    recover_rg3_cav,
)


IDENTITY = (0.0, 0.0, 0.0, 1.0)


def _norm(vector):
    return math.sqrt(math.fsum(value * value for value in vector))


def _qmul(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return normalize_quaternion_xyzw((
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    ))


def _qexp(vector):
    angle = _norm(vector)
    if angle == 0.0:
        return IDENTITY
    half = 0.5 * angle
    scale = math.sin(half) / angle
    return normalize_quaternion_xyzw((
        vector[0] * scale,
        vector[1] * scale,
        vector[2] * scale,
        math.cos(half),
    ))


def _rotation_matrix(quaternion):
    x, y, z, w = normalize_quaternion_xyzw(quaternion)
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def _transpose_rotate(matrix, vector):
    return tuple(
        math.fsum(matrix[row][column] * vector[row] for row in range(3))
        for column in range(3)
    )


def _q_equivalent(test, observed, expected, places=11):
    test.assertIsNotNone(observed)
    dot = math.fsum(left * right for left, right in zip(observed, expected))
    test.assertAlmostEqual(abs(dot), 1.0, places=places)


def _z_rotation(angle_rad):
    return _qexp((0.0, 0.0, angle_rad))


class RG3GeometryTests(unittest.TestCase):
    def test_unequal_cadence_constant_acceleration_formula(self):
        poses = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(4_000_000, 2, _z_rotation(0.04)),
            PoseSample(10_000_000, 3, _z_rotation(0.13)),
        )

        decision = recover_rg3_cav(poses, 12_000_000, 10)

        self.assertTrue(decision.candidate_used)
        self.assertEqual(decision.reason, "rg3_constant_acceleration")
        self.assertEqual(decision.used_measurement_timestamps_ns, (0, 4_000_000, 10_000_000))
        self.assertEqual(decision.used_commit_cycles, (1, 2, 3))
        # w01=10 rad/s, w12=15 rad/s, mean midpoint separation=5 ms,
        # a=1000 rad/s^2, and h=2 ms.  The frozen RG3 increment is .032 rad.
        _q_equivalent(self, decision.quaternion_xyzw, _z_rotation(0.162))
        self.assertAlmostEqual(decision.angular_velocity_xyz_rad_s[2], 15.0, places=11)
        self.assertAlmostEqual(
            decision.angular_acceleration_xyz_rad_s2[2], 1000.0, places=8
        )
        self.assertAlmostEqual(decision.prediction_rotation_vector_rad[2], 0.032, places=12)
        self.assertIs(decision.baseline_decision.mode, RecoveryMode.CAV)
        self.assertNotEqual(decision.quaternion_xyzw, decision.baseline_decision.quaternion_xyzw)

    def test_coupled_axis_prediction_uses_final_r12_transpose_transport(self):
        step01 = (0.20, 0.10, 0.00)
        step12 = (0.25, 0.12, 0.04)
        q01 = _qexp(step01)
        q12 = _qexp(step12)
        q2 = _qmul(q01, q12)
        poses = (
            PoseSample(0, 1, IDENTITY),
            PoseSample(4_000_000, 2, q01),
            PoseSample(9_000_000, 3, q2),
        )

        decision = recover_rg3_cav(poses, 11_000_000, 10)

        self.assertTrue(decision.candidate_used)
        w01_frame1 = tuple(value / 0.004 for value in step01)
        w12_frame1 = tuple(value / 0.005 for value in step12)
        acceleration_frame1 = tuple(
            (w12_frame1[index] - w01_frame1[index]) / 0.0045
            for index in range(3)
        )
        r12 = _rotation_matrix(q12)
        velocity_frame2 = _transpose_rotate(r12, w12_frame1)
        acceleration_frame2 = _transpose_rotate(r12, acceleration_frame1)
        expected_increment = tuple(
            velocity_frame2[index] * 0.002
            + 0.5 * acceleration_frame2[index] * 0.002 * 0.002
            for index in range(3)
        )
        expected = _qmul(q2, _qexp(expected_increment))
        _q_equivalent(self, decision.quaternion_xyzw, expected)

        untransported_increment = tuple(
            w12_frame1[index] * 0.002
            + 0.5 * acceleration_frame1[index] * 0.002 * 0.002
            for index in range(3)
        )
        wrong = _qmul(q2, _qexp(untransported_increment))
        wrong_dot = abs(math.fsum(
            left * right for left, right in zip(decision.quaternion_xyzw, wrong)
        ))
        self.assertLess(wrong_dot, 1.0 - 1.0e-8)

    def test_antipodal_pose_representations_do_not_change_prediction(self):
        poses = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(4_000_000, 2, _z_rotation(0.04)),
            PoseSample(10_000_000, 3, _z_rotation(0.13)),
        )
        antipodal = tuple(
            PoseSample(
                pose.measurement_timestamp_ns,
                pose.commit_cycle,
                tuple(-value for value in pose.quaternion_xyzw),
            )
            for pose in poses
        )

        original = recover_rg3_cav(poses, 12_000_000, 10)
        changed = recover_rg3_cav(antipodal, 12_000_000, 10)

        self.assertTrue(original.candidate_used)
        self.assertTrue(changed.candidate_used)
        _q_equivalent(self, changed.quaternion_xyzw, original.quaternion_xyzw)
        for observed, expected in zip(
            changed.prediction_rotation_vector_rad,
            original.prediction_rotation_vector_rad,
        ):
            self.assertAlmostEqual(observed, expected, places=12)


class RG3CausalityAndFallbackTests(unittest.TestCase):
    def assert_exact_baseline_fallback(self, poses, timestamp, cycle, reason):
        expected = recover_causal_cav(poses, timestamp, cycle)
        observed = recover_rg3_cav(poses, timestamp, cycle)
        self.assertFalse(observed.candidate_used)
        self.assertEqual(observed.reason, reason)
        self.assertEqual(observed.baseline_decision, expected)
        self.assertEqual(observed.quaternion_xyzw, expected.quaternion_xyzw)
        self.assertEqual(
            observed.used_measurement_timestamps_ns,
            expected.used_measurement_timestamps_ns,
        )
        self.assertEqual(observed.used_commit_cycles, expected.used_commit_cycles)
        self.assertEqual(observed.age_ns, expected.age_ns)
        return observed

    def test_same_edge_and_future_measurement_poses_are_invisible(self):
        causal = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(4_000_000, 2, _z_rotation(0.04)),
            PoseSample(10_000_000, 3, _z_rotation(0.13)),
        )
        supplied = causal + (
            PoseSample(11_000_000, 10, _z_rotation(2.0)),
            PoseSample(13_000_000, 4, _z_rotation(-2.0)),
        )

        expected = recover_rg3_cav(causal, 12_000_000, 10)
        observed = recover_rg3_cav(supplied, 12_000_000, 10)

        self.assertTrue(observed.candidate_used)
        self.assertEqual(observed.used_commit_cycles, (1, 2, 3))
        self.assertNotIn(10, observed.used_commit_cycles)
        self.assertNotIn(13_000_000, observed.used_measurement_timestamps_ns)
        _q_equivalent(self, observed.quaternion_xyzw, expected.quaternion_xyzw)

    def test_equal_timestamp_event_cluster_reads_one_immutable_pose_snapshot(self):
        poses = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(4_000_000, 2, _z_rotation(0.04)),
            PoseSample(10_000_000, 3, _z_rotation(0.13)),
            PoseSample(12_000_000, 11, _z_rotation(1.5)),
        )

        first = recover_rg3_cav(poses, 12_000_000, 11)
        second = recover_rg3_cav(poses, 12_000_000, 11)

        self.assertEqual(first, second)
        self.assertTrue(first.candidate_used)
        self.assertEqual(first.used_commit_cycles, (1, 2, 3))
        self.assertNotIn(11, first.used_commit_cycles)

    def test_two_pose_history_falls_back_to_exact_cav(self):
        poses = (
            PoseSample(0, 1, IDENTITY),
            PoseSample(5_000_000, 2, _z_rotation(0.05)),
        )
        observed = self.assert_exact_baseline_fallback(
            poses, 6_000_000, 10, "insufficient_visible_pose_history"
        )
        self.assertIs(observed.baseline_decision.mode, RecoveryMode.CAV)

    def test_cadence_direction_rate_and_acceleration_gates_fall_back_exactly(self):
        cases = (
            (
                "cadence",
                (
                    PoseSample(0, 1, IDENTITY),
                    PoseSample(20_000_000, 2, _z_rotation(0.10)),
                    PoseSample(25_000_000, 3, _z_rotation(0.15)),
                ),
                26_000_000,
                "pose_cadence_out_of_bounds",
            ),
            (
                "direction",
                (
                    PoseSample(0, 1, _z_rotation(0.0)),
                    PoseSample(5_000_000, 2, _z_rotation(0.10)),
                    PoseSample(10_000_000, 3, _z_rotation(0.0)),
                ),
                11_000_000,
                "direction_gate",
            ),
            (
                "rate change",
                (
                    PoseSample(0, 1, _z_rotation(0.0)),
                    PoseSample(5_000_000, 2, _z_rotation(0.05)),
                    PoseSample(10_000_000, 3, _z_rotation(0.20)),
                ),
                11_000_000,
                "rate_change_gate",
            ),
            (
                "acceleration horizon",
                (
                    PoseSample(0, 1, _z_rotation(0.0)),
                    PoseSample(1_000_000, 2, _z_rotation(0.01)),
                    PoseSample(6_000_000, 3, _z_rotation(0.09)),
                ),
                11_000_000,
                "acceleration_horizon_gate",
            ),
        )
        for label, poses, timestamp, reason in cases:
            with self.subTest(label=label):
                observed = self.assert_exact_baseline_fallback(
                    poses, timestamp, 20, reason
                )
                self.assertIs(observed.baseline_decision.mode, RecoveryMode.CAV)

    def test_near_pi_step_falls_back_to_exact_cav(self):
        poses = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(1_000_000, 2, _z_rotation(0.01)),
            PoseSample(2_000_000, 3, _z_rotation(0.01 + math.pi)),
        )
        self.assert_exact_baseline_fallback(
            poses, 2_000_000, 10, "near_pi_pose_step"
        )

    def test_stationary_step_falls_back_to_exact_cav(self):
        poses = (
            PoseSample(0, 1, _z_rotation(0.0)),
            PoseSample(5_000_000, 2, _z_rotation(0.05)),
            PoseSample(10_000_000, 3, _z_rotation(0.05)),
        )
        observed = self.assert_exact_baseline_fallback(
            poses, 10_500_000, 10, "stationary_pose_step"
        )
        self.assertIs(observed.baseline_decision.mode, RecoveryMode.CAV)

    def test_baseline_zoh_and_bypass_are_preserved_for_every_event(self):
        pose = (PoseSample(1_000_000, 1, _z_rotation(0.1)),)
        fresh = self.assert_exact_baseline_fallback(
            pose, 1_500_000, 10, "baseline_zoh_fallback"
        )
        stale = self.assert_exact_baseline_fallback(
            pose, 2_000_001, 10, "baseline_sensor_fixed_bypass"
        )
        empty = self.assert_exact_baseline_fallback(
            (), 2_000_001, 10, "baseline_sensor_fixed_bypass"
        )
        self.assertIs(fresh.baseline_decision.mode, RecoveryMode.ZOH)
        self.assertIs(stale.baseline_decision.mode, RecoveryMode.BYPASS)
        self.assertIs(empty.baseline_decision.mode, RecoveryMode.BYPASS)
        self.assertIsNone(stale.quaternion_xyzw)

    def test_public_input_surface_has_no_selector_or_result_controls(self):
        parameters = inspect.signature(recover_rg3_cav).parameters
        self.assertEqual(
            tuple(parameters),
            ("samples", "event_timestamp_ns", "event_cycle"),
        )
        self.assertIn("body_transport3", RG3_POLICY.candidate_id)


if __name__ == "__main__":
    unittest.main()
