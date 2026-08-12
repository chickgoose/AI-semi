#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import adapt_frozen_v4 as adapter
from evaluate_k2 import ContractError, evaluate_documents, evaluate_run
from generate_vectors import build_bundle
from k2_oracle import PolicyState, fold_prefix, validate_vector_bundle
from synthetic_reference import build_reference_evidence


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


class K2CommonEvaluatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = validate_vector_bundle(build_bundle())
        cls.thresholds = json.loads((ROOT / "thresholds.json").read_text(encoding="utf-8"))
        cls.reference = build_reference_evidence(cls.bundle)

    def test_scalar_fold_weight_and_same_row(self) -> None:
        state = PolicyState()
        rows = []
        for _ in range(120):
            grants, state = fold_prefix(range(16), state, 1)
            rows.append(grants[0] // 4)
        self.assertEqual([10, 50, 50, 10], [rows.count(row) for row in range(4)])
        grants, _ = fold_prefix({0, 1}, PolicyState(), 2)
        self.assertEqual([0, 1], grants)

    def test_adversarial_lock_matches_generator(self) -> None:
        lock = json.loads((ROOT / "adversarial-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["bundle_sha256"], self.bundle["bundle_sha256"])
        self.assertEqual(lock["runs"], [
            {"cycles": len(run["cycles"]), "name": run["name"],
             "run_sha256": run["run_sha256"]} for run in self.bundle["runs"]])

    def test_reference_all_required_runs_pass(self) -> None:
        observed = {run["name"]: run for run in self.reference["runs"]}
        results = [evaluate_run(vector, observed[vector["name"]], self.thresholds)
                   for vector in self.bundle["runs"]]
        failures = [failure for result in results for failure in result["hard_failures"]]
        self.assertEqual([], failures)
        reset = next(result for result in results if result["name"] == "reset_abort_no_phantom")
        self.assertEqual(1, reset["accounting"]["reset_aborted_pending"])
        self.assertEqual(2, reset["accounting"]["reset_aborted_inflight"])

    def test_exactly_three_candidates_and_no_duplicate_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            evidence = []
            for index in range(3):
                document = build_reference_evidence(self.bundle, f"candidate-{index}")
                path = Path(temporary) / f"candidate-{index}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                paths.append(path)
                evidence.append((path, document))
            result = evaluate_documents(self.bundle, evidence, self.thresholds)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(["candidate-0", "candidate-1", "candidate-2"],
                             result["comparison"]["pareto_frontier"])
            self.assertTrue(all(pair["verdict"] == "TIE_WITHIN_BANDS"
                                for pair in result["comparison"]["pairwise"]))
            with self.assertRaisesRegex(ContractError, "exactly three"):
                evaluate_documents(self.bundle, evidence[:2], self.thresholds)

    def test_frozen_v4_adapter_rejects_changed_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            trace_root = Path(temporary) / "traces"
            generator = adapter.locate_generator(None)
            adapter.generate_traces(generator, trace_root)
            bundle = adapter.build_bundle(trace_root, "capacity22", 16)
            self.assertEqual(22, len(bundle["runs"]))
            target = trace_root / "core_simultaneous_identity.events.jsonl"
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "SHA mismatch"):
                adapter.build_bundle(trace_root, "capacity22", 16)


if __name__ == "__main__":
    unittest.main()
