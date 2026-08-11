#!/usr/bin/env python3
"""Self-checks for the A4 W4 synthesis audit runner."""

from __future__ import annotations

import unittest

from scripts.w4_a4_moving_block_synth import run


class W4RunnerTest(unittest.TestCase):
    def test_frozen_commit_source_and_filelist(self) -> None:
        result = run.verify_a4(run.Path("/home/chickgoose/projects/a4"))
        self.assertEqual(result["commit"], run.A4_COMMIT)
        self.assertEqual(result["rtl_sha256"], run.RTL_SHA256)
        self.assertEqual(result["filelist_entries"], [run.RTL_PATH])

    def test_normalization_is_exactly_syntax_only(self) -> None:
        result = run.verify_a4(run.Path("/home/chickgoose/projects/a4"))
        normalized, receipt = run.normalize_yosys_port(result["rtl_bytes"])
        text = normalized.decode("utf-8")
        self.assertEqual(receipt["rewrite_count"], 9)
        self.assertFalse(receipt["state_or_logic_added"])
        self.assertIn(run.FLAT_PORT_DECL, text)
        self.assertNotIn(run.PORT_DECL, text)
        for old, new in run.ARRAY_DECLARATION_REWRITES.items():
            self.assertNotIn(old, text)
            self.assertIn(new, text)

    def test_normalization_fails_closed_on_source_mutation(self) -> None:
        result = run.verify_a4(run.Path("/home/chickgoose/projects/a4"))
        mutated = result["rtl_bytes"].replace(
            run.PORT_DECL.encode(), b"input logic broken_source_event,"
        )
        with self.assertRaises(run.AuditError):
            run.normalize_yosys_port(mutated)

    def test_warning_latch_and_unresolved_are_fail_closed(self) -> None:
        self.assertEqual(run.fail_closed_log_findings("No latch inferred.\n"), {})
        for text, category in (
            ("Warning: replaced object\n", "warning"),
            ("Latch inferred for signal x\n", "latch"),
            ("implicitly declared wire x\n", "unresolved"),
        ):
            self.assertIn(category, run.fail_closed_log_findings(text))


if __name__ == "__main__":
    unittest.main()
