from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    CandidateAttempt,
    CandidateModel,
    DecisionRoute,
    EventEnvelope,
    FallbackAttempt,
    FallbackHooks,
    PoseEnvelope,
    PredictorFrameworkError,
    StateUpdate,
    run_candidate_neutral_predictor,
    verify_predictor_run_integrity,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


IDENTITY = (0.0, 0.0, 0.0, 1.0)
HALF_TURN_Z = (0.0, 0.0, 1.0, 0.0)
RAY_X = (1.0, 0.0, 0.0)


class RecordingModel(CandidateModel):
    def __init__(self, update_every_cycle=True):
        self.update_every_cycle = update_every_cycle
        self.predict_calls = []
        self.commit_calls = []

    @property
    def model_id(self):
        return "candidate-neutral-test"

    @property
    def configuration_sha256(self):
        return "a" * 64

    def initial_state_payload(self):
        return b"initial"

    def predict(self, event, state):
        self.predict_calls.append((event, state))
        if event.polarity == 1:
            return CandidateAttempt.failure("test_unavailable")
        slots = (len(event.visible_poses) - 1,) if event.visible_poses else ()
        return CandidateAttempt.success(IDENTITY, slots)

    def commit_cycle(self, observations, state):
        self.commit_calls.append((observations, state))
        if not self.update_every_cycle:
            return None
        return StateUpdate(
            ("state-%d" % len(self.commit_calls)).encode("ascii"),
            "cycle_commit",
        )


def event(
    event_id,
    timestamp_ns,
    occurrence_cycle,
    decision_cycle,
    *,
    polarity=0,
    is_query=True
):
    return EventEnvelope(
        event_id,
        timestamp_ns,
        occurrence_cycle,
        decision_cycle,
        polarity,
        RAY_X,
        is_query,
    )


def pose(pose_id, timestamp_ns, commit_cycle):
    return PoseEnvelope(pose_id, timestamp_ns, commit_cycle, IDENTITY)


def always_cav(context):
    used = (context.visible_poses[-1].pose_id,) if context.visible_poses else ()
    return FallbackAttempt.success(IDENTITY, used)


def always_zoh(context):
    used = (context.visible_poses[-1].pose_id,) if context.visible_poses else ()
    return FallbackAttempt.success(IDENTITY, used)


class CommonFrameworkTests(unittest.TestCase):
    def test_ordered_q_is_exact_and_candidate_view_excludes_identity(self):
        model = RecordingModel(update_every_cycle=False)
        events = (
            event(10, 100, 0, 1, is_query=False),
            event(11, 200, 1, 2),
            event(12, 300, 2, 3),
        )
        result = run_candidate_neutral_predictor(
            model,
            events,
            (pose(7, 0, 0),),
            FallbackHooks(always_cav, always_zoh),
        )

        self.assertEqual(result.ordered_event_ids, (10, 11, 12))
        self.assertEqual(result.ordered_query_event_ids, (11, 12))
        self.assertEqual(
            tuple(row.event_id for row in result.query_decisions), (11, 12)
        )
        self.assertEqual(len(result.decisions), len(events))
        for view, state in model.predict_calls:
            for forbidden in (
                "event_id",
                "timestamp_ns",
                "decision_cycle",
                "is_query",
                "window_id",
                "sequence_id",
                "score",
                "loss",
            ):
                self.assertFalse(hasattr(view, forbidden), forbidden)
            for forbidden in (
                "version_id",
                "effective_cycle",
                "state_sha256",
                "model_id",
                "transition_reason",
            ):
                self.assertFalse(hasattr(state, forbidden), forbidden)
            self.assertIsInstance(state.payload, bytes)
        self.assertRegex(
            verify_predictor_run_integrity(result, events, (pose(7, 0, 0),)),
            r"^[0-9a-f]{64}$",
        )

    def test_pose_and_event_effects_publish_only_on_following_cycle(self):
        model = RecordingModel()
        events = (
            event(20, 100, 1, 2),
            event(21, 100, 1, 2),
            event(22, 200, 2, 3),
        )
        result = run_candidate_neutral_predictor(
            model,
            events,
            (pose(8, 0, 1),),
            FallbackHooks(always_cav, always_zoh),
        )

        # The pose commit at cycle 1 publishes version 1 at cycle 2. Both
        # equal-timestamp events consume it; their cycle-2 update is visible
        # only to the cycle-3 event as version 2.
        self.assertEqual(
            tuple(row.state_version_id for row in result.decisions), (1, 1, 2)
        )
        self.assertEqual(
            result.decisions[0].state_sha256, result.decisions[1].state_sha256
        )
        self.assertNotEqual(
            result.decisions[1].state_sha256, result.decisions[2].state_sha256
        )
        self.assertEqual(
            tuple(state.effective_cycle for state in result.state_versions),
            (0, 2, 3, 4),
        )
        self.assertEqual(
            result.cycle_state_receipts[0].pose_commit_ids, (8,)
        )
        self.assertEqual(
            result.cycle_state_receipts[1].event_cluster_ids, ((20, 21),)
        )
        self.assertEqual(
            result.cycle_state_receipts[1].next_state_effective_cycle, 3
        )
        self.assertEqual(
            tuple(row.used_pose_ids for row in result.decisions),
            ((8,), (8,), (8,)),
        )

    def test_same_edge_pose_is_invisible_and_cannot_enable_candidate(self):
        model = RecordingModel(update_every_cycle=False)

        def cav_requires_pose(context):
            if not context.visible_poses:
                return FallbackAttempt.failure("no_visible_pose")
            return FallbackAttempt.success(
                IDENTITY, (context.visible_poses[-1].pose_id,)
            )

        events = (
            event(30, 100, 1, 2),
            event(31, 200, 2, 3),
        )
        result = run_candidate_neutral_predictor(
            model,
            events,
            (pose(9, 0, 2),),
            FallbackHooks(cav_requires_pose, always_zoh),
        )

        self.assertEqual(
            tuple(row.route for row in result.decisions),
            (DecisionRoute.SENSOR_FIXED, DecisionRoute.CANDIDATE),
        )
        self.assertFalse(result.decisions[0].candidate_attempted)
        self.assertTrue(result.decisions[1].candidate_attempted)
        self.assertEqual(len(model.predict_calls), 1)

    def test_exact_fallback_chain_and_one_millisecond_zoh_limit(self):
        model = RecordingModel(update_every_cycle=False)
        calls = []

        def current(context):
            calls.append((context.event.event_id, "cav"))
            if context.event.event_id in (42, 43):
                return FallbackAttempt.failure("cav_unavailable")
            return FallbackAttempt.success(HALF_TURN_Z, (5,))

        def zoh(context):
            calls.append((context.event.event_id, "zoh"))
            return FallbackAttempt.success(IDENTITY, (5,))

        def sensor(context):
            calls.append((context.event.event_id, "sensor"))
            return FallbackAttempt.success(None)

        events = (
            event(40, 100, 0, 1, polarity=0),
            event(41, 200, 1, 2, polarity=1),
            event(42, 500_000, 2, 3, polarity=0),
            event(43, 2_000_000, 3, 4, polarity=0),
        )
        result = run_candidate_neutral_predictor(
            model,
            events,
            (pose(5, 0, 0),),
            FallbackHooks(current, zoh, sensor),
        )

        self.assertEqual(
            tuple(row.route for row in result.decisions),
            (
                DecisionRoute.CANDIDATE,
                DecisionRoute.CURRENT_CAV,
                DecisionRoute.FRESH_ZOH,
                DecisionRoute.SENSOR_FIXED,
            ),
        )
        self.assertEqual(result.decisions[1].fallback_trace, (
            "candidate:test_unavailable",
        ))
        self.assertEqual(
            result.decisions[1].output.quaternion_xyzw, HALF_TURN_Z
        )
        self.assertEqual(result.decisions[2].output.quaternion_xyzw, IDENTITY)
        self.assertEqual(result.decisions[2].fallback_trace, (
            "current_cav:cav_unavailable",
        ))
        self.assertEqual(result.decisions[3].fallback_trace, (
            "current_cav:cav_unavailable",
            "fresh_zoh:missing_or_stale_pose",
        ))
        self.assertNotIn((43, "zoh"), calls)
        self.assertIn((43, "sensor"), calls)
        self.assertNotIn((41, "zoh"), calls)

    def test_equal_timestamp_cluster_with_different_edges_fails_closed(self):
        events = (
            event(50, 100, 0, 1),
            event(51, 100, 1, 2),
        )
        with self.assertRaisesRegex(PredictorFrameworkError, "equal-timestamp"):
            run_candidate_neutral_predictor(
                RecordingModel(False),
                events,
                (),
                FallbackHooks(always_cav, always_zoh),
            )

    def test_candidate_cannot_name_a_future_or_invisible_pose(self):
        class InvalidSlotModel(RecordingModel):
            def predict(self, event_view, state):
                return CandidateAttempt.success(IDENTITY, (len(event_view.visible_poses),))

        with self.assertRaisesRegex(PredictorFrameworkError, "invisible pose slot"):
            run_candidate_neutral_predictor(
                InvalidSlotModel(False),
                (event(60, 100, 0, 1),),
                (),
                FallbackHooks(always_cav, always_zoh),
            )

    def test_fresh_zoh_hook_must_return_exact_latest_pose(self):
        def unavailable(_context):
            return FallbackAttempt.failure("unavailable")

        def forged_zoh(_context):
            return FallbackAttempt.success((0.0, 0.0, 1.0, 0.0), ())

        with self.assertRaisesRegex(PredictorFrameworkError, "latest visible pose"):
            run_candidate_neutral_predictor(
                RecordingModel(False),
                (event(65, 100, 0, 1),),
                (pose(6, 0, 0),),
                FallbackHooks(unavailable, forged_zoh),
            )

    def test_integrity_verifier_rejects_stale_decision_digest(self):
        events = (event(70, 100, 0, 1),)
        result = run_candidate_neutral_predictor(
            RecordingModel(False),
            events,
            (),
            FallbackHooks(always_cav, always_zoh),
        )
        changed_decision = replace(
            result.decisions[0], fallback_trace=("forged",)
        )
        changed = replace(result, decisions=(changed_decision,))
        with self.assertRaisesRegex(PredictorFrameworkError, "digest differs"):
            verify_predictor_run_integrity(changed, events, ())

        stale_inner = result.decisions[0]
        object.__setattr__(stale_inner, "fallback_trace", ("forged",))
        resealed = replace(
            result,
            decision_records_sha256=canonical_sha256([stale_inner.to_mapping()]),
        )
        with self.assertRaisesRegex(PredictorFrameworkError, "content digest"):
            verify_predictor_run_integrity(resealed, events, ())

    def test_candidate_exception_is_protocol_failure_not_silent_fallback(self):
        class RaisingModel(RecordingModel):
            def predict(self, event_view, state):
                raise RuntimeError("candidate bug")

        with self.assertRaisesRegex(RuntimeError, "candidate bug"):
            run_candidate_neutral_predictor(
                RaisingModel(False),
                (event(80, 100, 0, 1),),
                (),
                FallbackHooks(always_cav, always_zoh),
            )

    def test_sources_parse_with_python38_grammar(self):
        root = Path(__file__).resolve().parents[2]
        for relative in (
            "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
            "tests/redred_mc_wtb_predictor_stage3_common/test_framework.py",
        ):
            with self.subTest(relative=relative):
                ast.parse(
                    (root / relative).read_text(encoding="utf-8"),
                    filename=relative,
                    feature_version=(3, 8),
                )


if __name__ == "__main__":
    unittest.main()
