#!/usr/bin/env python3
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks" / "clean_slate_aer"))

from a6_codec import decode, encode, link_metrics, raw_bits  # noqa: E402


class A6CodecTest(unittest.TestCase):
    def assert_round_trip(self, addresses):
        encoded = encode(addresses)
        self.assertEqual(addresses, decode(encoded.bits))
        return encoded

    def test_each_token_and_boundary(self):
        addresses = [7, 7, 8, 7, 15, 15, 15, 15, 15, 15, 15, 15, 15, 0]
        encoded = self.assert_round_trip(addresses)
        self.assertEqual(3, encoded.tokens["raw"])
        self.assertEqual(1, encoded.tokens["delta_plus"])
        self.assertEqual(1, encoded.tokens["delta_minus"])
        self.assertEqual(1, encoded.tokens["same"])
        self.assertEqual(1, encoded.tokens["run"])

    def test_long_run_splits_without_count_loss(self):
        addresses = [3] * 100
        encoded = self.assert_round_trip(addresses)
        self.assertEqual(1, encoded.tokens["raw"])
        self.assertGreater(encoded.tokens["run"], 1)

    def test_first_token_requires_raw(self):
        for malformed in ("0", "100000", "110", "111"):
            with self.assertRaises(ValueError):
                decode(malformed)

    def test_truncated_tokens_rejected(self):
        for malformed in ("1", "10", "100", "10001", "101", "101001"):
            with self.assertRaises(ValueError):
                decode(malformed)

    def test_randomized_round_trip(self):
        for seed in range(200):
            rng = random.Random(seed)
            addresses = []
            for _ in range(rng.randrange(0, 2000)):
                mode = rng.randrange(5)
                if addresses and mode == 0:
                    addresses.append(addresses[-1])
                elif addresses and mode == 1 and addresses[-1] < 15:
                    addresses.append(addresses[-1] + 1)
                elif addresses and mode == 2 and addresses[-1] > 0:
                    addresses.append(addresses[-1] - 1)
                else:
                    addresses.append(rng.randrange(16))
            self.assert_round_trip(addresses)

    def test_worst_case_bound(self):
        # Alternating nonlocal values forces RAW after the first address.
        addresses = [0, 8] * 100
        encoded = self.assert_round_trip(addresses)
        self.assertLessEqual(len(encoded.bits), 7 * len(addresses))
        self.assertGreater(len(encoded.bits), 4 * len(addresses))

    def test_fixed_pin_metrics_are_actual_symbols(self):
        addresses = [4] * 9
        encoded = self.assert_round_trip(addresses)
        metrics = link_metrics(encoded.bits, len(addresses))
        raw = link_metrics(raw_bits(addresses), len(addresses))
        self.assertLess(metrics["link_cycles"], raw["link_cycles"])
        self.assertEqual(5, metrics["link_pins"])


if __name__ == "__main__":
    unittest.main()
