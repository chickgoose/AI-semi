from __future__ import annotations

import hashlib
import json
import math
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_causal_reference import reference as reference_module
from benchmarks.redred_mc_wtb_predictor_stage3.reference_prime import (
    PrimeReceipt,
    ScoreFreeCausalReferenceBank,
)
from benchmarks.redred_mc_wtb_causal_reference.reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    CausalReferenceError,
    ReferenceObservation,
)


def ray(angle: float):
    return (math.sin(angle), 0.0, math.cos(angle))


def observation(event_id: int, timestamp_ns: int, polarity: int, angle: float) -> ReferenceObservation:
    return ReferenceObservation(event_id, timestamp_ns, polarity, ray(angle))


def canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class ScoreFreePrimeTests(unittest.TestCase):
    def test_prime_never_calls_angular_distance_and_returns_a_verifiable_receipt(self) -> None:
        warmup = (
            observation(1, 10, 0, 0.0),
            observation(2, 11, 0, 0.1),
            observation(3, 11, 1, -0.1),
            observation(4, 14, 0, 0.2),
        )
        bank = ScoreFreeCausalReferenceBank()
        with mock.patch.object(
            reference_module,
            "angular_distance",
            side_effect=AssertionError("prime attempted to score warmup"),
        ) as distance_spy:
            receipt = bank.prime(warmup)

        distance_spy.assert_not_called()
        self.assertIsInstance(receipt, PrimeReceipt)
        self.assertEqual(receipt.observation_count, len(warmup))
        self.assertEqual(receipt.first_timestamp_ns, 10)
        self.assertEqual(receipt.last_timestamp_ns, 14)
        self.assertEqual(receipt.occupancy, bank.occupancy())

        observation_payload = [
            {
                "event_id": item.event_id,
                "polarity": item.polarity,
                "ray_hex": [float(value).hex() for value in item.ray],
                "timestamp_ns": item.timestamp_ns,
            }
            for item in warmup
        ]
        self.assertEqual(receipt.observations_sha256, canonical_sha256(observation_payload))
        receipt_payload = {
            "first_timestamp_ns": receipt.first_timestamp_ns,
            "last_timestamp_ns": receipt.last_timestamp_ns,
            "observation_count": receipt.observation_count,
            "observations_sha256": receipt.observations_sha256,
            "occupancy": list(receipt.occupancy),
            "schema": receipt.schema,
        }
        self.assertEqual(receipt.seal_sha256, canonical_sha256(receipt_payload))

    def test_empty_prime_is_score_free_countable_and_does_not_change_state(self) -> None:
        bank = ScoreFreeCausalReferenceBank()
        with mock.patch.object(reference_module, "angular_distance") as distance_spy:
            receipt = bank.prime(())
        distance_spy.assert_not_called()
        self.assertEqual(receipt.observation_count, 0)
        self.assertIsNone(receipt.first_timestamp_ns)
        self.assertIsNone(receipt.last_timestamp_ns)
        self.assertEqual(receipt.occupancy, (0, 0))
        self.assertEqual(bank.occupancy(), (0, 0))

    def test_prime_then_query_scores_equal_legacy_all_event_query_subset(self) -> None:
        config = CausalReferenceConfig(capacity_per_polarity=3, max_age_ns=5)
        warmup = (
            observation(1, 1, 0, 0.00),
            observation(2, 2, 1, -0.30),
            observation(3, 4, 0, 0.10),
            observation(4, 6, 0, 0.20),
            observation(5, 6, 0, 0.25),
        )
        query = (
            observation(10, 8, 0, 0.23),
            observation(11, 8, 1, -0.30),
            observation(12, 12, 0, 0.27),
            observation(13, 20, 0, 0.40),
        )

        legacy = CausalReferenceBank(config)
        legacy_all_scores = legacy.process(warmup + query)
        expected_query_scores = legacy_all_scores[len(warmup):]

        split = ScoreFreeCausalReferenceBank(config)
        split.prime(warmup)
        actual_query_scores = split.process(query)

        self.assertEqual(actual_query_scores, expected_query_scores)
        self.assertEqual(split.occupancy(), legacy.occupancy())

    def test_prime_preserves_process_validation_and_failure_is_atomic(self) -> None:
        invalid_sequences = (
            (
                observation(1, 2, 0, 0.0),
                observation(2, 1, 0, 0.1),
            ),
            (
                observation(7, 1, 0, 0.0),
                observation(7, 2, 1, 0.1),
            ),
            (
                observation(1, 1, 0, 0.0),
                object(),
            ),
        )
        for source in invalid_sequences:
            with self.subTest(source=source):
                process_bank = CausalReferenceBank()
                prime_bank = ScoreFreeCausalReferenceBank()
                with self.assertRaises(CausalReferenceError) as process_error:
                    process_bank.process(source)
                with self.assertRaises(CausalReferenceError) as prime_error:
                    prime_bank.prime(source)
                self.assertEqual(str(prime_error.exception), str(process_error.exception))
                self.assertEqual(prime_bank.occupancy(), (0, 0))

        bank = ScoreFreeCausalReferenceBank()
        bank.prime((observation(20, 10, 0, 0.0),))
        occupancy_before = bank.occupancy()
        for mutated, message in (
            ((observation(21, 10, 0, 0.1),), "split across calls"),
            ((observation(22, 9, 0, 0.1),), "backwards"),
            ((observation(20, 11, 0, 0.1),), "duplicate"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(CausalReferenceError, message):
                    bank.prime(mutated)
                self.assertEqual(bank.occupancy(), occupancy_before)

        process_then_prime = ScoreFreeCausalReferenceBank()
        process_then_prime.process((observation(30, 30, 0, 0.0),))
        with self.assertRaisesRegex(CausalReferenceError, "split across calls"):
            process_then_prime.prime((observation(31, 30, 1, 0.0),))

        prime_then_process = ScoreFreeCausalReferenceBank()
        prime_then_process.prime((observation(40, 40, 0, 0.0),))
        with self.assertRaisesRegex(CausalReferenceError, "split across calls"):
            prime_then_process.process((observation(41, 40, 1, 0.0),))

    def test_receipt_sealing_failure_does_not_commit_warmup_state(self) -> None:
        bank = ScoreFreeCausalReferenceBank()
        source = (observation(1, 10, 0, 0.0),)
        with mock.patch(
            "benchmarks.redred_mc_wtb_predictor_stage3.reference_prime._canonical_sha256",
            side_effect=RuntimeError("seal failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "seal failure"):
                bank.prime(source)
        self.assertEqual(bank.occupancy(), (0, 0))
        self.assertEqual(bank.prime(source).observation_count, 1)

    def test_prime_uses_process_expiry_capacity_and_cluster_insertion_rules(self) -> None:
        config = CausalReferenceConfig(capacity_per_polarity=2, max_age_ns=5)
        warmup = (
            observation(1, 1, 0, 0.0),
            observation(2, 4, 0, 0.1),
            observation(3, 4, 1, -0.1),
            observation(4, 10, 0, 0.2),
            observation(5, 10, 0, 0.3),
        )
        bank = ScoreFreeCausalReferenceBank(config)
        receipt = bank.prime(warmup)
        self.assertEqual(receipt.observation_count, 5)
        self.assertEqual(receipt.occupancy, (2, 0))

        scores = bank.process((
            observation(6, 11, 0, 0.29),
            observation(7, 11, 1, -0.1),
        ))
        self.assertEqual(scores[0].reference_event_id, 5)
        self.assertEqual(scores[0].reference_timestamp_ns, 10)
        self.assertFalse(scores[1].reference_available)

    def test_overlapping_windows_require_and_preserve_fresh_bank_isolation(self) -> None:
        overlap = (
            observation(100, 100, 0, 0.0),
            observation(101, 101, 1, -0.1),
        )
        left = ScoreFreeCausalReferenceBank()
        right = ScoreFreeCausalReferenceBank()
        self.assertEqual(left.prime(overlap), right.prime(overlap))

        left_query = left.process((observation(200, 102, 0, 0.05),))
        right_query = right.process((observation(200, 102, 0, 0.05),))
        self.assertEqual(left_query, right_query)

        left.prime((observation(300, 103, 0, 0.2),))
        self.assertEqual(left.occupancy(), (3, 1))
        self.assertEqual(right.occupancy(), (2, 1))
        right.prime((observation(300, 103, 0, 0.2),))
        self.assertEqual(right.occupancy(), (3, 1))

        with self.assertRaisesRegex(CausalReferenceError, "duplicate"):
            left.prime((observation(300, 104, 0, 0.3),))


if __name__ == "__main__":
    unittest.main()
