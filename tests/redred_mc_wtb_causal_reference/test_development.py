from __future__ import annotations

import unittest
import hashlib
import json
from pathlib import Path

from benchmarks.redred_mc_wtb_causal_reference.development import (
    CONSUMED_BLACKLIST,
    DevelopmentError,
    QUERY_START_SECONDS,
    _validate_registry,
    _runtime_tree_sha256,
    window_registry,
)


class DevelopmentContractTests(unittest.TestCase):
    def test_registry_is_fixed_score_blind_grid_and_excludes_consumed_interval(self) -> None:
        rows = window_registry()
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({row["window_id"] for row in rows}), 24)
        self.assertNotIn(41.321, QUERY_START_SECONDS)
        self.assertNotIn(43.321, QUERY_START_SECONDS)
        for row in rows:
            self.assertTrue(
                row["query_end_ns_exclusive"] <= CONSUMED_BLACKLIST[0]
                or row["warmup_start_ns_inclusive"] >= CONSUMED_BLACKLIST[1]
            )

    def test_registry_mutation_into_consumed_interval_is_detectable(self) -> None:
        start, end = CONSUMED_BLACKLIST
        self.assertLess(start, end)
        mutant = ({
            "window_id": "mutant_consumed",
            "warmup_start_ns_inclusive": 43_320_750_000,
            "query_start_ns_inclusive": 43_321_000_000,
            "query_end_ns_exclusive": 43_322_000_000,
        },)
        with self.assertRaisesRegex(DevelopmentError, "consumed interval"):
            _validate_registry(mutant)

    def test_stored_result_dependency_hashes_are_current(self) -> None:
        root = Path(__file__).resolve().parents[2]
        result = json.loads((
            root / "benchmarks/redred_mc_wtb_causal_reference/stage3_development_result.json"
        ).read_text(encoding="utf-8"))
        expected = {
            "development_py_sha256": root / "benchmarks/redred_mc_wtb_causal_reference/development.py",
            "reference_py_sha256": root / "benchmarks/redred_mc_wtb_causal_reference/reference.py",
            "routing_py_sha256": root / "benchmarks/redred_mc_wtb_causal_reference/routing.py",
            "geometry_reference_py_sha256": root / "benchmarks/redred_uzh_mc_wtb_motion_v3/geometry_reference.py",
            "evaluate_py_sha256": root / "benchmarks/redred_uzh_mc_wtb_motion_v3/evaluate.py",
            "motion_controller_py_sha256": root / "benchmarks/redred_mc_wtb_motion_qualification/controller.py",
        }
        for key, path in expected.items():
            self.assertEqual(result["reproduction"][key], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result["reproduction"]["runtime_python_tree_sha256"], _runtime_tree_sha256())
        receipt = json.loads((
            root / "docs/MC_WTB_STAGE1_3_CAUSAL_DEVELOPMENT_20260821.json"
        ).read_text(encoding="utf-8"))
        result_path = root / "benchmarks/redred_mc_wtb_causal_reference/stage3_development_result.json"
        self.assertEqual(
            receipt["artifacts"]["development_result_sha256"],
            hashlib.sha256(result_path.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
