import ast
from dataclasses import FrozenInstanceError
import inspect
import math
from pathlib import Path
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3.dspb import (
    CreditState,
    DSPBConfig,
    DSPBError,
    DSPBModel,
    DecisionMode,
    E0,
    E1,
    E2,
    E3,
    EpochState,
    EXPERT_IDS,
    EventRecord,
    SuppliedPose,
)


def z_rotation(degrees):
    half = 0.5 * math.radians(degrees)
    return (0.0, 0.0, math.sin(half), math.cos(half))


def pose(pose_id, timestamp_ns, commit_cycle, degrees, **flags):
    return SuppliedPose(
        pose_id,
        timestamp_ns,
        commit_cycle,
        z_rotation(degrees),
        **flags,
    )


def event(event_id, timestamp_ns, occurrence_cycle, decision_cycle):
    return EventRecord(event_id, timestamp_ns, occurrence_cycle, decision_cycle)


def assert_quaternion_equivalent(test, observed, expected, places=10):
    test.assertIsNotNone(observed)
    dot = sum(left * right for left, right in zip(observed, expected))
    test.assertAlmostEqual(abs(dot), 1.0, places=places)


def accelerating_model():
    model = DSPBModel()
    for index, degrees in enumerate((0.0, 10.0, 25.0, 45.0, 70.0)):
        model.commit_pose(pose(index, index * 5_000_000, index * 10, degrees))
    return model


class FrozenProfileTests(unittest.TestCase):
    def test_bank_has_exactly_four_frozen_experts_and_no_loss_api(self):
        model = DSPBModel()
        self.assertEqual(model.expert_ids, (E0, E1, E2, E3))
        self.assertEqual(EXPERT_IDS, model.expert_ids)
        self.assertEqual(
            tuple(inspect.signature(model.commit_pose).parameters), ("pose",)
        )
        self.assertEqual(
            tuple(inspect.signature(model.predict_event).parameters), ("event",)
        )
        self.assertNotIn("loss", inspect.signature(model.commit_pose).parameters)
        self.assertNotIn("loss", inspect.signature(model.predict_event).parameters)

    def test_numeric_profile_cannot_be_changed_under_the_same_candidate_id(self):
        config = DSPBConfig()
        self.assertEqual(config.candidate_id, "DSPB-A4-E0E1E2E3-V1")
        self.assertEqual(len(config.sha256), 64)
        with self.assertRaisesRegex(DSPBError, "profile is frozen"):
            DSPBConfig(credit_ewma_alpha=0.5)

    def test_inputs_and_receipts_are_immutable(self):
        sample = pose(0, 0, 0, 0.0)
        with self.assertRaises(FrozenInstanceError):
            sample.pose_id = 1
        receipt = DSPBModel().commit_pose(sample)
        with self.assertRaises(FrozenInstanceError):
            receipt.next_state_version = 99


class ExpertFormulaTests(unittest.TestCase):
    def test_all_four_expert_formulas_use_only_completed_pose_intervals(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 0, 0, 0.0))
        model.commit_pose(pose(1, 5_000_000, 10, 10.0))
        receipt = model.commit_pose(pose(2, 10_000_000, 20, 25.0))

        self.assertEqual(
            tuple(score.expert_id for score in receipt.scored_forecasts), EXPERT_IDS
        )
        self.assertTrue(all(2 not in score.source_pose_ids for score in receipt.scored_forecasts))
        state = model.pending_state
        self.assertIsNotNone(state)
        functions = {function.expert_id: function for function in state.expert_functions}
        self.assertEqual(tuple(functions), EXPERT_IDS)
        self.assertTrue(all(functions[item].valid for item in EXPERT_IDS))

        expected_degrees = {
            E0: 40.0,
            E1: 36.25,
            E2: 42.5,
            E3: 37.5,
        }
        for expert_id, degrees in expected_degrees.items():
            with self.subTest(expert_id=expert_id):
                forecast = functions[expert_id].forecast(15_000_000)
                self.assertTrue(forecast.valid, forecast.reason)
                assert_quaternion_equivalent(
                    self, forecast.quaternion_xyzw, z_rotation(degrees)
                )

    def test_rg3_and_signed_speed_reject_a_past_reversal(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 0, 0, 0.0))
        model.commit_pose(pose(1, 5_000_000, 10, 10.0))
        model.commit_pose(pose(2, 10_000_000, 20, 0.0))
        functions = {
            function.expert_id: function
            for function in model.pending_state.expert_functions
        }
        self.assertFalse(functions[E2].valid)
        self.assertIn("direction gate", functions[E2].invalid_reason)
        self.assertFalse(functions[E3].valid)
        self.assertIn("signed direction gate", functions[E3].invalid_reason)

    def test_antipodal_pose_encodings_do_not_change_expert_forecasts(self):
        positive = DSPBModel()
        negative = DSPBModel()
        angles = (0.0, 10.0, 25.0)
        for index, degrees in enumerate(angles):
            q = z_rotation(degrees)
            positive.commit_pose(SuppliedPose(index, index * 5_000_000, index * 10, q))
            negative.commit_pose(SuppliedPose(
                index,
                index * 5_000_000,
                index * 10,
                tuple(-component for component in q),
            ))
        for left, right in zip(
            positive.pending_state.expert_functions,
            negative.pending_state.expert_functions,
        ):
            self.assertEqual(left.expert_id, right.expert_id)
            first = left.forecast(15_000_000)
            second = right.forecast(15_000_000)
            self.assertEqual(first.valid, second.valid)
            if first.valid:
                assert_quaternion_equivalent(
                    self, first.quaternion_xyzw, second.quaternion_xyzw
                )


class CausalityAndAtomicityTests(unittest.TestCase):
    def test_new_pose_scores_old_functions_and_is_visible_next_cycle_only(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 0, 0, 0.0))
        model.commit_pose(pose(1, 5_000_000, 10, 10.0))
        receipt = model.commit_pose(pose(2, 10_000_000, 20, 25.0))

        self.assertEqual(receipt.prior_state_version, 2)
        self.assertEqual(receipt.next_state_version, 3)
        self.assertEqual(receipt.next_effective_cycle, 21)
        self.assertTrue(all(2 not in score.source_pose_ids for score in receipt.scored_forecasts))

        same_edge = model.predict_event(event(100, 10_000_000, 19, 20))
        self.assertEqual(same_edge.state_version, 2)
        self.assertEqual(same_edge.mode, DecisionMode.CURRENT_CAV)
        self.assertEqual(same_edge.used_pose_ids, (0, 1))
        assert_quaternion_equivalent(self, same_edge.output_quaternion_xyzw, z_rotation(20.0))

        next_edge = model.predict_event(event(101, 10_000_000, 20, 21))
        self.assertEqual(next_edge.state_version, 3)
        self.assertNotIn(20, same_edge.used_pose_commit_cycles)
        self.assertIn(20, next_edge.used_pose_commit_cycles)
        assert_quaternion_equivalent(self, next_edge.output_quaternion_xyzw, z_rotation(25.0))

    def test_equal_timestamp_cluster_is_atomic_and_event_id_neutral(self):
        model = accelerating_model()
        state = model.pending_state
        self.assertEqual(state.selected_expert_id, E2)
        cluster = model.predict_event_cluster((
            event(200, 20_000_000, 40, 41),
            event(201, 20_000_000, 40, 41),
            event(202, 20_000_000, 40, 41),
        ))
        self.assertEqual({decision.state_version for decision in cluster}, {5})
        self.assertTrue(all(decision.candidate_used for decision in cluster))
        self.assertEqual({decision.geometry_expert_id for decision in cluster}, {E2})
        self.assertTrue(all(
            decision.output_quaternion_xyzw == cluster[0].output_quaternion_xyzw
            for decision in cluster
        ))
        self.assertEqual(model.pending_state, None)

    def test_selected_winner_is_not_visible_on_its_pose_commit_edge(self):
        model = DSPBModel()
        for index, degrees in enumerate((0.0, 10.0, 25.0, 45.0)):
            model.commit_pose(pose(index, index * 5_000_000, index * 10, degrees))
        winning_receipt = model.commit_pose(pose(4, 20_000_000, 40, 70.0))
        self.assertEqual(winning_receipt.next_selected_expert_id, E2)

        same_edge = model.predict_event(event(300, 20_000_000, 39, 40))
        next_edge = model.predict_event(event(301, 20_000_000, 40, 41))
        self.assertFalse(same_edge.candidate_used)
        self.assertEqual(same_edge.state_version, 4)
        self.assertTrue(next_edge.candidate_used)
        self.assertEqual(next_edge.state_version, 5)
        self.assertEqual(next_edge.geometry_expert_id, E2)

    def test_future_measurement_and_occurrence_edge_violations_fail_closed(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 10_000_000, 0, 30.0))
        decision = model.predict_event(event(1, 5_000_000, 0, 1))
        self.assertEqual(decision.mode, DecisionMode.BYPASS)
        self.assertEqual(decision.used_pose_ids, ())
        with self.assertRaisesRegex(DSPBError, "strictly before"):
            EventRecord(2, 5_000_000, 2, 2)


class FallbackAndIntegrityTests(unittest.TestCase):
    def test_common_fallback_chain_is_bypass_then_zoh_then_exact_cav(self):
        empty = DSPBModel()
        bypass = empty.predict_event(event(0, 0, 0, 1))
        self.assertEqual(bypass.mode, DecisionMode.BYPASS)
        self.assertIsNone(bypass.output_quaternion_xyzw)

        one = DSPBModel()
        one.commit_pose(pose(0, 0, 0, 7.0))
        zoh = one.predict_event(event(1, 500_000, 0, 1))
        self.assertEqual(zoh.mode, DecisionMode.ZOH)
        self.assertEqual(zoh.used_pose_ids, (0,))
        assert_quaternion_equivalent(self, zoh.output_quaternion_xyzw, z_rotation(7.0))
        stale = one.predict_event(event(2, 1_000_001, 1, 2))
        self.assertEqual(stale.mode, DecisionMode.BYPASS)

        two = DSPBModel()
        two.commit_pose(pose(0, 0, 0, 0.0))
        two.commit_pose(pose(1, 1_000_000, 10, 10.0))
        cav = two.predict_event(event(3, 1_500_000, 10, 11))
        self.assertEqual(cav.mode, DecisionMode.CURRENT_CAV)
        self.assertFalse(cav.candidate_used)
        self.assertEqual(cav.used_pose_ids, (0, 1))
        assert_quaternion_equivalent(self, cav.output_quaternion_xyzw, z_rotation(15.0))

    def test_invalid_pose_updates_neither_credit_nor_expert_history(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 0, 0, 0.0))
        model.commit_pose(pose(1, 5_000_000, 10, 10.0))
        model.commit_pose(pose(2, 10_000_000, 20, 25.0))
        expected_credits = model.pending_state.credits

        receipt = model.commit_pose(pose(
            99,
            15_000_000,
            30,
            170.0,
            value_valid=False,
        ))
        self.assertEqual(receipt.next_credits, expected_credits)
        self.assertEqual(receipt.next_lock_reason, "invalid_supplied_pose")
        self.assertTrue(all(not score.forecast_valid for score in receipt.scored_forecasts))
        decision = model.predict_event(event(400, 15_000_000, 30, 31))
        self.assertNotIn(99, decision.used_pose_ids)

    def test_winner_tie_unlocks_and_uses_the_exact_baseline(self):
        model = DSPBModel()
        for index in range(5):
            model.commit_pose(pose(index, index * 5_000_000, index * 10, index * 10.0))
        self.assertEqual(model.pending_state.lock_reason, "winner_tie")
        self.assertIsNone(model.pending_state.selected_expert_id)
        decision = model.predict_event(event(410, 20_000_000, 40, 41))
        self.assertEqual(decision.mode, DecisionMode.CURRENT_CAV)
        self.assertFalse(decision.candidate_used)
        self.assertTrue(decision.fallback_reason.startswith("winner_tie:"))

    def test_invalid_winner_and_credit_corruption_unlock_fail_closed(self):
        model = accelerating_model()
        model.predict_event(event(420, 20_000_000, 40, 41))
        invalid_winner = model.commit_pose(pose(5, 25_000_000, 50, 100.0))
        self.assertTrue(invalid_winner.next_lock_reason.startswith("invalid_winner:"))
        fallback = model.predict_event(event(421, 25_000_000, 50, 51))
        self.assertFalse(fallback.candidate_used)
        self.assertEqual(fallback.mode, DecisionMode.CURRENT_CAV)

        state = model.published_state
        corrupted = (
            CreditState(E0, 3, math.nan),
            state.credits[1],
            state.credits[2],
            state.credits[3],
        )
        model._published = EpochState(
            state.state_version,
            state.effective_cycle,
            state.expert_functions,
            corrupted,
            state.selected_expert_id,
            state.lock_reason,
        )
        receipt = model.commit_pose(pose(6, 30_000_000, 60, 130.0))
        self.assertEqual(receipt.next_lock_reason, "credit_corruption")
        self.assertTrue(all(credit.sample_count == 0 for credit in receipt.next_credits))

    def test_duplicates_reordering_and_nonatomic_clusters_are_rejected(self):
        model = DSPBModel()
        model.commit_pose(pose(0, 0, 0, 0.0))
        with self.assertRaisesRegex(DSPBError, "exact-once"):
            model.commit_pose(pose(0, 1_000_000, 1, 1.0))
        first = model.predict_event(event(10, 0, 0, 1))
        self.assertEqual(first.event_id, 10)
        with self.assertRaisesRegex(DSPBError, "exact-once"):
            model.predict_event(event(10, 0, 0, 1))
        with self.assertRaisesRegex(DSPBError, "share timestamp"):
            model.predict_event_cluster((
                event(11, 0, 1, 2),
                event(12, 1, 1, 2),
            ))
        with self.assertRaisesRegex(DSPBError, "backwards"):
            model.predict_event(event(13, 0, -1, 0))

    def test_replay_is_deterministic_and_receipts_bind_identity(self):
        def replay():
            model = accelerating_model()
            decisions = model.predict_event_cluster((
                event(500, 20_000_000, 40, 41),
                event(501, 20_000_000, 40, 41),
            ))
            return (
                tuple(receipt.to_mapping() for receipt in model.pose_receipts),
                tuple(decision.to_mapping() for decision in decisions),
            )

        first = replay()
        second = replay()
        self.assertEqual(first, second)
        self.assertNotEqual(
            first[1][0]["decision_sha256"], first[1][1]["decision_sha256"]
        )
        self.assertTrue(all(
            len(receipt["receipt_sha256"]) == 64 for receipt in first[0]
        ))

    def test_sources_parse_with_python38_grammar(self):
        root = Path(__file__).resolve().parents[2]
        for relative in (
            "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
            "tests/redred_mc_wtb_predictor_stage3_dspb/test_dspb.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            ast.parse(source, filename=relative, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
