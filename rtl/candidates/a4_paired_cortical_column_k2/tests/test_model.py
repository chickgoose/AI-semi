#!/usr/bin/env python3
"""Directed semantic tests for the independent PCC-K2 model."""

from __future__ import annotations

import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from model import PairedCorticalColumnK2  # noqa: E402


class ModelTest(unittest.TestCase):
    def test_persistent_epoch_aggregate(self) -> None:
        model = PairedCorticalColumnK2()
        rows = [0, 0, 0, 0]
        for _ in range(60):
            result = model.step(0xFFFF, True)
            self.assertEqual(result.grant_count, 2)
            for source in (result.grant_addr0, result.grant_addr1):
                rows[source >> 2] += 1
        self.assertEqual(rows, [10, 50, 50, 10])

    def test_atomic_stall_freezes_offer_and_policy(self) -> None:
        model = PairedCorticalColumnK2()
        before = model.policy_state()
        offers = []
        for _ in range(7):
            result = model.step(0xFFFF, False)
            offers.append((result.grant_count, result.grant_addr0, result.grant_addr1))
            self.assertEqual(result.source_ready, 0)
            self.assertEqual(model.policy_state(), before)
        self.assertEqual(len(set(offers)), 1)
        committed = model.step(0xFFFF, True)
        self.assertEqual(committed.grant_count, 2)
        self.assertEqual(committed.source_ready.bit_count(), 2)
        self.assertNotEqual(model.policy_state(), before)

    def test_fallback_debt_saturates_without_wrap(self) -> None:
        model = PairedCorticalColumnK2(debt_width=2)
        for _ in range(20):
            result = model.step(0x000F, True)
            self.assertGreaterEqual(result.grant_count, 1)
            self.assertTrue(all(value <= model.debt_max for value in model.debt))
        self.assertIn(model.debt_max, model.debt)
        phase_token = (model.phase, model.token)
        for _ in range(4):
            model.step(0x000F, True)
        self.assertEqual((model.phase, model.token), phase_token)
        self.assertIn(model.debt_max, model.debt)

    def test_reset_discards_blocked_offer_and_history(self) -> None:
        model = PairedCorticalColumnK2()
        model.step(0xFFFF, False)
        self.assertIsNotNone(model.hold_requests)
        reset = model.step(0, False, rst_n=False)
        self.assertEqual(reset.grant_count, 0)
        self.assertTrue(reset.drain_idle)
        self.assertIsNone(model.hold_requests)
        self.assertEqual(model.policy_state(), (0, 0, (0, 0, 0, 0), (0, 0, 0, 0), 0, 0))

    def test_reset_is_quiet_with_live_inputs(self) -> None:
        model = PairedCorticalColumnK2()
        result = model.step(0xFFFF, True, rst_n=False)
        self.assertEqual(result.source_ready, 0)
        self.assertEqual(result.grant_count, 0)
        self.assertEqual((result.grant_addr0, result.grant_addr1), (0, 0))
        self.assertTrue(result.drain_idle)

    def test_row_local_column_rotation(self) -> None:
        model = PairedCorticalColumnK2()
        columns = []
        for _ in range(4):
            result = model.step(0x00F0, True)
            self.assertEqual(result.grant_count, 1)
            columns.append(result.grant_addr0 & 3)
        self.assertEqual(columns, [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
