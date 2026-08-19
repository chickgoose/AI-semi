#!/usr/bin/env python3
"""Mutation tests for scalar-free diagnostic A2/A3 selection."""

from __future__ import annotations

import copy
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts/redred_diagnostic_candidate_selection"
VERIFY_PATH = CONTRACT_DIR / "verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_diagnostic_selection", VERIFY_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class DiagnosticSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = verifier.load_json(CONTRACT_DIR / "contract.json")
        paths = verifier.verify_input_pins(ROOT, cls.contract)
        cls.digital, cls.physical = verifier.collect_receipts(ROOT, paths)

    def run_cli(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(VERIFY_PATH)], cwd=ROOT,
                              text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, check=False)

    def receipt(self, contract=None, digital=None, physical=None):
        return verifier.compute_receipt(ROOT, contract or self.contract,
                                        digital or self.digital, physical or self.physical)

    def test_canonical_recommends_a2_but_official_is_none(self) -> None:
        run = self.run_cli()
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('"diagnostic_pareto_front": [', run.stdout)
        self.assertIn('"conditional_default_candidate": "A2"', run.stdout)
        self.assertIn('"conditional_exact_prefix_candidate": "A3"', run.stdout)
        self.assertIn('"official_selected_candidate": null', run.stdout)
        self.assertIn('official=NONE release=HOLD', run.stdout)

    def test_current_candidates_are_mutually_nondominated(self) -> None:
        receipt = self.receipt()
        self.assertEqual(receipt["diagnostic_pareto_front"], ["A2", "A3"])
        self.assertFalse(receipt["a2_dominates_a3"])
        self.assertFalse(receipt["a3_dominates_a2"])

    def test_scalar_score_policy_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["policy"]["scalar_score"] = "ALLOWED"
        with self.assertRaisesRegex(verifier.ContractError, "selection policy differs"):
            self.receipt(contract=contract)

    def test_objective_direction_mutation_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["pareto_objectives"][0]["direction"] = "max"
        with self.assertRaisesRegex(verifier.ContractError, "objectives/directions differ"):
            self.receipt(contract=contract)

    def test_input_hash_mutation_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["inputs"]["digital"]["contract"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(verifier.ContractError, "pinned input hash differs"):
            self.receipt(contract=contract)

    def test_correctness_gate_failure_is_rejected(self) -> None:
        digital = copy.deepcopy(self.digital)
        digital["accepted_event_exact_once"] = False
        with self.assertRaisesRegex(verifier.ContractError, "digital diagnostic hard gate"):
            self.receipt(digital=digital)

    def test_physical_violation_is_rejected(self) -> None:
        physical = copy.deepcopy(self.physical)
        physical["candidates"]["a2"]["physical"]["setup_violations"] = 1
        with self.assertRaisesRegex(verifier.ContractError, "physical hard gate failed"):
            self.receipt(physical=physical)

    def test_self_claimed_producer_authority_is_rejected(self) -> None:
        physical = copy.deepcopy(self.physical)
        physical["producer_authenticated"] = True
        with self.assertRaisesRegex(verifier.ContractError, "illegally claims authority"):
            self.receipt(physical=physical)

    def test_shared_hold_cannot_be_promoted(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["hard_gates"]["organizer_constraints"] = "PASS"
        with self.assertRaisesRegex(verifier.ContractError, "hard-gate policy differs"):
            self.receipt(contract=contract)

    def test_release_ceiling_cannot_be_promoted(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["maximum_decision"] = "PASS"
        with self.assertRaisesRegex(verifier.ContractError, "raises the release ceiling"):
            self.receipt(contract=contract)


if __name__ == "__main__":
    unittest.main()

