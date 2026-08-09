import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FULL = ROOT / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json"
MULTILANE = ROOT / "benchmarks/clean_slate_aer/manifest.multilane-n16.json"


class MultilaneManifestTest(unittest.TestCase):
    def test_is_exact_named_subset_of_frozen_suite(self):
        full = json.loads(FULL.read_text(encoding="utf-8"))
        multilane = json.loads(MULTILANE.read_text(encoding="utf-8"))
        by_name = {run["name"]: run for run in full["runs"]}
        self.assertEqual(len(full["runs"]), 50)
        self.assertEqual(len(multilane["runs"]), 22)
        self.assertEqual(len({run["name"] for run in multilane["runs"]}), 22)
        for run in multilane["runs"]:
            self.assertIn(run["name"], by_name)
            self.assertEqual(run, by_name[run["name"]])

    def test_covers_lane_capacity_boundaries(self):
        document = json.loads(MULTILANE.read_text(encoding="utf-8"))
        names = {run["name"] for run in document["runs"]}
        loads = {run["load"] for run in document["runs"] if run["workload"] == "uniform"}
        self.assertEqual(loads, {1.0, 1.25, 1.5, 2.0})
        self.assertTrue({
            "shape_b4", "shape_b16", "global_fanin_identity",
            "pairwise_contention_identity", "pairwise_contention_affine",
            "mixed_phase_always_ready_identity",
            "mixed_phase_always_ready_bit_reverse",
        } <= names)
        self.assertEqual(
            sum(run["workload"] == "phase_transition" for run in document["runs"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
