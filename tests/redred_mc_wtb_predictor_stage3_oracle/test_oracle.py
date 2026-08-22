"""Independent synthetic tests for Stage-3 prediction and feedback semantics."""

from __future__ import annotations

import ast
import math
import sys
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from oracle import (  # noqa: E402
    DecisionRecord,
    OracleHarness,
    OracleViolation,
    fallback_equivalent,
    reference_fallback,
    rotation_distance,
    validate_identity_order_exact_once,
)
from protocol import (  # noqa: E402
    AdapterDecision,
    CandidateNumericError,
    CausalView,
    EventRecord,
    FallbackDecision,
    PoseFeedback,
    PoseRecord,
    PredictorEvent,
    Quaternion,
)
from scenarios import (  # noqa: E402
    constant_acceleration,
    constant_rate,
    make_motion_scenario,
    near_pi_pair,
    reversal,
    stationary,
    stop,
    z_axis_quaternion,
)


@dataclass(frozen=True)
class _State:
    accepted_pose_ids: Tuple[str, ...] = ()


class FallbackAdapter:
    candidate_id = "synthetic-fallback-adapter-v1"

    def initial_state(self) -> _State:
        return _State()

    def forecast_pose(
        self,
        state: _State,
        target_timestamp_ns: int,
        visible_poses: Tuple[PoseRecord, ...],
    ) -> Optional[Quaternion]:
        del state, target_timestamp_ns
        return visible_poses[-1].quaternion_xyzw if visible_poses else None

    def accept_pose(self, state: _State, feedback: PoseFeedback) -> _State:
        return _State(state.accepted_pose_ids + (feedback.pose.pose_id,))

    def decide(
        self,
        state: _State,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del state, event, fallback
        return AdapterDecision(True, None, (), view.state_version, "REQUESTED_FALLBACK")


class NumericFailureAdapter(FallbackAdapter):
    candidate_id = "synthetic-numeric-failure-v1"

    def decide(
        self,
        state: _State,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del state, event, view, fallback
        raise CandidateNumericError("synthetic overflow")


class NonFiniteDecisionAdapter(FallbackAdapter):
    candidate_id = "synthetic-nonfinite-decision-v1"

    def decide(
        self,
        state: _State,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del state, event, fallback
        return AdapterDecision(False, (math.nan, 0.0, 0.0, 1.0), (), view.state_version)


class NonFiniteForecastAdapter(FallbackAdapter):
    candidate_id = "synthetic-nonfinite-forecast-v1"

    def forecast_pose(
        self,
        state: _State,
        target_timestamp_ns: int,
        visible_poses: Tuple[PoseRecord, ...],
    ) -> Optional[Quaternion]:
        del state, target_timestamp_ns, visible_poses
        return (math.inf, 0.0, 0.0, 1.0)


class BadPoseCitationAdapter(FallbackAdapter):
    candidate_id = "bad-pose-citation"

    def decide(
        self,
        state: _State,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del state, event, fallback
        return AdapterDecision(False, z_axis_quaternion(0.0), ("future-pose",), view.state_version)


class BadStateCitationAdapter(FallbackAdapter):
    candidate_id = "bad-state-citation"

    def decide(
        self,
        state: _State,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del state, event, fallback
        return AdapterDecision(False, z_axis_quaternion(0.0), (), view.state_version + 1)


class MutatingAdapter(FallbackAdapter):
    candidate_id = "bad-mutating-adapter"

    def initial_state(self) -> Any:
        return []

    def decide(
        self,
        state: Any,
        event: PredictorEvent,
        view: CausalView,
        fallback: FallbackDecision,
    ) -> AdapterDecision:
        del event, fallback
        state.append("mutation")
        return AdapterDecision(True, None, (), view.state_version)


def _single_decision(scenario: Any) -> DecisionRecord:
    receipt = OracleHarness().run(FallbackAdapter(), scenario.poses, scenario.events)
    return receipt.decisions[0]


class MotionCorrectnessTests(unittest.TestCase):
    def test_stationary_is_exact(self) -> None:
        scenario = make_motion_scenario(
            "stationary", (0, 1_000_000, 2_000_000), (2_500_000,), stationary(0.3)
        )
        decision = _single_decision(scenario)
        self.assertEqual(decision.mode, "CAV")
        self.assertLess(rotation_distance(decision.quaternion_xyzw, scenario.truth_at(2_500_000)), 1.0e-12)

    def test_constant_rate_is_exact(self) -> None:
        scenario = make_motion_scenario(
            "constant-rate",
            (0, 1_000_000, 2_000_000),
            (2_500_000,),
            constant_rate(17.0, 0.2),
        )
        decision = _single_decision(scenario)
        self.assertEqual(decision.mode, "CAV")
        self.assertLess(rotation_distance(decision.quaternion_xyzw, scenario.truth_at(2_500_000)), 1.0e-12)

    def test_acceleration_exposes_nonzero_cav_residual(self) -> None:
        scenario = make_motion_scenario(
            "acceleration",
            (0, 1_000_000, 2_000_000),
            (2_500_000,),
            constant_acceleration(4_000.0),
        )
        decision = _single_decision(scenario)
        residual = rotation_distance(decision.quaternion_xyzw, scenario.truth_at(2_500_000))
        self.assertEqual(decision.mode, "CAV")
        self.assertGreater(residual, 1.0e-4)

    def test_stop_does_not_receive_future_motion_knowledge(self) -> None:
        scenario = make_motion_scenario(
            "stop", (0, 1_000_000, 2_000_000), (2_500_000,), stop(40.0, 2_000_000)
        )
        decision = _single_decision(scenario)
        self.assertEqual(decision.mode, "CAV")
        self.assertGreater(rotation_distance(decision.quaternion_xyzw, scenario.truth_at(2_500_000)), 0.01)

    def test_reversal_does_not_receive_future_motion_knowledge(self) -> None:
        scenario = make_motion_scenario(
            "reversal",
            (0, 1_000_000, 2_000_000),
            (2_500_000,),
            reversal(40.0, 2_000_000),
        )
        decision = _single_decision(scenario)
        self.assertEqual(decision.mode, "CAV")
        self.assertGreater(rotation_distance(decision.quaternion_xyzw, scenario.truth_at(2_500_000)), 0.03)

    def test_unequal_pose_and_event_cadence_remains_exact_at_constant_rate(self) -> None:
        scenario = make_motion_scenario(
            "unequal-cadence",
            (0, 700_000, 2_000_000),
            (2_100_000, 2_350_000, 2_900_000),
            constant_rate(11.0),
        )
        receipt = OracleHarness().run(FallbackAdapter(), scenario.poses, scenario.events)
        self.assertEqual([item.mode for item in receipt.decisions], ["CAV", "CAV", "CAV"])
        for event, decision in zip(scenario.events, receipt.decisions):
            self.assertLess(
                rotation_distance(decision.quaternion_xyzw, scenario.truth_at(event.timestamp_ns)),
                1.0e-12,
            )


class CausalityAndFailureTests(unittest.TestCase):
    def test_pose_delay_dropout_and_invalid_pose_are_explicit(self) -> None:
        scenario = make_motion_scenario(
            "transport-faults",
            (0, 1_000_000, 2_000_000, 3_000_000),
            (1_500_000, 3_200_000, 3_700_000),
            constant_rate(3.0),
            pose_delay_overrides={1: 25},
            dropped_pose_indices=(2,),
            invalid_pose_indices=(3,),
        )
        receipt = OracleHarness().run(FallbackAdapter(), scenario.poses, scenario.events)
        self.assertEqual(receipt.event_audits[0].visible_pose_ids, ("p0",))
        self.assertEqual(receipt.event_audits[1].visible_pose_ids, ("p0", "p3"))
        self.assertNotIn("p2", {pose.pose_id for pose in scenario.poses})
        invalid = next(item for item in receipt.feedback_audits if item.pose_id == "p3")
        self.assertFalse(invalid.updated)
        self.assertEqual(invalid.reason, "INVALID_POSE")
        self.assertNotIn("p3", receipt.decisions[1].used_pose_ids)
        self.assertEqual(receipt.event_audits[1].state_version, 1)
        self.assertEqual(receipt.event_audits[2].state_version, 2)

    def test_pose_committed_on_decision_edge_is_excluded(self) -> None:
        poses = (
            PoseRecord("p0", 0, 1, z_axis_quaternion(0.0)),
            PoseRecord("p1", 1_000_000, 15, z_axis_quaternion(0.01)),
        )
        events = (
            EventRecord("same-edge", 1_200_000, 12, 15),
            EventRecord("next-edge", 1_300_000, 13, 16),
        )
        receipt = OracleHarness().run(FallbackAdapter(), poses, events)
        self.assertEqual(receipt.event_audits[0].visible_pose_ids, ("p0",))
        self.assertEqual(receipt.event_audits[1].visible_pose_ids, ("p0", "p1"))
        self.assertEqual(receipt.event_audits[0].state_version, 1)
        self.assertEqual(receipt.event_audits[1].state_version, 2)

    def test_equal_timestamp_cluster_is_atomic(self) -> None:
        poses = (
            PoseRecord("p0", 0, 1, z_axis_quaternion(0.0)),
            PoseRecord("p1", 1_000_000, 15, z_axis_quaternion(0.01)),
        )
        events = (
            EventRecord("cluster-a", 1_200_000, 12, 15, x=1),
            EventRecord("cluster-b", 1_200_000, 12, 15, x=2),
            EventRecord("after", 1_300_000, 13, 16, x=3),
        )
        receipt = OracleHarness().run(FallbackAdapter(), poses, events)
        first, second, after = receipt.event_audits
        self.assertEqual(first.state_version, second.state_version)
        self.assertEqual(first.visible_pose_ids, second.visible_pose_ids)
        self.assertNotIn("p1", first.visible_pose_ids)
        self.assertIn("p1", after.visible_pose_ids)
        self.assertEqual(after.state_version, first.state_version + 1)

    def test_pre_pose_forecast_receipt_and_next_cycle_effectivity(self) -> None:
        poses = (
            PoseRecord("p0", 0, 1, z_axis_quaternion(0.0)),
            PoseRecord("p1", 1_000_000, 15, z_axis_quaternion(0.01)),
        )
        events = (
            EventRecord("at-commit", 1_200_000, 12, 15),
            EventRecord("at-effective", 1_300_000, 13, 16),
        )
        receipt = OracleHarness().run(FallbackAdapter(), poses, events)
        feedback = next(item for item in receipt.feedback_audits if item.pose_id == "p1")
        self.assertEqual(feedback.source_state_version, 1)
        self.assertEqual(feedback.forecast_generation_cycle, 2)
        self.assertEqual(feedback.forecast_target_timestamp_ns, 1_000_000)
        self.assertEqual(feedback.effective_cycle, 16)
        self.assertEqual(feedback.published_state_version, 2)
        self.assertEqual(receipt.event_audits[0].state_version, 1)
        self.assertEqual(receipt.event_audits[1].state_version, 2)

    def test_near_pi_relative_pose_uses_fresh_zoh(self) -> None:
        poses = near_pi_pair()
        event = EventRecord("near-pi", 1_100_000, 11, 12)
        decision = OracleHarness().run(FallbackAdapter(), poses, (event,)).decisions[0]
        self.assertEqual(decision.mode, "ZOH")
        self.assertEqual(decision.used_pose_ids, ("p1",))
        self.assertEqual(decision.baseline_reason, "CAV_NEAR_PI")

    def test_numeric_failure_is_exact_fallback(self) -> None:
        scenario = make_motion_scenario(
            "numeric", (0, 1_000_000), (1_200_000,), constant_rate(2.0)
        )
        receipt = OracleHarness().run(NumericFailureAdapter(), scenario.poses, scenario.events)
        visible = tuple(
            pose
            for pose in scenario.poses
            if pose.commit_cycle < scenario.events[0].decision_cycle
        )
        fallback = reference_fallback(scenario.events[0], visible)
        self.assertTrue(fallback_equivalent(receipt.decisions[0], fallback))
        self.assertEqual(receipt.decisions[0].fallback_reason, "CandidateNumericError")

    def test_nonfinite_decision_is_exact_fallback(self) -> None:
        scenario = make_motion_scenario(
            "nonfinite-decision", (0, 1_000_000), (1_200_000,), constant_rate(2.0)
        )
        receipt = OracleHarness().run(NonFiniteDecisionAdapter(), scenario.poses, scenario.events)
        visible = tuple(
            pose
            for pose in scenario.poses
            if pose.commit_cycle < scenario.events[0].decision_cycle
        )
        fallback = reference_fallback(scenario.events[0], visible)
        self.assertTrue(fallback_equivalent(receipt.decisions[0], fallback))
        self.assertEqual(receipt.decisions[0].fallback_reason, "CandidateNumericError")

    def test_nonfinite_pose_forecast_does_not_publish_state(self) -> None:
        scenario = make_motion_scenario(
            "nonfinite-forecast", (0, 1_000_000), (1_200_000,), constant_rate(2.0)
        )
        receipt = OracleHarness().run(NonFiniteForecastAdapter(), scenario.poses, scenario.events)
        self.assertEqual(receipt.event_audits[0].state_version, 0)
        self.assertTrue(all(not item.updated for item in receipt.feedback_audits))
        self.assertTrue(
            all(item.reason.startswith("NUMERIC_FAILURE") for item in receipt.feedback_audits)
        )

    def test_fallback_equivalence_covers_raw_zoh_and_cav(self) -> None:
        poses = (
            PoseRecord("p0", 0, 5, z_axis_quaternion(0.0)),
            PoseRecord("p1", 1_000_000, 20, z_axis_quaternion(0.02)),
        )
        events = (
            EventRecord("raw", 100_000, 1, 2),
            EventRecord("zoh", 500_000, 5, 7),
            EventRecord("cav", 1_200_000, 21, 22),
        )
        receipt = OracleHarness().run(FallbackAdapter(), poses, events)
        self.assertEqual([item.mode for item in receipt.decisions], ["RAW", "ZOH", "CAV"])
        for event, audit, decision in zip(events, receipt.event_audits, receipt.decisions):
            visible = tuple(pose for pose in poses if pose.pose_id in audit.visible_pose_ids)
            self.assertTrue(fallback_equivalent(decision, reference_fallback(event, visible)))

    def test_invisible_pose_and_future_state_citations_are_rejected(self) -> None:
        scenario = make_motion_scenario("citations", (0,), (200_000,), stationary())
        with self.assertRaisesRegex(OracleViolation, "invisible or invalid pose"):
            OracleHarness().run(BadPoseCitationAdapter(), scenario.poses, scenario.events)
        with self.assertRaisesRegex(OracleViolation, "non-visible state"):
            OracleHarness().run(BadStateCitationAdapter(), scenario.poses, scenario.events)

    def test_candidate_cannot_mutate_supplied_snapshot(self) -> None:
        scenario = make_motion_scenario("mutation", (), (200_000,), stationary())
        with self.assertRaisesRegex(OracleViolation, "mutated its decision input state"):
            OracleHarness().run(MutatingAdapter(), scenario.poses, scenario.events)


class StreamIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = make_motion_scenario(
            "identity", (0, 1_000_000), (1_100_000, 1_200_000, 1_300_000), constant_rate(1.0)
        )
        self.receipt = OracleHarness().run(
            FallbackAdapter(), self.scenario.poses, self.scenario.events
        )

    def test_identity_order_and_exact_once_pass_for_sealed_stream(self) -> None:
        validate_identity_order_exact_once(self.scenario.events, self.receipt.decisions)
        self.assertEqual(
            [event.event_id for event in self.scenario.events],
            [decision.event_id for decision in self.receipt.decisions],
        )
        self.assertEqual(len(self.receipt.decision_digests), len(self.scenario.events))

    def test_missing_duplicate_and_reordered_outputs_are_killed(self) -> None:
        decisions = list(self.receipt.decisions)
        with self.assertRaises(OracleViolation):
            validate_identity_order_exact_once(self.scenario.events, decisions[:-1])
        with self.assertRaises(OracleViolation):
            validate_identity_order_exact_once(
                self.scenario.events, (decisions[0], decisions[0], decisions[2])
            )
        with self.assertRaises(OracleViolation):
            validate_identity_order_exact_once(
                self.scenario.events, (decisions[1], decisions[0], decisions[2])
            )

    def test_post_seal_identity_mutation_is_detectable(self) -> None:
        mutated = list(self.receipt.decisions)
        mutated[0] = replace(mutated[0], event_id="rewritten")
        with self.assertRaisesRegex(OracleViolation, "identity or order"):
            validate_identity_order_exact_once(self.scenario.events, mutated)

    def test_new_python_sources_parse_as_python_38(self) -> None:
        for path in sorted(HERE.glob("*.py")):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
