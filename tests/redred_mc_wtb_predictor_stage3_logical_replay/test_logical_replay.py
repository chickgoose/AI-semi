from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ast
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import logical_cav_evaluator
from benchmarks.redred_mc_wtb_predictor_stage3 import logical_cycle_replay
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    FROZEN_LOGICAL_REPLAY_SHA256,
    FROZEN_STAGE4_EVALUATOR_SHA256,
    evaluate_current_cav_registry,
    verify_current_cav_evaluation_integrity,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    FROZEN_STAGE4_API_SHA256,
    FROZEN_STAGE4_MODEL_SHA256,
    LogicalCycleReplayError,
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
    logical_replay_authority,
    run_stage3_logical_cycle_model,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry as evaluate_frozen_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit import evaluator as canonical_evaluator
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    pose_timestamp_to_cycle,
    run_cycle_model,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import model as canonical_model


SHA_A = "a" * 64
SHA_B = "b" * 64
WINDOW_ID = "stage3-logical-isolation"


def _poses():
    return (
        PosePacket(10, 0, 0, PoseSource.DATASET, SHA_A),
        PosePacket(11, 6, 1, PoseSource.DATASET, SHA_B),
        PosePacket(12, 13, 2, PoseSource.DATASET, SHA_A),
    )


def _events(count):
    return tuple(Event(100 + index, 13, True, 11) for index in range(count))


def _logical(count):
    return run_stage3_logical_cycle_model(
        window_id=WINDOW_ID,
        window_start_ns=0,
        arm=Arm.CAUSAL_CAV,
        events=_events(count),
        poses=_poses(),
        synthetic_test_mode=True,
    )


def _decision_projection(result):
    return {
        "records": [record.to_mapping() for record in result.records],
        "decision_records_sha256": result.decision_records_sha256,
        "cycle_receipts": [receipt.to_mapping() for receipt in result.cycle_receipts],
        "cycle_receipts_sha256": result.cycle_receipts_sha256,
        "common_serializer_cycles": result.common_serializer_cycles,
        "always_bypass_retire_cycles": result.always_bypass_retire_cycles,
        "policy_added_latency_cycles": result.policy_added_latency_cycles,
        "peak_ingress_staging_occupancy": result.peak_ingress_staging_occupancy,
        "event_lanes": result.event_lanes,
        "pose_ring_accounting": result.pose_ring_accounting.to_mapping(),
        "pose_ring_accounting_sha256": result.pose_ring_accounting_sha256,
    }


def _neutral_event(event_id, timestamp_ns, is_query, pose_index):
    ray = (0.0, 1.0, 0.0) if is_query else (1.0, 0.0, 0.0)
    digest = canonical_event_content_sha256(
        event_id, timestamp_ns, 0, is_query, ray, pose_index
    )
    return NeutralEventInput(
        event_id, timestamp_ns, 0, is_query, ray, pose_index, digest
    )


def _neutral_pose(pose_id, timestamp_ns, quaternion, digest):
    expected = canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion)
    if digest != expected:
        digest = expected
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        digest,
    )


class LogicalReplayIsolationTests(unittest.TestCase):
    def test_frozen_files_and_canonical_globals_are_unchanged(self):
        model_path = Path(canonical_model.__file__).resolve()
        api_path = model_path.with_name("__init__.py")
        self.assertEqual(
            hashlib.sha256(model_path.read_bytes()).hexdigest(),
            FROZEN_STAGE4_MODEL_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(api_path.read_bytes()).hexdigest(),
            FROZEN_STAGE4_API_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                Path(canonical_evaluator.__file__).read_bytes()
            ).hexdigest(),
            FROZEN_STAGE4_EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                Path(
                    run_stage3_logical_cycle_model.__code__.co_filename
                ).read_bytes()
            ).hexdigest(),
            FROZEN_LOGICAL_REPLAY_SHA256,
        )
        self.assertIs(canonical_evaluator.run_cycle_model, run_cycle_model)
        self.assertEqual(
            (
                canonical_model.RAW_INGRESS_LANES,
                canonical_model.INGRESS_STAGING_ENTRIES,
                canonical_model.EVENT_LANES,
            ),
            (6, 6, 2),
        )
        authority = logical_replay_authority()
        self.assertEqual(authority["overrides"], {
            "RAW_INGRESS_LANES": 8,
            "INGRESS_STAGING_ENTRIES": 8,
        })
        self.assertFalse(authority["canonical_module_mutation"])

    def test_new_runtime_sources_parse_with_python38_grammar(self):
        sources = (
            Path(run_stage3_logical_cycle_model.__code__.co_filename),
            Path(evaluate_current_cav_registry.__code__.co_filename),
        )
        for source in sources:
            with self.subTest(source=source.name):
                ast.parse(source.read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_frozen_source_hash_mutations_fail_closed(self):
        with mock.patch.object(
            logical_cycle_replay, "_file_sha256", return_value="0" * 64
        ), self.assertRaises(LogicalCycleReplayError):
            logical_replay_authority()
        with mock.patch.object(
            logical_cav_evaluator, "_file_sha256", return_value="0" * 64
        ), self.assertRaises(logical_cav_evaluator.LogicalCAVEvaluatorError):
            logical_cav_evaluator.logical_evaluator_authority()

    def test_at_most_six_has_exact_decision_and_receipt_equivalence(self):
        for count in range(1, 7):
            with self.subTest(count=count):
                frozen = run_cycle_model(
                    window_id=WINDOW_ID,
                    window_start_ns=0,
                    arm=Arm.CAUSAL_CAV,
                    events=_events(count),
                    poses=_poses(),
                    synthetic_test_mode=True,
                )
                self.assertEqual(_decision_projection(_logical(count)),
                                 _decision_projection(frozen))

    def test_seven_and_eight_are_lossless_but_nine_is_rejected(self):
        for count in (7, 8):
            with self.subTest(count=count):
                result = _logical(count)
                self.assertEqual(
                    [record.event_id for record in result.records],
                    list(range(100, 100 + count)),
                )
                self.assertEqual(result.peak_ingress_staging_occupancy, count)
                self.assertEqual(
                    (result.raw_ingress_lanes,
                     result.ingress_staging_entries,
                     result.event_lanes),
                    (8, 8, 2),
                )
                self.assertEqual(
                    result.__class__.__module__,
                    "benchmarks.redred_mc_wtb_predictor_stage3."
                    "_stage3_private_stage4_cyclemodel",
                )
        with self.assertRaises(CycleModelError):
            _logical(9)
        with self.assertRaises(CycleModelError):
            run_cycle_model(
                window_id=WINDOW_ID,
                window_start_ns=0,
                arm=Arm.CAUSAL_CAV,
                events=_events(7),
                poses=_poses(),
                synthetic_test_mode=True,
            )

    def test_eight_entry_staging_is_lossless_and_ninth_slot_rejects(self):
        first_edge = _events(8)
        two_next_edge = tuple(Event(108 + index, 14, True, 12)
                              for index in range(2))
        result = run_stage3_logical_cycle_model(
            window_id=WINDOW_ID,
            window_start_ns=0,
            arm=Arm.CAUSAL_CAV,
            events=first_edge + two_next_edge,
            poses=_poses(),
            synthetic_test_mode=True,
        )
        self.assertEqual(result.peak_ingress_staging_occupancy, 8)
        self.assertEqual([row.event_id for row in result.records],
                         list(range(100, 110)))
        three_next_edge = tuple(Event(108 + index, 14, True, 12)
                                for index in range(3))
        with self.assertRaisesRegex(CycleModelError, "staging overflow"):
            run_stage3_logical_cycle_model(
                window_id=WINDOW_ID,
                window_start_ns=0,
                arm=Arm.CAUSAL_CAV,
                events=first_edge + three_next_edge,
                poses=_poses(),
                synthetic_test_mode=True,
            )

    def test_strict_past_and_same_edge_cluster_are_atomic(self):
        result = _logical(8)
        self.assertEqual([row.occurrence_cycle for row in result.records], [2] * 8)
        self.assertEqual([row.occurrence_pose_ids for row in result.records],
                         [(10, 11)] * 8)
        self.assertNotIn(12, tuple(
            pose_id for row in result.records for pose_id in row.occurrence_pose_ids
        ))
        self.assertEqual([row.retire_cycle for row in result.records],
                         [3, 3, 4, 4, 5, 5, 6, 6])

    def test_concurrent_runs_do_not_mutate_canonical_authority(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(_logical, (1, 2, 3, 4, 5, 6, 7, 8)))
        self.assertEqual([len(result.records) for result in results],
                         list(range(1, 9)))
        self.assertEqual(
            (canonical_model.RAW_INGRESS_LANES,
             canonical_model.INGRESS_STAGING_ENTRIES,
             canonical_model.EVENT_LANES),
            (6, 6, 2),
        )

    def test_closed_profile_and_arm_cannot_be_substituted(self):
        self.assertEqual(
            STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.to_mapping()["scope"],
            "MODEL_ONLY_LOGICAL_REPLAY_NO_RTL_OR_PPA_CLAIM",
        )
        with self.assertRaises(LogicalCycleReplayError):
            run_stage3_logical_cycle_model(
                window_id=WINDOW_ID,
                window_start_ns=0,
                arm=Arm.ZOH_FRESHNESS,
                events=_events(1),
                poses=_poses(),
                synthetic_test_mode=True,
            )

    def test_private_evaluator_accepts_eight_and_integrity_replays(self):
        registry = NeutralRegistryWindow(WINDOW_ID, 0, 13, 20)
        quaternion = (0.0, 0.0, 0.0, 1.0)
        poses = (
            _neutral_pose(10, 0, quaternion, SHA_A),
            _neutral_pose(11, 6, quaternion, SHA_B),
        )
        events = (_neutral_event(1, 6, False, 10),) + tuple(
            _neutral_event(100 + index, 13, True, 11) for index in range(8)
        )
        with self.assertRaises(CycleModelError):
            evaluate_frozen_current_cav_registry(
                (registry,), {WINDOW_ID: events}, {WINDOW_ID: poses}
            )
        result = evaluate_current_cav_registry(
            (registry,), {WINDOW_ID: events}, {WINDOW_ID: poses}
        )
        self.assertEqual(result.accepted_events, 8)
        self.assertEqual(result.windows[0].simulation.raw_ingress_lanes, 8)
        self.assertEqual(
            result.__class__.__module__,
            "benchmarks.redred_mc_wtb_predictor_stage3."
            "_stage3_private_cav_evaluator",
        )
        self.assertEqual(
            verify_current_cav_evaluation_integrity(result),
            result.neutral_input_sha256,
        )


if __name__ == "__main__":
    unittest.main()
