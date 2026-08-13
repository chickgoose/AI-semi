from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "physical/k2_w2_genus/run_genus.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_GENUS = FIXTURES / "fake_genus.py"
LIBRARY = FIXTURES / "slow_vdd1v0_basicCells.lib"
SMOKE = FIXTURES / "mapped_smoke.py"
FABRICATED_SMOKE = FIXTURES / "fabricated_smoke.py"
GOLDEN_ARCHIVE = Path("/tmp/ganghee-pnr-golden-20260813.tar.gz")


def load_flow():
    spec = importlib.util.spec_from_file_location("k2_w2_genus", FLOW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GenusFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_flow()

    def invoke(self, output: Path, design: str = "a2_k2", attempt: str = "attempt-1",
               mode: str = "pass",
               smoke: Path | None = SMOKE,
               golden_archive: Path | None = GOLDEN_ARCHIVE) -> subprocess.CompletedProcess[str]:
        command = [
            "python3", "-B", str(FLOW), "--repo-root", str(ROOT),
            "--design", design, "--genus", str(FAKE_GENUS),
            "--library", str(LIBRARY), "--output-root", str(output),
            "--attempt", attempt,
        ]
        if golden_archive is not None:
            command.extend(["--golden-archive", str(golden_archive)])
        if smoke is not None:
            command.extend(["--mapped-smoke-hook", str(smoke)])
        environment = os.environ.copy()
        environment["W2_FAKE_GENUS_MODE"] = mode
        return subprocess.run(
            command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False,
        )

    def test_registry_exact_five_and_all_sources_match_commit(self):
        self.assertTrue(GOLDEN_ARCHIVE.is_file(), "authoritative archive is required")
        registry = self.module.load_registry()
        self.assertEqual(set(registry["designs"]), {
            "a2_k2", "a3_k2", "p6_endpoint", "a2_p6", "a3_p6"})
        self.module.verify_source_commit(ROOT, registry)
        for design in registry["designs"]:
            self.module.verify_design(ROOT, registry, design)

    def test_authoritative_archive_and_actual_report_anchors(self):
        golden = self.module.load_golden_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            snapshot = Path(directory) / golden["archive_filename"]
            identity = self.module.verify_golden_archive(
                GOLDEN_ARCHIVE, snapshot, golden)
            self.assertEqual(identity["archive_sha256"], golden["archive_sha256"])
            self.assertEqual(identity["anchor_count"], 25)
            self.assertEqual(identity["genus_version"], "23.14-s090_1")

    def test_all_five_designs_publish_bound_receipts(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            for index, design in enumerate((
                    "a2_k2", "a3_k2", "p6_endpoint", "a2_p6", "a3_p6")):
                attempt = f"positive-{index}-{design}"
                result = self.invoke(output, design, attempt)
                self.assertEqual(result.returncode, 0, result.stdout)
                receipt = json.loads((output / attempt / "receipt.json").read_text())
                self.assertEqual(receipt["status"], "PASS")
                self.assertEqual(receipt["design"], design)
                self.assertEqual(receipt["mapped_inventory"]["mapped_cell_count"], 1)
                self.assertEqual(receipt["mapped_smoke"]["status"], "PASS")

    def test_existing_attempt_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            first = self.invoke(output)
            self.assertEqual(first.returncode, 0, first.stdout)
            receipt = (output / "attempt-1/receipt.json").read_bytes()
            second = self.invoke(output)
            self.assertNotEqual(second.returncode, 0, second.stdout)
            self.assertEqual((output / "attempt-1/receipt.json").read_bytes(), receipt)

    def test_unresolved_blackbox_is_rejected_without_receipt(self):
        for mode in ("blackbox", "defined_blackbox"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                    prefix="k2-w2-genus-") as directory:
                output = Path(directory)
                result = self.invoke(output, mode=mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("blackbox", result.stdout)
                self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_scan_cell_is_rejected_without_receipt(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            result = self.invoke(output, mode="scan")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("scan cells are forbidden", result.stdout)
            self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_missing_or_fabricated_actual_report_and_log_are_rejected(self):
        for mode in ("missing_report", "bad_report", "bad_summary", "missing_pass"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                    prefix="k2-w2-genus-") as directory:
                output = Path(directory)
                result = self.invoke(output, mode=mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_missing_or_mutated_golden_archive_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            result = self.invoke(Path(directory), golden_archive=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("--golden-archive", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            root = Path(directory)
            fake = root / "ganghee-pnr-golden-20260813.tar.gz"
            shutil.copyfile(GOLDEN_ARCHIVE, fake)
            payload = bytearray(fake.read_bytes())
            payload[-1] ^= 1
            fake.write_bytes(payload)
            result = self.invoke(root / "out", golden_archive=fake)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("golden archive SHA mismatch", result.stdout)
            self.assertFalse((root / "out/attempt-1/receipt.json").exists())

    def test_smoke_is_mandatory_and_fabricated_hash_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            result = self.invoke(Path(directory), smoke=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("smoke hook is required", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            result = self.invoke(output, smoke=FABRICATED_SMOKE)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("not bound to the mapped netlist/library/top", result.stdout)
            self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_filelist_and_source_hash_mutations_are_rejected(self):
        registry = self.module.load_registry()
        design = registry["designs"]["a2_k2"]
        original = design["filelist_sha256"]
        design["filelist_sha256"] = "0" * 64
        with self.assertRaisesRegex(self.module.FlowError, "filelist SHA"):
            self.module.verify_design(ROOT, registry, "a2_k2")
        design["filelist_sha256"] = original
        design["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(self.module.FlowError, "source byte mismatch"):
            self.module.verify_design(ROOT, registry, "a2_k2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
