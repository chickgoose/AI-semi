#!/usr/bin/env python3
import pathlib
import random
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks" / "clean_slate_aer"))

from a6_v2_codec import Block, decode, decode_block, encode, encode_block  # noqa: E402


class A6V2CodecTest(unittest.TestCase):
    def test_modes(self):
        raw = encode_block([0, 8, 2, 10, 4, 12, 6, 14])
        token = encode_block([3] * 8)
        dictionary = encode_block([5, 6, 9, 10] * 4)
        self.assertEqual("raw", raw.mode)
        self.assertEqual("token", token.mode)
        self.assertEqual("dictionary", dictionary.mode)
        for original, block in (([0, 8, 2, 10, 4, 12, 6, 14], raw),
                                ([3] * 8, token),
                                ([5, 6, 9, 10] * 4, dictionary)):
            self.assertEqual(original, decode_block(block))
            self.assertLessEqual(len(block.bits), 4 * len(original))

    def test_randomized_roundtrip_and_bound(self):
        for seed in range(500):
            rng = random.Random(seed)
            values = [rng.randrange(16) for _ in range(rng.randrange(1, 513))]
            blocks = encode(values)
            self.assertEqual(values, decode(blocks))
            self.assertTrue(all(len(block.bits) <= 4 * block.event_count
                                for block in blocks))

    def test_uniform_adversarial_is_exact_raw(self):
        values = [0, 8, 2, 10, 4, 12, 6, 14] * 2
        block = encode_block(values)
        self.assertEqual("raw", block.mode)
        self.assertEqual(64, len(block.bits))

    def test_malformed_footer_and_count_rejected(self):
        good = encode_block([7] * 8)
        with self.assertRaises(ValueError):
            decode_block(Block(good.bits[:-2] + "11", good.mode, good.event_count))
        with self.assertRaises(ValueError):
            decode_block(Block(good.bits, good.mode, good.event_count - 1))


if __name__ == "__main__":
    unittest.main()
