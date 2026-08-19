from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "audits/k2_final_selection/generate_selection.py"


def load_module():
    spec = importlib.util.spec_from_file_location("k2_final_selection", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FinalSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()
        cls.replay = json.loads((ROOT / cls.module.REPLAY_PATH).read_text())
        cls.cost = json.loads((ROOT / cls.module.COST_PATH).read_text())

    def test_committed_selection_is_recomputed_exactly(self):
        generated = self.module.generate(ROOT)
        committed = json.loads(
            (ROOT / "audits/k2_final_selection/result.json").read_text())
        self.assertEqual(generated, committed)
        self.assertEqual(
            generated["status"],
            "HISTORICAL_DIGITAL_SELECTION_SUPERSEDED_NONCURRENT",
        )
        self.assertEqual(generated["selected_key"], "a2")
        self.assertEqual(generated["retained_fallback"]["candidate"],
                         "a3_exact_scalar_prefix_k2_plus_p6")
        self.assertEqual(generated["claim_boundary"]
                         ["standard_cell_area_fmax_power_energy_routing"], "HOLD")

    def test_historical_selection_has_no_current_authority(self):
        generated = self.module.generate(ROOT)
        lifecycle = generated["lifecycle"]
        goal = json.loads(
            (ROOT / "contracts/redred_system_goal/active_goal.json").read_text())
        self.assertEqual(lifecycle["status"],
                         "SUPERSEDED_HISTORICAL_NONCURRENT")
        for key in (
                "current_goal_authority",
                "current_candidate_selection_authority",
                "current_release_interface_authority",
                "team_release_authority"):
            self.assertIs(lifecycle[key], False)
        self.assertEqual(lifecycle["superseded_by"]["path"],
                         "contracts/redred_system_goal/active_goal.json")
        self.assertEqual(lifecycle["superseded_by"]["contract_id"],
                         goal["contract_id"])
        self.assertEqual(
            lifecycle["current_implemented_endpoint_boundary"],
            goal["endpoint_boundary"]["boundary_id"],
        )
        self.assertEqual(lifecycle["current_release_interface"],
                         "PARALLEL_FALLBACK")
        self.assertEqual(lifecycle["current_release_interface_status"],
                         "IMPLEMENTED_RELEASE_HELD")
        self.assertEqual(lifecycle["current_final_a2_a3_decision"], "HOLD")

    def test_actual_rtl_mutation_gate_is_fail_closed(self):
        document = copy.deepcopy(self.replay)
        row = next(row for row in document["mutations"]
                   if row["owner"] == "a2" and row["mutation"] == "drop")
        row["killed"] = False
        with self.assertRaisesRegex(self.module.SelectionError, "mutation gate"):
            self.module.validate_replay(document)

    def test_source_package_commit_is_fail_closed(self):
        document = copy.deepcopy(self.replay)
        document["provenance"]["package_commit"] = "0" * 40
        with self.assertRaisesRegex(self.module.SelectionError,
                                    "source replay package commit"):
            self.module.verify_rebased_replay_provenance(ROOT, document)

    def test_publication_is_exclusive_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(prefix="k2-selection-") as directory:
            output = Path(directory) / "result.json"
            self.module.write_exclusive(output, b"first\n")
            self.assertEqual(output.read_bytes(), b"first\n")
            with self.assertRaises(FileExistsError):
                self.module.write_exclusive(output, b"second\n")
            self.assertEqual(output.read_bytes(), b"first\n")
            if hasattr(os, "symlink"):
                target = Path(directory) / "target.json"
                link = Path(directory) / "link.json"
                target.write_bytes(b"target\n")
                link.symlink_to(target)
                with self.assertRaises(FileExistsError):
                    self.module.write_exclusive(link, b"replacement\n")
                self.assertEqual(target.read_bytes(), b"target\n")

    def test_conservation_is_fail_closed(self):
        document = copy.deepcopy(self.replay)
        document["owners"]["a3"]["full50"]["aggregate"]["totals"]["retired"] -= 1
        with self.assertRaisesRegex(self.module.SelectionError, "conservation"):
            self.module.validate_replay(document)

    def test_capacity_subset_is_fail_closed(self):
        document = copy.deepcopy(self.replay)
        document["generator"]["capacity22_is_full50_subset_view"] = False
        with self.assertRaisesRegex(self.module.SelectionError, "subset"):
            self.module.validate_replay(document)

    def test_semantic_grade_is_fail_closed(self):
        document = copy.deepcopy(self.cost)
        document["candidates"]["a2"]["semantic_grade"] = "FLAT"
        with self.assertRaisesRegex(self.module.SelectionError, "semantic"):
            self.module.validate_cost(document)

    def test_physical_metric_fabrication_is_fail_closed(self):
        document = copy.deepcopy(self.cost)
        document["physical_metrics"]["power"] = 0.1
        with self.assertRaisesRegex(self.module.SelectionError, "physical"):
            self.module.validate_cost(document)

    def test_same_flow_pareto_is_fail_closed(self):
        document = copy.deepcopy(self.cost)
        document["pareto"]["full_p6"] = ["a3"]
        with self.assertRaisesRegex(self.module.SelectionError, "comparison"):
            self.module.validate_cost(document)


if __name__ == "__main__":
    unittest.main(verbosity=2)
