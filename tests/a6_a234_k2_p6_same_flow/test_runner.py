#!/usr/bin/env python3

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audits/a6_a234_k2_p6_same_flow"
BASE = ROOT / "audits/a6_a23_k2_p6_same_flow/results"
RUNNER = AUDIT / "run.py"
REGISTRY = AUDIT / "registry.json"
CANONICAL = AUDIT / "results"
YOSYS = Path(os.environ.get("A6_YOSYS", "/tmp/a7-toolchain/usr/bin/yosys"))


class A234SameFlowTest(unittest.TestCase):
    def run_tool(self, output: Path, registry: Path = REGISTRY
                 ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(RUNNER), "--yosys", str(YOSYS),
             "--registry", str(registry), "--output-dir", str(output)],
            cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)

    def write_registry(self, root: Path, document: dict) -> Path:
        path = root / "registry.json"
        path.write_text(json.dumps(document))
        return path

    def test_two_runs_canonical_and_base_results_are_identical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a234-full-") as text:
            root = Path(text)
            first, second = root / "first", root / "second"
            self.assertEqual(self.run_tool(first).returncode, 0)
            self.assertEqual(self.run_tool(second).returncode, 0)
            names = sorted(path.name for path in first.iterdir())
            self.assertEqual(names, sorted(path.name for path in second.iterdir()))
            self.assertEqual(names, sorted(path.name for path in CANONICAL.iterdir()))
            for name in names:
                self.assertEqual((first / name).read_bytes(),
                                 (second / name).read_bytes())
                self.assertEqual((first / name).read_bytes(),
                                 (CANONICAL / name).read_bytes())
            for target in ("a2_k2", "a3_k2", "a2_p6", "a3_p6"):
                self.assertEqual((first / f"{target}.json").read_bytes(),
                                 (BASE / f"{target}.json").read_bytes())

            result = json.loads((first / "a4_k2.json").read_text())
            generic, mapped = result["metrics"]["generic"], result["metrics"]["mapped"]
            self.assertEqual((generic["cells"], generic["state_bits"]), (629, 49))
            self.assertEqual(
                (mapped["cells"], mapped["state_bits"], mapped["depth_levels"],
                 mapped["fanout_max"], mapped["fanout_p95"], mapped["nets"],
                 mapped["sink_pin_net_proxy"]),
                (1794, 49, 102, 33, 6, 1812, 3612))
            self.assertEqual(result["p6"]["metrics"], None)
            self.assertEqual(result["p6"]["status"],
                             "HOLD_NO_A4_INTEGRATED_P6_TOP")
            self.assertEqual(result["qualification"]["physical_ppa"],
                             "HOLD_GENERIC_YOSYS_ONLY")

    def test_changed_a4_source_sha_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a234-sha-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            source = next(iter(document["target"]["sources"]))
            document["target"]["sources"][source] = "0" * 64
            run = self.run_tool(root / "out", self.write_registry(root, document))
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("A4 source SHA mismatch", run.stdout)

    def test_missing_source_and_changed_commit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a234-missing-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            document["target"]["sources"] = {}
            run = self.run_tool(root / "empty-out", self.write_registry(root, document))
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("source inventory is empty", run.stdout)

        with tempfile.TemporaryDirectory(prefix="a6-a234-commit-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            document["target"]["commit"] = "0e613b6"
            run = self.run_tool(root / "commit-out", self.write_registry(root, document))
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("not an exact full identity", run.stdout)

    def test_path_duplicate_key_and_fabricated_p6_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a234-path-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            digest = next(iter(document["target"]["sources"].values()))
            document["target"]["sources"] = {"../a4.sv": digest}
            run = self.run_tool(root / "path-out", self.write_registry(root, document))
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("not normalized relative", run.stdout)

        with tempfile.TemporaryDirectory(prefix="a6-a234-dup-") as text:
            root = Path(text)
            raw = REGISTRY.read_text().replace(
                '"schema": "a6-a234-k2-p6-extension-registry-v1",',
                '"schema": "a6-a234-k2-p6-extension-registry-v1",\n'
                '  "schema": "a6-a234-k2-p6-extension-registry-v1",', 1)
            duplicate = root / "registry.json"
            duplicate.write_text(raw)
            run = self.run_tool(root / "dup-out", duplicate)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("duplicate JSON key", run.stdout)

        with tempfile.TemporaryDirectory(prefix="a6-a234-p6-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            document["p6"]["a4_p6"] = {"cells": 1}
            run = self.run_tool(root / "p6-out", self.write_registry(root, document))
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("P6 result must remain null", run.stdout)

    def test_stale_output_and_symlink_registry_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a234-stale-") as text:
            root = Path(text)
            output = root / "out"
            output.mkdir()
            run = self.run_tool(output)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("refusing existing output", run.stdout)

            link = root / "registry-link.json"
            link.symlink_to(REGISTRY)
            run = self.run_tool(root / "link-out", link)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("regular non-symlink", run.stdout)


if __name__ == "__main__":
    unittest.main()
