#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
BINDING = HERE / "a7_w5_binding.json"
PREPARE = HERE / "prepare_bound_snapshot.py"


class BoundSnapshotTest(unittest.TestCase):
    def run_prepare(self, binding: Path, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(PREPARE), "--binding", str(binding), "--output-dir", str(output)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_exact_followup_snapshot_materializes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a8-w5-bind-test-") as temp:
            result = self.run_prepare(BINDING, Path(temp) / "owner")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("commit=42377ca81340951bfcd453b3bd664e673091f9f3", result.stdout)
            sources = (Path(temp) / "owner/bound_sources.list").read_text().splitlines()
            self.assertEqual(len(sources), 7)

    def test_mutated_owner_blob_hash_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a8-w5-bind-test-") as temp:
            root = Path(temp)
            document = json.loads(BINDING.read_text(encoding="utf-8"))
            document["sources"][0]["sha256"] = "0" * 64
            mutated = root / "mutated-binding.json"
            mutated.write_text(json.dumps(document), encoding="utf-8")
            result = self.run_prepare(mutated, root / "owner")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("blob SHA mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
