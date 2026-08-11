#!/usr/bin/env python3
"""Self-checks for the W4 final economics runner."""

from __future__ import annotations

import unittest
import json
from pathlib import Path

from scripts.w4_a4_final_economics import run
from scripts.w4_a4_moving_block_synth import run as base


class FinalEconomicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.objects = run.pinned_objects(Path("/home/chickgoose/projects/a4"))

    def test_all_git_object_pins_and_filelists(self) -> None:
        self.assertEqual(set(self.objects), set(run.PINS))
        self.assertEqual(
            self.objects[run.W3_FILELIST_PATH].decode(), run.W3_RTL_PATH + "\n"
        )
        self.assertEqual(
            self.objects[run.W4_FILELIST_PATH].decode(), run.W4_RTL_PATH + "\n"
        )

    def test_normalizations_are_frozen_and_state_free(self) -> None:
        result = run.normalize_sources(self.objects)
        self.assertFalse(result["receipt"]["state_or_function_added"])
        self.assertEqual(
            result["receipt"]["w3_normalized_sha256"], run.W3_NORMALIZED_SHA256
        )
        self.assertEqual(
            result["receipt"]["w4_normalized_sha256"], run.W4_NORMALIZED_SHA256
        )
        self.assertIn(b"source_event_flat", result["w3"])

    def test_normalization_mutation_fails_closed(self) -> None:
        mutated = dict(self.objects)
        mutated[run.W4_RTL_PATH] = mutated[run.W4_RTL_PATH].replace(
            b"logic data_write_d [TOTAL_NODES];", b"logic broken_data_write;"
        )
        with self.assertRaises(base.AuditError):
            run.normalize_sources(mutated)

    def test_functional_delta_and_tail_are_exact(self) -> None:
        evidence = run.functional_evidence(self.objects)
        self.assertTrue(evidence["selected_exactly_matches_frozen_max_advance2"])
        self.assertEqual(evidence["suites"]["full50"]["accepted_delta"], 41)
        self.assertEqual(evidence["suites"]["capacity22"]["accepted_delta"], 35)
        self.assertEqual(evidence["suites"]["full50"]["p99_delta"], 1)
        self.assertEqual(evidence["suites"]["capacity22"]["p99_delta"], 1)

    def test_published_six_way_metrics_and_gate(self) -> None:
        report = json.loads(
            (run.REPO_ROOT / "reports/w4_a4_final_economics.json").read_text()
        )
        rows = {
            (row["num_sources"], row["variant"]): row for row in report["runs"]
        }
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[16, "w3_max_advance1"]["total_cells"], 6467)
        self.assertEqual(rows[16, "frozen_max_advance2"]["total_cells"], 11474)
        self.assertEqual(
            rows[16, "shared_clearance_local_enable"]["total_cells"], 7469
        )
        self.assertEqual(rows[64, "w3_max_advance1"]["total_cells"], 29830)
        self.assertEqual(rows[64, "frozen_max_advance2"]["total_cells"], 51132)
        self.assertEqual(
            rows[64, "shared_clearance_local_enable"]["total_cells"], 32620
        )
        self.assertFalse(report["gate"]["selected_as_max2_replacement_pass"])
        self.assertFalse(report["gate"]["selected_over_max1_economic_go"])


if __name__ == "__main__":
    unittest.main()
