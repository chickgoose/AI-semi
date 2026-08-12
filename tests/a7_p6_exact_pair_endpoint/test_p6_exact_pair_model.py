#!/usr/bin/env python3

import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).with_name("p6_exact_pair_model.py")
SPEC = importlib.util.spec_from_file_location("p6_exact_pair_model", MODULE_PATH)
assert SPEC and SPEC.loader
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


class P6ModelTest(unittest.TestCase):
    def test_exhaustive_round_trip(self) -> None:
        self.assertEqual(MODEL.self_test(), {"transactions": 272, "unique_words": 272})

    def test_order_is_not_commutative(self) -> None:
        self.assertNotEqual(MODEL.encode(2, 3, 12), MODEL.encode(2, 12, 3))

    def test_malformed_words_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):
            MODEL.decode(0x100)
        with self.assertRaisesRegex(ValueError, "singleton"):
            MODEL.decode(0x00F)


if __name__ == "__main__":
    unittest.main()
