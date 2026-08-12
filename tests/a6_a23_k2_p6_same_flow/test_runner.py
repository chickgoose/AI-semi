#!/usr/bin/env python3

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "audits/a6_a23_k2_p6_same_flow/run.py"
REGISTRY = ROOT / "audits/a6_a23_k2_p6_same_flow/registry.json"
CANONICAL = ROOT / "audits/a6_a23_k2_p6_same_flow/results"
YOSYS = Path(os.environ.get("A6_YOSYS", "/tmp/a7-toolchain/usr/bin/yosys"))


class SameFlowTest(unittest.TestCase):
    def run_tool(self, output: Path, registry: Path = REGISTRY,
                 target: str | None = None) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(RUNNER), "--yosys", str(YOSYS),
                   "--registry", str(registry), "--output-dir", str(output)]
        if target:
            command += ["--target", target]
        return subprocess.run(command, cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_full_results_are_reproducible_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-test-") as text:
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

            expected = {
                "a2_k2": (210, 22, 741, 22, 52, 13, 1450),
                "a3_k2": (283, 34, 650, 26, 43, 20, 1235),
                "a2_p6": (323, 73, 983, 73, 50, 22, 1932),
                "a3_p6": (362, 74, 733, 66, 47, 31, 1368),
            }
            for target, values in expected.items():
                result = json.loads((first / f"{target}.json").read_text())
                generic, mapped = result["metrics"]["generic"], result["metrics"]["mapped"]
                observed = (generic["cells"], generic["state_bits"],
                            mapped["cells"], mapped["state_bits"],
                            mapped["depth_levels"], mapped["fanout_max"],
                            mapped["sink_pin_net_proxy"])
                self.assertEqual(observed, values)
                self.assertTrue(result["source_inventory_closed"])
                self.assertEqual(result["qualification"]["physical_ppa"],
                                 "HOLD_GENERIC_YOSYS_ONLY")
                self.assertEqual(result["warnings"]["unclassified"], 0)

    def test_changed_source_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-mut-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            sources = document["targets"]["a2_k2"]["sources"]
            path = next(iter(sources))
            sources[path] = "0" * 64
            mutated = root / "registry.json"
            mutated.write_text(json.dumps(document))
            run = self.run_tool(root / "out", mutated, "a2_k2")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("source SHA mismatch", run.stdout)

    def test_partial_p6_bundle_fails_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-partial-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            del document["targets"]["a3_p6"]["sources"][
                "rtl/candidates/a7_p6_exact_pair_endpoint/a7_p6_pair_rx.sv"]
            mutated = root / "registry.json"
            mutated.write_text(json.dumps(document))
            run = self.run_tool(root / "out", mutated, "a3_p6")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("Yosys failed", run.stdout)

    def test_duplicate_registry_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-dup-") as text:
            root = Path(text)
            raw = REGISTRY.read_text().replace(
                '"schema": "a6-a23-k2-p6-source-registry-v1",',
                '"schema": "a6-a23-k2-p6-source-registry-v1",\n'
                '  "schema": "a6-a23-k2-p6-source-registry-v1",', 1)
            mutated = root / "registry.json"
            mutated.write_text(raw)
            run = self.run_tool(root / "out", mutated, "a2_k2")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("duplicate JSON key", run.stdout)

    def test_path_traversal_and_duplicate_blob_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-path-") as text:
            root = Path(text)
            document = json.loads(REGISTRY.read_text())
            sources = document["targets"]["a2_k2"]["sources"]
            digest = next(iter(sources.values()))
            sources["../escape.sv"] = "1" * 64
            traversing = root / "traversing.json"
            traversing.write_text(json.dumps(document))
            run = self.run_tool(root / "path-out", traversing, "a2_k2")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("not normalized relative", run.stdout)

            document = json.loads(REGISTRY.read_text())
            document["targets"]["a2_k2"]["sources"]["duplicate.sv"] = digest
            duplicate = root / "duplicate.json"
            duplicate.write_text(json.dumps(document))
            run = self.run_tool(root / "dup-out", duplicate, "a2_k2")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("duplicate source blob", run.stdout)

    def test_stale_output_and_symlink_tool_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a6-a23-stale-") as text:
            root = Path(text)
            output = root / "out"
            output.mkdir()
            stale = self.run_tool(output, target="a2_k2")
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("refusing existing output", stale.stdout)

            link = root / "yosys-link"
            link.symlink_to(YOSYS)
            run = subprocess.run(
                ["python3", str(RUNNER), "--yosys", str(link),
                 "--output-dir", str(root / "link-out"), "--target", "a2_k2"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("must not be a symlink", run.stdout)


if __name__ == "__main__":
    unittest.main()
