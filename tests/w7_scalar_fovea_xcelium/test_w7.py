#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_w7.py"
A1 = Path("/home/chickgoose/projects/a1")


class W7Tests(unittest.TestCase):
    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNNER), *args], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_exact_blobs_verify(self) -> None:
        result = self.invoke("verify", "--a1-repo", str(A1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("W7_PROVENANCE_PASS blobs=12", result.stdout)

    def test_contract_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="w7-mutation.") as temporary:
            contract = json.loads((HERE / "contract.json").read_text())
            contract["fovea"]["weight"] = 4
            mutated = Path(temporary) / "contract.json"
            mutated.write_text(json.dumps(contract) + "\n")
            result = self.invoke("verify", "--contract", str(mutated), "--a1-repo", str(A1))
        self.assertEqual(result.returncode, 2)
        self.assertIn("W7_CONTRACT_FAIL contract SHA256 mismatch", result.stderr)

    def test_missing_repo_fails_closed(self) -> None:
        result = self.invoke("verify", "--a1-repo", "/tmp/w7-no-such-repo")
        self.assertEqual(result.returncode, 2)
        self.assertIn("W7_CONTRACT_FAIL --a1-repo", result.stderr)

    def test_missing_xrun_is_not_skip(self) -> None:
        result = self.invoke("run", "--a1-repo", str(A1), "--xrun", "/tmp/no-such-xrun")
        self.assertEqual(result.returncode, 3)
        self.assertIn("W7_TOOL_MISSING", result.stderr)

    def test_zero_exit_fake_xrun_cannot_false_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="w7-fake-tool.") as temporary:
            root = Path(temporary)
            fake = root / "xrun"
            fake.write_text("#!/bin/sh\nexit 0\n")
            fake.chmod(fake.stat().st_mode | 0o111)
            output = root / "attempt"
            result = self.invoke("run", "--a1-repo", str(A1), "--suite", "capacity22",
                                 "--xrun", str(fake), "--out", str(output))
        self.assertEqual(result.returncode, 2)
        self.assertIn("W7_CONTRACT_FAIL missing exact PASS sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
