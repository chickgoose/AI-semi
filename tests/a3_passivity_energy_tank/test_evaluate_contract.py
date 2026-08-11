#!/usr/bin/env python3
"""Fail-closed provenance and automation-exit tests for the W3 evaluator."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.a3_passivity_energy_tank import evaluate


class EvaluateContractTest(unittest.TestCase):
    def test_current_generator_and_official_run_sets_match_pins(self) -> None:
        provenance = evaluate.validate_provenance()
        self.assertTrue(provenance["ok"], provenance["errors"])
        self.assertEqual(provenance["generator"]["actual_version"], "4.0")
        self.assertEqual(provenance["full50"]["actual_run_count"], 50)
        self.assertEqual(provenance["capacity22"]["actual_run_count"], 22)

    def test_byte_mutation_fails_sha_pin_even_when_run_set_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mutated = Path(directory) / "full.json"
            mutated.write_bytes(evaluate.FULL_MANIFEST.read_bytes() + b"\n")
            result = evaluate.verify_manifest_pin(
                mutated,
                evaluate.EXPECTED_FULL_MANIFEST_SHA256,
                evaluate.EXPECTED_FULL_RUNS,
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["sha256_matches"])
        self.assertTrue(result["run_order_matches"])

    def test_run_name_mutation_fails_even_with_matching_mutated_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mutated = Path(directory) / "full.json"
            payload = json.loads(evaluate.FULL_MANIFEST.read_text(encoding="utf-8"))
            payload["runs"][0]["name"] = "mutated_run_name"
            mutated.write_text(json.dumps(payload), encoding="utf-8")
            mutated_sha = hashlib.sha256(mutated.read_bytes()).hexdigest()
            result = evaluate.verify_manifest_pin(
                mutated, mutated_sha, evaluate.EXPECTED_FULL_RUNS
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["sha256_matches"])
        self.assertFalse(result["run_order_matches"])

    def test_generator_version_mutation_fails_closed(self) -> None:
        provenance = evaluate.validate_provenance(generator_version="4.1")
        self.assertFalse(provenance["ok"])
        self.assertIn("generator: generator version mismatch", provenance["errors"])

    def test_provenance_failure_exits_before_replay_and_preserves_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutated = root / "full.json"
            receipt = root / "diagnostic.json"
            mutated.write_bytes(evaluate.FULL_MANIFEST.read_bytes() + b"\n")
            with mock.patch.object(evaluate, "build_report") as build_report:
                with redirect_stdout(io.StringIO()):
                    exit_code = evaluate.main([
                        "--full-manifest", str(mutated), "--output", str(receipt)
                    ])
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertFalse((root / "diagnostic.json.tmp").exists())
        self.assertEqual(exit_code, evaluate.EXIT_PROVENANCE_MISMATCH)
        build_report.assert_not_called()
        self.assertEqual(payload["diagnostic"]["code"], "PROVENANCE_MISMATCH")
        self.assertFalse(payload["provenance"]["ok"])

    @staticmethod
    def no_go_report(provenance: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "architecture": "a3_passivity_energy_tank_credit_fabric",
            "provenance": provenance,
            "parameters": {},
            "directed_energy_island": {"counterexample_pass": True},
            "exhaustive_n16": {"skipped": True},
            "full50": {
                "manifest": "full",
                "aggregate": {},
                "randomized_ready_trials_per_trace": 0,
                "randomized_invariant_replays": 0,
            },
            "capacity22": {
                "manifest": "cap",
                "aggregate": {},
                "randomized_ready_trials_per_trace": 0,
                "randomized_invariant_replays": 0,
            },
            "go_gate": {
                "go": False,
                "sv_permitted": False,
                "checks": {"strict_target_benefit": False},
            },
        }

    def test_require_go_returns_nonzero_after_atomic_diagnostic_receipt(self) -> None:
        provenance = evaluate.validate_provenance()
        report = self.no_go_report(provenance)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "required-go.json"
            with mock.patch.object(evaluate, "build_report", return_value=report):
                with redirect_stdout(io.StringIO()):
                    exit_code = evaluate.main([
                        "--require-go", "--compact", "--output", str(receipt)
                    ])
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertFalse((root / "required-go.json.tmp").exists())
        self.assertEqual(exit_code, evaluate.EXIT_REQUIRED_GO_FAILED)
        self.assertFalse(payload["go_gate"]["go"])
        self.assertEqual(payload["diagnostic"]["code"], "REQUIRED_GO_FAILED")
        self.assertEqual(
            payload["diagnostic"]["failed_checks"], ["strict_target_benefit"]
        )


if __name__ == "__main__":
    unittest.main()
