"""Native synthetic-fixture tests for the source-bound six-arm generator."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarks.redred_uzh_mc_wtb_controls import ARM_NAMES, evaluate_records, load_records_jsonl
from benchmarks.redred_uzh_mc_wtb_six_arm_generator import generator


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class SyntheticFixture:
    """Small non-official source plus externally supplied fixture retire records."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.joined = root / "joined"
        self.adapter = root / "adapter"
        self.join_spec = root / "join_spec.json"
        self.retire = root / "retire_receipt.jsonl"
        self.generator_spec = root / "generator_spec.json"
        self.joined.mkdir()
        self.adapter.mkdir()
        self.epoch = "synthetic_source_epoch_ns"
        self.times = [41_315_000_000, 41_320_000_000, 41_325_000_000]
        self.quaternions = [
            (0.0, -math.sin(0.02), 0.0, math.cos(0.02)),
            (0.0, 0.0, 0.0, 1.0),
            (0.0, math.sin(0.03), 0.0, math.cos(0.03)),
        ]
        self.calibration = (100.0, 100.0, 120.0, 90.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.events = [
            {"dataset_event_index": 100 + index, "join_sequence_index": index,
             "timestamp_ns": 41_321_000_000 + index * 100_000,
             "timestamp_seconds_lexeme": f"41.321{index:03d}000",
             "x": x, "y": y, "polarity_01": index & 1}
            for index, (x, y) in enumerate(((120, 90), (239, 90), (10, 20), (180, 150)))
        ]
        self._write_join()
        self._write_adapter()
        self._write_retire()
        self._write_spec()

    def _write_join(self) -> None:
        calibration = {
            "parameters_exact_decimal": {
                key: format(value, ".17g") for key, value in zip(
                    ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3"),
                    self.calibration,
                )
            },
            "sensor": {"width": 240, "height": 180},
        }
        poses = [{"record_type": "header", "timebase": {"epoch": self.epoch, "unit": "ns"}}]
        for index, (timestamp, quaternion) in enumerate(zip(self.times, self.quaternions)):
            poses.append({
                "record_type": "pose", "source_pose_index": index, "timestamp_ns": timestamp,
                "quaternion_exact_decimal": {
                    key: format(value, ".17g") for key, value in zip(("qx", "qy", "qz", "qw"), quaternion)
                },
            })
        event_rows = [{
            "record_type": "header",
            "selection": {"start_timestamp_ns_inclusive": 41_321_000_000},
        }]
        for event in self.events:
            _, bracket = generator._pose_at(self.times, self.quaternions, event["timestamp_ns"])
            event_rows.append({"record_type": "event", "bracket": bracket, **event})
        write(self.joined / "calibration.json", canonical(calibration))
        write(self.joined / "poses.jsonl", b"".join(canonical(row) for row in poses))
        write(self.joined / "events_pose_join.jsonl", b"".join(canonical(row) for row in event_rows))
        write(self.joined / "receipt.json", canonical({"fixture": "pose-join"}))
        write(self.joined / "COMPLETE.json", canonical({"fixture": "pose-join-complete"}))
        write(self.join_spec, canonical({"schema": "redred.uzh_shapes_pose_join.spec/v1", "fixture": True}))

    def _write_adapter(self) -> None:
        reference_q, _ = generator._pose_at(self.times, self.quaternions, 41_321_000_000)
        reference_inverse = generator._transpose(generator._rotation(reference_q))
        rows = [{"record_type": "header"}]
        for event in self.events:
            occurrence_q, _ = generator._pose_at(self.times, self.quaternions, event["timestamp_ns"])
            raw_ray = generator._raw_ray(event["x"], event["y"], self.calibration)
            matrix = generator._matmul(reference_inverse, generator._rotation(occurrence_q))
            ray = generator._normalize(generator._matvec(matrix, raw_ray), "fixture ray")
            projection = generator._project(ray, self.calibration, 240, 180)
            source_event = {
                "dataset_event_index": event["dataset_event_index"],
                "join_sequence_index": event["join_sequence_index"],
                "timestamp_ns": event["timestamp_ns"],
                "timestamp_seconds_lexeme": event["timestamp_seconds_lexeme"],
                "x_sensor": event["x"], "y_sensor": event["y"],
                "polarity_01": event["polarity_01"],
            }
            disposition = (
                "WORLD_REFERENCE_EVENT" if projection[0] == "in_fov" else
                "RAW_ESCAPE_GEOMETRIC_OOF" if projection[0] in ("outside_reference_image", "behind_reference") else
                "RAW_BYPASS_INVALID_GEOMETRY"
            )
            rows.append({
                "record_type": "event_disposition", "source_event": source_event,
                "disposition": disposition,
                "geometry": {
                    "status": projection[0],
                    "x_reference_float_decimal": None if projection[1] is None else format(projection[1], ".17g"),
                    "y_reference_float_decimal": None if projection[2] is None else format(projection[2], ".17g"),
                    "x_reference": projection[3], "y_reference": projection[4],
                },
            })
        artifact = b"".join(canonical(row) for row in rows)
        write(self.adapter / "events_mc_wtb_adapter.jsonl", artifact)
        write(self.adapter / "receipt.json", canonical({"artifact": {"name": "events_mc_wtb_adapter.jsonl"}}))
        write(self.adapter / "COMPLETE.json", canonical({"fixture": "adapter-complete"}))

    def _write_retire(self, records: list[dict] | None = None) -> None:
        ids = [event["dataset_event_index"] for event in self.events]
        header = {
            "schema": generator.RETIRE_STREAM_SCHEMA,
            "record_type": "header",
            "provenance_class": generator.SYNTHETIC_RETIRE_PROVENANCE,
            "producer": {
                "implementation_id": "native-fixture-retire-recorder",
                "implementation_commit": "fixture-only",
                "config_sha256": sha(b"fixture-config"),
                "run_id": "fixture-run-1",
                "raw_run_artifact_sha256": sha(b"fixture-observed-run"),
            },
            "source_timebase": {"unit": "ns", "epoch": self.epoch},
            "retire_clock": {"clock_domain": "fixture-clock", "unit": "ns", "epoch": self.epoch},
            "mapping_to_source_timebase": {"method": "fixture_shared_clock", "evidence_sha256": sha(b"fixture-clock-evidence"), "validated": False},
            "record_count": len(self.events) if records is None else len(records),
            "ordered_dataset_event_index_sha256": sha(b"".join(f"{value}\n".encode("ascii") for value in (ids if records is None else [row["dataset_event_index"] for row in records]))),
        }
        if records is None:
            records = [{
                "schema": generator.RETIRE_RECORD_SCHEMA, "record_type": "retire",
                "dataset_event_index": event["dataset_event_index"],
                "join_sequence_index": event["join_sequence_index"],
                "occurrence_timestamp_ns": event["timestamp_ns"],
                "accepted_count": 1, "retired_count": 1,
                "retire_timestamp_ns": event["timestamp_ns"] + 50_000,
            } for event in self.events]
        write(self.retire, canonical(header) + b"".join(canonical(row) for row in records))

    def _input_pins(self) -> dict:
        def identity(path: Path) -> dict:
            payload = path.read_bytes()
            return {"size_bytes": len(payload), "sha256": sha(payload)}
        return {
            "pose_join": {
                "status": "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED",
                "promotion_status": "HOLD_MC_WTB_ADAPTER",
                "receipt": identity(self.joined / "receipt.json"),
                "completion": identity(self.joined / "COMPLETE.json"),
                "events": identity(self.joined / "events_pose_join.jsonl"),
                "poses": identity(self.joined / "poses.jsonl"),
                "calibration": identity(self.joined / "calibration.json"),
            },
            "join_spec": identity(self.join_spec),
            "adapter": {
                "status": "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED",
                "promotion_status": "HOLD_MC_WTB_REAL_DATA_BENEFIT",
                "receipt": identity(self.adapter / "receipt.json"),
                "completion": identity(self.adapter / "COMPLETE.json"),
                "events": identity(self.adapter / "events_mc_wtb_adapter.jsonl"),
            },
            "retire_receipt": identity(self.retire),
        }

    def _write_spec(self, mode: str = generator.SYNTHETIC_MODE) -> None:
        ids = [event["dataset_event_index"] for event in self.events]
        decimal = b"".join(f"{value}\n".encode("ascii") for value in ids)
        compact = canonical(ids)
        claims = generator._claim_scope(mode, False)
        spec = {
            "schema": generator.GENERATOR_SPEC_SCHEMA,
            "mode": mode,
            "parameter_set_id": "SIXARM-NATIVE-SYNTHETIC-FIXTURE-V1",
            "input_pins": self._input_pins(),
            "cohort": {
                "record_count": len(self.events), "first_dataset_event_index": ids[0],
                "last_dataset_event_index": ids[-1], "decimal_id_lf_sha256": sha(decimal),
                "compact_id_array_lf_sha256": sha(compact),
                "polarity_0": 2, "polarity_1": 2, "timestamp_tie_extras": 0,
            },
            "geometry_contract": {
                "record_schema": generator.RECORD_SCHEMA, "source_pose": "camera_to_world_T_WC",
                "quaternion_order": "xyzw", "reference_timestamp_ns": 41_321_000_000,
                "translation_policy": "preserved_not_applied", "pixel_rounding": "floor(value_plus_0.5)",
                "bounds": "continuous_before_rounding",
            },
            "delay_contract": {"mc_delayed_delta_ns": 4_998_186, "lookup": "occurrence_minus_delta_no_clamp"},
            "retire_contract": {
                "provenance_class": generator.SYNTHETIC_RETIRE_PROVENANCE if mode == generator.SYNTHETIC_MODE else generator.PRODUCTION_RETIRE_PROVENANCE,
                "source_timebase": {"unit": "ns", "epoch": self.epoch},
                "missing_policy": "fail_no_partial_output", "receipt_sha256": sha(self.retire.read_bytes()),
            },
            "controls_preregistration": {
                "schema": "redred.uzh_mc_wtb_controls.preregistration/v2",
                "parameter_set_id": "UZH-S2-CONTROLS-8X8-1MS-V2",
                "raw_sha256": sha(generator._PREREG_PATH.read_bytes()),
            },
            "serialization": {"encoding": "ASCII", "json": "compact_sorted_keys", "line_ending": "LF", "header_in_output": False},
            "claim_scope": claims,
            "resource_limits": {"max_pose_bytes": 1_000_000, "max_event_bytes": 1_000_000, "max_adapter_bytes": 1_000_000, "max_retire_bytes": 1_000_000, "max_records": 100},
        }
        write(self.generator_spec, canonical(spec))

    def inspector_patches(self, official: bool = False):
        stack = mock.patch.multiple(
            generator,
            inspect_pose_join=mock.Mock(return_value={
                "status": "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED",
                "promotion_status": "HOLD_MC_WTB_ADAPTER",
                "official_uzh_source": official,
            }),
            inspect_adapter=mock.Mock(return_value={
                "status": "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED",
                "promotion_status": "HOLD_MC_WTB_REAL_DATA_BENEFIT",
                "official_uzh_source_input": official,
            }),
        )
        return stack

    def args(self, result: Path) -> tuple[Path, ...]:
        return self.joined, self.join_spec, self.adapter, self.retire, self.generator_spec, result


class GeneratorNativeTest(unittest.TestCase):
    def test_synthetic_six_arm_publish_inspect_and_deterministic_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            first, second = fixture.root / "first", fixture.root / "second"
            with fixture.inspector_patches():
                receipt = generator.generate(*fixture.args(first))
                checked = generator.inspect(first, *fixture.args(first)[:-1])
                generator.generate(*fixture.args(second))
            self.assertEqual(receipt["status"], generator.SYNTHETIC_STATUS)
            self.assertEqual(receipt["promotion_status"], "HOLD_MC_WTB_REAL_DATA_BENEFIT")
            self.assertFalse(receipt["claim_scope"]["official_uzh_source_input"])
            self.assertFalse(receipt["claim_scope"]["actual_retire_receipt_bound"])
            self.assertEqual(checked["record_count"], 4)
            for name in generator.FINAL_NAMES:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
            records = load_records_jsonl(first / generator.OUTPUT_NAME)
            self.assertEqual(len(records), 4)
            self.assertEqual(set(records[0]["arms"]), set(ARM_NAMES))
            for source, record in zip(fixture.events, records):
                self.assertEqual(record["arms"]["RETIRE_WARP"]["pose_lookup_timestamp_ns"], source["timestamp_ns"] + 50_000)
                self.assertIsNone(record["arms"]["RAW"]["pose_lookup_timestamp_ns"])
                self.assertEqual(record["arms"]["RAW"]["locality_x"], record["arms"]["SENSOR_FIXED"]["locality_x"])
            self.assertEqual(evaluate_records(records)["status"], "CONTROL_EVALUATION_ONLY_NO_BANDWIDTH_OR_BENEFIT_CLAIM")

    def test_missing_retire_rows_like_a23_1019_of_1100_never_get_filled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            original = generator._jsonl(fixture.retire.read_bytes(), "fixture retire")[1:]
            fixture._write_retire(original[:-1])
            fixture._write_spec()
            result = fixture.root / "blocked"
            with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())

    def test_official_mode_rejects_synthetic_retire_and_publishes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            fixture._write_spec(generator.PRODUCTION_MODE)
            result = fixture.root / "official-blocked"
            with fixture.inspector_patches(official=True), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())

    def test_source_free_inspection_tamper_and_overwrite_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            result = fixture.root / "result"
            with fixture.inspector_patches():
                generator.generate(*fixture.args(result))
            with self.assertRaises(TypeError):
                generator.inspect(result)
            artifact = result / generator.OUTPUT_NAME
            artifact.chmod(0o644)
            artifact.write_bytes(artifact.read_bytes() + b"{}\n")
            with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                generator.inspect(result, *fixture.args(result)[:-1])
            existing = fixture.root / "existing"
            existing.mkdir()
            with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(existing))


if __name__ == "__main__":
    unittest.main()
