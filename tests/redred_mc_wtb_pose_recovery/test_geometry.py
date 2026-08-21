import math
import unittest

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryMode,
    extrapolate_constant_angular_velocity,
    interpolate_committed_bracket,
    normalize_quaternion_xyzw,
    recover_causal_cav,
    resample_oracle_groundtruth_1khz,
    resample_counterfactual_1khz,
    shortest_arc_slerp_xyzw,
)


Q_IDENTITY = (0.0, 0.0, 0.0, 1.0)


def z_rotation(degrees):
    half = math.radians(degrees) / 2.0
    return (0.0, 0.0, math.sin(half), math.cos(half))


def assert_quaternion_equivalent(test, observed, expected, places=12):
    dot = sum(left * right for left, right in zip(observed, expected))
    test.assertAlmostEqual(abs(dot), 1.0, places=places)
    test.assertAlmostEqual(sum(value * value for value in observed), 1.0, places=places)


class QuaternionGeometryTests(unittest.TestCase):
    def test_normalization_and_invalid_inputs(self):
        self.assertEqual(normalize_quaternion_xyzw((0, 0, 0, 7)), Q_IDENTITY)
        with self.assertRaisesRegex(GeometryError, "nonzero"):
            normalize_quaternion_xyzw((0.0, 0.0, 0.0, 0.0))
        with self.assertRaisesRegex(GeometryError, "finite"):
            normalize_quaternion_xyzw((0.0, 0.0, math.inf, 1.0))

    def test_shortest_arc_slerp_is_antipodal_invariant(self):
        endpoint = z_rotation(120.0)
        midpoint = shortest_arc_slerp_xyzw(Q_IDENTITY, endpoint, 0.5)
        antipodal = shortest_arc_slerp_xyzw(
            Q_IDENTITY, tuple(-value for value in endpoint), 0.5
        )
        assert_quaternion_equivalent(self, midpoint, z_rotation(60.0))
        assert_quaternion_equivalent(self, midpoint, antipodal)

    def test_exact_180_degree_tie_is_antipodal_invariant(self):
        endpoints = (
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0),
        )
        for endpoint in endpoints:
            expected = shortest_arc_slerp_xyzw(Q_IDENTITY, endpoint, 0.5)
            for left_sign, right_sign in ((1, -1), (-1, 1), (-1, -1)):
                observed = shortest_arc_slerp_xyzw(
                    tuple(left_sign * value for value in Q_IDENTITY),
                    tuple(right_sign * value for value in endpoint),
                    0.5,
                )
                with self.subTest(endpoint=endpoint, signs=(left_sign, right_sign)):
                    assert_quaternion_equivalent(self, observed, expected)

    def test_committed_bracket_interpolation_and_same_cycle_rejection(self):
        left = PoseSample(1_000_000, 10, Q_IDENTITY)
        right = PoseSample(3_000_000, 20, z_rotation(90.0))
        result = interpolate_committed_bracket(left, right, 2_000_000, 21)
        self.assertEqual(result.left_commit_cycle, 10)
        self.assertEqual(result.right_commit_cycle, 20)
        self.assertEqual(result.alpha, 0.5)
        assert_quaternion_equivalent(self, result.quaternion_xyzw, z_rotation(45.0))
        with self.assertRaisesRegex(GeometryError, "commit before"):
            interpolate_committed_bracket(left, right, 2_000_000, 20)
        with self.assertRaisesRegex(GeometryError, "left <= event < right"):
            interpolate_committed_bracket(left, right, 3_000_000, 21)

    def test_exact_constant_angular_velocity_extrapolation(self):
        predicted = extrapolate_constant_angular_velocity(
            Q_IDENTITY, z_rotation(45.0), 1_000_000, 1_000_000
        )
        assert_quaternion_equivalent(self, predicted, z_rotation(90.0))
        antipodal_latest = tuple(-value for value in z_rotation(45.0))
        antipodal = extrapolate_constant_angular_velocity(
            Q_IDENTITY, antipodal_latest, 1_000_000, 1_000_000
        )
        assert_quaternion_equivalent(self, predicted, antipodal)


class CausalRecoveryTests(unittest.TestCase):
    def test_guard_is_inclusive_and_limited_by_previous_interval(self):
        poses = (
            PoseSample(0, 1, Q_IDENTITY),
            PoseSample(2_000_000, 2, z_rotation(20.0)),
        )
        at_limit = recover_causal_cav(poses, 4_000_000, 10)
        self.assertIs(at_limit.mode, RecoveryMode.CAV)
        self.assertEqual(at_limit.age_ns, 2_000_000)
        self.assertEqual(at_limit.horizon_limit_ns, 2_000_000)
        assert_quaternion_equivalent(self, at_limit.quaternion_xyzw, z_rotation(40.0))

        over_limit = recover_causal_cav(poses, 4_000_001, 10)
        self.assertIs(over_limit.mode, RecoveryMode.BYPASS)

    def test_five_ms_cap_and_fresh_zoh_fallback(self):
        long_interval = (
            PoseSample(0, 1, Q_IDENTITY),
            PoseSample(10_000_000, 2, z_rotation(10.0)),
        )
        capped = recover_causal_cav(long_interval, 15_000_000, 10)
        self.assertIs(capped.mode, RecoveryMode.CAV)
        self.assertEqual(capped.horizon_limit_ns, 5_000_000)
        beyond_cap = recover_causal_cav(long_interval, 15_000_001, 10)
        self.assertIs(beyond_cap.mode, RecoveryMode.BYPASS)

        short_interval = (
            PoseSample(0, 1, Q_IDENTITY),
            PoseSample(500_000, 2, z_rotation(10.0)),
        )
        fallback = recover_causal_cav(short_interval, 1_250_000, 10)
        self.assertIs(fallback.mode, RecoveryMode.ZOH)
        self.assertEqual(fallback.age_ns, 750_000)
        assert_quaternion_equivalent(self, fallback.quaternion_xyzw, z_rotation(10.0))
        stale = recover_causal_cav(short_interval, 1_500_001, 10)
        self.assertIs(stale.mode, RecoveryMode.BYPASS)
        self.assertIsNone(stale.quaternion_xyzw)

    def test_cav_never_uses_same_cycle_or_future_measurement(self):
        poses = (
            PoseSample(0, 1, Q_IDENTITY),
            PoseSample(1_000_000, 5, z_rotation(10.0)),
            PoseSample(1_400_000, 6, z_rotation(14.0)),
            PoseSample(3_000_000, 2, z_rotation(30.0)),
        )
        decision = recover_causal_cav(poses, 1_500_000, 6)
        self.assertIs(decision.mode, RecoveryMode.CAV)
        self.assertEqual(decision.used_measurement_timestamps_ns, (0, 1_000_000))
        self.assertEqual(decision.used_commit_cycles, (1, 5))
        self.assertNotIn(1_400_000, decision.used_measurement_timestamps_ns)
        self.assertNotIn(3_000_000, decision.used_measurement_timestamps_ns)

    def test_one_committed_pose_selects_zoh_then_bypass(self):
        pose = (PoseSample(1_000_000, 4, z_rotation(15.0)),)
        fresh = recover_causal_cav(pose, 1_500_000, 5)
        self.assertIs(fresh.mode, RecoveryMode.ZOH)
        self.assertEqual(fresh.used_commit_cycles, (4,))
        self.assertEqual(pose[0].availability_cycle, 5)
        self.assertEqual(pose[0].visible_cycle, 5)
        stale = recover_causal_cav(pose, 2_000_001, 5)
        self.assertIs(stale.mode, RecoveryMode.BYPASS)


class CounterfactualResamplingTests(unittest.TestCase):
    def test_deterministic_exact_1khz_grid_and_commit_cycles(self):
        truth = (
            PoseSample(0, 999, Q_IDENTITY),
            PoseSample(2_000_000, 1_999, z_rotation(90.0)),
        )
        first = resample_counterfactual_1khz(truth, 0, 3_000_000, 0)
        second = resample_counterfactual_1khz(truth, 0, 3_000_000, 0)
        self.assertEqual(first, second)
        self.assertEqual(
            tuple(sample.measurement_timestamp_ns for sample in first),
            (0, 1_000_000, 2_000_000),
        )
        self.assertEqual(
            tuple(sample.commit_cycle for sample in first),
            (1, 153_848, 307_694),
        )
        assert_quaternion_equivalent(self, first[0].quaternion_xyzw, z_rotation(0.0))
        assert_quaternion_equivalent(self, first[1].quaternion_xyzw, z_rotation(45.0))
        assert_quaternion_equivalent(self, first[2].quaternion_xyzw, z_rotation(90.0))

    def test_resampling_requires_ordered_closed_truth(self):
        reversed_truth = (
            PoseSample(2_000_000, 2, z_rotation(90.0)),
            PoseSample(0, 1, Q_IDENTITY),
        )
        with self.assertRaisesRegex(GeometryError, "strictly increasing"):
            resample_counterfactual_1khz(reversed_truth, 0, 2_000_000, 0)
        incomplete = (PoseSample(1_000_000, 1, z_rotation(45.0)),)
        with self.assertRaisesRegex(GeometryError, "closed truth bracket"):
            resample_counterfactual_1khz(incomplete, 0, 1_000_000, 0)

    def test_oracle_grid_is_global_phase_and_frozen_timing(self):
        truth = (
            PoseSample(0, 1, Q_IDENTITY),
            PoseSample(3_000_000, 2, z_rotation(90.0)),
        )
        stream = resample_oracle_groundtruth_1khz(
            truth, 250_000, 2_500_000, 0
        )
        self.assertEqual(
            tuple(sample.measurement_timestamp_ns for sample in stream),
            (1_000_000, 2_000_000),
        )
        with self.assertRaisesRegex(GeometryError, "requires"):
            resample_oracle_groundtruth_1khz(
                truth, 0, 2_000_000, 0, commit_delay_cycles=2
            )

    def test_unsigned_widths_fail_closed(self):
        with self.assertRaisesRegex(GeometryError, "must be an integer in"):
            PoseSample((1 << 64), 0, Q_IDENTITY)
        with self.assertRaisesRegex(GeometryError, "visible cycle"):
            PoseSample(0, (1 << 64) - 1, Q_IDENTITY).visible_cycle


if __name__ == "__main__":
    unittest.main()
