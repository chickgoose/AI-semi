#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import hashlib
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
                self.assertGreater(row["total_cell_cost_ratio"], 1.09)
                self.assertLess(row["throughput_per_total_cell_ratio"], 0.92)
                self.assertGreater(
                    row["break_even_throughput_gain_percent"],
                    80 * row["observed_throughput_gain_percent"],
                )

    def test_selected_max1_six_way_counts_are_exact(self):
        n16 = self.document["same_flow_cost"]["n16"]["raw"]
        n64 = self.document["same_flow_cost"]["n64"]["raw"]
        self.assertEqual((6467, 5305, 13, 4107, 6998, 42, 91, 11607),
                         tuple(n16["max1"][key] for key in (
                             "total_cells", "comb_cells", "comb_depth_cells",
                             "net_count", "net_bit_count", "max_fanout_data",
                             "data_nets_fanout_ge16", "wire_data_sink_pin_proxy")))
        self.assertEqual((7469, 6307, 23, 5080, 8001, 39, 137, 16081),
                         tuple(n16["selected"][key] for key in (
                             "total_cells", "comb_cells", "comb_depth_cells",
                             "net_count", "net_bit_count", "max_fanout_data",
                             "data_nets_fanout_ge16", "wire_data_sink_pin_proxy")))
        self.assertEqual((29830, 24814, 18, 19712, 31945, 55, 398, 53674),
                         tuple(n64["max1"][key] for key in (
                             "total_cells", "comb_cells", "comb_depth_cells",
                             "net_count", "net_bit_count", "max_fanout_data",
                             "data_nets_fanout_ge16", "wire_data_sink_pin_proxy")))
        self.assertEqual((32620, 27604, 31, 22377, 34736, 66, 566, 70060),
                         tuple(n64["selected"][key] for key in (
                             "total_cells", "comb_cells", "comb_depth_cells",
                             "net_count", "net_bit_count", "max_fanout_data",
                             "data_nets_fanout_ge16", "wire_data_sink_pin_proxy")))

    def test_predeclared_depth_gate_is_no_go(self):
        frozen = self.document["a4_predeclared_gate"]
        self.assertEqual("NO_GO", frozen["decision"])
        self.assertEqual(10, frozen["checks"]["n16"]["depth_premium_levels"])
        self.assertEqual(13, frozen["checks"]["n64"]["depth_premium_levels"])
        self.assertFalse(frozen["checks"]["n16"]["checks"]["depth_levels"])
        self.assertFalse(frozen["checks"]["n64"]["checks"]["depth_fraction"])

    def test_d1e979e_is_historical_not_formal(self):
        excluded = self.document["historical_cost_excluded"]
        self.assertTrue(excluded["commit"].startswith("d1e979e"))
        self.assertEqual("EXTERNAL_HISTORICAL_DIAGNOSTIC_ONLY",
                         excluded["classification"])

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

    def test_committed_byte_receipt_binds_result_and_producer(self):
        receipt_path = PROJECT / (
            "docs/research/results/a5_w4_moving_block_final_gate.receipt.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        result_path = PROJECT / "docs/research/results/a5_w4_moving_block_final_gate.json"
        producer_path = PROJECT / receipt["producer"]
        self.assertEqual(hashlib.sha256(result_path.read_bytes()).hexdigest(),
                         receipt["artifact_sha256"])
        self.assertEqual(hashlib.sha256(producer_path.read_bytes()).hexdigest(),
                         receipt["producer_sha256"])
        self.assertEqual("REJECT_AS_DEFAULT_REPLACEMENT", receipt["decision"])
        self.assertEqual(4, receipt["decision_exit"])


if __name__ == "__main__":
    unittest.main()
