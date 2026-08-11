from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ecrf.tools.contracts import ContractError, decision_exit, validate_common


class EcrfContractMutationTest(unittest.TestCase):
    COMMIT = "1" * 40
    CONTRACT_CLI = Path(__file__).resolve().parents[1] / "tools/contracts.py"

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ecrf-contract-")
        self.root = Path(self.temporary.name)
        benchmark = self.root / "benchmarks/clean_slate_aer"
        benchmark.mkdir(parents=True)
        self.files = {
            "benchmarks/clean_slate_aer/generate_trace.py":
                b'GENERATOR_VERSION = "4.0"\n',
            "benchmarks/clean_slate_aer/manifest.neutrality-n16.json":
                json.dumps({"runs": [{"id": 0}]}).encode(),
            "benchmarks/clean_slate_aer/manifest.multilane-n16.json":
                json.dumps({"runs": [{"id": 0}, {"id": 1}]}).encode(),
        }
        for relative, content in self.files.items():
            (self.root / relative).write_bytes(content)
        self.hashes = {
            relative: hashlib.sha256(content).hexdigest()
            for relative, content in self.files.items()
        }
        self.counts = {
            "benchmarks/clean_slate_aer/manifest.neutrality-n16.json": 1,
            "benchmarks/clean_slate_aer/manifest.multilane-n16.json": 2,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, head: str | None = None) -> None:
        validate_common(
            self.root,
            expected_commit=self.COMMIT,
            expected_hashes=self.hashes,
            expected_counts=self.counts,
            head_resolver=lambda _root: head or self.COMMIT,
        )

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.CONTRACT_CLI), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def assert_cli_contract_failure(
        self, result: subprocess.CompletedProcess[str], diagnostic: str
    ) -> None:
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertTrue(
            result.stderr.startswith("ECRF_CONTRACT_FAIL "), result.stderr
        )
        self.assertIn(diagnostic, result.stderr)

    def test_pinned_inputs_pass_unmodified(self) -> None:
        self.validate()

    def test_each_pinned_input_mutation_fails_closed(self) -> None:
        for relative, original in self.files.items():
            with self.subTest(relative=relative):
                path = self.root / relative
                path.write_bytes(original + b"mutation")
                with self.assertRaisesRegex(ContractError, "SHA-256 mismatch"):
                    self.validate()
                path.write_bytes(original)

    def test_common_commit_mutation_fails_closed(self) -> None:
        with self.assertRaisesRegex(ContractError, "common commit mismatch"):
            self.validate(head="2" * 40)

    def test_hold_is_zero_only_for_evaluation_mode(self) -> None:
        path = self.root / "summary.json"
        path.write_text(
            json.dumps({"decision": "HOLD", "rtl_permitted": False}),
            encoding="utf-8",
        )
        self.assertEqual(0, decision_exit(path, False)[0])
        self.assertEqual(3, decision_exit(path, True)[0])

    def test_inconsistent_decision_fails_closed(self) -> None:
        path = self.root / "summary.json"
        path.write_text(
            json.dumps({"decision": "GO", "rtl_permitted": False}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ContractError, "inconsistent"):
            decision_exit(path, True)

    def test_cli_nonexistent_common_root_is_exact_exit_two(self) -> None:
        result = self.run_cli(
            "inputs", "--common-root", str(self.root / "does-not-exist")
        )
        self.assert_cli_contract_failure(result, "missing pinned common input")

    def test_cli_common_sha_mutation_is_exact_exit_two(self) -> None:
        generator = self.root / "benchmarks/clean_slate_aer/generate_trace.py"
        generator.write_bytes(self.files[
            "benchmarks/clean_slate_aer/generate_trace.py"
        ] + b"mutation")
        result = self.run_cli("inputs", "--common-root", str(self.root))
        self.assert_cli_contract_failure(result, "SHA-256 mismatch")

    def test_cli_schema_error_is_exact_exit_two(self) -> None:
        summary = self.root / "bad-summary.json"
        summary.write_text(json.dumps({"decision": "MAYBE"}), encoding="utf-8")
        result = self.run_cli("decision", "--summary", str(summary))
        self.assert_cli_contract_failure(
            result, "invalid ECRF decision summary schema"
        )
