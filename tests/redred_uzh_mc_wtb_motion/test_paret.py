from __future__ import annotations

import unittest
import hashlib
from pathlib import Path

from benchmarks.redred_uzh_mc_wtb_motion.evaluate_paret import (
    EvaluationFailure,
    EXPECTED_PREREGISTRATION_SHA256,
    costs,
    decide_status,
    timestamp_ns,
)


class ParetUnitTests(unittest.TestCase):
    def test_preregistration_bytes_are_frozen(self):
        path = Path("benchmarks/redred_uzh_mc_wtb_motion/paret_preregistered.json")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), EXPECTED_PREREGISTRATION_SHA256)

    def test_exact_timestamp_parser(self):
        self.assertEqual(timestamp_ns("41.321000001"), 41_321_000_001)
        with self.assertRaises(EvaluationFailure):
            timestamp_ns("41.321")

    def test_same_polarity_distance_and_oof_penalty(self):
        anchor = [
            {"x": 0, "y": 0, "polarity_01": 0},
            {"x": 9, "y": 9, "polarity_01": 1},
        ]
        in_fov = {"geometry_status": "in_fov", "locality_x": 0.0, "locality_y": 0.0}
        oof = {"geometry_status": "outside_reference_image", "locality_x": None, "locality_y": None}
        rows = [{
            "polarity_01": 0,
            "arms": {
                "RAW": in_fov,
                "SENSOR_FIXED": dict(in_fov),
                "MC_CORRECT": oof,
                "MC_WRONG": oof,
                "MC_DELAYED": oof,
                "RETIRE_WARP": oof,
            },
        }]
        result = costs(rows, anchor, {"sensor": {"width": 10, "height": 10}})
        self.assertEqual(result["RAW"], [0.0])
        self.assertEqual(result["SENSOR_FIXED"], [0.0])
        self.assertEqual(result["MC_CORRECT"], [1.0])

    def test_sensor_fixed_uses_raw_locality_even_when_projection_is_oof(self):
        anchor = [{"x": 0, "y": 0, "polarity_01": 0}]
        raw = {"geometry_status": "in_fov", "locality_x": 0.0, "locality_y": 0.0}
        sensor = {"geometry_status": "outside_reference_image", "locality_x": 0.0, "locality_y": 0.0}
        projected_oof = {"geometry_status": "outside_reference_image", "locality_x": None, "locality_y": None}
        rows = [{
            "polarity_01": 0,
            "arms": {
                "RAW": raw,
                "SENSOR_FIXED": sensor,
                "MC_CORRECT": projected_oof,
                "MC_WRONG": projected_oof,
                "MC_DELAYED": projected_oof,
                "RETIRE_WARP": projected_oof,
            },
        }]
        result = costs(rows, anchor, {"sensor": {"width": 10, "height": 10}})
        self.assertEqual(result["RAW"], result["SENSOR_FIXED"])
        self.assertEqual(result["MC_CORRECT"], [1.0])

    def test_raw_sensor_fixed_divergence_is_fatal(self):
        anchor = [{"x": 0, "y": 0, "polarity_01": 0}]
        common = {"geometry_status": "in_fov", "locality_y": 0.0}
        rows = [{
            "polarity_01": 0,
            "arms": {
                "RAW": {**common, "locality_x": 0.0},
                "SENSOR_FIXED": {**common, "locality_x": 1.0},
                "MC_CORRECT": {**common, "locality_x": 0.0},
                "MC_WRONG": {**common, "locality_x": 0.0},
                "MC_DELAYED": {**common, "locality_x": 0.0},
                "RETIRE_WARP": {**common, "locality_x": 0.0},
            },
        }]
        with self.assertRaises(EvaluationFailure):
            costs(rows, anchor, {"sensor": {"width": 10, "height": 10}})

    def test_uninformative_retire_holds_without_hiding_primary_failure(self):
        samples = {
            name: {
                "lower_97_5_one_sided": -1.0,
                "lower_98_333_one_sided_bonferroni_three_controls": -1.0,
            }
            for name in ("SENSOR_FIXED", "MC_WRONG", "MC_DELAYED", "RETIRE_WARP")
        }
        geometry = {
            "mc_wrong": {"identified": True},
            "timing_controls": {
                "MC_DELAYED": {"identified": True},
                "RETIRE_WARP": {"identified": False, "informative": False},
            },
        }
        status, primary_gate, controls_pass = decide_status(
            1.0,
            -0.5,
            samples,
            geometry,
            {"primary_effect": {"relative_reduction_strictly_greater_than": 0.05}},
        )
        self.assertEqual(status, "HOLD_RETIRE_CONTROL_UNINFORMATIVE")
        self.assertEqual(primary_gate, "FAIL_NO_PREREGISTERED_BENEFIT")
        self.assertFalse(controls_pass)

if __name__ == "__main__":
    unittest.main()
