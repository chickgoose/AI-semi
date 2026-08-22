import inspect
import math
import unittest

from benchmarks.redred_mc_wtb_pose_recovery import recover_causal_cav
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import (
    SO3PLLConfig,
    SO3PLLError,
    SO3PLLMode,
    SO3PLLModel,
)


Q_IDENTITY = (0.0, 0.0, 0.0, 1.0)


def z_rotation(degrees):
    half_angle = math.radians(degrees) / 2.0
    return (0.0, 0.0, math.sin(half_angle), math.cos(half_angle))


def multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def assert_quaternion_equivalent(test, observed, expected, places=11):
    test.assertIsNotNone(observed)
    test.assertIsNotNone(expected)
    dot = sum(left * right for left, right in zip(observed, expected))
    test.assertAlmostEqual(abs(dot), 1.0, places=places)
    test.assertAlmostEqual(
        sum(component * component for component in observed), 1.0, places=places
    )


def commit_constant_z(model, count, *, start_cycle=10, cycle_step=10):
    receipts = []
    for index in range(count):
        receipts.append(model.commit_pose(
            index,
            index * 1_000_000,
            start_cycle + index * cycle_step,
            z_rotation(float(index)),
        ))
    return tuple(receipts)


class SO3PLLGeometryTests(unittest.TestCase):
    def test_constant_body_rate_locks_and_extrapolates(self):
        model = SO3PLLModel()
        receipts = commit_constant_z(model, 3)

        self.assertEqual(receipts[0].update_kind, "initialize")
        self.assertEqual(receipts[1].update_kind, "bootstrap_rate")
        self.assertEqual(receipts[1].lock_streak_after, 1)
        self.assertEqual(receipts[2].update_kind, "bounded_pi_update")
        self.assertTrue(receipts[2].locked_after)
        self.assertTrue(model.locked)

        decision = model.predict(3_000_000, 31)
        self.assertIs(decision.mode, SO3PLLMode.PLL)
        self.assertTrue(decision.candidate_used)
        self.assertEqual(decision.state_version, 2)
        self.assertEqual(decision.anchor_pose_id, 2)
        self.assertIsNone(decision.fallback_decision)
        assert_quaternion_equivalent(self, decision.quaternion_xyzw, z_rotation(3.0))

    def test_forecast_is_anchored_at_measurement_time_not_commit_time(self):
        model = SO3PLLModel()
        model.commit_pose(0, 0, 1, z_rotation(0.0))
        model.commit_pose(1, 1_000_000, 100, z_rotation(1.0))
        receipt = model.commit_pose(2, 2_000_000, 10_000, z_rotation(2.0))

        self.assertEqual(receipt.source_state_version, 1)
        assert_quaternion_equivalent(
            self, receipt.forecast_quaternion_xyzw, z_rotation(2.0)
        )
        self.assertAlmostEqual(receipt.residual_norm_rad, 0.0, places=13)
        self.assertTrue(receipt.locked_after)

    def test_unequal_pose_cadence_preserves_constant_body_rate(self):
        model = SO3PLLModel()
        model.commit_pose(0, 0, 1, z_rotation(0.0))
        model.commit_pose(1, 2_000_000, 50, z_rotation(2.0))
        receipt = model.commit_pose(2, 7_000_000, 500, z_rotation(7.0))
        self.assertAlmostEqual(receipt.residual_norm_rad, 0.0, places=13)
        self.assertTrue(receipt.locked_after)
        decision = model.predict(10_000_000, 501)
        self.assertIs(decision.mode, SO3PLLMode.PLL)
        assert_quaternion_equivalent(
            self, decision.quaternion_xyzw, z_rotation(10.0)
        )

    def test_rotated_body_axis_forecast_is_not_euler_subtraction(self):
        # Start from a rotated body, then apply repeated right-side X steps.
        # A left/world increment or Euler subtraction gives a different path.
        half = math.radians(1.0) / 2.0
        step = (math.sin(half), 0.0, 0.0, math.cos(half))
        q0 = z_rotation(90.0)
        q1 = multiply(q0, step)
        q2 = multiply(q1, step)
        q3 = multiply(q2, step)
        model = SO3PLLModel()
        model.commit_pose(0, 0, 1, q0)
        model.commit_pose(1, 1_000_000, 2, tuple(-v for v in q1))
        model.commit_pose(2, 2_000_000, 3, q2)
        decision = model.predict(3_000_000, 4)
        self.assertIs(decision.mode, SO3PLLMode.PLL)
        assert_quaternion_equivalent(self, decision.quaternion_xyzw, q3)


class CausalityAndFallbackTests(unittest.TestCase):
    def test_pose_update_is_invisible_on_its_commit_edge(self):
        model = SO3PLLModel()
        commit_constant_z(model, 2)
        receipt = model.commit_pose(2, 2_000_000, 30, z_rotation(2.0))
        self.assertEqual(receipt.effective_cycle, 31)
        self.assertEqual(receipt.forecast_generation_cycle, 20)
        self.assertTrue(receipt.locked_after)

        same_edge_cluster = tuple(model.predict(2_000_000, 30) for _ in range(6))
        self.assertTrue(all(item == same_edge_cluster[0] for item in same_edge_cluster))
        same_edge = same_edge_cluster[0]
        next_edge = model.predict(2_000_000, 31)
        self.assertIs(same_edge.mode, SO3PLLMode.CAV)
        self.assertFalse(same_edge.candidate_used)
        self.assertEqual(same_edge.state_version, 1)
        self.assertEqual(same_edge.fallback_decision.used_commit_cycles, (10, 20))
        self.assertNotIn(30, same_edge.fallback_decision.used_commit_cycles)
        assert_quaternion_equivalent(
            self, same_edge.quaternion_xyzw, z_rotation(2.0)
        )

        self.assertIs(next_edge.mode, SO3PLLMode.PLL)
        self.assertEqual(next_edge.state_version, 2)
        self.assertEqual(next_edge.anchor_pose_id, 2)
        assert_quaternion_equivalent(
            self, next_edge.quaternion_xyzw, z_rotation(2.0)
        )

    def test_pose_residual_correction_changes_only_future_decisions(self):
        model = SO3PLLModel(SO3PLLConfig(lock_count=1))
        model.commit_pose(0, 0, 10, z_rotation(0.0))
        model.commit_pose(1, 1_000_000, 20, z_rotation(1.0))
        before = model.predict(2_000_000, 29)
        self.assertIs(before.mode, SO3PLLMode.PLL)
        assert_quaternion_equivalent(self, before.quaternion_xyzw, z_rotation(2.0))

        receipt = model.commit_pose(2, 2_000_000, 30, z_rotation(2.5))
        self.assertGreater(receipt.residual_norm_rad, 0.0)
        same_edge = model.predict(2_000_000, 30)
        after = model.predict(2_000_000, 31)
        repeated_past = model.predict(2_000_000, 29)

        self.assertEqual(repeated_past, before)
        self.assertEqual(same_edge.state_version, before.state_version)
        assert_quaternion_equivalent(
            self, same_edge.quaternion_xyzw, z_rotation(2.0)
        )
        self.assertEqual(after.state_version, receipt.published_state_version)
        assert_quaternion_equivalent(self, after.quaternion_xyzw, z_rotation(2.5))

    def test_unlocked_path_is_exact_frozen_current_cav(self):
        model = SO3PLLModel(SO3PLLConfig(lock_count=3))
        commit_constant_z(model, 2)
        timestamp = 2_000_000
        cycle = 21
        expected = recover_causal_cav(
            model.pose_history,
            timestamp,
            cycle,
            model.config.cav_max_horizon_ns,
            model.config.zoh_max_age_ns,
        )
        observed = model.predict(timestamp, cycle)

        self.assertFalse(observed.candidate_used)
        self.assertEqual(observed.mode.value, expected.mode.value)
        self.assertEqual(observed.quaternion_xyzw, expected.quaternion_xyzw)
        self.assertEqual(observed.fallback_decision, expected)

    def test_fallback_preserves_cav_zoh_and_bypass_modes_exactly(self):
        model = SO3PLLModel(SO3PLLConfig(lock_count=3))
        model.commit_pose(0, 0, 1, z_rotation(0.0))
        model.commit_pose(1, 500_000, 2, z_rotation(0.5))

        cases = (
            (1_000_000, SO3PLLMode.CAV),
            (1_250_000, SO3PLLMode.ZOH),
            (1_500_001, SO3PLLMode.BYPASS),
        )
        for timestamp, mode in cases:
            with self.subTest(timestamp=timestamp):
                expected = recover_causal_cav(
                    model.pose_history,
                    timestamp,
                    3,
                    model.config.cav_max_horizon_ns,
                    model.config.zoh_max_age_ns,
                )
                observed = model.predict(timestamp, 3)
                self.assertIs(observed.mode, mode)
                self.assertEqual(observed.quaternion_xyzw, expected.quaternion_xyzw)
                self.assertEqual(observed.fallback_decision, expected)

    def test_predict_is_read_only_and_repeatable(self):
        model = SO3PLLModel()
        commit_constant_z(model, 3)
        poses = model.pose_history
        states = model.state_versions
        receipts = model.update_receipts
        first = model.predict(3_000_000, 31)
        second = model.predict(3_000_000, 31)
        self.assertEqual(first, second)
        self.assertEqual(model.pose_history, poses)
        self.assertEqual(model.state_versions, states)
        self.assertEqual(model.update_receipts, receipts)


class UnlockRelockTests(unittest.TestCase):
    def test_invalid_pose_never_updates_pose_or_loop_state(self):
        model = SO3PLLModel()
        commit_constant_z(model, 3)
        poses = model.pose_history
        states = model.state_versions

        flagged = model.commit_pose(
            3, 3_000_000, 40, z_rotation(3.0), valid=False
        )
        malformed = model.commit_pose(
            3, 3_000_000, 40, (0.0, 0.0, 0.0, 0.0)
        )
        self.assertFalse(flagged.accepted)
        self.assertEqual(flagged.fault_reason, "pose_validity_false")
        self.assertFalse(malformed.accepted)
        self.assertEqual(malformed.fault_reason, "invalid_quaternion")
        self.assertEqual(model.pose_history, poses)
        self.assertEqual(model.state_versions, states)
        self.assertTrue(model.locked)

    def test_nonmonotonic_pose_inputs_are_ignored_without_state_change(self):
        model = SO3PLLModel()
        commit_constant_z(model, 2)
        poses = model.pose_history
        states = model.state_versions
        receipts = (
            model.commit_pose(1, 2_000_000, 30, z_rotation(2.0)),
            model.commit_pose(2, 1_000_000, 30, z_rotation(2.0)),
            model.commit_pose(2, 2_000_000, 20, z_rotation(2.0)),
        )
        self.assertEqual(
            tuple(receipt.fault_reason for receipt in receipts),
            (
                "nonmonotonic_pose_id",
                "nonmonotonic_measurement_timestamp",
                "nonmonotonic_commit_cycle",
            ),
        )
        self.assertEqual(model.pose_history, poses)
        self.assertEqual(model.state_versions, states)

    def test_long_gap_unlocks_then_clean_poses_relock(self):
        config = SO3PLLConfig(max_gap_ns=2_500_000)
        model = SO3PLLModel(config)
        commit_constant_z(model, 3)
        self.assertTrue(model.locked)

        fault = model.commit_pose(3, 10_000_000, 40, z_rotation(10.0))
        self.assertEqual(fault.update_kind, "unlock_reset")
        self.assertEqual(fault.fault_reason, "pose_gap")
        self.assertFalse(model.locked)
        fallback = model.predict(10_500_000, 41)
        self.assertFalse(fallback.candidate_used)
        self.assertEqual(fallback.quaternion_xyzw, fallback.fallback_decision.quaternion_xyzw)

        bootstrap = model.commit_pose(4, 11_000_000, 50, z_rotation(11.0))
        relock = model.commit_pose(5, 12_000_000, 60, z_rotation(12.0))
        self.assertEqual(bootstrap.update_kind, "bootstrap_rate")
        self.assertFalse(bootstrap.locked_after)
        self.assertTrue(relock.locked_after)
        decision = model.predict(13_000_000, 61)
        self.assertIs(decision.mode, SO3PLLMode.PLL)
        assert_quaternion_equivalent(
            self, decision.quaternion_xyzw, z_rotation(13.0)
        )

    def test_near_pi_and_phase_jump_residuals_unlock(self):
        near_pi = SO3PLLModel()
        commit_constant_z(near_pi, 3)
        receipt = near_pi.commit_pose(3, 3_000_000, 40, z_rotation(183.0))
        self.assertEqual(receipt.fault_reason, "near_pi_residual")
        self.assertFalse(receipt.locked_after)

        phase_jump = SO3PLLModel()
        commit_constant_z(phase_jump, 3)
        receipt = phase_jump.commit_pose(3, 3_000_000, 40, z_rotation(50.0))
        self.assertEqual(receipt.fault_reason, "phase_jump")
        self.assertFalse(receipt.locked_after)

    def test_rate_saturation_unlocks_without_clipping(self):
        config = SO3PLLConfig(max_angular_rate_rad_s=math.radians(100.0))
        model = SO3PLLModel(config)
        model.commit_pose(0, 0, 1, z_rotation(0.0))
        receipt = model.commit_pose(1, 1_000_000, 2, z_rotation(1.0))
        self.assertEqual(receipt.fault_reason, "angular_rate_saturation")
        self.assertFalse(receipt.locked_after)
        self.assertEqual(
            model.current_state.angular_velocity_body_rad_s, (0.0, 0.0, 0.0)
        )

    def test_opposing_bounded_residuals_trigger_limit_cycle_unlock(self):
        model = SO3PLLModel(SO3PLLConfig(lock_count=1))
        model.commit_pose(0, 0, 1, z_rotation(0.0))
        model.commit_pose(1, 1_000_000, 2, z_rotation(1.0))
        first = model.commit_pose(2, 2_000_000, 3, z_rotation(2.2))
        self.assertIsNone(first.fault_reason)
        self.assertTrue(first.locked_after)

        oscillation = model.commit_pose(3, 3_000_000, 4, z_rotation(3.05))
        self.assertEqual(oscillation.fault_reason, "limit_cycle")
        self.assertFalse(oscillation.locked_after)

    def test_reset_clears_recording_local_state(self):
        model = SO3PLLModel()
        commit_constant_z(model, 3)
        model.reset()
        self.assertEqual(model.pose_history, ())
        self.assertEqual(model.state_versions, ())
        self.assertEqual(model.update_receipts, ())
        self.assertFalse(model.locked)
        decision = model.predict(0, 0)
        self.assertIs(decision.mode, SO3PLLMode.BYPASS)


class FrozenBoundaryTests(unittest.TestCase):
    def test_configuration_rejects_unbounded_or_ambiguous_values(self):
        bad_configs = (
            {"proportional_gain": -0.1},
            {"integral_gain": 1.1},
            {"lock_count": 0},
            {"max_gap_ns": 0},
            {"phase_jump_max_rad": math.pi},
            {"lock_residual_max_rad": math.radians(31.0)},
            {"max_angular_rate_rad_s": math.inf},
        )
        for kwargs in bad_configs:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(SO3PLLError):
                    SO3PLLConfig(**kwargs)

        default = SO3PLLConfig()
        ablation = SO3PLLConfig(integral_gain=0.0)
        self.assertNotEqual(default.candidate_id, ablation.candidate_id)

    def test_public_runtime_api_has_no_event_or_outcome_feedback_input(self):
        commit_parameters = tuple(
            inspect.signature(SO3PLLModel.commit_pose).parameters
        )
        predict_parameters = tuple(inspect.signature(SO3PLLModel.predict).parameters)
        self.assertEqual(
            commit_parameters,
            (
                "self",
                "pose_id",
                "measurement_timestamp_ns",
                "commit_cycle",
                "quaternion_xyzw",
                "valid",
            ),
        )
        self.assertEqual(
            predict_parameters,
            ("self", "event_timestamp_ns", "decision_cycle"),
        )

    def test_invalid_api_scalars_fail_closed(self):
        model = SO3PLLModel()
        with self.assertRaises(SO3PLLError):
            model.commit_pose(True, 0, 0, Q_IDENTITY)
        with self.assertRaises(SO3PLLError):
            model.commit_pose(0, -1, 0, Q_IDENTITY)
        with self.assertRaises(SO3PLLError):
            model.commit_pose(0, 0, 0, Q_IDENTITY, valid=1)
        with self.assertRaises(SO3PLLError):
            model.predict(0, -1)


if __name__ == "__main__":
    unittest.main()
