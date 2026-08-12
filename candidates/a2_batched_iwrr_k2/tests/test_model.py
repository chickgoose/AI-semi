from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

from batched_iwrr_k2 import CALENDAR, Scheduler, offer, pick_column  # noqa: E402


class CalendarProofTest(unittest.TestCase):
    def test_minimal_exact_cyclic_calendar(self) -> None:
        self.assertEqual(12, len(CALENDAR))
        self.assertEqual([1, 5, 5, 1], [CALENDAR.count(i) for i in range(4)])
        self.assertTrue(all(CALENDAR[i] != CALENDAR[(i + 1) % 12] for i in range(12)))
        for rotation in range(12):
            window = tuple(CALENDAR[(rotation + i) % 12] for i in range(12))
            self.assertEqual([1, 5, 5, 1], [window.count(i) for i in range(4)])

    def test_full_demand_k2_exact_period(self) -> None:
        model = Scheduler()
        rows: list[int] = []
        for _ in range(6):
            result = model.cycle(0xFFFF, True)
            self.assertEqual(2, result.count)
            self.assertNotEqual(*result.address)
            rows.extend(address // 4 for address in result.address)
        self.assertEqual([1, 5, 5, 1], [rows.count(i) for i in range(4)])
        self.assertEqual(0, model.cursor)


class ExhaustiveSelectionTest(unittest.TestCase):
    def test_exhaustive_row_picker(self) -> None:
        for row_mask in range(16):
            for pointer in range(4):
                chosen = pick_column(row_mask, pointer)
                expected = next(
                    (column for offset in range(4)
                     if row_mask & (1 << (column := (pointer + offset) & 3))),
                    None,
                )
                self.assertEqual(expected, chosen)

    def test_exhaustive_n16_bitmap_cursor_uniform_pointer_states(self) -> None:
        # 65,536 bitmaps x twelve token cursors x four pointer rotations.
        cases = 0
        for req in range(1 << 16):
            for cursor in range(12):
                for pointer in range(4):
                    result = offer(req, cursor, (pointer,) * 4)
                    cases += 1
                    self.assertEqual(result.bitmap.bit_count(), result.count)
                    for lane in range(result.count):
                        source = result.address[lane]
                        self.assertTrue(req & (1 << source))
                        self.assertTrue(result.bitmap & (1 << source))
                    if result.count == 2:
                        self.assertNotEqual(*result.address)
                    self.assertEqual((cursor + result.count) % 12, result.next_cursor)
        self.assertEqual(3_145_728, cases)


class SparseAndHandshakeTest(unittest.TestCase):
    def test_sparse_cyclic_fallback_without_credit(self) -> None:
        model = Scheduler()
        # Token zero prefers row 1; a sole row-3 request is selected by cyclic
        # fallback and consumes exactly one microstep without creating debt.
        result = model.cycle(1 << 12, True)
        self.assertEqual(1, result.count)
        self.assertEqual(12, result.address[0])
        self.assertEqual(1, model.cursor)
        self.assertEqual((0, 0, 0, 1), model.pointers)

    def test_stall_freezes_all_state_and_offer(self) -> None:
        model = Scheduler()
        first = model.cycle(0xA55A, False)
        state = model.cursor, model.pointers
        for changed_req in (0, 0xFFFF, 0x0001, 0x8000, 0x5AA5):
            self.assertEqual(first, model.cycle(changed_req, False))
            self.assertEqual(state, (model.cursor, model.pointers))
        committed = model.cycle(0, True)
        self.assertEqual(first, committed)
        self.assertEqual(first.next_cursor, model.cursor)
        self.assertIsNone(model.held)

    def test_exact_once_clear_on_accept(self) -> None:
        model = Scheduler()
        req = 0xFFFF
        accepted: list[int] = []
        for _ in range(24):
            if not req:
                break
            result = model.cycle(req, True)
            for lane in range(result.count):
                source = result.address[lane]
                self.assertTrue(req & (1 << source))
                accepted.append(source)
            req &= ~result.bitmap
        self.assertEqual(16, len(accepted))
        self.assertEqual(16, len(set(accepted)))
        self.assertEqual(0, req)

    def test_row_local_round_robin(self) -> None:
        model = Scheduler()
        seen = {row: [] for row in range(4)}
        for _ in range(24):
            result = model.cycle(0xFFFF, True)
            for source in result.address[:result.count]:
                seen[source // 4].append(source & 3)
        for sequence in seen.values():
            self.assertEqual([i & 3 for i in range(len(sequence))], sequence)

    def test_count_microstep_commit_and_empty_freeze(self) -> None:
        model = Scheduler()
        self.assertEqual(0, model.cycle(0, True).count)
        self.assertEqual(0, model.cursor)
        one = model.cycle(1 << 12, True)
        self.assertEqual(1, one.count)
        self.assertEqual(1, model.cursor)
        two = model.cycle(0xFFFF, True)
        self.assertEqual(2, two.count)
        self.assertEqual(3, model.cursor)


if __name__ == "__main__":
    unittest.main()
