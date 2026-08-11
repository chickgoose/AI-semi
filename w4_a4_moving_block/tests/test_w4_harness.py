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
    def test_claims_are_scoped_and_complete_qualification_holds(self) -> None:
        receipt = json.loads(
            (ROOT / "results/qualification.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["decision"], "HOLD")
        self.assertEqual(receipt["complete_common_qualification"], "HOLD")
        self.assertEqual(receipt["evidence_result"], "PASS")
        self.assertEqual(
            receipt["evidence_scope"],
            "always-ready generator-v4 full50+capacity22 actual-RTL lockstep",
        )
        self.assertEqual(receipt["economic_gate"], "NO-GO")
        self.assertTrue(
            receipt["evidence_origin"]["correction_applied_without_replay"]
        )
        self.assertEqual(
            receipt["evidence_origin"]["historical_execution_commit"],
            "aef76b8a7def52fa7ea407227f8e54eae0f550f4",
        )
        missing = receipt["missing_qualification_evidence"]
        self.assertIn("mandatory direct-SV basic_reset_drain", missing)
        self.assertIn(
            "immutable simulator executable/package/tool-invocation receipt", missing
        )
        self.assertEqual(
            receipt["provenance"]["tool_receipt_status"],
            "MISSING_IMMUTABLE_TOOL_RECEIPT_VERSION_STRING_ONLY",
        )

    def test_no_broad_common_qualification_pass_sentinel_remains(self) -> None:
        checked = [
            ROOT / "README.md",
            ROOT / "docs/w4_a2_report.md",
            ROOT / "execute_regression.py",
            ROOT / "run_w4.sh",
            ROOT / "tb/a4_w4_common_tb.sv",
        ]
        joined = "\n".join(path.read_text(encoding="utf-8") for path in checked)
        self.assertNotIn("W4_A4_COMMON_QUALIFICATION_PASS", joined)
        self.assertNotIn("W4_A2_PASS", joined)
        self.assertNotIn("PASS for common functional qualification", joined)
        self.assertIn("complete_common_qualification=HOLD", joined)

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

    def test_initial_reset_preamble_is_quiet(self) -> None:
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
