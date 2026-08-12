import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "structural_compare.py"
A7_REPO = HERE.parents[2] / "a7"


class StructuralContractTest(unittest.TestCase):
    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--a7-repo", str(A7_REPO), *arguments],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

    def test_exact_pinned_provenance(self) -> None:
        result = self.run_tool("--verify-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("A5_FOVEA_A7_PROVENANCE_PASS synthesis=NOT_RUN", result.stdout)
        self.assertNotIn("STRUCTURAL_PASS", result.stdout)

    def test_mutated_fovea_blob_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixtures = Path(temporary) / "fixtures"
            shutil.copytree(HERE / "fixtures", fixtures)
            target = fixtures / "arbiter2.v"
            target.write_bytes(target.read_bytes() + b"\n")
            result = self.run_tool("--verify-only", "--fixture-dir", str(fixtures))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical fovea blob mismatch", result.stderr)

    def test_missing_a7_repository_is_rejected(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--a7-repo", "/definitely/missing", "--verify-only"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing A7 repository", result.stderr)

    def test_missing_yosys_fails_closed_without_publishing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = self.run_tool(
                "--yosys", "/definitely/missing/yosys", "--output", str(output)
            )
            self.assertFalse(output.exists())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required Yosys executable unavailable", result.stderr)

    def test_requested_real_yosys_matches_frozen_structure(self) -> None:
        yosys = os.environ.get("A5_STRUCTURAL_YOSYS")
        if not yosys:
            self.skipTest("set A5_STRUCTURAL_YOSYS to require the real synthesis gate")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            result = self.run_tool("--yosys", yosys, "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("A5_FOVEA_A7_STRUCTURAL_PASS", result.stdout)
            with (output / "structural.csv").open(newline="") as stream:
                rows = {row["variant"]: row for row in csv.DictReader(stream)}
        self.assertEqual(rows["ddr2"]["physical_link_pins"], "3")
        self.assertEqual(rows["ddr2"]["state_bits"], "37")
        self.assertEqual(rows["ddr2"]["charged_functional_cells"], "150")
        self.assertEqual(rows["ddr2"]["generic_gate_depth"], "33")
        self.assertEqual(rows["parallel4"]["physical_link_pins"], "5")
        self.assertEqual(rows["parallel4"]["state_bits"], "35")
        self.assertEqual(rows["parallel4"]["charged_functional_cells"], "148")
        self.assertEqual(rows["parallel4"]["generic_gate_depth"], "33")


if __name__ == "__main__":
    unittest.main()
