#!/usr/bin/env python3
"""Mutation tests for the latest-RTL full50 evidence contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts/redred_single_edge_digital_authority"
VERIFY_PATH = CONTRACT_DIR / "verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_digital_authority_verifier", VERIFY_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class DigitalAuthorityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = verifier.load_file(CONTRACT_DIR / "contract.json", "contract")[1]
        cls.binding = verifier.load_file(CONTRACT_DIR / "evidence_binding.json", "binding")[1]
        required = verifier.validate_binding(cls.binding)
        archive = cls.binding["archive"]
        cls.members = verifier.read_archive(
            ROOT / archive["path"], archive["size_bytes"], archive["sha256"], required)
        cls.pins = verifier.load_json_bytes(cls.members["pins.json"], "pins")
        cls.result = verifier.load_json_bytes(cls.members["result.json"], "result")
        cls.prior = verifier.load_file(
            ROOT / cls.binding["prior_canonical_result"]["path"], "prior")[1]

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(VERIFY_PATH), *arguments],
                              cwd=ROOT, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, check=False)

    def verify_mutant(self, result: dict[str, object]) -> None:
        verifier.verify_result(result, self.pins, self.binding, self.prior)

    def test_canonical_diagnostic_pass_cannot_promote_release(self) -> None:
        run = self.run_cli()
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn('"digital_rtl_diagnostic_status": "PASS"', run.stdout)
        self.assertIn('"accepted_event_exact_once": true', run.stdout)
        self.assertIn('"fixed_window_events_per_cycle": 0.896281733', run.stdout)
        self.assertIn('"fixed_window_events_per_cycle": 0.806670806', run.stdout)
        self.assertIn('"final_digital_release_gate": "HOLD"', run.stdout)
        self.assertIn('"producer_authenticated": false', run.stdout)
        self.assertIn("DIAGNOSTIC_PASS_RELEASE_HOLD", run.stdout)

    def test_archive_byte_tamper_fails(self) -> None:
        original = (ROOT / self.binding["archive"]["path"]).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.tar"
            path.write_bytes(original + b"x")
            run = self.run_cli("--archive", str(path))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("archive byte identity differs", run.stdout)

    def test_source_commit_tamper_fails(self) -> None:
        pins = copy.deepcopy(self.pins)
        pins["rtl_provenance"]["source_commit"] = "0" * 40
        with self.assertRaisesRegex(verifier.ContractError, "pins RTL provenance differs"):
            verifier.verify_git_authority(ROOT, pins)

    def test_execution_accounting_tamper_fails(self) -> None:
        result = copy.deepcopy(self.result)
        result["execution_accounting"]["full50_actual_RTL_executions"] = 99
        with self.assertRaisesRegex(verifier.ContractError, "execution accounting differs"):
            self.verify_mutant(result)

    def test_generated_conservation_tamper_fails(self) -> None:
        result = copy.deepcopy(self.result)
        run = next(iter(result["owners"]["a2"]["full50"]["runs"].values()))
        run["generated"] += 1
        with self.assertRaisesRegex(verifier.ContractError, "generated conservation fails"):
            self.verify_mutant(result)

    def test_accepted_retired_tamper_fails(self) -> None:
        result = copy.deepcopy(self.result)
        run = next(iter(result["owners"]["a3"]["full50"]["runs"].values()))
        run["retired"] -= 1
        with self.assertRaisesRegex(verifier.ContractError, "accepted/retired exact-once"):
            self.verify_mutant(result)

    def test_reset_without_clean_drain_fails(self) -> None:
        result = copy.deepcopy(self.result)
        result["owners"]["a2"]["reset"]["pre_reset_clean_drain"] = 0
        with self.assertRaisesRegex(verifier.ContractError, "reset was not preceded"):
            self.verify_mutant(result)

    def test_mutation_not_killed_fails(self) -> None:
        result = copy.deepcopy(self.result)
        result["mutations"][0]["killed"] = False
        with self.assertRaisesRegex(verifier.ContractError, "was not compiled, executed"):
            self.verify_mutant(result)

    def test_prior_full50_mismatch_fails(self) -> None:
        prior = copy.deepcopy(self.prior)
        prior["owners"]["a2"]["full50"]["aggregate"]["totals"]["accepted"] += 1
        with self.assertRaisesRegex(verifier.ContractError, "differ from prior canonical"):
            verifier.verify_result(self.result, self.pins, self.binding, prior)

    def test_contract_cannot_raise_release_ceiling(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["maximum_release_decision"] = "PASS"
        with self.assertRaisesRegex(verifier.ContractError, "raises the release ceiling"):
            verifier.validate_contract(contract)


if __name__ == "__main__":
    unittest.main()

