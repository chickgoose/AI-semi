from __future__ import annotations

import math
import unittest

from benchmarks.redred_uzh_mc_wtb_motion_v3.coverage import (
    CoverageError,
    build_coverage_ledger,
    classify_reference_coordinate,
    padded_tile_halo_bounds,
)


def arm(status, disposition, x=None, y=None):
    return {
        "geometry_status": status,
        "disposition": disposition,
        "locality_x": x,
        "locality_y": y,
    }


def row(event_id, **arms):
    return {"dataset_event_index": event_id, "arms": arms}


def assert_no_penalty_key(testcase, value):
    if isinstance(value, dict):
        for key, child in value.items():
            testcase.assertNotIn("penalty", key.lower())
            assert_no_penalty_key(testcase, child)
    elif isinstance(value, list):
        for child in value:
            assert_no_penalty_key(testcase, child)


class PaddedTileHaloTests(unittest.TestCase):
    def test_partial_edge_tile_and_half_open_halo_bounds(self):
        bounds = padded_tile_halo_bounds(240, 180, 8, 8)
        self.assertEqual(bounds["core_tiles"]["columns"], 30)
        self.assertEqual(bounds["core_tiles"]["rows"], 23)
        self.assertEqual(bounds["padded_tiles"], {
            "x_min_inclusive": -1,
            "x_max_exclusive": 31,
            "y_min_inclusive": -1,
            "y_max_exclusive": 24,
        })
        self.assertEqual(bounds["padded_pixels"], {
            "x_min_inclusive": -8,
            "x_max_exclusive": 248,
            "y_min_inclusive": -8,
            "y_max_exclusive": 192,
        })
        self.assertEqual(classify_reference_coordinate(239.999, 179.999, bounds), "sensor_in_fov")
        self.assertEqual(classify_reference_coordinate(240.0, 179.0, bounds), "padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(10.0, 180.0, bounds), "padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(-8.0, 0.0, bounds), "padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(247.999, 191.999, bounds), "padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(-8.0001, 0.0, bounds), "outside_padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(248.0, 0.0, bounds), "outside_padded_tile_halo")
        self.assertEqual(classify_reference_coordinate(0.0, 192.0, bounds), "outside_padded_tile_halo")

    def test_bounds_reject_boolean_negative_and_nonfinite_values(self):
        for args in ((True, 180, 8, 8), (240, 0, 8, 8), (240, 180, -1, 8)):
            with self.subTest(args=args), self.assertRaises(CoverageError):
                padded_tile_halo_bounds(*args)
        bounds = padded_tile_halo_bounds(4, 4, 2, 2)
        for coordinate in ((math.nan, 0), (0, math.inf), (True, 0)):
            with self.subTest(coordinate=coordinate), self.assertRaises(CoverageError):
                classify_reference_coordinate(*coordinate, bounds)


class CoverageLedgerTests(unittest.TestCase):
    KWARGS = {
        "sensor_width": 240,
        "sensor_height": 180,
        "tile_width": 8,
        "tile_height": 8,
    }

    def test_geometry_disposition_and_loss_are_not_scalar_mixed(self):
        records = [
            row(10, A=arm("in_fov", "WORLD_REFERENCE_EVENT", 10.0, 20.0)),
            row(11, A=arm("outside_reference_image", "RAW_ESCAPE_GEOMETRIC_OOF", 242.0, 20.0)),
            row(12, A=arm("behind_reference", "RAW_ESCAPE_GEOMETRIC_OOF")),
            row(13, A=arm("invalid_distortion", "RAW_BYPASS_INVALID_GEOMETRY")),
            row(14, A=arm("in_fov", "DROPPED", 30.0, 40.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=range(10, 15), arm_names=("A",), **self.KWARGS
        )
        result = ledger["arms"]["A"]
        self.assertEqual(result["denominator"], 5)
        self.assertEqual(result["geometry"]["counts"], {
            "in_fov": 2,
            "valid_reference_oof_world_valid": 2,
            "invalid_geometry": 1,
            "geometry_unavailable": 0,
        })
        self.assertEqual(result["disposition"]["counts"], {
            "reference_event": 1,
            "raw_escape": 2,
            "invalid_geometry_bypass": 1,
            "dropped": 1,
            "missing": 0,
            "duplicate": 0,
        })
        self.assertEqual(result["valid_reference_oof_halo"]["counts"], {
            "covered_by_padded_tile_halo": 1,
            "outside_padded_tile_halo": 0,
            "coordinate_unavailable": 1,
        })
        self.assertTrue(result["equal_denominator_invariant"])
        self.assertFalse(result["no_drop_missing_duplicate"])
        self.assertFalse(result["geometry_disposition_contract_valid"])
        self.assertFalse(result["complete_coverage_contract"])
        self.assertEqual(result["loss_dataset_event_ids"], [14])
        self.assertEqual(result["cross_contract_violations"], [{
            "dataset_event_index": 14,
            "geometry": "in_fov",
            "disposition": "dropped",
            "expected_disposition": "reference_event",
        }])
        self.assertFalse(ledger["eligible_complete_coverage"])
        assert_no_penalty_key(self, ledger)

    def test_every_arm_keeps_the_same_expected_denominator(self):
        records = [
            row(1,
                A=arm("in_fov", "REFERENCE_EVENT", 1.0, 1.0),
                B=arm("outside_reference_image", "RAW_ESCAPE", -1.0, 1.0)),
            row(2,
                A=arm("invalid_geometry", "INVALID_GEOMETRY_BYPASS"),
                B=arm("in_fov", "WORLD_REFERENCE_EVENT", 2.0, 2.0)),
            row(3,
                A=arm("behind_reference", "RAW_ESCAPE"),
                B=arm("in_fov", "REFERENCE_EVENT", 3.0, 3.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(1, 2, 3), arm_names=("A", "B"), **self.KWARGS
        )
        self.assertTrue(ledger["equal_denominator_invariant"])
        self.assertTrue(ledger["eligible_complete_coverage"])
        for value in ledger["arms"].values():
            self.assertEqual(value["denominator"], 3)
            self.assertEqual(sum(value["geometry"]["counts"].values()), 3)
            self.assertEqual(sum(value["disposition"]["counts"].values()), 3)
            self.assertEqual(sum(value["geometry_disposition_cross_counts"].values()), 3)

    def test_missing_duplicate_and_unexpected_ids_remain_visible(self):
        records = [
            row(1, A=arm("in_fov", "REFERENCE_EVENT", 1.0, 1.0)),
            row(1, A=arm("in_fov", "REFERENCE_EVENT", 1.0, 1.0)),
            row(3, A=arm("in_fov", "REFERENCE_EVENT", 3.0, 3.0)),
            row(99, A=arm("in_fov", "REFERENCE_EVENT", 4.0, 4.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(1, 2, 3), arm_names=("A",), **self.KWARGS
        )
        identity = ledger["input_identity"]
        self.assertEqual(identity["missing_expected_dataset_event_ids"], [2])
        self.assertEqual(identity["duplicate_expected_dataset_event_ids"], [1])
        self.assertEqual(identity["duplicate_extra_record_count"], 1)
        self.assertEqual(identity["unexpected_dataset_event_ids"], [99])
        self.assertFalse(identity["first_occurrence_order_matches_expected"])
        arm_result = ledger["arms"]["A"]
        self.assertEqual(arm_result["disposition"]["counts"]["duplicate"], 1)
        self.assertEqual(arm_result["disposition"]["counts"]["missing"], 1)
        self.assertEqual(arm_result["disposition"]["counts"]["reference_event"], 1)
        self.assertEqual(arm_result["geometry"]["counts"]["geometry_unavailable"], 2)
        self.assertEqual(arm_result["denominator"], 3)
        self.assertFalse(ledger["eligible_complete_coverage"])

    def test_missing_arm_is_missing_only_for_that_arm(self):
        records = [
            row(7, A=arm("in_fov", "REFERENCE_EVENT", 1.0, 1.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(7,), arm_names=("A", "B"), **self.KWARGS
        )
        self.assertTrue(ledger["arms"]["A"]["complete_coverage_contract"])
        self.assertEqual(ledger["arms"]["B"]["disposition"]["dataset_event_ids"]["missing"], [7])
        self.assertEqual(ledger["arms"]["B"]["geometry"]["dataset_event_ids"]["geometry_unavailable"], [7])
        self.assertFalse(ledger["eligible_complete_coverage"])

    def test_reordered_complete_ids_keep_denominator_but_are_ineligible(self):
        records = [
            row(2, A=arm("in_fov", "REFERENCE_EVENT", 2.0, 2.0)),
            row(1, A=arm("in_fov", "REFERENCE_EVENT", 1.0, 1.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(1, 2), arm_names=("A",), **self.KWARGS
        )
        self.assertTrue(ledger["equal_denominator_invariant"])
        self.assertTrue(ledger["arms"]["A"]["complete_coverage_contract"])
        self.assertFalse(ledger["input_identity"]["first_occurrence_order_matches_expected"])
        self.assertFalse(ledger["eligible_complete_coverage"])

    def test_cross_contract_mismatch_is_reported_not_hidden_as_cost(self):
        records = [
            row(1, A=arm("in_fov", "RAW_ESCAPE", 1.0, 1.0)),
            row(2, A=arm("outside_reference_image", "REFERENCE_EVENT", 241.0, 1.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(1, 2), arm_names=("A",), **self.KWARGS
        )
        violations = ledger["arms"]["A"]["cross_contract_violations"]
        self.assertEqual([row["dataset_event_index"] for row in violations], [1, 2])
        self.assertTrue(ledger["arms"]["A"]["no_drop_missing_duplicate"])
        self.assertFalse(ledger["arms"]["A"]["geometry_disposition_contract_valid"])
        self.assertFalse(ledger["eligible_complete_coverage"])
        self.assertEqual(ledger["arms"]["A"]["denominator"], 2)

    def test_oof_halo_does_not_become_in_fov(self):
        records = [
            row(1, A=arm("outside_reference_image", "RAW_ESCAPE", 240.0, 10.0)),
            row(2, A=arm("outside_reference_image", "RAW_ESCAPE", 260.0, 10.0)),
        ]
        ledger = build_coverage_ledger(
            records, expected_event_ids=(1, 2), arm_names=("A",), **self.KWARGS
        )
        result = ledger["arms"]["A"]
        self.assertEqual(result["geometry"]["counts"]["in_fov"], 0)
        self.assertEqual(
            result["valid_reference_oof_halo"]["dataset_event_ids"]["covered_by_padded_tile_halo"],
            [1],
        )
        self.assertEqual(
            result["valid_reference_oof_halo"]["dataset_event_ids"]["outside_padded_tile_halo"],
            [2],
        )
        self.assertTrue(ledger["eligible_complete_coverage"])

    def test_invalid_coordinates_and_status_contracts_fail_closed(self):
        bad_arms = (
            arm("in_fov", "REFERENCE_EVENT", 240.0, 10.0),
            arm("outside_reference_image", "RAW_ESCAPE", 10.0, 10.0),
            arm("outside_reference_image", "RAW_ESCAPE"),
            arm("invalid_distortion", "RAW_BYPASS_INVALID_GEOMETRY", 1.0, 1.0),
            arm("in_fov", "REFERENCE_EVENT", math.nan, 1.0),
            arm("new_status", "REFERENCE_EVENT", 1.0, 1.0),
            arm("in_fov", "NEW_DISPOSITION", 1.0, 1.0),
            arm([], "REFERENCE_EVENT", 1.0, 1.0),
            arm("in_fov", {}, 1.0, 1.0),
        )
        for value in bad_arms:
            with self.subTest(value=value), self.assertRaises(CoverageError):
                build_coverage_ledger(
                    [row(1, A=value)],
                    expected_event_ids=(1,),
                    arm_names=("A",),
                    **self.KWARGS,
                )

    def test_expected_identity_and_arm_contracts_fail_closed(self):
        cases = (
            {"expected_event_ids": (), "arm_names": ("A",)},
            {"expected_event_ids": (1, 1), "arm_names": ("A",)},
            {"expected_event_ids": (True,), "arm_names": ("A",)},
            {"expected_event_ids": (1,), "arm_names": ("A", "A")},
            {"expected_event_ids": (1,), "arm_names": ()},
        )
        for parameters in cases:
            with self.subTest(parameters=parameters), self.assertRaises(CoverageError):
                build_coverage_ledger([], **parameters, **self.KWARGS)


if __name__ == "__main__":
    unittest.main()
