from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    BUFFER_ENTRIES,
    DELAYED_DEADLINE_CYCLES,
    Arm,
    CycleModelError,
    Event,
    PosePacket,
    run_cycle_model,
    run_delayed_unbounded_diagnostic,
    timestamp_to_cycle,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
WINDOW = "synthetic-stage4-window"
START = 0


def dataset_pose(pose_id, timestamp_ns):
    return PosePacket.dataset(
        pose_id,
        timestamp_ns,
        START,
        SHA_A if pose_id % 2 == 0 else SHA_B,
    )


def timestamp_for_cycle(cycle):
    timestamp_ns = (cycle * 6_500) // 1_000
    if timestamp_to_cycle(timestamp_ns, START) != cycle:
        raise AssertionError("test helper did not produce the requested cycle")
    return timestamp_ns


def bounded(events, poses):
    return run_cycle_model(
        window_id=WINDOW,
        window_start_ns=START,
        arm=Arm.DELAYED_EXACT,
        events=events,
        poses=poses,
        synthetic_test_mode=True,
    )


def unbounded(events, poses):
    return run_delayed_unbounded_diagnostic(
        window_id=WINDOW,
        window_start_ns=START,
        events=events,
        poses=poses,
        synthetic_test_mode=True,
    )


class DelayedUnboundedDiagnosticTests(unittest.TestCase):
    def assert_equivalent_below_pressure(self, bounded_result, evidence):
        self.assertEqual(
            [record.to_mapping() for record in bounded_result.records],
            [record.to_mapping() for record in evidence.records],
        )
        self.assertEqual(
            [receipt.to_mapping() for receipt in bounded_result.cycle_receipts],
            [receipt.to_mapping() for receipt in evidence.cycle_receipts],
        )
        self.assertEqual(
            bounded_result.decision_records_sha256,
            evidence.decision_records_sha256,
        )
        self.assertEqual(
            bounded_result.cycle_receipts_sha256,
            evidence.cycle_receipts_sha256,
        )
        self.assertEqual(
            bounded_result.common_serializer_cycles,
            evidence.common_serializer_cycles,
        )
        self.assertEqual(
            bounded_result.always_bypass_retire_cycles,
            evidence.always_bypass_retire_cycles,
        )
        self.assertEqual(
            bounded_result.policy_added_latency_cycles,
            evidence.policy_added_latency_cycles,
        )
        self.assertEqual(
            bounded_result.pose_ring_accounting,
            evidence.pose_ring_accounting,
        )

    def test_below_pressure_is_byte_equivalent_and_evidence_is_immutable(self):
        events = [Event(7, 13, causal_pose_index=0)]
        poses = [dataset_pose(0, 0), dataset_pose(1, 14)]
        bounded_result = bounded(events, poses)
        evidence = unbounded(events, poses)

        # These digests were captured from the bounded implementation before
        # the unbounded entry point was added. They protect bounded bytes.
        self.assertEqual(
            bounded_result.decision_records_sha256,
            "55ed8b193b61a18a23f0f1a59f9c5e506ca3d58ff280ae3c07634d36b956aa3e",
        )
        self.assertEqual(
            bounded_result.cycle_receipts_sha256,
            "37a4d297d0a8c19105e0b33d685ece145124a86bbe7fcd4f027242e23b602c83",
        )
        self.assert_equivalent_below_pressure(bounded_result, evidence)
        self.assertEqual(evidence.window_start_ns, START)
        self.assertEqual(evidence.input_events, tuple(events))
        self.assertEqual(evidence.input_poses, tuple(poses))
        self.assertEqual(evidence.input_pose_count, 2)
        self.assertEqual(len(evidence.input_events_sha256), 64)
        self.assertEqual(len(evidence.input_poses_sha256), 64)
        self.assertEqual(evidence.input_event_ids, (7,))
        self.assertEqual(evidence.retired_event_ids, (7,))
        self.assertTrue(evidence.exact_once_ordered_conservation)
        self.assertTrue(evidence.no_full_pressure_reasons)
        self.assertEqual(evidence.peak_fifo_depth, 1)
        self.assertEqual(len(evidence.config_identity_sha256), 64)
        self.assertEqual(len(evidence.evidence_sha256), 64)
        self.assertEqual(
            evidence.to_mapping()["evidence_sha256"],
            evidence.evidence_sha256,
        )
        evidence.validate()
        with self.assertRaises(FrozenInstanceError):
            evidence.peak_fifo_depth = 2

    def test_deadline_visibility_and_pipeline_match_bounded_below_pressure(self):
        occurrence_cycle = 2
        deadline = occurrence_cycle + DELAYED_DEADLINE_CYCLES
        for right_commit_cycle in (deadline - 1, deadline, deadline + 1):
            with self.subTest(right_commit_cycle=right_commit_cycle):
                events = [Event(1, 13, causal_pose_index=0)]
                poses = [
                    dataset_pose(0, 0),
                    dataset_pose(1, timestamp_for_cycle(right_commit_cycle)),
                ]
                bounded_result = bounded(events, poses)
                evidence = unbounded(events, poses)
                self.assert_equivalent_below_pressure(bounded_result, evidence)
                if right_commit_cycle == deadline - 1:
                    self.assertEqual(evidence.records[0].retire_cycle, deadline + 1)
                    self.assertEqual(
                        evidence.records[0].disposition_reason,
                        "bracket_interpolation",
                    )
                else:
                    self.assertEqual(evidence.records[0].retire_cycle, deadline)
                    self.assertEqual(
                        evidence.records[0].disposition_reason,
                        "deadline_timeout",
                    )

    def test_depth_above_1024_removes_only_full_pressure_action(self):
        events = []
        event_id = 0
        for cycle in range(2, 515):
            timestamp_ns = timestamp_for_cycle(cycle)
            events.extend(
                (
                    Event(event_id, timestamp_ns, causal_pose_index=0),
                    Event(event_id + 1, timestamp_ns, causal_pose_index=0),
                )
            )
            event_id += 2
        self.assertEqual(len(events), BUFFER_ENTRIES + 2)
        poses = [
            dataset_pose(0, 0),
            dataset_pose(1, timestamp_for_cycle(2_000)),
        ]

        bounded_result = bounded(events, poses)
        evidence = unbounded(events, poses)

        self.assertEqual(
            [record.disposition_reason for record in bounded_result.records[:2]],
            ["fifo_full_forced_bypass", "fifo_full_forced_bypass"],
        )
        self.assertEqual(evidence.peak_fifo_depth, BUFFER_ENTRIES + 2)
        self.assertEqual(evidence.input_event_ids, tuple(range(BUFFER_ENTRIES + 2)))
        self.assertEqual(evidence.retired_event_ids, evidence.input_event_ids)
        self.assertEqual(evidence.input_count, BUFFER_ENTRIES + 2)
        self.assertEqual(evidence.retired_count, BUFFER_ENTRIES + 2)
        self.assertTrue(evidence.exact_once_ordered_conservation)
        self.assertTrue(evidence.no_full_pressure_reasons)
        self.assertNotIn(
            "fifo_full_forced_bypass",
            [record.disposition_reason for record in evidence.records],
        )
        self.assertTrue(
            all(
                record.disposition_reason == "bracket_interpolation"
                for record in evidence.records
            )
        )
        self.assertEqual(
            [receipt.admission_cycle for receipt in bounded_result.cycle_receipts],
            [receipt.admission_cycle for receipt in evidence.cycle_receipts],
        )
        self.assertEqual(
            [receipt.admission_lane for receipt in bounded_result.cycle_receipts],
            [receipt.admission_lane for receipt in evidence.cycle_receipts],
        )
        self.assertEqual(
            evidence.config.removed_bounded_fifo_entries,
            BUFFER_ENTRIES,
        )
        self.assertEqual(
            evidence.config.fifo_policy,
            "unbounded_remove_only_fifo_full_pressure_action",
        )
        evidence.validate()

    def test_mutated_evidence_and_config_fail_closed(self):
        evidence = unbounded(
            [Event(7, 13, causal_pose_index=0)],
            [dataset_pose(0, 0), dataset_pose(1, 14)],
        )
        mutations = (
            replace(evidence, retired_event_ids=(8,)),
            replace(evidence, peak_fifo_depth=2),
            replace(evidence, no_full_pressure_reasons=False),
            replace(evidence, input_count=True),
            replace(evidence, window_start_ns=1),
            replace(
                evidence,
                input_events=(
                    replace(evidence.input_events[0], transform_guard_valid=False),
                ),
            ),
            replace(
                evidence,
                input_poses=(
                    replace(evidence.input_poses[0], pose_sha256=SHA_B),
                    evidence.input_poses[1],
                ),
            ),
            replace(
                evidence,
                config=replace(evidence.config, event_lanes=1),
            ),
            replace(evidence, decision_records_sha256="0" * 64),
            replace(evidence, pose_ring_accounting_sha256="0" * 64),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertNotEqual(mutation.evidence_sha256, evidence.evidence_sha256)
                with self.assertRaises(CycleModelError):
                    mutation.validate()

        mutable_container_mutation = replace(evidence, input_event_ids=[7])
        self.assertEqual(
            mutable_container_mutation.evidence_sha256,
            evidence.evidence_sha256,
        )
        with self.assertRaisesRegex(CycleModelError, "not immutable"):
            mutable_container_mutation.validate()

        pressure_record = replace(
            evidence.records[0],
            disposition="raw_bypass",
            disposition_reason="fifo_full_forced_bypass",
            intentional_future_pose_use=False,
            used_pose_ids=(0,),
            used_pose_timestamps_ns=(0,),
            used_pose_commit_cycles=(0,),
            used_pose_sha256=(SHA_A,),
        )
        pressure_mutation = replace(evidence, records=(pressure_record,))
        with self.assertRaises(CycleModelError):
            pressure_mutation.validate()

        guard_false_evidence = unbounded(
            [Event(7, 13, transform_guard_valid=False, causal_pose_index=0)],
            list(evidence.input_poses),
        )
        coherent_input_mutation = replace(
            evidence,
            input_events=guard_false_evidence.input_events,
            input_events_sha256=guard_false_evidence.input_events_sha256,
        )
        with self.assertRaisesRegex(CycleModelError, "replay evidence differs"):
            coherent_input_mutation.validate()

    def test_unused_pose_guard_and_window_start_are_evidence_bound(self):
        occurrence_cycle = 2
        deadline = occurrence_cycle + DELAYED_DEADLINE_CYCLES
        event = Event(7, 13, causal_pose_index=0)
        late_pose = dataset_pose(1, timestamp_for_cycle(deadline))
        base = unbounded([event], [dataset_pose(0, 0), late_pose])
        pose_mutated = unbounded(
            [event],
            [
                dataset_pose(0, 0),
                replace(late_pose, pose_sha256=SHA_A, value_valid=False),
            ],
        )
        guard_mutated = unbounded(
            [replace(event, transform_guard_valid=False)],
            [dataset_pose(0, 0), late_pose],
        )
        different_start = run_delayed_unbounded_diagnostic(
            window_id=WINDOW,
            window_start_ns=1,
            events=[],
            poses=[],
            synthetic_test_mode=True,
        )
        zero_start = unbounded([], [])

        self.assertEqual(base.records[0].disposition_reason, "deadline_timeout")
        self.assertEqual(
            base.decision_records_sha256,
            pose_mutated.decision_records_sha256,
        )
        self.assertEqual(
            base.decision_records_sha256,
            guard_mutated.decision_records_sha256,
        )
        self.assertNotEqual(base.input_poses_sha256, pose_mutated.input_poses_sha256)
        self.assertNotEqual(base.input_events_sha256, guard_mutated.input_events_sha256)
        self.assertNotEqual(base.evidence_sha256, pose_mutated.evidence_sha256)
        self.assertNotEqual(base.evidence_sha256, guard_mutated.evidence_sha256)
        self.assertNotEqual(zero_start.evidence_sha256, different_start.evidence_sha256)
        base.validate()
        pose_mutated.validate()
        guard_mutated.validate()
        different_start.validate()

    def test_input_mutation_changes_decision_and_evidence_hashes(self):
        poses = [dataset_pose(0, 0), dataset_pose(1, 14)]
        first = unbounded([Event(7, 13, causal_pose_index=0)], poses)
        second = unbounded([Event(8, 13, causal_pose_index=0)], poses)
        self.assertNotEqual(
            first.decision_records_sha256,
            second.decision_records_sha256,
        )
        self.assertNotEqual(first.evidence_sha256, second.evidence_sha256)
        self.assertEqual(
            first.config_identity_sha256,
            second.config_identity_sha256,
        )
        forbidden_keys = {"score", "loss", "quality", "ranking"}
        self.assertTrue(
            forbidden_keys.isdisjoint(first.to_mapping()),
            first.to_mapping().keys(),
        )


if __name__ == "__main__":
    unittest.main()
