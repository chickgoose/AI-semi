#!/usr/bin/env python3
"""Self-checking tests for the A3 passivity energy-tank cycle model."""

from __future__ import annotations

import random
import unittest

from tests.a3_passivity_energy_tank.evaluate import directed_energy_island
from tests.a3_passivity_energy_tank.passivity_model import CreditFabric, Mode


class PassivityModelTest(unittest.TestCase):
    def test_directed_energy_island_and_escape(self) -> None:
        result = directed_energy_island()
        self.assertTrue(result["counterexample_pass"])
        self.assertEqual(result[Mode.RAW.value]["retired_after_8"], 0)
        self.assertEqual(result[Mode.RAW.value]["pending_after_8"], 2)
        self.assertEqual(result[Mode.ESCAPE.value]["retired_after_8"], 3)
        self.assertEqual(result[Mode.ESCAPE.value]["pending_after_8"], 0)
        raw_history = result[Mode.RAW.value]["history"]
        self.assertTrue(any(step["progress"] == 0 for step in raw_history[3:]))

    def test_conservation_energy_and_potential_randomized(self) -> None:
        for mode in Mode:
            fabric = CreditFabric(mode=mode, energy_max=1)
            rng = random.Random(0xA3E)
            for cycle in range(512):
                occurrence = rng.randrange(1 << 16) if cycle < 384 else 0
                ready = 0b1111 if cycle % 8 == 7 else rng.randrange(1 << 4)
                before = fabric.potential()
                fabric.step(occurrence, ready)
                if occurrence == 0:
                    self.assertLessEqual(fabric.potential(), before)
                self.assertGreaterEqual(min(fabric.energy), 0)
                self.assertEqual(
                    fabric.metrics.accepted - fabric.metrics.retired,
                    fabric.stored_count(),
                )
            fabric.drain(limit=4096)
            self.assertEqual(
                fabric.metrics.generated,
                fabric.metrics.overrun + fabric.metrics.retired,
            )

    def test_exact_pending_and_per_source_order(self) -> None:
        fabric = CreditFabric(mode=Mode.ESCAPE, energy_max=1)
        for cycle in range(64):
            occurrence = (1 << (cycle % 4)) | (1 << ((cycle % 4) + 4))
            fabric.step(occurrence, 0b1111)
        fabric.drain()
        self.assertTrue(fabric.quiescent())
        for source, token in enumerate(fabric.last_retired):
            self.assertEqual(token, fabric.next_token[source] - 1)

    def test_escape_is_stateless_and_minimal_cost(self) -> None:
        baseline = CreditFabric(mode=Mode.BASELINE, energy_max=1)
        raw = CreditFabric(mode=Mode.RAW, energy_max=1)
        escaped = CreditFabric(mode=Mode.ESCAPE, energy_max=1)
        self.assertEqual(raw.state_bits(), escaped.state_bits())
        self.assertEqual(escaped.state_bits() - baseline.state_bits(), 4)
        occurrence = sum(1 << source for source in (0, 4, 8, 12))
        raw.step(occurrence)
        escaped.step(occurrence)
        self.assertEqual(raw.metrics.bootstrap_admissions, 0)
        self.assertEqual(escaped.metrics.bootstrap_admissions, 3)
        self.assertEqual(raw.energy, [0, 0, 0, 0])
        self.assertEqual(escaped.energy, [0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
