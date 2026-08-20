from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from benchmarks.redred_uzh_mc_wtb_controls import (
    ARM_NAMES,
    EVALUATION_STATUS,
    EvaluationFailure,
    evaluate_records,
    load_records_jsonl,
)


PACKAGE = Path(__file__).resolve().parents[2] / "benchmarks" / "redred_uzh_mc_wtb_controls"


def arm(
    timestamp_ns: int,
    ray: list[float] | None,
    x: float | None,
    y: float | None,
    *,
    status: str = "in_fov",
    lookup: int | None = None,
) -> dict[str, object]:
    return {
        "geometry_status": status,
        "reference_ray": ray,
        "locality_x": x,
        "locality_y": y,
        "pose_lookup_timestamp_ns": timestamp_ns if lookup is None else lookup,
    }


def record(
    dataset_event_index: int,
    timestamp_ns: int,
    *,
    join_sequence_index: int = 0,
    x: int = 8,
    y: int = 8,
) -> dict[str, object]:
    correct = [0.0, 0.0, 1.0]
    raw = [math.sin(math.radians(0.5)), 0.0, math.cos(math.radians(0.5))]
    wrong = [math.sin(math.radians(1.0)), 0.0, math.cos(math.radians(1.0))]
    delayed = [0.0, math.sin(math.radians(0.2)), math.cos(math.radians(0.2))]
    retired = [0.0, math.sin(math.radians(-0.3)), math.cos(math.radians(-0.3))]
    raw_output = arm(timestamp_ns, raw, x, y)
    raw_output["pose_lookup_timestamp_ns"] = None
    return {
        "schema": "redred.uzh_mc_wtb_controls.adapter_record/v2",
        "dataset_event_index": dataset_event_index,
        "join_sequence_index": join_sequence_index,
        "timestamp_ns": timestamp_ns,
        "x_raw": x,
        "y_raw": y,
        "polarity_01": dataset_event_index & 1,
        "oracle_status": "in_fov",
        "oracle_reference_ray": correct,
        "arms": {
            "RAW": raw_output,
            "SENSOR_FIXED": arm(timestamp_ns, correct, x, y),
            "MC_CORRECT": arm(timestamp_ns, correct, x, y),
            "MC_WRONG": arm(timestamp_ns, wrong, x + 8.0, y),
            "MC_DELAYED": arm(timestamp_ns, delayed, x, y + 8.0, lookup=timestamp_ns - 1),
            "RETIRE_WARP": arm(timestamp_ns, retired, x + 8.0, y + 8.0, lookup=timestamp_ns + 1),
        },
    }


class ControlEvaluatorTest(unittest.TestCase):
    def test_equal_id_six_arm_geometry_controls_and_claim_boundary(self) -> None:
        rows = [
            record(
                index + 10,
                41_321_000_000 + index,
                join_sequence_index=index,
            )
            for index in range(4)
        ]
        result = evaluate_records(rows)
        self.assertEqual(result["status"], EVALUATION_STATUS)
        self.assertEqual(result["cohort"]["admitted_event_count"], 4)
        self.assertEqual(result["cohort"]["arm_names"], list(ARM_NAMES))
        self.assertTrue(result["cohort"]["equal_event_ids_by_construction"])
        self.assertEqual(result["cohort"]["raw_sensor_fixed_relation"], {
            "locality_coordinates_exact_equal": True,
            "raw_pose_transform": "none",
            "sensor_fixed_geometry_evaluation": "receiver_side_occurrence_pose",
            "geometry_ray_equality_required": False,
        })
        self.assertEqual(
            result["cohort"]["ordered_dataset_event_index_sha256"],
            hashlib.sha256(b"10\n11\n12\n13\n").hexdigest(),
        )
        self.assertEqual(result["geometry_control_gate"]["status"], "PASS_GEOMETRY_CONTROLS_ONLY")
        self.assertTrue(result["arms"]["SENSOR_FIXED"]["geometry"]["meets_correct_geometry_gate"])
        self.assertTrue(result["arms"]["MC_CORRECT"]["geometry"]["meets_correct_geometry_gate"])
        self.assertGreater(
            result["arms"]["RAW"]["geometry"]["angular_error_degrees"]["p50"],
            result["arms"]["SENSOR_FIXED"]["geometry"]["angular_error_degrees"]["p50"],
        )
        self.assertEqual(
            result["arms"]["RAW"]["tile_locality_opportunity"],
            result["arms"]["SENSOR_FIXED"]["tile_locality_opportunity"],
        )
        self.assertTrue(result["geometry_control_gate"]["mc_wrong"]["identified"])
        claims = result["claim_scope"]
        self.assertFalse(claims["bandwidth_measured"])
        self.assertFalse(claims["compression_measured"])
        self.assertFalse(claims["benefit_claimed"])
        self.assertNotIn("bits", json.dumps(result).lower())
        json.dumps(result, allow_nan=False)

    def test_missing_arm_duplicate_id_and_timestamp_reordering_are_rejected(self) -> None:
        missing = record(1, 41_321_000_000)
        del missing["arms"]["MC_WRONG"]  # type: ignore[index]
        with self.assertRaisesRegex(EvaluationFailure, "arms.*keys mismatch"):
            evaluate_records([missing])
        with self.assertRaisesRegex(EvaluationFailure, "strictly increasing"):
            evaluate_records([
                record(1, 41_321_000_000, join_sequence_index=0),
                record(1, 41_321_000_001, join_sequence_index=1),
            ])
        with self.assertRaisesRegex(EvaluationFailure, "timestamps must be nondecreasing"):
            evaluate_records([
                record(1, 41_321_000_001, join_sequence_index=0),
                record(2, 41_321_000_000, join_sequence_index=1),
            ])
        with self.assertRaisesRegex(EvaluationFailure, "join_sequence_index"):
            evaluate_records([record(1, 41_321_000_000, join_sequence_index=1)])
        old_schema = record(1, 41_321_000_000)
        old_schema["schema"] = "redred.uzh_mc_wtb_controls.adapter_record/v1"
        with self.assertRaisesRegex(EvaluationFailure, "schema mismatch"):
            evaluate_records([old_schema])

    def test_lookup_time_semantics_are_fail_closed(self) -> None:
        cases = (
            ("RAW", 0),
            ("MC_CORRECT", 1),
            ("MC_WRONG", 1),
            ("SENSOR_FIXED", 1),
            ("MC_DELAYED", 0),
            ("RETIRE_WARP", -1),
        )
        timestamp = 41_321_000_000
        for arm_name, delta in cases:
            row = record(1, timestamp)
            row["arms"][arm_name]["pose_lookup_timestamp_ns"] = timestamp + delta  # type: ignore[index]
            with self.subTest(arm=arm_name), self.assertRaises(EvaluationFailure):
                evaluate_records([row])

    def test_raw_and_sensor_fixed_locality_must_not_diverge(self) -> None:
        row = record(1, 41_321_000_000)
        row["arms"]["RAW"]["locality_x"] = 9.0  # type: ignore[index]
        with self.assertRaisesRegex(EvaluationFailure, "identical raw locality"):
            evaluate_records([row])
        outside = record(1, 41_321_000_000)
        outside["arms"]["RAW"]["geometry_status"] = (  # type: ignore[index]
            "outside_reference_image"
        )
        with self.assertRaisesRegex(EvaluationFailure, "cannot be OOF or behind"):
            evaluate_records([outside])

    def test_invalid_negative_control_cannot_count_as_identified(self) -> None:
        row = record(1, 41_321_000_000)
        wrong = row["arms"]["MC_WRONG"]  # type: ignore[index]
        wrong.update({
            "geometry_status": "invalid_distortion",
            "reference_ray": None,
            "locality_x": None,
            "locality_y": None,
        })
        result = evaluate_records([row])
        gate = result["geometry_control_gate"]
        self.assertEqual(gate["status"], "HOLD_WRONG_CONTROL_NOT_IDENTIFIED")
        self.assertFalse(gate["mc_wrong"]["all_rays_valid"])
        self.assertFalse(gate["mc_wrong"]["identified"])

    def test_oof_and_invalid_remain_in_geometry_and_locality_denominators(self) -> None:
        normal = record(1, 41_321_000_000, join_sequence_index=0)
        outside = record(2, 41_321_000_001, join_sequence_index=1, x=16)
        outside_arm = outside["arms"]["MC_CORRECT"]  # type: ignore[index]
        outside_arm["geometry_status"] = "outside_reference_image"
        outside_arm["locality_x"] = 248.0
        invalid = record(3, 41_321_000_002, join_sequence_index=2, x=24)
        invalid_arm = invalid["arms"]["MC_CORRECT"]  # type: ignore[index]
        invalid_arm.update({
            "geometry_status": "invalid_distortion",
            "reference_ray": None,
            "locality_x": None,
            "locality_y": None,
        })
        result = evaluate_records([normal, outside, invalid])
        geometry = result["arms"]["MC_CORRECT"]["geometry"]
        locality = result["arms"]["MC_CORRECT"]["tile_locality_opportunity"]
        self.assertEqual(geometry["denominator_events"], 3)
        self.assertEqual(geometry["penalized_error_events"], 1)
        self.assertEqual(geometry["angular_error_degrees"]["max"], 180.0)
        self.assertEqual(locality["coordinate_events"], 2)
        self.assertEqual(locality["escape_events"], 1)
        self.assertEqual(locality["persistent_map"]["denominator_events"], 3)
        self.assertEqual(locality["packet_key"]["denominator_events"], 3)

    def test_synthetic_1100_event_cohort_keeps_six_oof_in_every_denominator(self) -> None:
        rows = [
            record(
                index,
                41_321_000_000 + index,
                join_sequence_index=index,
            )
            for index in range(1_100)
        ]
        for row in rows[-6:]:
            output = row["arms"]["MC_CORRECT"]  # type: ignore[index]
            output["geometry_status"] = "outside_reference_image"
            output["locality_x"] = 248.0
        result = evaluate_records(rows)
        for arm_name in ARM_NAMES:
            arm_result = result["arms"][arm_name]
            self.assertEqual(arm_result["geometry"]["denominator_events"], 1_100)
            self.assertEqual(
                arm_result["tile_locality_opportunity"]["persistent_map"]["denominator_events"],
                1_100,
            )
            self.assertEqual(
                arm_result["tile_locality_opportunity"]["packet_key"]["denominator_events"],
                1_100,
            )
        self.assertEqual(
            result["arms"]["MC_CORRECT"]["geometry"]["status_counts"][
                "outside_reference_image"
            ],
            6,
        )

    def test_predeclared_half_open_tile_and_time_boundaries(self) -> None:
        rows = [
            record(1, 41_320_999_999, join_sequence_index=0, x=7, y=7),
            record(2, 41_321_000_000, join_sequence_index=1, x=8, y=8),
            record(3, 41_321_999_999, join_sequence_index=2, x=9, y=9),
            record(4, 41_322_000_000, join_sequence_index=3, x=16, y=16),
        ]
        result = evaluate_records(rows)
        parameters = result["pre_registered_parameters"]
        self.assertEqual(parameters, {
            "tile_width_px": 8,
            "tile_height_px": 8,
            "tile_origin_x_px": 0,
            "tile_origin_y_px": 0,
            "tile_boundary": "half_open_floor",
            "time_bin_ns": 1_000_000,
            "time_origin_ns": 41_321_000_000,
            "time_assignment": "occurrence_timestamp_for_every_arm",
        })
        locality = result["arms"]["SENSOR_FIXED"]["tile_locality_opportunity"]
        self.assertEqual(locality["persistent_map"]["active_keys"], 4)
        self.assertEqual(locality["packet_key"]["active_keys"], 4)

    def test_jsonl_loader_rejects_duplicate_keys_and_non_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_bytes(b'{"schema":"a","schema":"b"}\n')
            with self.assertRaisesRegex(EvaluationFailure, "duplicate JSON key"):
                load_records_jsonl(path)
            path.write_bytes(b"{}")
            with self.assertRaisesRegex(EvaluationFailure, "LF-terminated"):
                load_records_jsonl(path)

    def test_committed_preregistration_matches_runtime_constants(self) -> None:
        registration = json.loads((PACKAGE / "preregistered.json").read_text(encoding="ascii"))
        self.assertEqual(
            registration["schema"],
            "redred.uzh_mc_wtb_controls.preregistration/v2",
        )
        self.assertEqual(
            registration["parameter_set_id"],
            "UZH-S2-CONTROLS-8X8-1MS-V2",
        )
        self.assertEqual(registration["arms"], list(ARM_NAMES))
        self.assertEqual(registration["adapter_compatibility"], {
            "native_identity_fields": [
                "dataset_event_index",
                "join_sequence_index",
                "timestamp_ns",
                "x_raw",
                "y_raw",
                "polarity_01",
            ],
            "join_sequence_policy": "exact_zero_to_n_minus_one_source_order",
            "package_boundary": (
                "separate_evaluator_does_not_modify_or_relabel_native_adapter"
            ),
        })
        self.assertEqual(registration["tile"], {
            "width_px": 8,
            "height_px": 8,
            "origin_x_px": 0,
            "origin_y_px": 0,
            "boundary": "half_open_floor",
        })
        self.assertEqual(registration["time_bin"], {
            "width_ns": 1_000_000,
            "origin_ns": 41_321_000_000,
            "assignment": "occurrence_timestamp_for_every_arm",
        })
        self.assertEqual(registration["raw_semantics"], {
            "geometry": "unwarped_source_sensor_ray_interpreted_without_pose_transform",
            "locality": "original_raw_sensor_coordinates",
            "pose_lookup_timestamp_ns": None,
            "expected_relation_to_sensor_fixed": (
                "identical_locality_coordinates_but_distinct_geometry_semantics"
            ),
        })
        self.assertEqual(
            registration["claim_scope"],
            evaluate_records([record(1, 41_321_000_000)])["claim_scope"],
        )


if __name__ == "__main__":
    unittest.main()
