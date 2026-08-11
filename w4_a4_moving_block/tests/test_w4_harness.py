from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reference"))

from model import MovingBlockReference  # noqa: E402
from prepare_vectors import load_trace  # noqa: E402


class W4HarnessTest(unittest.TestCase):
    def test_adapter_has_no_behavioral_or_sequential_state(self) -> None:
        adapter = (ROOT / "rtl/a4_w4_zero_state_adapter.sv").read_text()
        for forbidden in ("always_ff", "always_latch", "always_comb", "initial begin"):
            self.assertNotIn(forbidden, adapter)
        self.assertIn("assign native_source_event[source] = 32'(source);", adapter)
        self.assertIn("assign retire_address = native_retire_source;", adapter)

    def test_two_step_fill_precedes_fixed_but_conserves(self) -> None:
        moving = MovingBlockReference(2)
        fixed = MovingBlockReference(1)
        moving_pending = [0] * 16
        fixed_pending = [0] * 16
        for source in range(16):
            token = (1 << 32) | (source << 24) | 1
            moving_pending[source] = token
            fixed_pending[source] = token
        moving_accept = fixed_accept = moving_retire = fixed_retire = 0
        moving_first = fixed_first = None
        for cycle in range(64):
            m = moving.step(moving_pending)
            f = fixed.step(fixed_pending)
            moving_accept += m.ready_mask.bit_count()
            fixed_accept += f.ready_mask.bit_count()
            for source in range(16):
                if (m.ready_mask >> source) & 1:
                    moving_pending[source] = 0
                if (f.ready_mask >> source) & 1:
                    fixed_pending[source] = 0
            if m.retire_valid:
                moving_retire += 1
                moving_first = cycle if moving_first is None else moving_first
            if f.retire_valid:
                fixed_retire += 1
                fixed_first = cycle if fixed_first is None else fixed_first
            if not any(moving_pending) and not any(fixed_pending) and moving.occupancy() == 0 and fixed.occupancy() == 0:
                break
        self.assertEqual(moving_accept, moving_retire)
        self.assertEqual(fixed_accept, fixed_retire)
        self.assertLess(moving_first, fixed_first)

    def test_reset_is_quiet(self) -> None:
        model = MovingBlockReference(2)
        result = model.step([1] * 16, rst_n=False)
        self.assertEqual(0, result.ready_mask)
        self.assertFalse(result.retire_valid)
        self.assertEqual(0, model.occupancy())

    def test_duplicate_source_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="w4-trace-") as temporary:
            trace = Path(temporary) / "duplicate.events.jsonl"
            records = [
                {"occurrence_cycle": 3, "logical_source": 2, "tb_only_event_id": 1},
                {"occurrence_cycle": 3, "logical_source": 2, "tb_only_event_id": 2},
            ]
            trace.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_trace(trace)


if __name__ == "__main__":
    unittest.main()
