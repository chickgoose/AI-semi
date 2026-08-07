import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks/clean_slate_aer"))

from a6_v3_break_even import (  # noqa: E402
    Event, charged_storage_bits, encode_length, physical_pins, simulate,
)


class A6V3BreakEvenTest(unittest.TestCase):
    def test_pin_and_equalized_storage_accounting(self):
        self.assertEqual([physical_pins(width) for width in (1, 2, 4)], [3, 5, 8])
        self.assertEqual([charged_storage_bits(block) for block in (4, 8, 16, 32)],
                         [74, 138, 266, 522])

    def test_nonexpanding_arbitrary_blocks(self):
        previous = None
        for block_size in (4, 8, 16, 32):
            values = [(index * 7 + 3) & 15 for index in range(block_size)]
            bits, _ = encode_length(values, previous, block_size, True)
            self.assertLessEqual(bits, 4 * block_size)
            previous = values[-1]

    def test_ping_pong_conservation_and_service_monotonicity(self):
        events = [Event(index, (index // 4) & 15) for index in range(128)]
        for block_size in (4, 8, 16, 32):
            for width in (1, 2, 4):
                raw = simulate(events, 128, block_size, width, False)
                codec = simulate(events, 128, block_size, width, True)
                self.assertGreaterEqual(codec.accepted, raw.accepted)
                self.assertEqual(codec.offered, codec.accepted + codec.overrun)
                self.assertEqual(codec.accepted, codec.delivered)


if __name__ == "__main__":
    unittest.main()
