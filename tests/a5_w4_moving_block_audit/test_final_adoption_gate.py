#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location(
    "a5_w4_final_gate", HERE / "compute_final_adoption_gate.py"
)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)
PROJECT = HERE.parents[1]


class FinalAdoptionGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = gate.evaluate(
            PROJECT / "docs/research/results/a5_w4_moving_block_audit.json",
            gate.A4_REPO_DEFAULT, gate.A3_REPO_DEFAULT, gate.A9_REPO_DEFAULT,
        )

    def test_decision_fails_on_tail_and_cost_not_on_correctness(self):
        self.assertEqual("REJECT_AS_DEFAULT_REPLACEMENT", self.document["decision"])
        hard = self.document["hard_gates"]
        self.assertTrue(hard["exact_functional_equivalence"])
        self.assertTrue(hard["capacity_direction_detected_both_suites"])
        self.assertFalse(hard["matched_tail_non_regression_both_suites"])
        self.assertFalse(hard["same_flow_throughput_per_cell_break_even_both_suites"])

    def test_churn_and_matched_p99_are_not_lost(self):
        full = self.document["suites"]["full50"]
        capacity = self.document["suites"]["capacity22"]
        self.assertEqual((41, 11023),
                         (full["accepted_delta"], full["discordant_accepted_ids"]))
        self.assertEqual((35, 10841),
                         (capacity["accepted_delta"], capacity["discordant_accepted_ids"]))
        self.assertEqual((46, 46),
                         (full["matched_tail"]["p99"]["fixed"],
                          full["matched_tail"]["p99"]["moving"]))
        self.assertEqual((46, 47),
                         (capacity["matched_tail"]["p99"]["fixed"],
                          capacity["matched_tail"]["p99"]["moving"]))

    def test_same_flow_cost_efficiency_is_below_break_even(self):
        for suite in ("full50", "capacity22"):
            for size in ("n16", "n64"):
                row = self.document["suites"][suite]["same_flow_efficiency"][size]
                self.assertGreater(row["total_cell_cost_ratio"], 1.7)
                self.assertLess(row["throughput_per_total_cell_ratio"], 0.59)
                self.assertGreater(
                    row["break_even_throughput_gain_percent"],
                    600 * row["observed_throughput_gain_percent"],
                )

    def test_a4_optimized_counts_are_not_promoted_to_same_flow_evidence(self):
        diagnostic = self.document["a4_optimized_cost_diagnostic"]
        self.assertEqual("DIAGNOSTIC_ONLY_NOT_SAME_FLOW",
                         diagnostic["n16"]["classification"])
        self.assertEqual("DIAGNOSTIC_ONLY_NOT_SAME_FLOW",
                         diagnostic["n64"]["classification"])

    def test_a9_citation_is_only_partial(self):
        audit = self.document["a9_citation_audit"]
        self.assertEqual("PARTIAL_CAVEAT_INADEQUATE_FOR_MATCHED_COHORT_CLAIM",
                         audit["verdict"])
        self.assertIn("11,023", audit["correct_restatement"]["full50"])
        self.assertIn("46->47", audit["correct_restatement"]["capacity22"])

    def test_committed_gate_matches_pinned_recalculation(self):
        path = PROJECT / "docs/research/results/a5_w4_moving_block_final_gate.json"
        committed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(self.document, committed)


if __name__ == "__main__":
    unittest.main()
