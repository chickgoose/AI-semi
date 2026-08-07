#!/usr/bin/env python3
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks" / "clean_slate_aer"))

from a6_v2_entropy import block_result  # noqa: E402


class A6V2BoundTest(unittest.TestCase):
    def test_every_block_has_raw_upper_bound(self):
        for block_size in (1, 4, 8, 16, 32):
            for addresses in ([0, 8] * 37, list(range(16)) * 9, [3] * 101):
                result = block_result(addresses, block_size)
                self.assertLessEqual(result["bits"], 4 * len(addresses))

    def test_random_raw_bypass(self):
        result = block_result([0, 8, 2, 10, 4, 12, 6, 14] * 8, 8)
        self.assertEqual(4.0, result["bits_per_event"])
        self.assertEqual(1.0, result["raw_bypass_ratio"])

    def test_repeat_can_break_even(self):
        result = block_result([5] * 32, 8)
        self.assertLess(result["bits_per_event"], 2.0)
        self.assertEqual(0.0, result["raw_bypass_ratio"])


if __name__ == "__main__":
    unittest.main()
