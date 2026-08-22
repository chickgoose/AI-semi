from __future__ import annotations

from dataclasses import replace
import math
import unittest

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.framework import (
    DecisionRoute,
    PredictionOutput,
    PredictorDecision,
)
from benchmarks.redred_mc_wtb_predictor_stage3.output_common import (
    CANDIDATE_OUTPUT_SCHEMA,
    CandidateOutputError,
    build_candidate_output_window,
    seal_candidate_output_envelope,
)
from benchmarks.redred_mc_wtb_predictor_stage3.screen108 import (
    Screen108Error,
    seal_candidate_output,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import DecisionRecord


IDENTITY = (0.0, 0.0, 0.0, 1.0)
QUARTER_Z = (0.0, 0.0, math.sin(math.pi / 4.0), math.cos(math.pi / 4.0))
RAY_X = (1.0, 0.0, 0.0)
CONFIG_SHA = "a" * 64
STATE_SHA = "b" * 64
WINDOW_ID = "shapes_rotation/query_start_ns=123"


def pose(pose_id, timestamp_ns, commit_cycle, quaternion):
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        commit_cycle,
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def event(event_id, timestamp_ns, is_query):
    digest = canonical_event_content_sha256(
        event_id, timestamp_ns, event_id % 2, is_query, RAY_X, 1
    )
    return NeutralEventInput(
        event_id, timestamp_ns, event_id % 2, is_query, RAY_X, 1, digest
    )


def cycle(event_value, poses, *, edge=3, reason="causal_cav"):
    occurrence = tuple(poses)
    if reason == "causal_cav":
        used = occurrence
        disposition = "corrected_world_ray"
    elif reason == "fresh_zoh_fallback":
        used = occurrence[-1:]
        disposition = "corrected_world_ray"
    else:
        used = occurrence[-1:]
        disposition = "raw_bypass"
    return DecisionRecord(
        WINDOW_ID,
        event_value.event_id,
        event_value.timestamp_ns,
        "causal_cav",
        "CAUSAL_CAV",
        edge,
        edge + 4,
        tuple(value.pose_id for value in occurrence),
        tuple(value.timestamp_ns for value in occurrence),
        tuple(value.commit_cycle for value in occurrence),
        tuple(value.pose_sha256 for value in occurrence),
        tuple(value.pose_id for value in used),
        tuple(value.timestamp_ns for value in used),
        tuple(value.commit_cycle for value in used),
        tuple(value.pose_sha256 for value in used),
        False,
        event_value.timestamp_ns - used[-1].timestamp_ns,
        disposition,
        reason,
        0,
    )


def prediction(
    event_value,
    quaternion,
    route,
    used_ids,
    *,
    version=0,
    state_sha=STATE_SHA,
    trace=(),
    edge=3,
):
    return PredictorDecision(
        event_value.event_id,
        event_value.timestamp_ns,
        edge - 1,
        edge,
        event_value.is_query,
        "candidate-model",
        CONFIG_SHA,
        version,
        state_sha,
        route,
        route in (DecisionRoute.CANDIDATE, DecisionRoute.CURRENT_CAV),
        route is DecisionRoute.CANDIDATE,
        PredictionOutput(quaternion),
        tuple(used_ids),
        tuple(trace),
    )


class CandidateOutputCommonTests(unittest.TestCase):
    def setUp(self):
        self.poses = (
            pose(0, 0, 0, IDENTITY),
            pose(1, 100, 1, QUARTER_Z),
        )
        self.events = (
            event(10, 150, False),
            event(11, 150, True),
            event(12, 160, True),
        )
        self.cycles = tuple(cycle(value, self.poses) for value in self.events)
        recovered = recover_causal_cav(
            tuple(
                PoseSample(
                    row.timestamp_ns, row.commit_cycle, row.quaternion_xyzw
                )
                for row in self.poses
            ),
            self.events[-1].timestamp_ns,
            3,
        )
        self.assertIsNotNone(recovered.quaternion_xyzw)
        self.predictions = (
            prediction(
                self.events[0], QUARTER_Z, DecisionRoute.CANDIDATE, (0, 1)
            ),
            prediction(
                self.events[1], QUARTER_Z, DecisionRoute.CANDIDATE, (0, 1)
            ),
            prediction(
                self.events[2],
                recovered.quaternion_xyzw,
                DecisionRoute.CURRENT_CAV,
                (0, 1),
                version=1,
                state_sha="c" * 64,
                trace=("candidate:test_unavailable",),
            ),
        )

    def test_exact_world_rays_ordered_q_and_current_cav_fallback(self):
        output = build_candidate_output_window(
            WINDOW_ID, self.events, self.poses, self.cycles, self.predictions
        )

        self.assertEqual(output.ordered_event_ids, (10, 11, 12))
        self.assertEqual(output.ordered_query_event_ids, (11, 12))
        self.assertEqual(
            output.ordered_query_ids_sha256, canonical_sha256([11, 12])
        )
        expected = rotate_sensor_ray_to_world(QUARTER_Z, RAY_X)
        for receipt in output.events[:2]:
            self.assertEqual(receipt.world_ray, expected)
            self.assertTrue(receipt.candidate_used)
            self.assertEqual(receipt.used_pose_ids, (0, 1))
            self.assertEqual(
                receipt.decision_sha256,
                canonical_sha256(receipt.to_mapping(False)),
            )
        fallback = output.events[2]
        self.assertFalse(fallback.candidate_used)
        self.assertEqual(fallback.model_id, "CURRENT_CAV")
        self.assertIsNone(fallback.world_ray)
        self.assertEqual(fallback.used_pose_ids, (0, 1))
        self.assertEqual(fallback.fallback_reason, "candidate:test_unavailable")

    def test_legacy_common_envelope_cannot_enter_screen_v2(self):
        window = build_candidate_output_window(
            WINDOW_ID, self.events, self.poses, self.cycles, self.predictions
        )
        args = (
            "candidate-model",
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
        )
        common = seal_candidate_output_envelope(*args, (window,))
        with self.assertRaisesRegex(Screen108Error, "field schema differs"):
            seal_candidate_output(*args, [window.to_unsealed_mapping()])
        self.assertEqual(common["schema"], CANDIDATE_OUTPUT_SCHEMA)
        self.assertEqual(
            common["aggregate_sha256"],
            canonical_sha256({
                key: value
                for key, value in common.items()
                if key != "aggregate_sha256"
            }),
        )

    def test_equal_timestamp_cluster_must_keep_state_version_and_digest(self):
        changed = replace(
            self.predictions[1],
            state_version_id=1,
            state_sha256="d" * 64,
        )
        with self.assertRaisesRegex(
            CandidateOutputError,
            "equal-timestamp events changed predictor state or edge",
        ):
            build_candidate_output_window(
                WINDOW_ID,
                self.events,
                self.poses,
                self.cycles,
                (self.predictions[0], changed, self.predictions[2]),
            )

    def test_occurrence_decision_edge_and_order_are_immutable(self):
        wrong_edge = prediction(
            self.events[0],
            QUARTER_Z,
            DecisionRoute.CANDIDATE,
            (0, 1),
            edge=4,
        )
        with self.assertRaisesRegex(CandidateOutputError, "cycle-model occurrence edge"):
            build_candidate_output_window(
                WINDOW_ID,
                self.events,
                self.poses,
                self.cycles,
                (wrong_edge, self.predictions[1], self.predictions[2]),
            )
        with self.assertRaisesRegex(CandidateOutputError, "identity or Q membership"):
            build_candidate_output_window(
                WINDOW_ID,
                self.events,
                self.poses,
                self.cycles,
                (self.predictions[1], self.predictions[0], self.predictions[2]),
            )

    def test_used_pose_causality_and_current_cav_value_fail_closed(self):
        same_edge_pose = pose(2, 120, 3, IDENTITY)
        bad_prediction = prediction(
            self.events[2],
            IDENTITY,
            DecisionRoute.CURRENT_CAV,
            (0, 1),
            version=1,
            state_sha="c" * 64,
            trace=("candidate:test_unavailable",),
        )
        with self.assertRaisesRegex(CandidateOutputError, "quaternion differs"):
            build_candidate_output_window(
                WINDOW_ID,
                self.events,
                self.poses,
                self.cycles,
                (self.predictions[0], self.predictions[1], bad_prediction),
            )
        bad_cycle = cycle(self.events[2], self.poses + (same_edge_pose,))
        with self.assertRaisesRegex(CandidateOutputError, "occurrence pose evidence"):
            build_candidate_output_window(
                WINDOW_ID,
                self.events,
                self.poses + (same_edge_pose,),
                (self.cycles[0], self.cycles[1], bad_cycle),
                self.predictions,
            )

    def test_fresh_zoh_and_sensor_fixed_remain_exact_baseline_routes(self):
        zoh_event = event(20, 150, False)
        bypass_event = event(21, 2_000_000, True)
        zoh_cycle = cycle(
            zoh_event, self.poses[-1:], reason="fresh_zoh_fallback"
        )
        bypass_cycle = cycle(
            bypass_event, self.poses[-1:], reason="stale_pose"
        )
        zoh = prediction(
            zoh_event,
            self.poses[-1].quaternion_xyzw,
            DecisionRoute.FRESH_ZOH,
            (1,),
            trace=("current_cav:unavailable",),
        )
        bypass = prediction(
            bypass_event,
            None,
            DecisionRoute.SENSOR_FIXED,
            (),
            trace=(
                "current_cav:unavailable",
                "fresh_zoh:missing_or_stale_pose",
            ),
        )

        output = build_candidate_output_window(
            WINDOW_ID,
            (zoh_event, bypass_event),
            self.poses[-1:],
            (zoh_cycle, bypass_cycle),
            (zoh, bypass),
        )

        self.assertEqual(
            tuple(receipt.model_id for receipt in output.events),
            ("CURRENT_CAV", "CURRENT_CAV"),
        )
        self.assertEqual(
            tuple(receipt.used_pose_ids for receipt in output.events),
            ((1,), ()),
        )
        self.assertEqual(
            tuple(receipt.world_ray for receipt in output.events), (None, None)
        )


if __name__ == "__main__":
    unittest.main()
