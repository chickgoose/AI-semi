from __future__ import annotations

import math
import unittest

from benchmarks.redred_mc_wtb_causal_reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    CausalReferenceError,
    ReferenceObservation,
)


def ray(angle: float):
    return (math.sin(angle), 0.0, math.cos(angle))


class CausalReferenceTests(unittest.TestCase):
    def test_equal_timestamp_cluster_cannot_reference_itself(self) -> None:
        bank = CausalReferenceBank()
        scores = bank.process((
            ReferenceObservation(1, 100, 0, ray(0.0)),
            ReferenceObservation(2, 100, 0, ray(0.1)),
        ))
        self.assertFalse(scores[0].reference_available)
        self.assertFalse(scores[1].reference_available)
        later = bank.process((ReferenceObservation(3, 101, 0, ray(0.05)),))[0]
        self.assertTrue(later.reference_available)
        self.assertAlmostEqual(later.angular_cost_rad, 0.05)
        self.assertEqual(later.reference_timestamp_ns, 100)
        self.assertEqual(later.reference_age_ns, 1)

    def test_banks_are_polarity_separated(self) -> None:
        bank = CausalReferenceBank()
        bank.process((ReferenceObservation(1, 1, 0, ray(0.0)),))
        score = bank.process((ReferenceObservation(2, 2, 1, ray(0.0)),))[0]
        self.assertFalse(score.reference_available)

    def test_age_and_capacity_are_bounded(self) -> None:
        bank = CausalReferenceBank(CausalReferenceConfig(2, 5))
        bank.process(tuple(ReferenceObservation(i, i, 0, ray(i / 100.0)) for i in range(3)))
        self.assertEqual(bank.occupancy(), (2, 0))
        score = bank.process((ReferenceObservation(3, 20, 0, ray(0.0)),))[0]
        self.assertFalse(score.reference_available)
        self.assertEqual(bank.occupancy(), (1, 0))

    def test_rejects_time_reversal(self) -> None:
        bank = CausalReferenceBank()
        bank.process((ReferenceObservation(1, 10, 0, ray(0.0)),))
        with self.assertRaisesRegex(CausalReferenceError, "backwards"):
            bank.process((ReferenceObservation(2, 9, 0, ray(0.0)),))

    def test_rejects_split_timestamp_duplicate_and_noninteger_identity(self) -> None:
        with self.assertRaisesRegex(CausalReferenceError, "duplicate"):
            CausalReferenceBank().process((
                ReferenceObservation(7, 1, 0, ray(0.0)),
                ReferenceObservation(7, 2, 0, ray(0.1)),
            ))
        bank = CausalReferenceBank()
        bank.process((ReferenceObservation(1, 10, 0, ray(0.0)),))
        with self.assertRaisesRegex(CausalReferenceError, "split across calls"):
            bank.process((ReferenceObservation(2, 10, 0, ray(0.1)),))
        with self.assertRaisesRegex(CausalReferenceError, "duplicate"):
            bank.process((ReferenceObservation(1, 11, 0, ray(0.1)),))
        with self.assertRaisesRegex(CausalReferenceError, "identity"):
            ReferenceObservation(True, 12, 0, ray(0.0))

    def test_age_boundary_includes_exact_limit_and_excludes_one_more(self) -> None:
        bank = CausalReferenceBank(CausalReferenceConfig(4, 5))
        bank.process((ReferenceObservation(1, 10, 0, ray(0.0)),))
        exact = bank.process((ReferenceObservation(2, 15, 0, ray(0.1)),))[0]
        self.assertTrue(exact.reference_available)
        later = bank.process((ReferenceObservation(3, 21, 0, ray(0.2)),))[0]
        self.assertFalse(later.reference_available)


if __name__ == "__main__":
    unittest.main()
