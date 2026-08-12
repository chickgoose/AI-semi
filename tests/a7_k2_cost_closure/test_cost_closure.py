from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "audits/a7_k2_cost_closure/generate_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cost_closure", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CostClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="a7-k2-cost-test-")
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "A7 test"],
                       cwd=self.repo, check=True)
        self.module = load_module()
        self.paths = dict(self.module.DEFAULTS)
        for relative in self.paths.values():
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        self.commit("baseline")

    def tearDown(self):
        self.temp.cleanup()

    def commit(self, message: str):
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=self.repo, check=True)

    def mutate(self, label: str, callback, *, commit=True):
        path = self.repo / self.paths[label]
        document = json.loads(path.read_text())
        callback(document)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        if commit:
            self.commit("mutation")

    def test_recomputes_charged_deltas_and_pareto_without_physical_claims(self):
        report = self.module.generate(self.repo, self.paths)
        self.assertEqual(report["pareto"]["normalized"], ["a2", "a3"])
        self.assertEqual(report["pareto"]["full_p6"], ["a2", "a3"])
        self.assertEqual(
            report["candidates"]["a2"]["whole_cone_delta_full_minus_normalized"]
            ["mapped_state_bits"], 51)
        self.assertEqual(
            report["candidates"]["a3"]["whole_cone_delta_full_minus_normalized"]
            ["mapped_state_bits"], 40)
        self.assertEqual(report["isolated_p6_seam"]["metrics"]["mapped_state_bits"], 40)
        self.assertEqual(
            report["candidates"]["a2"]["integration_adapter_seam"]
            ["measured_state_residual_full_minus_normalized_minus_p6"],
            {"generic": 11, "mapped": 11})
        self.assertEqual(
            report["candidates"]["a3"]["integration_adapter_seam"]
            ["measured_state_residual_full_minus_normalized_minus_p6"],
            {"generic": 0, "mapped": 0})
        self.assertEqual(report["physical_metrics"]["area"], None)
        self.assertEqual(report["physical_metrics"]["power"], None)
        self.assertIn("HOLD", report["physical_metrics"]["status"])

    def test_dirty_receipt_is_rejected(self):
        self.mutate("a2_integration", lambda row: row["metrics"].__setitem__(
            "mapped_cells", row["metrics"]["mapped_cells"] + 1), commit=False)
        with self.assertRaisesRegex(self.module.ClosureError, "uncommitted"):
            self.module.generate(self.repo, self.paths)

    def test_untracked_receipt_is_rejected(self):
        relative = "untracked.json"
        shutil.copyfile(self.repo / self.paths["a2_integration"], self.repo / relative)
        paths = {**self.paths, "a2_integration": relative}
        with self.assertRaises(self.module.ClosureError):
            self.module.generate(self.repo, paths)

    def test_symlink_substitution_is_rejected(self):
        path = self.repo / self.paths["a3_normalized"]
        copy = self.repo / "same-bytes.json"
        shutil.copyfile(path, copy)
        path.unlink()
        path.symlink_to(copy)
        with self.assertRaisesRegex(self.module.ClosureError, "regular non-linked"):
            self.module.generate(self.repo, self.paths)

    def test_incomparable_recipe_is_rejected(self):
        self.mutate("a2_integration", lambda row: row["common_method"].__setitem__(
            "flow", row["common_method"]["flow"] + "; opt"))
        with self.assertRaisesRegex(self.module.ClosureError, "Yosys recipe"):
            self.module.generate(self.repo, self.paths)

    def test_incomparable_top_boundary_is_rejected(self):
        self.mutate("a3_integration", lambda row: row["common_method"].__setitem__(
            "boundary", "different boundary"))
        with self.assertRaisesRegex(self.module.ClosureError, "top boundary"):
            self.module.generate(self.repo, self.paths)

    def test_missing_adapter_cost_is_rejected(self):
        self.mutate("a2_integration", lambda row: row["closure"]["components"].pop(1))
        with self.assertRaisesRegex(self.module.ClosureError, "adapter/P6 cost"):
            self.module.generate(self.repo, self.paths)

    def test_missing_isolated_p6_receipt_is_rejected(self):
        (self.repo / self.paths["p6_endpoint"]).unlink()
        with self.assertRaises((self.module.ClosureError, OSError)):
            self.module.generate(self.repo, self.paths)

    def test_incomparable_isolated_p6_recipe_is_rejected(self):
        self.mutate("p6_endpoint", lambda row: row["common_method"].__setitem__(
            "flow", row["common_method"]["flow"] + "; opt"))
        with self.assertRaisesRegex(self.module.ClosureError, "different Yosys recipes"):
            self.module.generate(self.repo, self.paths)

    def test_uncharged_p6_is_rejected(self):
        def change(row):
            next(item for item in row["closure"]["components"]
                 if item["role"] == "p6_endpoint")["charged"] = False
        self.mutate("a3_integration", change)
        with self.assertRaisesRegex(self.module.ClosureError, "not explicitly charged"):
            self.module.generate(self.repo, self.paths)

    def test_fabricated_area_or_power_is_rejected(self):
        self.mutate("a2_integration", lambda row: row["metrics"].__setitem__("area_um2", 1))
        with self.assertRaisesRegex(self.module.ClosureError, "physical metrics"):
            self.module.generate(self.repo, self.paths)

    def test_missing_charged_state_in_full_cone_is_rejected(self):
        self.mutate("a3_integration", lambda row: row["metrics"].__setitem__(
            "mapped_state_bits", row["metrics"]["mapped_state_bits"] - 1))
        with self.assertRaisesRegex(self.module.ClosureError, "charged state is absent"):
            self.module.generate(self.repo, self.paths)


if __name__ == "__main__":
    unittest.main()
