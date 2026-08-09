#!/usr/bin/env python3
"""Structural guard for fail-closed mixed-phase common runner wiring."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
RUNNERS = (
    ROOT / "scripts" / "run_common_multilane_candidate.sh",
    ROOT / "scripts" / "run_common_multilane_benchmark.sh",
)


class CommonMultilaneMixedRunnerContractTest(unittest.TestCase):
    def test_both_common_runners_require_fresh_qualified_outputs(self):
        for runner in RUNNERS:
            with self.subTest(runner=runner.name):
                text = runner.read_text(encoding="utf-8")
                self.assertIn('--require-qualified --output "$output_path"', text)
                self.assertIn('"$summary_path" -nt "$freshness_marker"', text)
                self.assertIn('"$output_path" -nt "$freshness_marker"', text)
                self.assertIn('"$event_result" -nt "$freshness_marker"', text)
                self.assertIn('clear_mixed_outputs "$trace_stem"', text)
                self.assertIn('"$result_root/trace.events.csv"', text)
                self.assertRegex(
                    text,
                    r'analyze_mixed_phase[\s\S]{0,240}"\$freshness_marker"',
                    "mixed analyzer must receive the per-run marker",
                )
                self.assertEqual(
                    text.count("mixed_phase_always_ready_metrics.py"), 1,
                    "the outer common runner must own exactly one analyzer call",
                )


if __name__ == "__main__":
    unittest.main()
