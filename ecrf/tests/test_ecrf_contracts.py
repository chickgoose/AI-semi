from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ecrf.tools.contracts import ContractError, decision_exit, validate_common


class EcrfContractMutationTest(unittest.TestCase):
    COMMIT = "1" * 40

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
