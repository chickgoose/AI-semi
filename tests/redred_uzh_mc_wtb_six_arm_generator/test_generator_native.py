"""Native synthetic-fixture tests for the source-bound six-arm generator."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
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


class SchemaSubsetError(AssertionError):
    """The committed receipt schema subset rejected an instance."""


def validate_schema_subset(value: object, schema: dict, root: dict | None = None, path: str = "$") -> None:
    """Validate the Draft 2020-12 keywords used by the committed receipt schema."""
    root = schema if root is None else root
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise SchemaSubsetError(f"{path}: unsupported reference {reference!r}")
        validate_schema_subset(value, root["$defs"][reference.removeprefix("#/$defs/")], root, path)
        return
    if "const" in schema and value != schema["const"]:
        raise SchemaSubsetError(f"{path}: const mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaSubsetError(f"{path}: enum mismatch")
    type_names = schema.get("type")
    if type_names is not None:
        type_names = [type_names] if isinstance(type_names, str) else type_names
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(checks[name](value) for name in type_names):
            raise SchemaSubsetError(f"{path}: type mismatch")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = set(required) - set(value)
        if missing:
            raise SchemaSubsetError(f"{path}: missing {sorted(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaSubsetError(f"{path}: extra {sorted(extra)}")
        for name, child in properties.items():
            if name in value:
                validate_schema_subset(value[name], child, root, f"{path}.{name}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise SchemaSubsetError(f"{path}: array length")
        for index, child in enumerate(schema.get("prefixItems", [])):
            if index < len(value):
                validate_schema_subset(value[index], child, root, f"{path}[{index}]")
        items = schema.get("items")
        if items is False and len(value) > len(schema.get("prefixItems", [])):
            raise SchemaSubsetError(f"{path}: additional array item")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_schema_subset(item, items, root, f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise SchemaSubsetError(f"{path}: string too short")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise SchemaSubsetError(f"{path}: pattern mismatch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaSubsetError(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaSubsetError(f"{path}: above maximum")


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

    def production_pin_view(self) -> dict[str, str]:
        pins = self._input_pins()
        return {
            "pose_join_receipt": pins["pose_join"]["receipt"]["sha256"],
            "pose_join_completion": pins["pose_join"]["completion"]["sha256"],
            "pose_join_events": pins["pose_join"]["events"]["sha256"],
            "pose_join_poses": pins["pose_join"]["poses"]["sha256"],
            "pose_join_calibration": pins["pose_join"]["calibration"]["sha256"],
            "join_spec": pins["join_spec"]["sha256"],
            "adapter_events": pins["adapter"]["events"]["sha256"],
            "adapter_receipt": pins["adapter"]["receipt"]["sha256"],
            "adapter_completion": pins["adapter"]["completion"]["sha256"],
        }

    def args(self, result: Path) -> tuple[Path, ...]:
        return self.joined, self.join_spec, self.adapter, self.retire, self.generator_spec, result


class GeneratorNativeTest(unittest.TestCase):
    def test_receipt_schema_accepts_emitted_sample_and_rejects_nested_malformed_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            result = fixture.root / "schema-sample"
            with fixture.inspector_patches():
                generator.generate(*fixture.args(result))
            receipt = json.loads((result / generator.RECEIPT_NAME).read_text(encoding="ascii"))
            schema_path = Path(generator.__file__).resolve().parent / "generator_receipt.schema.json"
            schema = json.loads(schema_path.read_text(encoding="ascii"))
            validate_schema_subset(receipt, schema)

            malformed = []
            missing_artifact_sha = copy.deepcopy(receipt)
            del missing_artifact_sha["artifact"]["sha256"]
            malformed.append(missing_artifact_sha)
            extra_claim = copy.deepcopy(receipt)
            extra_claim["claim_scope"]["producer_authenticity_verified"] = True
            malformed.append(extra_claim)
            missing_conservation = copy.deepcopy(receipt)
            del missing_conservation["arm_ledgers"]["conservation"]["missing_arms"]
            malformed.append(missing_conservation)
            extra_retire_provenance = copy.deepcopy(receipt)
            extra_retire_provenance["input_binding"]["retire_receipt"]["mapping_to_source_timebase"]["reviewed"] = True
            malformed.append(extra_retire_provenance)
            missing_parameter = copy.deepcopy(receipt)
            del missing_parameter["parameters"]["reference_bracket"]["alpha_denominator_ns"]
            malformed.append(missing_parameter)
            wrong_evaluator_status = copy.deepcopy(receipt)
            wrong_evaluator_status["evaluator_result"]["status"] = "PASS_GEOMETRY_CONTROLS_ONLY"
            malformed.append(wrong_evaluator_status)
            unbounded_evaluator_detail = copy.deepcopy(receipt)
            unbounded_evaluator_detail["evaluator_result"]["arms"]["RAW"]["geometry"]["undocumented_metric"] = 1
            malformed.append(unbounded_evaluator_detail)

            for index, mutant in enumerate(malformed):
                with self.subTest(mutant=index), self.assertRaises(SchemaSubsetError):
                    validate_schema_subset(mutant, schema)

    def test_raw_top_edge_is_observed_in_fov_without_roundtrip_reclassification(self) -> None:
        calibration = (
            199.092366542, 198.82882047, 132.192071378, 110.712660011,
            -0.368436311798, 0.150947243557, -0.000296130534385,
            -0.000759431726241, 0.0,
        )
        ray = generator._raw_ray(215, 0, calibration)
        self.assertIsNotNone(ray)
        roundtrip = generator._project(ray, calibration, 240, 180)
        self.assertLess(roundtrip[2], 0.0)
        self.assertEqual(roundtrip[0], "outside_reference_image")
        self.assertEqual(
            generator._raw_observation(215, 0, ray),
            ("in_fov", 215.0, 0.0, 215, 0),
        )

    def test_official_event_13857156_projection_uses_oracle_multiplication_order(self) -> None:
        calibration = (
            199.092366542, 198.82882047, 132.192071378, 110.712660011,
            -0.368436311798, 0.150947243557, -0.000296130534385,
            -0.000759431726241, 0.0,
        )
        ray = (-0.5428099828647766, -0.2186013185656448, 0.8109073843687098)
        projection = generator._project(ray, calibration, 240, 180)
        self.assertEqual(projection[0], "in_fov")
        self.assertEqual((generator._q12(projection[1]), generator._q12(projection[2])), (18_801_187_420_721, 65_109_152_625_717))
        self.assertEqual(projection[3:], (19, 65))

    def test_frozen_public_constants_and_committed_schema_ids(self) -> None:
        self.assertEqual(generator.PRODUCTION_STATUS, "PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED")
        self.assertEqual(generator.SYNTHETIC_STATUS, "PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE")
        self.assertEqual(generator.PROMOTION_STATUS, "HOLD_MC_WTB_REAL_DATA_BENEFIT")
        self.assertEqual(generator.IMPLEMENTATION_STATUS, "PASS_SIX_ARM_GENERATOR_IMPLEMENTATION_SCOPED")
        self.assertEqual(
            generator._PRODUCTION_FIVE_ORACLE_HASHES["AVAILABLE_FIVE_COMBINED"],
            "55566cdc189c3519f56ac8d648a74c7b33bb003067e0b1c53c62b404a89cfe2a",
        )
        package = Path(generator.__file__).resolve().parent
        receipt_schema = json.loads((package / "generator_receipt.schema.json").read_text(encoding="ascii"))
        completion_schema = json.loads((package / "generator_completion.schema.json").read_text(encoding="ascii"))
        self.assertEqual(receipt_schema["properties"]["schema"]["const"], generator.RECEIPT_SCHEMA)
        self.assertEqual(completion_schema["properties"]["schema"]["const"], generator.COMPLETION_SCHEMA)

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
                self.assertEqual(record["arms"]["RAW"]["geometry_status"], "in_fov")
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
            with fixture.inspector_patches(official=True), mock.patch.object(generator, "_PRODUCTION_SHA256", fixture.production_pin_view()), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())

    def test_retire_identity_timebase_and_timestamp_mutants_fail_closed(self) -> None:
        mutations = ("duplicate", "reordered", "pre_occurrence", "out_of_pose_coverage", "timebase")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                fixture = SyntheticFixture(Path(directory))
                rows = generator._jsonl(fixture.retire.read_bytes(), "fixture retire")
                header, records = rows[0], rows[1:]
                if mutation == "duplicate":
                    records[1]["dataset_event_index"] = records[0]["dataset_event_index"]
                elif mutation == "reordered":
                    records[1], records[2] = records[2], records[1]
                elif mutation == "pre_occurrence":
                    records[0]["retire_timestamp_ns"] = records[0]["occurrence_timestamp_ns"] - 1
                elif mutation == "out_of_pose_coverage":
                    records[-1]["retire_timestamp_ns"] = 41_326_000_000
                else:
                    header["source_timebase"]["epoch"] = "wrong_epoch"
                if mutation == "timebase":
                    write(fixture.retire, canonical(header) + b"".join(canonical(row) for row in records))
                else:
                    fixture._write_retire(records)
                fixture._write_spec()
                result = fixture.root / "blocked"
                with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                    generator.generate(*fixture.args(result))
                self.assertFalse(result.exists())

    def test_coherent_repin_cannot_hide_source_divergence_and_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            rows = generator._jsonl((fixture.joined / "events_pose_join.jsonl").read_bytes(), "join events")
            rows[1]["x"] += 1
            write(fixture.joined / "events_pose_join.jsonl", b"".join(canonical(row) for row in rows))
            fixture._write_spec()
            result = fixture.root / "coherent-rehash-blocked"
            with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())

        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            linked = fixture.root / "linked-retire.jsonl"
            linked.symlink_to(fixture.retire)
            result = fixture.root / "symlink-blocked"
            with fixture.inspector_patches(), self.assertRaises(generator.GeneratorFailure):
                generator.generate(fixture.joined, fixture.join_spec, fixture.adapter, linked, fixture.generator_spec, result)
            self.assertFalse(result.exists())

    def test_wrong_transpose_and_delayed_lookup_are_not_correct_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            result = fixture.root / "result"
            with fixture.inspector_patches():
                generator.generate(*fixture.args(result))
            records = load_records_jsonl(result / generator.OUTPUT_NAME)
            separations = []
            for record in records:
                correct = record["arms"]["MC_CORRECT"]["reference_ray"]
                wrong = record["arms"]["MC_WRONG"]["reference_ray"]
                separations.append(math.sqrt(sum((a - b) ** 2 for a, b in zip(correct, wrong))))
                self.assertEqual(
                    record["arms"]["MC_DELAYED"]["pose_lookup_timestamp_ns"],
                    record["timestamp_ns"] - 4_998_186,
                )
            self.assertGreater(max(separations), 1e-4)

    def test_short_write_fails_without_result_or_staging_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            result = fixture.root / "short-write-blocked"
            with fixture.inspector_patches(), mock.patch.object(generator.os, "write", side_effect=OSError("injected short write")), self.assertRaises(generator.GeneratorFailure):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())
            self.assertEqual(list(fixture.root.glob(".short-write-blocked.sixarm-*")), [])

    def test_missing_linux_renameat2_fails_closed_without_publication(self) -> None:
        class LibcWithoutRenameAt2:
            pass

        with tempfile.TemporaryDirectory() as directory:
            fixture = SyntheticFixture(Path(directory))
            result = fixture.root / "renameat2-unavailable"
            with fixture.inspector_patches(), mock.patch.object(generator.ctypes, "CDLL", return_value=LibcWithoutRenameAt2()), self.assertRaisesRegex(generator.GeneratorFailure, "renameat2 is unavailable"):
                generator.generate(*fixture.args(result))
            self.assertFalse(result.exists())
            self.assertEqual(list(fixture.root.glob(".renameat2-unavailable.sixarm-*")), [])

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
