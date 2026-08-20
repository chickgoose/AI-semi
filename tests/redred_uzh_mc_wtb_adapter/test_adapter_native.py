"""Native source-bound integration and fail-closed tests for the UZH adapter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.redred_uzh_mc_wtb import geometry
from benchmarks.redred_uzh_mc_wtb_adapter import adapter


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "benchmarks" / "redred_uzh_shapes_pose_join" / "join_spec.json"
SOURCE_ENV = "REDRED_UZH_POSE_JOIN_PACKAGE"
EXPECTED_RAW_IDS = [13_856_524, 13_856_654, 13_856_794, 13_857_092, 13_857_160, 13_857_171]
EXPECTED_STATUS = "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED"
EXPECTED_PROMOTION = "HOLD_MC_WTB_REAL_DATA_BENEFIT"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="ascii"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AdapterIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        value = os.environ.get(SOURCE_ENV)
        if value is None:
            raise unittest.SkipTest(f"set {SOURCE_ENV} to a completed bound pose-join package")
        cls.source = Path(value)
        if not cls.source.is_dir():
            raise unittest.SkipTest(f"pose-join package is absent: {cls.source}")

    def test_exact_dispositions_identity_and_scoped_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            receipt = adapter.adapt(self.source, SPEC, result)
            inspected = adapter.inspect(result, self.source, SPEC)
            completion = read_json(result / adapter.COMPLETION_NAME)
            header = read_jsonl(result / adapter.EVENTS_NAME)[0]
            for value in (receipt, completion, header):
                self.assertEqual(value["status"], EXPECTED_STATUS)
                self.assertEqual(value["promotion_status"], EXPECTED_PROMOTION)
            self.assertEqual(inspected["status"], EXPECTED_STATUS)
            self.assertEqual(inspected["promotion_status"], EXPECTED_PROMOTION)
            self.assertEqual(inspected["record_count"], 1100)
            self.assertEqual(inspected["world_reference_events"], 1094)
            self.assertEqual(inspected["raw_escape_geometric_oof"], 6)
            self.assertEqual(inspected["raw_bypass_invalid_geometry"], 0)

            source_rows = read_jsonl(self.source / "events_pose_join.jsonl")[1:]
            output_rows = read_jsonl(result / adapter.EVENTS_NAME)
            header, records = output_rows[0], output_rows[1:]
            self.assertEqual(header["record_count"], 1100)
            self.assertEqual(len(records), len(source_rows))
            source_tuples = [
                (row["dataset_event_index"], row["join_sequence_index"], row["timestamp_ns"],
                 row["timestamp_seconds_lexeme"], row["x"], row["y"], row["polarity_01"])
                for row in source_rows
            ]
            output_tuples = [
                (row["source_event"]["dataset_event_index"], row["source_event"]["join_sequence_index"],
                 row["source_event"]["timestamp_ns"], row["source_event"]["timestamp_seconds_lexeme"],
                 row["source_event"]["x_sensor"], row["source_event"]["y_sensor"],
                 row["source_event"]["polarity_01"])
                for row in records
            ]
            self.assertEqual(output_tuples, source_tuples)
            raw = [row for row in records if row["disposition"] == adapter.RAW_ESCAPE_GEOMETRIC_OOF]
            self.assertEqual([row["source_event"]["dataset_event_index"] for row in raw], EXPECTED_RAW_IDS)
            self.assertEqual({row["geometry"]["status"] for row in raw}, {geometry.OUTSIDE_REFERENCE_IMAGE})
            self.assertTrue(all(row["geometry"]["y_reference_float_decimal"] is not None for row in raw))
            self.assertEqual(receipt["conservation"]["dropped_events"], 0)
            self.assertEqual(receipt["conservation"]["duplicate_events"], 0)
            self.assertEqual(receipt["conservation"]["reordered_events"], 0)
            claims = receipt["claim_scope"]
            self.assertTrue(claims["orientation_only"])
            self.assertTrue(claims["translation_preserved_not_applied"])
            self.assertTrue(claims["offline_future_bracket_slerp"])
            self.assertTrue(claims["future_pose_lookahead_required"])
            self.assertFalse(claims["causal_hardware_claimed"])
            self.assertFalse(claims["clock_alignment_validated"])
            self.assertTrue(claims["raw_escape_is_disposition_only"])
            self.assertFalse(claims["raw_packet_fifo_or_decoder_implemented"])
            self.assertFalse(claims["controls_implemented"])
            self.assertFalse(claims["codec_or_wire_benefit_claimed"])
            self.assertFalse(claims["rtl_timing_power_or_ppa_claimed"])

    def test_reference_and_event_pose_use_the_exact_source_brackets(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            adapter.adapt(self.source, SPEC, result)
            output = read_jsonl(result / adapter.EVENTS_NAME)
            header, records = output[0], output[1:]
            source_poses = read_jsonl(self.source / "poses.jsonl")[1:]

            def expected_quaternion(bracket, timestamp):
                left = source_poses[bracket["left_source_pose_index"]]
                right = source_poses[bracket["right_source_pose_index"]]
                self.assertEqual(left["timestamp_ns"], bracket["left_timestamp_ns"])
                self.assertEqual(right["timestamp_ns"], bracket["right_timestamp_ns"])
                alpha = bracket["alpha_numerator_ns"] / bracket["alpha_denominator_ns"]
                before = tuple(float(left["quaternion_exact_decimal"][key]) for key in ("qx", "qy", "qz", "qw"))
                after = tuple(float(right["quaternion_exact_decimal"][key]) for key in ("qx", "qy", "qz", "qw"))
                self.assertEqual(timestamp - left["timestamp_ns"], bracket["alpha_numerator_ns"])
                return geometry.slerp_xyzw(before, after, alpha)

            reference = header["reference_pose"]
            expected_reference = expected_quaternion(reference["source_bracket"], reference["timestamp_ns"])
            for actual, expected in zip(reference["quaternion_xyzw_decimal"], expected_reference):
                self.assertAlmostEqual(float(actual), expected, places=15)

            for index in (0, 550, 1099):
                pose = records[index]["event_pose"]
                expected = expected_quaternion(pose["source_bracket"], pose["timestamp_ns"])
                for actual, expected_component in zip(pose["quaternion_xyzw_decimal"], expected):
                    self.assertAlmostEqual(float(actual), expected_component, places=15)
                self.assertEqual(records[index]["source_pose_join"]["bracket"], pose["source_bracket"])

    def test_output_is_byte_deterministic_and_tamper_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            adapter.adapt(self.source, SPEC, first)
            adapter.adapt(self.source, SPEC, second)
            self.assertEqual(sorted(path.name for path in first.iterdir()), sorted(path.name for path in second.iterdir()))
            for name in adapter.FINAL_NAMES:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
            artifact = second / adapter.EVENTS_NAME
            artifact.chmod(0o644)
            artifact.write_bytes(artifact.read_bytes() + b" \n")
            with self.assertRaises(adapter.AdapterFailure):
                adapter.inspect(second, self.source, SPEC)

    def test_inspection_requires_source_and_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result"
            adapter.adapt(self.source, SPEC, result)
            with self.assertRaises(TypeError):
                adapter.inspect(result)

    def test_source_tamper_overwrite_symlink_and_invalid_geometry_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_copy = root / "source-copy"
            shutil.copytree(self.source, source_copy)
            events = source_copy / "events_pose_join.jsonl"
            events.write_bytes(events.read_bytes() + b" \n")
            rejected = root / "rejected"
            with self.assertRaises(adapter.AdapterFailure):
                adapter.adapt(source_copy, SPEC, rejected)
            self.assertFalse(rejected.exists())

            existing = root / "existing"
            existing.mkdir()
            sentinel = existing / "user-owned"
            sentinel.write_text("keep", encoding="ascii")
            with self.assertRaises(adapter.AdapterFailure):
                adapter.adapt(self.source, SPEC, existing)
            self.assertEqual(sentinel.read_text(encoding="ascii"), "keep")

            linked_source = root / "linked-source"
            linked_source.symlink_to(self.source, target_is_directory=True)
            with self.assertRaises(adapter.AdapterFailure):
                adapter.adapt(linked_source, SPEC, root / "linked-result")

            calls = 0
            original = geometry.warp_raw_sensor_to_reference

            def inject_one_invalid(*args, **kwargs):
                nonlocal calls
                calls += 1
                # adapt() performs one source-bound recomputation after
                # publication; inject the same first-event condition in both
                # 1,100-event passes.
                if (calls - 1) % 1100 == 0:
                    return geometry.WarpResult(status=geometry.INVALID_DISTORTION, distortion_iterations=50)
                return original(*args, **kwargs)

            invalid_result = root / "invalid-result"
            with mock.patch.object(adapter.geometry, "warp_raw_sensor_to_reference", side_effect=inject_one_invalid):
                receipt = adapter.adapt(self.source, SPEC, invalid_result)
            self.assertEqual(receipt["conservation"]["world_reference_events"], 1093)
            self.assertEqual(receipt["conservation"]["raw_escape_geometric_oof"], 6)
            self.assertEqual(receipt["conservation"]["raw_bypass_invalid_geometry"], 1)
            first = read_jsonl(invalid_result / adapter.EVENTS_NAME)[1]
            self.assertEqual(first["disposition"], adapter.RAW_BYPASS_INVALID_GEOMETRY)
            self.assertEqual(first["geometry"]["status"], geometry.INVALID_DISTORTION)


if __name__ == "__main__":
    unittest.main()
