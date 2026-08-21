from __future__ import annotations

from dataclasses import replace
import unittest

from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    ARM_LABELS,
    BUFFER_ENTRIES,
    CAUSAL_POSE_INDEX_BITS,
    CAUSAL_POSE_INDEX_LIMIT,
    DELAYED_DEADLINE_CYCLES,
    INGRESS_STAGING_ENTRIES,
    POSE_RING_ENTRIES,
    POSE_RING_STATE_BITS,
    POSE_ID_GAPS_ALLOWED,
    RAW_INGRESS_LANES,
    Arm,
    CycleModelError,
    Event,
    PosePacket,
    PoseRingSafetyError,
    PoseSource,
    ceil_div,
    pose_timestamp_to_cycle,
    pose_ring_slot,
    run_cycle_model,
    signed_ceil_div,
    timestamp_to_cycle,
)
from benchmarks.redred_mc_wtb_stage4_contract.receipt import (
    ARM_LABELS as RECEIPT_ARM_LABELS,
    DELAYED_RAW_REASONS as RECEIPT_DELAYED_RAW_REASONS,
    DecisionRecord as ReceiptDecisionRecord,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
WINDOW = "synthetic-stage4-window"
START = 0


def dataset_pose(
    pose_id: int,
    timestamp_ns: int,
    *,
    value_valid: bool = True,
    arithmetic_valid: bool = True,
) -> PosePacket:
    return PosePacket.dataset(
        pose_id,
        timestamp_ns,
        START,
        SHA_A if pose_id % 2 == 0 else SHA_B,
        value_valid=value_valid,
        arithmetic_valid=arithmetic_valid,
    )


def timestamp_for_cycle(cycle: int) -> int:
    timestamp_ns = (cycle * 6_500) // 1_000
    if timestamp_to_cycle(timestamp_ns, START) != cycle:
        raise AssertionError("test helper did not produce the requested cycle")
    return timestamp_ns


def simulate(arm, events, poses):
    return run_cycle_model(
        window_id=WINDOW,
        window_start_ns=START,
        arm=arm,
        events=events,
        poses=poses,
        synthetic_test_mode=True,
    )


class IntegerTimingTests(unittest.TestCase):
    def test_timestamp_to_cycle_is_exact_ceiling_integer_math(self):
        self.assertEqual(timestamp_to_cycle(0, 0), 0)
        self.assertEqual(timestamp_to_cycle(6, 0), 1)
        self.assertEqual(timestamp_to_cycle(7, 0), 2)
        self.assertEqual(timestamp_to_cycle(13, 0), 2)
        self.assertEqual(timestamp_to_cycle(14, 0), 3)
        self.assertEqual(ceil_div(6_000_000 * 1_000, 6_500), 923_077)
        self.assertEqual(DELAYED_DEADLINE_CYCLES, 923_077)

    def test_pose_cycles_allow_signed_pre_window_history_only(self):
        self.assertEqual(pose_timestamp_to_cycle(80, 100), -3)
        self.assertEqual(pose_timestamp_to_cycle(90, 100), -1)
        self.assertEqual(signed_ceil_div(-20_000, 6_500), -3)
        self.assertEqual(signed_ceil_div(-10_000, 6_500), -1)
        with self.assertRaisesRegex(CycleModelError, "precedes window"):
            timestamp_to_cycle(90, 100)

        poses = [
            PosePacket.dataset(10, 80, 100, SHA_A),
            PosePacket.dataset(11, 90, 100, SHA_B),
        ]
        result = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=100,
            arm=Arm.CAUSAL_CAV,
            events=[Event(1, 100)],
            poses=poses,
            synthetic_test_mode=True,
        )
        record = result.records[0]
        self.assertEqual(record.occurrence_cycle, 0)
        self.assertEqual(record.occurrence_pose_commit_cycles, (-3, -1))
        self.assertEqual(record.used_pose_ids, (10, 11))
        self.assertEqual(record.retire_cycle, 1)

    def test_integer_api_rejects_bool_negative_and_pre_window_time(self):
        with self.assertRaises(CycleModelError):
            timestamp_to_cycle(True, 0)
        with self.assertRaises(CycleModelError):
            timestamp_to_cycle(9, 10)
        with self.assertRaises(CycleModelError):
            ceil_div(1, 0)


class CausalArmTests(unittest.TestCase):
    def test_same_edge_pose_is_invisible_and_equal_timestamp_snapshot_is_atomic(self):
        poses = [dataset_pose(10, 0), dataset_pose(11, 13)]
        events = [Event(100 + index, 13) for index in range(5)]
        result = simulate(Arm.ZOH_FRESHNESS, events, poses)

        self.assertEqual(
            [record.occurrence_cycle for record in result.records],
            [2, 2, 2, 2, 2],
        )
        self.assertEqual(
            [record.retire_cycle for record in result.records],
            [3, 3, 4, 4, 5],
        )
        self.assertEqual([record.queue_cycles for record in result.records], [0] * 5)
        self.assertEqual(
            [record.occurrence_pose_ids for record in result.records],
            [(10,)] * 5,
        )
        self.assertEqual([record.used_pose_ids for record in result.records], [(10,)] * 5)
        self.assertEqual(
            [record.event_id for record in result.records],
            [100, 101, 102, 103, 104],
        )
        self.assertEqual(result.common_serializer_cycles, (0, 0, 1, 1, 2))
        self.assertEqual(result.always_bypass_retire_cycles, (3, 3, 4, 4, 5))
        self.assertEqual(result.policy_added_latency_cycles, (0, 0, 0, 0, 0))
        self.assertEqual(result.peak_ingress_staging_occupancy, 5)
        self.assertEqual(result.raw_ingress_lanes, RAW_INGRESS_LANES)
        self.assertEqual(result.ingress_staging_entries, INGRESS_STAGING_ENTRIES)
        self.assertEqual(result.event_record_bits, 102)
        self.assertEqual(
            result.causal_pose_index_bits_in_event_record,
            CAUSAL_POSE_INDEX_BITS,
        )
        self.assertEqual(
            [receipt.admission_lane for receipt in result.cycle_receipts],
            [0, 1, 0, 1, 0],
        )
        self.assertEqual(
            [receipt.launch_cycle for receipt in result.cycle_receipts],
            [2, 2, 3, 3, 4],
        )
        self.assertEqual(
            [receipt.retire_lane for receipt in result.cycle_receipts],
            [0, 1, 0, 1, 0],
        )

    def test_zoh_age_limit_is_inclusive_and_bypass_is_still_retired(self):
        events = [Event(1, 1_000_000), Event(2, 1_000_001)]
        result = simulate(Arm.ZOH_FRESHNESS, events, [dataset_pose(10, 0)])
        first, second = result.records
        self.assertEqual(first.disposition, "corrected_world_ray")
        self.assertEqual(first.pose_age_ns, 1_000_000)
        self.assertEqual(second.disposition, "raw_bypass")
        self.assertEqual(second.disposition_reason, "stale_pose")
        self.assertGreaterEqual(second.retire_cycle, second.occurrence_cycle + 1)

    def test_cav_uses_only_two_occurrence_poses_then_falls_back_to_zoh(self):
        poses = [dataset_pose(10, 0), dataset_pose(11, 1_000_000)]
        events = [
            Event(1, 1_500_000),
            Event(2, 1_600_000, transform_guard_valid=False),
            Event(3, 2_000_000),
            Event(4, 2_000_001),
        ]
        result = simulate(Arm.CAUSAL_CAV, events, poses)
        cav, fallback, horizon_edge, bypass = result.records
        self.assertEqual(cav.used_pose_ids, (10, 11))
        self.assertEqual(cav.disposition_reason, "causal_cav")
        self.assertEqual(fallback.used_pose_ids, (11,))
        self.assertEqual(fallback.disposition_reason, "fresh_zoh_fallback")
        self.assertEqual(horizon_edge.pose_age_ns, 1_000_000)
        self.assertEqual(horizon_edge.disposition_reason, "causal_cav")
        self.assertEqual(bypass.disposition, "raw_bypass")
        self.assertEqual(bypass.disposition_reason, "stale_pose")

    def test_oracle_commit_and_visibility_delays_are_both_observed(self):
        oracle = PosePacket.oracle_1khz(0, 0, START, SHA_A)
        self.assertEqual(oracle.commit_cycle, 1)
        events = [Event(1, 6), Event(2, 13)]
        result = simulate(Arm.ORACLE_1KHZ, events, [oracle])
        self.assertEqual(result.records[0].occurrence_cycle, 1)
        self.assertEqual(result.records[0].disposition_reason, "no_occurrence_pose")
        self.assertEqual(result.records[1].occurrence_cycle, 2)
        self.assertEqual(result.records[1].used_pose_ids, (0,))
        self.assertEqual(result.records[1].disposition_reason, "oracle_fresh_zoh")
        self.assertEqual(result.arm_disposition_label, "INTERFACE_VALUE_ONLY")


class DelayedArmTests(unittest.TestCase):
    def _deadline_case(self, right_commit_cycle):
        occurrence_cycle = 2
        deadline_cycle = occurrence_cycle + DELAYED_DEADLINE_CYCLES
        self.assertIn(
            right_commit_cycle,
            (deadline_cycle - 1, deadline_cycle, deadline_cycle + 1),
        )
        right_timestamp = timestamp_for_cycle(right_commit_cycle)
        result = simulate(
            Arm.DELAYED_EXACT,
            [Event(1, 13)],
            [dataset_pose(10, 0), dataset_pose(11, right_timestamp)],
        )
        return deadline_cycle, result

    def test_deadline_d_minus_1_commit_is_visible_at_d_and_corrects(self):
        deadline = 2 + DELAYED_DEADLINE_CYCLES
        expected_deadline, result = self._deadline_case(deadline - 1)
        record = result.records[0]
        self.assertEqual(expected_deadline, deadline)
        self.assertEqual(record.disposition, "corrected_world_ray")
        self.assertEqual(record.disposition_reason, "bracket_interpolation")
        self.assertEqual(record.used_pose_ids, (10, 11))
        self.assertTrue(record.intentional_future_pose_use)
        self.assertEqual(record.retire_cycle, deadline + 1)
        receipt = result.cycle_receipts[0]
        self.assertEqual(receipt.admission_cycle, 2)
        self.assertEqual(receipt.admission_lane, 0)
        self.assertEqual(receipt.launch_cycle, deadline)
        self.assertEqual(receipt.launch_lane, 0)
        self.assertEqual(receipt.retire_cycle, deadline + 1)
        self.assertEqual(receipt.retire_lane, 0)
        self.assertEqual(receipt.fifo_occupancy_before_admission, 0)
        self.assertEqual(receipt.fifo_occupancy_after_admission, 1)
        self.assertEqual(receipt.fifo_occupancy_before_retire, 1)
        self.assertEqual(receipt.fifo_occupancy_after_retire, 0)

    def test_deadline_d_and_d_plus_1_commits_are_too_late(self):
        deadline = 2 + DELAYED_DEADLINE_CYCLES
        for commit_cycle in (deadline, deadline + 1):
            with self.subTest(commit_cycle=commit_cycle):
                expected_deadline, result = self._deadline_case(commit_cycle)
                record = result.records[0]
                self.assertEqual(record.retire_cycle, expected_deadline)
                self.assertEqual(record.disposition, "raw_bypass")
                self.assertEqual(record.disposition_reason, "deadline_timeout")
                self.assertEqual(record.used_pose_ids, (10,))
                self.assertFalse(record.intentional_future_pose_use)
                self.assertIsNone(result.cycle_receipts[0].launch_cycle)
                self.assertIsNone(result.cycle_receipts[0].launch_lane)

    def test_full_fifo_forces_two_oldest_raw_before_admitting_new_events(self):
        events = []
        event_id = 0
        for cycle in range(2, 515):
            timestamp_ns = timestamp_for_cycle(cycle)
            events.extend((Event(event_id, timestamp_ns), Event(event_id + 1, timestamp_ns)))
            event_id += 2
        self.assertEqual(len(events), BUFFER_ENTRIES + 2)
        right_commit_cycle = 2_000
        poses = [
            dataset_pose(10, 0),
            dataset_pose(11, timestamp_for_cycle(right_commit_cycle)),
        ]
        result = simulate(Arm.DELAYED_EXACT, events, poses)

        self.assertEqual(result.peak_buffer_occupancy, BUFFER_ENTRIES)
        self.assertEqual(len(result.records), BUFFER_ENTRIES + 2)
        self.assertEqual(
            [record.event_id for record in result.records],
            list(range(BUFFER_ENTRIES + 2)),
        )
        self.assertEqual(
            [record.disposition_reason for record in result.records[:2]],
            ["fifo_full_forced_bypass", "fifo_full_forced_bypass"],
        )
        self.assertFalse(result.records[0].intentional_future_pose_use)
        self.assertFalse(result.records[1].intentional_future_pose_use)
        self.assertEqual(result.records[0].used_pose_ids, (10,))
        self.assertEqual(result.records[1].used_pose_ids, (10,))
        first_receipt, second_receipt = result.cycle_receipts[:2]
        self.assertEqual(first_receipt.retire_lane, 0)
        self.assertEqual(second_receipt.retire_lane, 1)
        self.assertEqual(first_receipt.fifo_occupancy_before_retire, 1_024)
        self.assertEqual(first_receipt.fifo_occupancy_after_retire, 1_023)
        self.assertEqual(second_receipt.fifo_occupancy_before_retire, 1_023)
        self.assertEqual(second_receipt.fifo_occupancy_after_retire, 1_022)
        self.assertIsNone(first_receipt.launch_cycle)
        self.assertIsNone(second_receipt.launch_cycle)
        self.assertTrue(
            all(
                record.disposition == "corrected_world_ray"
                for record in result.records[2:]
            )
        )
        self.assertTrue(
            all(
                right.retire_cycle >= left.retire_cycle
                for left, right in zip(result.records, result.records[1:])
            )
        )
        self.assertEqual(result.arm_disposition_label, "DIAGNOSTIC_UPPER_BOUND")

    def test_missing_left_pose_raw_bypasses_without_drop(self):
        result = simulate(Arm.DELAYED_EXACT, [Event(7, 0)], [])
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].event_id, 7)
        self.assertEqual(result.records[0].disposition, "raw_bypass")
        self.assertEqual(result.records[0].disposition_reason, "missing_bracket")

    def test_invalid_right_bracket_raw_bypass_records_no_future_pose_use(self):
        result = simulate(
            Arm.DELAYED_EXACT,
            [Event(7, 13)],
            [dataset_pose(10, 0), dataset_pose(11, 14, value_valid=False)],
        )
        record = result.records[0]
        self.assertEqual(record.disposition, "raw_bypass")
        self.assertEqual(record.disposition_reason, "invalid_pose")
        self.assertEqual(record.used_pose_ids, (10,))
        self.assertFalse(record.intentional_future_pose_use)
        self.assertTrue(
            all(
                timestamp <= record.event_timestamp_ns
                for timestamp in record.used_pose_timestamps_ns
            )
        )
        receipt = result.cycle_receipts[0]
        self.assertEqual(receipt.arm, Arm.DELAYED_EXACT.value)
        self.assertEqual(receipt.admission_cycle, 2)
        self.assertIsNone(receipt.launch_cycle)
        self.assertEqual(receipt.retire_cycle, 4)
        self.assertEqual(receipt.fifo_occupancy_before_admission, 0)
        self.assertEqual(receipt.fifo_occupancy_after_admission, 1)
        self.assertEqual(receipt.fifo_occupancy_before_retire, 1)
        self.assertEqual(receipt.fifo_occupancy_after_retire, 0)
        self.assertEqual(receipt.decision_record_sha256, record.canonical_sha256())
        self.assertEqual(len(receipt.canonical_sha256()), 64)
        self.assertEqual(result.arm_disposition_label, "DIAGNOSTIC_UPPER_BOUND")

    def test_deadline_head_retires_before_same_cycle_new_admission(self):
        deadline = 2 + DELAYED_DEADLINE_CYCLES
        at_deadline = timestamp_for_cycle(deadline)
        after_deadline = timestamp_for_cycle(deadline + 1)
        result = simulate(
            Arm.DELAYED_EXACT,
            [Event(1, 13), Event(2, at_deadline)],
            [
                dataset_pose(10, 0),
                dataset_pose(11, at_deadline),
                dataset_pose(12, after_deadline),
            ],
        )
        old, new = result.records
        self.assertEqual(old.retire_cycle, deadline)
        self.assertEqual(old.disposition_reason, "deadline_timeout")
        self.assertEqual(new.occurrence_cycle, deadline)
        self.assertEqual(new.occurrence_pose_ids, (10,))
        self.assertEqual(new.used_pose_ids, (10, 12))
        self.assertEqual(new.retire_cycle, deadline + 3)
        self.assertEqual(new.queue_cycles, 2)
        self.assertEqual(result.common_serializer_cycles, (0, 0))
        self.assertEqual(result.policy_added_latency_cycles[1], 2)
        self.assertEqual([old.event_id, new.event_id], [1, 2])


class ContractAndRecordTests(unittest.TestCase):
    def test_integration_pose_index_is_required_verified_and_exposed(self):
        pose = dataset_pose(10, 0)
        result = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=START,
            arm=Arm.ZOH_FRESHNESS,
            events=[Event(1, 13, causal_pose_index=10)],
            poses=[pose],
        )
        self.assertTrue(result.all_event_pose_indices_verified)
        self.assertFalse(result.synthetic_test_mode)
        self.assertTrue(result.cycle_receipts[0].causal_pose_index_verified)
        self.assertEqual(result.cycle_receipts[0].event_causal_pose_index, 10)

        with self.assertRaisesRegex(CycleModelError, "missing causal_pose_index"):
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.ZOH_FRESHNESS,
                events=[Event(1, 13)],
                poses=[pose],
            )
        synthetic = simulate(Arm.ZOH_FRESHNESS, [Event(1, 13)], [pose])
        self.assertFalse(synthetic.all_event_pose_indices_verified)
        self.assertFalse(
            synthetic.cycle_receipts[0].causal_pose_index_verified
        )

    def test_mutated_event_pose_index_fails_closed(self):
        with self.assertRaisesRegex(CycleModelError, "latest occurrence pose"):
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.ZOH_FRESHNESS,
                events=[Event(1, 13, causal_pose_index=9)],
                poses=[dataset_pose(10, 0)],
            )

    def test_pose_identity_range_duplicates_order_and_gaps_are_explicit(self):
        self.assertEqual(CAUSAL_POSE_INDEX_LIMIT, 1 << 14)
        self.assertTrue(POSE_ID_GAPS_ALLOWED)
        self.assertEqual(pose_ring_slot(0), 0)
        self.assertEqual(pose_ring_slot(16), 0)
        with self.assertRaisesRegex(CycleModelError, "14 bits"):
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.ZOH_FRESHNESS,
                events=[
                    Event(
                        1,
                        13,
                        causal_pose_index=CAUSAL_POSE_INDEX_LIMIT,
                    )
                ],
                poses=[dataset_pose(10, 0)],
            )
        large_packet = dataset_pose(CAUSAL_POSE_INDEX_LIMIT, 0)
        self.assertEqual(
            pose_ring_slot(large_packet.pose_id),
            CAUSAL_POSE_INDEX_LIMIT % 16,
        )
        self.assertEqual(
            simulate(Arm.ZOH_FRESHNESS, [], [large_packet]).pose_ring_accounting.writes,
            1,
        )

        duplicate = [dataset_pose(2, 0), dataset_pose(2, 1)]
        with self.assertRaisesRegex(CycleModelError, "duplicate pose IDs"):
            simulate(Arm.ZOH_FRESHNESS, [], duplicate)
        reordered = [dataset_pose(2, 0), dataset_pose(1, 1)]
        with self.assertRaisesRegex(CycleModelError, "in increasing order"):
            simulate(Arm.ZOH_FRESHNESS, [], reordered)

        deleted_packet = [
            dataset_pose(pose_id, pose_id)
            for pose_id in range(17)
            if pose_id != 8
        ]
        accounting = simulate(
            Arm.ZOH_FRESHNESS, [], deleted_packet
        ).pose_ring_accounting
        self.assertEqual(accounting.writes, 16)
        self.assertEqual(accounting.safe_overwrites, 1)
        self.assertEqual(accounting.peak_occupied_entries, 15)

    def test_negative_cycle_ring_phase_crosses_window_edge_fail_closed(self):
        poses = [
            PosePacket.dataset(0, 90, 100, SHA_A),
            PosePacket.dataset(16, 100, 100, SHA_B),
        ]
        self.assertEqual(poses[0].commit_cycle, -1)
        self.assertEqual(poses[1].commit_cycle, 0)
        with self.assertRaises(PoseRingSafetyError) as raised:
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=100,
                arm=Arm.ZOH_FRESHNESS,
                events=[Event(1, 100, causal_pose_index=0)],
                poses=poses,
            )
        evidence = raised.exception.evidence
        self.assertEqual(evidence.reason, "live_reference_overwrite")
        self.assertEqual(evidence.cycle, 0)
        self.assertEqual(evidence.ring_slot, 0)
        self.assertEqual(evidence.incoming_pose_id, 16)
        self.assertEqual(evidence.resident_pose_id, 0)

    def test_oracle_pre_window_commit_applies_signed_plus_one(self):
        window_start = 2_000_000
        oracle = PosePacket.oracle_1khz(
            1, 1_000_000, window_start, SHA_A
        )
        self.assertEqual(
            oracle.commit_cycle,
            pose_timestamp_to_cycle(1_000_000, window_start) + 1,
        )
        self.assertLess(oracle.commit_cycle, 0)
        result = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=window_start,
            arm=Arm.ORACLE_1KHZ,
            events=[Event(1, window_start)],
            poses=[oracle],
        )
        self.assertTrue(result.all_event_pose_indices_verified)
        self.assertFalse(result.cycle_receipts[0].causal_pose_index_applicable)
        self.assertFalse(result.cycle_receipts[0].causal_pose_index_verified)
        self.assertEqual(
            result.records[0].occurrence_pose_commit_cycles,
            (oracle.commit_cycle,),
        )

    def test_oracle_global_phase_id_may_exceed_dataset_index_width(self):
        oracle_id = CAUSAL_POSE_INDEX_LIMIT + 17
        pose_timestamp = oracle_id * 1_000_000
        window_start = pose_timestamp + 1_000_000
        oracle = PosePacket.oracle_1khz(
            oracle_id, pose_timestamp, window_start, SHA_A
        )
        result = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=window_start,
            arm=Arm.ORACLE_1KHZ,
            events=[Event(1, window_start)],
            poses=[oracle],
        )
        self.assertEqual(result.records[0].used_pose_ids, (oracle_id,))
        self.assertEqual(pose_ring_slot(oracle_id), oracle_id % 16)
        self.assertTrue(result.all_event_pose_indices_verified)

        with self.assertRaisesRegex(CycleModelError, "must be None"):
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=window_start,
                arm=Arm.ORACLE_1KHZ,
                events=[Event(1, window_start, causal_pose_index=1)],
                poses=[oracle],
            )
        with self.assertRaisesRegex(CycleModelError, "14 bits"):
            Event(
                1,
                window_start,
                causal_pose_index=CAUSAL_POSE_INDEX_LIMIT,
            )
        wrong_id = PosePacket.oracle_1khz(
            oracle_id + 1, pose_timestamp, window_start, SHA_A
        )
        with self.assertRaisesRegex(CycleModelError, "global phase schedule"):
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=window_start,
                arm=Arm.ORACLE_1KHZ,
                events=[],
                poses=[wrong_id],
            )

    def test_cav_reference_to_evicted_gap_phase_pose_is_rejected(self):
        with self.assertRaises(PoseRingSafetyError) as raised:
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.CAUSAL_CAV,
                events=[Event(1, 13, causal_pose_index=16)],
                poses=[dataset_pose(0, 0), dataset_pose(16, 6)],
            )
        evidence = raised.exception.evidence
        self.assertEqual(evidence.reason, "referenced_pose_not_resident")
        self.assertEqual(evidence.ring_slot, 0)
        self.assertEqual(evidence.referenced_pose_id, 0)
        self.assertEqual(evidence.resident_pose_id, 16)

    def test_invalid_right_is_cycle_evidence_but_not_receipt_used_pose(self):
        result = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=START,
            arm=Arm.DELAYED_EXACT,
            events=[Event(7, 13, causal_pose_index=0)],
            poses=[
                dataset_pose(0, 0),
                dataset_pose(1, 14, value_valid=False),
            ],
        )
        record = result.records[0]
        cycle_receipt = result.cycle_receipts[0]
        self.assertEqual(record.disposition_reason, "invalid_pose")
        self.assertEqual(record.used_pose_ids, (0,))
        ReceiptDecisionRecord.from_mapping(record.to_mapping())
        self.assertEqual(cycle_receipt.inspection_cycle, 4)
        self.assertEqual(cycle_receipt.inspected_pose_ids, (1,))
        self.assertEqual(cycle_receipt.inspected_pose_commit_cycles, (3,))
        self.assertEqual(
            cycle_receipt.inspection_failure_causes,
            ("right_value_invalid",),
        )
        self.assertEqual(
            cycle_receipt.to_mapping()["inspection_failure_causes"],
            ["right_value_invalid"],
        )
        self.assertEqual(result.pose_ring_accounting.live_reference_checks, 2)

    def test_invalid_pose_evidence_names_each_failing_guard(self):
        cases = (
            (
                "left_value_invalid",
                dataset_pose(0, 0, value_valid=False),
                dataset_pose(1, 14),
                True,
            ),
            (
                "right_value_invalid",
                dataset_pose(0, 0),
                dataset_pose(1, 14, value_valid=False),
                True,
            ),
            (
                "left_arithmetic_invalid",
                dataset_pose(0, 0, arithmetic_valid=False),
                dataset_pose(1, 14),
                True,
            ),
            (
                "right_arithmetic_invalid",
                dataset_pose(0, 0),
                dataset_pose(1, 14, arithmetic_valid=False),
                True,
            ),
            (
                "transform_guard_invalid",
                dataset_pose(0, 0),
                dataset_pose(1, 14),
                False,
            ),
        )
        for expected_cause, left, right, guard_valid in cases:
            with self.subTest(expected_cause=expected_cause):
                result = run_cycle_model(
                    window_id=WINDOW,
                    window_start_ns=START,
                    arm=Arm.DELAYED_EXACT,
                    events=[
                        Event(
                            7,
                            13,
                            transform_guard_valid=guard_valid,
                            causal_pose_index=0,
                        )
                    ],
                    poses=[left, right],
                )
                cycle_receipt = result.cycle_receipts[0]
                self.assertEqual(
                    cycle_receipt.inspection_failure_causes,
                    (expected_cause,),
                )
                self.assertEqual(cycle_receipt.inspected_pose_ids, (1,))
                self.assertEqual(result.records[0].used_pose_ids, (0,))

        all_failed = run_cycle_model(
            window_id=WINDOW,
            window_start_ns=START,
            arm=Arm.DELAYED_EXACT,
            events=[
                Event(
                    7,
                    13,
                    transform_guard_valid=False,
                    causal_pose_index=0,
                )
            ],
            poses=[
                dataset_pose(
                    0,
                    0,
                    value_valid=False,
                    arithmetic_valid=False,
                ),
                dataset_pose(
                    1,
                    14,
                    value_valid=False,
                    arithmetic_valid=False,
                ),
            ],
        )
        self.assertEqual(
            all_failed.cycle_receipts[0].inspection_failure_causes,
            (
                "left_value_invalid",
                "right_value_invalid",
                "left_arithmetic_invalid",
                "right_arithmetic_invalid",
                "transform_guard_invalid",
            ),
        )

    def test_invalid_right_same_cycle_overwrite_precedes_release(self):
        with self.assertRaises(PoseRingSafetyError) as raised:
            run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.DELAYED_EXACT,
                events=[Event(7, 13, causal_pose_index=0)],
                poses=[
                    dataset_pose(0, 0),
                    dataset_pose(1, 14, value_valid=False),
                    dataset_pose(17, 20),
                ],
            )
        evidence = raised.exception.evidence
        self.assertEqual(evidence.reason, "live_reference_overwrite")
        self.assertEqual(evidence.cycle, 4)
        self.assertEqual(evidence.ring_slot, 1)
        self.assertEqual(evidence.incoming_pose_id, 17)
        self.assertEqual(evidence.resident_pose_id, 1)
        self.assertEqual(evidence.live_event_ids, (7,))

    def test_every_arm_constructs_complete_ingress_accounting_result(self):
        required_fields = (
            "common_serializer_cycles",
            "always_bypass_retire_cycles",
            "policy_added_latency_cycles",
            "peak_ingress_staging_occupancy",
            "raw_ingress_lanes",
            "ingress_staging_entries",
        )
        for arm in Arm:
            with self.subTest(arm=arm.value):
                result = simulate(arm, [], [])
                for field_name in required_fields:
                    self.assertTrue(hasattr(result, field_name), field_name)
                self.assertEqual(result.common_serializer_cycles, ())
                self.assertEqual(result.always_bypass_retire_cycles, ())
                self.assertEqual(result.policy_added_latency_cycles, ())
                self.assertEqual(result.peak_ingress_staging_occupancy, 0)
                self.assertEqual(result.raw_ingress_lanes, 6)
                self.assertEqual(result.ingress_staging_entries, 6)
                self.assertEqual(result.pose_ring_entries, 16)
                self.assertEqual(result.pose_ring_state_bits, 16 * 192)
                self.assertEqual(result.pose_ring_accounting.entries, 16)
                self.assertEqual(result.pose_ring_accounting.entry_bits, 192)
                self.assertEqual(
                    result.pose_ring_accounting.state_bits,
                    POSE_RING_STATE_BITS,
                )
                self.assertEqual(result.pose_ring_accounting.failures, 0)
                self.assertEqual(
                    result.pose_ring_accounting_sha256,
                    result.pose_ring_accounting.canonical_sha256(),
                )

    def test_arm_labels_and_delayed_reasons_match_receipt_v2_exactly(self):
        self.assertEqual(ARM_LABELS, RECEIPT_ARM_LABELS)
        cases = (
            (
                Arm.ZOH_FRESHNESS,
                [Event(1, 13)],
                [dataset_pose(10, 0)],
            ),
            (Arm.DELAYED_EXACT, [Event(1, 0)], []),
            (
                Arm.CAUSAL_CAV,
                [Event(1, 13)],
                [dataset_pose(10, 0)],
            ),
            (
                Arm.ORACLE_1KHZ,
                [Event(1, 13)],
                [PosePacket.oracle_1khz(0, 0, START, SHA_A)],
            ),
        )
        for arm, events, poses in cases:
            with self.subTest(arm=arm.value):
                result = simulate(arm, events, poses)
                record = result.records[0]
                self.assertEqual(record.arm_semantic_label, RECEIPT_ARM_LABELS[arm.value])
                self.assertEqual(result.arm_disposition_label, RECEIPT_ARM_LABELS[arm.value])
                parsed = ReceiptDecisionRecord.from_mapping(record.to_mapping())
                self.assertEqual(parsed.arm_semantic_label, record.arm_semantic_label)
        self.assertEqual(
            {
                "deadline_timeout",
                "fifo_full_forced_bypass",
                "invalid_pose",
                "missing_bracket",
            },
            set(RECEIPT_DELAYED_RAW_REASONS),
        )

    def test_pose_ring_charges_safe_overwrite_without_live_events(self):
        poses = [dataset_pose(index, index) for index in range(17)]
        result = simulate(Arm.ZOH_FRESHNESS, [], poses)
        accounting = result.pose_ring_accounting
        self.assertEqual(accounting.entries, POSE_RING_ENTRIES)
        self.assertEqual(accounting.state_bits, 16 * 192)
        self.assertEqual(accounting.writes, 17)
        self.assertEqual(accounting.safe_overwrites, 1)
        self.assertEqual(accounting.peak_occupied_entries, 16)
        self.assertEqual(accounting.live_reference_checks, 0)

    def test_pose_ring_live_reference_overwrite_fails_closed_with_evidence(self):
        poses = [dataset_pose(index, 6 + index) for index in range(17)]
        events = [Event(index, 13) for index in range(6)]
        with self.assertRaises(PoseRingSafetyError) as raised:
            simulate(Arm.ZOH_FRESHNESS, events, poses)
        evidence = raised.exception.evidence
        self.assertEqual(evidence.reason, "live_reference_overwrite")
        self.assertEqual(evidence.cycle, 4)
        self.assertEqual(evidence.ring_slot, 0)
        self.assertEqual(evidence.incoming_pose_id, 16)
        self.assertEqual(evidence.resident_pose_id, 0)
        self.assertEqual(evidence.live_event_ids, (2, 3, 4, 5))
        self.assertEqual(evidence.writes_completed, 16)
        self.assertEqual(evidence.occupied_entries, 16)
        self.assertEqual(evidence.live_reference_count, 4)
        self.assertEqual(len(evidence.canonical_sha256()), 64)
        self.assertEqual(
            evidence.to_mapping()["pose_ring_state_bits"],
            POSE_RING_STATE_BITS,
        )

    def test_decision_record_digest_is_deterministic_and_score_free(self):
        events = [Event(1, 13)]
        poses = [dataset_pose(10, 0)]
        first = simulate(Arm.ZOH_FRESHNESS, events, poses)
        second = simulate(Arm.ZOH_FRESHNESS, events, poses)
        self.assertEqual(first.decision_records_sha256, second.decision_records_sha256)
        self.assertEqual(first.cycle_receipts_sha256, second.cycle_receipts_sha256)
        mapping = first.records[0].to_mapping()
        self.assertNotIn("score", str(mapping).lower())
        self.assertNotIn("loss", str(mapping).lower())
        changed = simulate(Arm.ZOH_FRESHNESS, [Event(2, 13)], poses)
        self.assertNotEqual(first.decision_records_sha256, changed.decision_records_sha256)
        self.assertNotEqual(first.cycle_receipts_sha256, changed.cycle_receipts_sha256)

    def test_pose_source_delivery_phase_and_source_rate_fail_closed(self):
        bad_commit = replace(dataset_pose(10, 0), commit_cycle=1)
        with self.assertRaisesRegex(CycleModelError, "delivery timing"):
            simulate(Arm.ZOH_FRESHNESS, [Event(1, 13)], [bad_commit])

        off_phase_oracle = PosePacket(
            pose_id=20,
            timestamp_ns=1,
            commit_cycle=2,
            source=PoseSource.ORACLE_1KHZ,
            pose_sha256=SHA_A,
        )
        with self.assertRaisesRegex(CycleModelError, "global 1 kHz phase"):
            simulate(Arm.ORACLE_1KHZ, [Event(1, 13)], [off_phase_oracle])

        raw_lane_overrun = [Event(index, 13) for index in range(7)]
        with self.assertRaisesRegex(CycleModelError, "more than six"):
            simulate(Arm.ZOH_FRESHNESS, raw_lane_overrun, [])

        adjacent_five_record_bursts = [
            Event(index, 13) for index in range(5)
        ] + [
            Event(5 + index, 14) for index in range(5)
        ]
        self.assertEqual(
            [
                timestamp_to_cycle(event.timestamp_ns, START)
                for event in adjacent_five_record_bursts
            ],
            [2] * 5 + [3] * 5,
        )
        with self.assertRaisesRegex(CycleModelError, "staging overflow"):
            simulate(Arm.ZOH_FRESHNESS, adjacent_five_record_bursts, [])

    def test_empty_input_conserves_exactly_and_has_stable_digest(self):
        result = simulate(Arm.ZOH_FRESHNESS, [], [])
        self.assertEqual(result.records, ())
        self.assertEqual(result.cycle_receipts, ())
        self.assertEqual(result.common_serializer_cycles, ())
        self.assertEqual(result.policy_added_latency_cycles, ())
        self.assertEqual(result.peak_ingress_staging_occupancy, 0)
        self.assertEqual(result.peak_buffer_occupancy, 0)
        self.assertEqual(len(result.decision_records_sha256), 64)


if __name__ == "__main__":
    unittest.main()
