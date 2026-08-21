from __future__ import annotations

import copy
import unittest

from benchmarks.redred_mc_wtb_stage4_contract import (
    DecisionRecord,
    ReceiptError,
    canonical_sha256,
    load_comparison_contract,
    validate_decision_records,
)


WINDOW = "shapes_rotation_dev_005321"
ARM = "zoh_freshness"


def record(event_id: int, occurrence: int, retire: int):
    hashes = ["1" * 64, "2" * 64]
    return {
        "window_id": WINDOW,
        "event_id": event_id,
        "event_timestamp_ns": 5_320_500_000,
        "arm": ARM,
        "occurrence_cycle": occurrence,
        "retire_cycle": retire,
        "occurrence_pose_ids": [10, 11],
        "occurrence_pose_timestamps_ns": [5_319_000_000, 5_320_000_000],
        "occurrence_pose_commit_cycles": [1, 2],
        "occurrence_pose_sha256": hashes,
        "used_pose_ids": [10, 11],
        "used_pose_timestamps_ns": [5_319_000_000, 5_320_000_000],
        "used_pose_commit_cycles": [1, 2],
        "used_pose_sha256": hashes,
        "intentional_future_pose_use": False,
        "pose_age_ns": 500000,
        "disposition": "corrected_world_ray",
        "disposition_reason": "fresh_pose",
        "queue_cycles": retire - occurrence,
    }


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_comparison_contract()
        self.rows = [record(101, 4, 5), record(102, 4, 6), record(103, 7, 8)]

    def validate(self, rows):
        return validate_decision_records(
            self.contract,
            [101, 102, 103],
            rows,
            expected_window_id=WINDOW,
            expected_arm=ARM,
        )

    def test_valid_records_make_deterministic_score_free_receipt(self) -> None:
        receipt = self.validate(self.rows)
        mapping = receipt.to_mapping()
        self.assertEqual(mapping["expected_events"], 3)
        self.assertEqual(mapping["retired_records"], 3)
        self.assertTrue(mapping["conservation"]["exact_once"])
        self.assertTrue(mapping["conservation"]["ordered_retirement"])
        self.assertNotIn("score", str(mapping).lower())
        self.assertNotIn("loss", str(mapping).lower())

        reordered_keys = []
        for row in self.rows:
            reordered_keys.append(dict(reversed(list(row.items()))))
        self.assertEqual(
            receipt.decision_records_sha256,
            self.validate(reordered_keys).decision_records_sha256,
        )
        self.assertEqual(receipt.canonical_sha256(), canonical_sha256(mapping))

    def test_score_and_loss_fields_are_rejected_before_schema_extensions(self) -> None:
        for field in ("score", "quality_score", "loss", "sensor_loss_rad"):
            mutated = copy.deepcopy(self.rows[0])
            mutated[field] = 0
            with self.subTest(field=field), self.assertRaisesRegex(
                ReceiptError, "score/loss"
            ):
                DecisionRecord.from_mapping(mutated)

    def test_duplicate_missing_unexpected_and_reordered_ids_fail(self) -> None:
        cases = (
            ([self.rows[0], self.rows[0], self.rows[2]], "duplicate"),
            (self.rows[:2], "missing"),
            ([self.rows[0], self.rows[1], record(999, 7, 8)], "missing"),
            ([self.rows[1], self.rows[0], self.rows[2]], "order"),
        )
        for rows, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ReceiptError, message
            ):
                self.validate(rows)

    def test_retirement_cycle_reversal_and_queue_overstatement_fail(self) -> None:
        reversed_cycle = copy.deepcopy(self.rows)
        reversed_cycle[1]["retire_cycle"] = 4
        reversed_cycle[1]["queue_cycles"] = 0
        with self.assertRaisesRegex(ReceiptError, "retirement cycles"):
            self.validate(reversed_cycle)

        bad_queue = copy.deepcopy(self.rows[0])
        bad_queue["queue_cycles"] = 2
        with self.assertRaisesRegex(ReceiptError, "queue_cycles"):
            DecisionRecord.from_mapping(bad_queue)

    def test_pose_provenance_is_paired_ordered_and_pose_age_may_be_signed(self) -> None:
        delayed = record(101, 4, 8)
        delayed["arm"] = "delayed_exact"
        delayed["used_pose_ids"] = [11, 12]
        delayed["used_pose_timestamps_ns"] = [5_320_000_000, 5_321_000_000]
        delayed["used_pose_commit_cycles"] = [2, 5]
        delayed["used_pose_sha256"] = ["2" * 64, "3" * 64]
        delayed["intentional_future_pose_use"] = True
        delayed["pose_age_ns"] = -500000
        parsed = DecisionRecord.from_mapping(delayed)
        self.assertEqual(parsed.pose_age_ns, -500000)

        malformed = copy.deepcopy(self.rows[0])
        malformed["occurrence_pose_timestamps_ns"] = [5_320_000_000]
        with self.assertRaisesRegex(ReceiptError, "different lengths"):
            DecisionRecord.from_mapping(malformed)

    def test_boolean_identity_and_nonstring_arm_fail_closed(self) -> None:
        boolean_id = copy.deepcopy(self.rows[0])
        boolean_id["event_id"] = True
        with self.assertRaisesRegex(ReceiptError, "event_id"):
            DecisionRecord.from_mapping(boolean_id)

        nonstring_arm = copy.deepcopy(self.rows[0])
        nonstring_arm["arm"] = []
        with self.assertRaisesRegex(ReceiptError, "arm"):
            DecisionRecord.from_mapping(nonstring_arm)

    def test_causal_pose_snapshot_cannot_see_same_edge_or_dequeue_pose(self) -> None:
        same_edge = copy.deepcopy(self.rows[0])
        same_edge["occurrence_pose_commit_cycles"][-1] = same_edge["occurrence_cycle"]
        same_edge["used_pose_commit_cycles"][-1] = same_edge["occurrence_cycle"]
        with self.assertRaisesRegex(ReceiptError, "not visible before"):
            DecisionRecord.from_mapping(same_edge)

        dequeue_pose = copy.deepcopy(self.rows[0])
        dequeue_pose["used_pose_ids"] = [12]
        dequeue_pose["used_pose_timestamps_ns"] = [5_320_250_000]
        dequeue_pose["used_pose_commit_cycles"] = [3]
        dequeue_pose["used_pose_sha256"] = ["3" * 64]
        dequeue_pose["pose_age_ns"] = 250_000
        with self.assertRaisesRegex(ReceiptError, "outside occurrence snapshot"):
            DecisionRecord.from_mapping(dequeue_pose)

    def test_only_delayed_arm_may_declare_future_pose_use(self) -> None:
        causal = copy.deepcopy(self.rows[0])
        causal["intentional_future_pose_use"] = True
        with self.assertRaisesRegex(ReceiptError, "causal arm"):
            DecisionRecord.from_mapping(causal)

        delayed_without_label = copy.deepcopy(self.rows[0])
        delayed_without_label["arm"] = "delayed_exact"
        with self.assertRaisesRegex(ReceiptError, "must declare"):
            DecisionRecord.from_mapping(delayed_without_label)


if __name__ == "__main__":
    unittest.main()
