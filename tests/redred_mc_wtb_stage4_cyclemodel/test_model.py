from __future__ import annotations

from dataclasses import replace
import unittest

from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    BUFFER_ENTRIES,
    CAUSAL_POSE_INDEX_BITS,
    DELAYED_DEADLINE_CYCLES,
    INGRESS_STAGING_ENTRIES,
    RAW_INGRESS_LANES,
    Arm,
    CycleModelError,
    Event,
    PosePacket,
    PoseSource,
    ceil_div,
    run_cycle_model,
    timestamp_to_cycle,
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
        oracle = PosePacket.oracle_1khz(20, 0, START, SHA_A)
        self.assertEqual(oracle.commit_cycle, 1)
        events = [Event(1, 6), Event(2, 13)]
        result = simulate(Arm.ORACLE_1KHZ, events, [oracle])
        self.assertEqual(result.records[0].occurrence_cycle, 1)
        self.assertEqual(result.records[0].disposition_reason, "no_occurrence_pose")
        self.assertEqual(result.records[1].occurrence_cycle, 2)
        self.assertEqual(result.records[1].used_pose_ids, (20,))
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
            ["full_pressure_oldest_bypass", "full_pressure_oldest_bypass"],
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
        self.assertEqual(result.records[0].disposition_reason, "missing_left_pose")

    def test_invalid_right_bracket_raw_bypass_records_no_future_pose_use(self):
        result = simulate(
            Arm.DELAYED_EXACT,
            [Event(7, 13)],
            [dataset_pose(10, 0), dataset_pose(11, 14, value_valid=False)],
        )
        record = result.records[0]
        self.assertEqual(record.disposition, "raw_bypass")
        self.assertEqual(record.disposition_reason, "invalid_bracket")
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
