from __future__ import annotations

import copy
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/generate_cluster2_cav_presentation_assets.py"
RECEIPT = ROOT / (
    "benchmarks/redred_cluster2_cav_bridge/results/"
    "official_uzh_cluster2_cav_result.json"
)
ASSETS = ROOT / "docs/presentation/assets"
SPEC = importlib.util.spec_from_file_location("cluster2_cav_assets", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PresentationAssetTests(unittest.TestCase):
    def test_generator_imports_standard_library_only(self):
        tree = ast.parse(SCRIPT.read_text("utf-8"), filename=str(SCRIPT))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                roots.add(node.module.split(".", 1)[0])
        self.assertEqual(roots, {
            "argparse", "hashlib", "hmac", "html", "json", "os", "pathlib",
            "re", "tempfile", "typing",
        })

    def test_committed_assets_are_exact_deterministic_render(self):
        receipt = module.load_official_result(RECEIPT)
        rendered = module.render_assets(receipt)
        self.assertEqual(tuple(rendered), module.OUTPUT_NAMES)
        for name in module.OUTPUT_NAMES:
            payload = (ASSETS / name).read_bytes()
            self.assertEqual(payload, rendered[name])
            root = ElementTree.fromstring(payload)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_generation_does_not_embed_host_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "host-private-output"
            first = module.generate(RECEIPT, output)
            second = module.generate(RECEIPT, output)
            self.assertEqual(first, second)
            for name, payload in first.items():
                self.assertEqual((output / name).read_bytes(), payload)
                self.assertNotIn(temporary.encode("utf-8"), payload)
                self.assertNotIn(str(RECEIPT).encode("utf-8"), payload)

    def test_figures_report_only_receipt_backed_scope_and_values(self):
        receipt = module.load_official_result(RECEIPT)
        assets = module.render_assets(receipt)
        population = assets["cluster2_cav_population_flow.svg"]
        latency = assets["cluster2_cav_latency_histogram.svg"]
        grid = assets["cluster2_cav_world_grid_coverage.svg"]
        for expected in (b"8503 events", b"11883 poses", b"8420", b"83"):
            self.assertIn(expected, population)
        for expected in (b"6393", b"2077", b"33", b"not physical replay"):
            self.assertIn(expected, latency)
        for expected in (
            b"8420 rows", b"821 unique cells", b"238", b"298", b"93",
            b"165", b"47876", b"84754", b"not a per-cell occupancy plot",
        ):
            self.assertIn(expected, grid)

    def test_bad_seal_and_resealed_bad_population_fail_closed(self):
        value = json.loads(RECEIPT.read_text("ascii"))
        changed = copy.deepcopy(value)
        changed["seal"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            path.write_bytes(module._canonical_bytes(changed))
            with self.assertRaises(module.PresentationAssetError):
                module.load_official_result(path)

            changed = copy.deepcopy(value)
            changed["population"]["causal_cav"] -= 1
            body = dict(changed)
            body.pop("seal")
            changed["seal"]["sha256"] = hashlib.sha256(
                module._canonical_bytes(body)
            ).hexdigest()
            path.write_bytes(module._canonical_bytes(changed))
            with self.assertRaisesRegex(
                module.PresentationAssetError, "population conservation"
            ):
                module.load_official_result(path)


if __name__ == "__main__":
    unittest.main()
