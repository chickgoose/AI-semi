#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).with_name("analyze_moving_block.py")
SPEC = importlib.util.spec_from_file_location("a5_w4_analysis", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class MovingBlockAuditTest(unittest.TestCase):
    def test_committed_exact_suite_result_is_complete(self):
        result_path = MODULE_PATH.parents[2] / (
            "docs/research/results/a5_w4_moving_block_audit.json"
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(audit.A4_COMMIT, result["provenance"]["a4_commit"])
        self.assertEqual(50, len(result["suites"]["full50"]["runs"]))
        self.assertEqual(22, len(result["suites"]["capacity22"]["runs"]))
        self.assertEqual(41, result["suites"]["full50"]["aggregate"]["accepted_delta"])
        self.assertEqual(35, result["suites"]["capacity22"]["aggregate"]["accepted_delta"])
        pairwise = result["pairwise_mapping"]["per_mapping_moving_minus_fixed"]
        self.assertEqual(240, pairwise["identity"]["matched_complete_pairs"])
        self.assertEqual(240, pairwise["affine"]["matched_complete_pairs"])

    def test_percentile_is_frozen_nearest_rank(self):
        self.assertEqual(1, audit.percentile([1, 2, 3, 4], 0.01))
        self.assertEqual(2, audit.percentile([1, 2, 3, 4], 0.50))
        self.assertEqual(4, audit.percentile([1, 2, 3, 4], 0.99))
        self.assertIsNone(audit.percentile([], 0.99))

    def test_run_sign_test_ignores_zero_deltas(self):
        self.assertEqual(1.0, audit.sign_test_pvalue([0, 0]))
        self.assertEqual(0.25, audit.sign_test_pvalue([1, 1, 1, 0]))
        self.assertEqual(1.0, audit.sign_test_pvalue([1, -1, 0]))

    def test_fairness_is_demand_normalized(self):
        events = {
            0: audit.ObservedEvent(0, 0, 0, None, None, "delivered", 0, 1),
            1: audit.ObservedEvent(1, 0, 1, None, None, "source_overrun"),
            2: audit.ObservedEvent(2, 1, 2, None, None, "delivered", 2, 3),
        }
        run = audit.Replay("fair", "synthetic", 4, 5, 0, events)
        observed = audit.fairness_document(run)
        # Acceptance ratios are [1/2, 1], rather than raw service counts [1, 1].
        self.assertAlmostEqual(0.9, observed["demand_normalized_jain"])
        self.assertEqual(0.5, observed["min_source_acceptance_ratio"])

    def test_occurrence_id_intersection_exposes_survivor_swap(self):
        def event(event_id, source, latency):
            return audit.ObservedEvent(
                event_id, source, 0, None, None, "delivered", 0, latency - 1
            )
        fixed = audit.Replay("swap", "synthetic", 4, 5, 0,
                             {0: event(0, 0, 2), 1: event(1, 1, 9),
                              2: audit.ObservedEvent(2, 2, 0, None, None,
                                                     "source_overrun")})
        moving = audit.Replay("swap", "synthetic", 4, 5, 0,
                              {0: event(0, 0, 1),
                               1: audit.ObservedEvent(1, 1, 0, None, None,
                                                      "source_overrun"),
                               2: event(2, 2, 8)})
        metadata = {"run": {"workload": "synthetic", "seed": 1},
                    "trace_sha256": "0" * 64}
        row = audit.run_comparison("swap", metadata, fixed, moving)
        self.assertEqual(1, row["matched_accepted"])
        self.assertEqual(1, row["fixed_only"])
        self.assertEqual(1, row["moving_only"])
        self.assertEqual(-1, row["paired_latency_delta_moving_minus_fixed"]["mean"])


if __name__ == "__main__":
    unittest.main()
