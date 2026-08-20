"""Independent black-box acceptance for the source-bound six-arm generator."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import shutil
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any, Iterable

from benchmarks.redred_uzh_mc_wtb_adapter import adapt as adapt_geometry
from benchmarks.redred_uzh_mc_wtb_six_arm_generator import (
    GeneratorFailure,
    generate,
    inspect,
)
from benchmarks.redred_uzh_shapes_pose_join import import_join


ROOT = Path(__file__).resolve().parents[2]
JOIN_SPEC = ROOT / "benchmarks" / "redred_uzh_shapes_pose_join" / "join_spec.json"

START_NS = 41_321_000_000
END_NS = 41_322_000_000
DELAY_NS = 4_998_186
CONTROLS_NAME = "controls_six_arm.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETE_NAME = "COMPLETE.json"
INVENTORY = {CONTROLS_NAME, RECEIPT_NAME, COMPLETE_NAME}
ARMS = {
    "RAW", "SENSOR_FIXED", "MC_CORRECT", "MC_WRONG", "MC_DELAYED",
    "RETIRE_WARP",
}
PRODUCTION_STATUS = "PASS_SOURCE_BOUND_SIX_ARM_GENERATOR_SCOPED"
FIXTURE_STATUS = "PASS_SYNTHETIC_SIX_ARM_GENERATOR_FIXTURE"
PROMOTION = "HOLD_MC_WTB_REAL_DATA_BENEFIT"
RETIRE_STREAM_SCHEMA = "redred.uzh_mc_wtb_controls.retire_stream/v1"
RETIRE_RECORD_SCHEMA = "redred.uzh_mc_wtb_controls.retire_record/v1"
GENERATOR_SPEC_SCHEMA = "redred.uzh_mc_wtb_controls.generator_spec/v1"
RECEIPT_SCHEMA = "redred.uzh_mc_wtb_controls.generator_receipt/v1"
COMPLETION_SCHEMA = "redred.uzh_mc_wtb_controls.generator_completion/v1"
A23_EXPORT = ROOT / "tests" / "a23_full_single_edge_replay" / "public_projected_export.tar.gz"

LICENSE = (
    b"Creative Commons Legal Code\n\n"
    b"Attribution-NonCommercial-ShareAlike 3.0 Unported\n"
    b"Independent synthetic fixture; not official source evidence.\n"
)
CALIBRATION = (
    b"199.092366542 198.82882047 132.192071378 110.712660011 "
    b"-0.368436311798 0.150947243557 -0.000296130534385 "
    b"-0.000759431726241 0.0\n"
)
# Pose halo covers t_event-DELAY_NS, occurrence, and synthetic retire time.
POSES = (
    b"41.315500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
    b"41.320500000 0.001 0.0 0.0 0.0 0.0 0.17364817766693033 0.984807753012208\n"
    b"41.325500000 0.002 0.0 0.0 0.0 0.0 0.34202014332566871 0.93969262078590843\n"
)
EVENTS = (
    b"41.321000000 217 16 0\n"
    b"41.321120000 160 179 1\n"
    b"41.321250000 120 90 0\n"
    b"41.321500000 30 40 1\n"
    b"41.321900000 200 150 1\n"
)
RETIRE_OFFSETS_NS = (75_000, 90_000, 125_000, 160_000, 210_000)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ) + "\n").encode("ascii")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected object in {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    if not all(isinstance(row, dict) for row in rows):
        raise AssertionError(f"expected object rows in {path}")
    return rows


def timestamp_ns(token: bytes) -> int:
    whole, fraction = token.split(b".", 1)
    if len(fraction) != 9:
        raise AssertionError("fixture timestamps require exactly nine fractional digits")
    return int(whole) * 1_000_000_000 + int(fraction)


def zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 2, 3, 4, 6))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def build_zip(path: Path, members: Iterable[tuple[str, bytes]]) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(zip_info(name), payload)
    return path.read_bytes()


def update_member(row: dict[str, Any], info: zipfile.ZipInfo, payload: bytes) -> None:
    row.update({
        "name": info.filename,
        "size_bytes": len(payload),
        "compressed_size_bytes": info.compress_size,
        "crc32": f"{info.CRC:08x}",
        "sha256": sha256(payload),
        "line_count": len(payload.splitlines()),
    })


class Fixture:
    """Create independently pinned pose-join, adapter, and retire packages."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.spec_value = copy.deepcopy(json.loads(JOIN_SPEC.read_text(encoding="ascii")))
        self.archive = root / self.spec_value["source_archive"]["basename"]
        self.license = root / self.spec_value["license"]["basename"]
        self.spec = root / "fixture_join_spec.json"
        self.joined = root / "joined"
        self.adapter = root / "adapter"
        self.retire = root / "retire_receipt.jsonl"
        self.generator_spec = root / "generator_spec.json"
        self.license.write_bytes(LICENSE)
        self._build_join()
        adapt_geometry(self.joined, self.spec, self.adapter)
        self._build_retire()
        self._build_generator_spec()

    def _build_join(self) -> None:
        members = [
            ("events.txt", EVENTS), ("groundtruth.txt", POSES),
            ("calib.txt", CALIBRATION),
        ]
        archive_bytes = build_zip(self.archive, members)
        self.spec_value["source_archive"].update({
            "size_bytes": len(archive_bytes), "sha256": sha256(archive_bytes),
            "expected_entry_count": 3,
        })
        self.spec_value["license"].update({
            "size_bytes": len(LICENSE), "sha256": sha256(LICENSE),
        })
        with zipfile.ZipFile(self.archive) as archive:
            update_member(self.spec_value["required_members"]["events"], archive.getinfo("events.txt"), EVENTS)
            update_member(self.spec_value["required_members"]["poses"], archive.getinfo("groundtruth.txt"), POSES)
            update_member(self.spec_value["required_members"]["calibration"], archive.getinfo("calib.txt"), CALIBRATION)
        lines = EVENTS.splitlines(keepends=True)
        raw = b"".join(lines)
        self.spec_value["selection"].update({
            "start_timestamp_ns_inclusive": START_NS,
            "end_timestamp_ns_exclusive": END_NS,
            "expected_event_count": len(lines),
            "expected_first_dataset_event_index": 0,
            "expected_last_dataset_event_index": len(lines) - 1,
            "expected_first_timestamp_ns": timestamp_ns(lines[0].split(b" ", 1)[0]),
            "expected_last_timestamp_ns": timestamp_ns(lines[-1].split(b" ", 1)[0]),
            "selected_raw_lines_sha256": sha256(raw),
        })
        self.spec.write_bytes(canonical(self.spec_value))
        import_join(self.archive, self.license, self.spec, self.joined)

    def _build_retire(self) -> None:
        source = joined_events(self.joined)
        records = []
        for row, offset in zip(source, RETIRE_OFFSETS_NS):
            records.append({
                "schema": RETIRE_RECORD_SCHEMA,
                "record_type": "retire",
                "dataset_event_index": row["dataset_event_index"],
                "join_sequence_index": row["join_sequence_index"],
                "occurrence_timestamp_ns": row["timestamp_ns"],
                "retire_timestamp_ns": row["timestamp_ns"] + offset,
                "accepted_count": 1,
                "retired_count": 1,
            })
        id_bytes = b"".join(f"{row['dataset_event_index']}\n".encode("ascii") for row in records)
        header = {
            "schema": RETIRE_STREAM_SCHEMA,
            "record_type": "header",
            "provenance_class": "SYNTHETIC_TEST_FIXTURE",
            "producer": {
                "implementation_id": "independent-test-fixture",
                "implementation_commit": "not-a-production-commit",
                "config_sha256": "1" * 64,
                "run_id": "synthetic-five-event-run",
                "raw_run_artifact_sha256": "2" * 64,
            },
            "source_timebase": {
                "unit": "ns",
                "epoch": "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction",
            },
            "retire_clock": {
                "clock_domain": "synthetic_fixture_clock",
                "unit": "ns",
                "epoch": "synthetic_fixture_epoch",
            },
            "mapping_to_source_timebase": {
                "method": "synthetic_identity_mapping_for_test_only",
                "evidence_sha256": "3" * 64,
                "validated": False,
            },
            "record_count": len(records),
            "ordered_dataset_event_index_sha256": sha256(id_bytes),
        }
        self.retire.write_bytes(b"".join(canonical(row) for row in [header, *records]))

    def _build_generator_spec(self) -> None:
        source = joined_events(self.joined)
        indices = [row["dataset_event_index"] for row in source]
        id_lines = b"".join(f"{index}\n".encode("ascii") for index in indices)
        compact_ids = canonical(indices)
        preregistration = ROOT / "benchmarks" / "redred_uzh_mc_wtb_controls" / "preregistered.json"
        join_receipt = read_json(self.joined / RECEIPT_NAME)
        adapter_receipt = read_json(self.adapter / RECEIPT_NAME)

        def pin(path: Path) -> dict[str, Any]:
            payload = path.read_bytes()
            return {"size_bytes": len(payload), "sha256": sha256(payload)}

        polarity_0 = sum(row["polarity_01"] == 0 for row in source)
        polarity_1 = sum(row["polarity_01"] == 1 for row in source)
        timestamp_tie_extras = sum(
            left["timestamp_ns"] == right["timestamp_ns"]
            for left, right in zip(source, source[1:])
        )
        spec = {
            "schema": GENERATOR_SPEC_SCHEMA,
            "mode": "SYNTHETIC_FIXTURE",
            "parameter_set_id": "SYNTHETIC-SIXARM-DELAY4998186-V1",
            "input_pins": {
                "pose_join": {
                    "status": join_receipt["status"],
                    "promotion_status": join_receipt["promotion_status"],
                    "receipt": pin(self.joined / RECEIPT_NAME),
                    "completion": pin(self.joined / COMPLETE_NAME),
                    "events": pin(self.joined / "events_pose_join.jsonl"),
                    "poses": pin(self.joined / "poses.jsonl"),
                    "calibration": pin(self.joined / "calibration.json"),
                },
                "join_spec": pin(self.spec),
                "adapter": {
                    "status": adapter_receipt["status"],
                    "promotion_status": adapter_receipt["promotion_status"],
                    "receipt": pin(self.adapter / RECEIPT_NAME),
                    "completion": pin(self.adapter / COMPLETE_NAME),
                    "events": pin(self.adapter / "events_mc_wtb_adapter.jsonl"),
                },
                "retire_receipt": pin(self.retire),
            },
            "cohort": {
                "record_count": len(source),
                "first_dataset_event_index": indices[0],
                "last_dataset_event_index": indices[-1],
                "decimal_id_lf_sha256": sha256(id_lines),
                "compact_id_array_lf_sha256": sha256(compact_ids),
                "polarity_0": polarity_0,
                "polarity_1": polarity_1,
                "timestamp_tie_extras": timestamp_tie_extras,
            },
            "geometry_contract": {
                "record_schema": "redred.uzh_mc_wtb_controls.adapter_record/v2",
                "reference_timestamp_ns": START_NS,
                "source_pose": "camera_to_world_T_WC",
                "quaternion_order": "xyzw",
                "translation_policy": "preserved_not_applied",
                "pixel_rounding": "floor(value_plus_0.5)",
                "bounds": "continuous_before_rounding",
            },
            "delay_contract": {
                "mc_delayed_delta_ns": DELAY_NS,
                "lookup": "occurrence_minus_delta_no_clamp",
            },
            "retire_contract": {
                "provenance_class": "SYNTHETIC_TEST_FIXTURE",
                "source_timebase": {
                    "unit": "ns",
                    "epoch": "uzh_shapes_rotation_sequence_zero_after_source_minimum_timestamp_subtraction",
                },
                "missing_policy": "fail_no_partial_output",
                "receipt_sha256": sha256(self.retire.read_bytes()),
            },
            "controls_preregistration": {
                "schema": "redred.uzh_mc_wtb_controls.preregistration/v2",
                "parameter_set_id": "UZH-S2-CONTROLS-8X8-1MS-V2",
                "raw_sha256": sha256(preregistration.read_bytes()),
            },
            "serialization": {
                "encoding": "ASCII",
                "json": "compact_sorted_keys",
                "line_ending": "LF",
                "header_in_output": False,
            },
            "claim_scope": {
                "official_uzh_source_input": False,
                "generated_artifact_official_uzh": False,
                "official_redred_traffic": False,
                "canonical_redred_traffic": False,
                "source_bound_pose_join": False,
                "source_bound_correct_adapter": False,
                "actual_retire_receipt_bound": False,
                "retire_timebase_mapping_validated": False,
                "six_controls_generated": True,
                "orientation_only": True,
                "translation_preserved_not_applied": True,
                "depth_or_plane_model_applied": False,
                "offline_future_bracket_slerp": True,
                "future_pose_lookahead_required": True,
                "causal_hardware_claimed": False,
                "clock_alignment_validated": False,
                "codec_evaluated": False,
                "bandwidth_measured": False,
                "compression_measured": False,
                "benefit_claimed": False,
                "rtl_or_ppa_evaluated": False,
            },
            "resource_limits": {
                "max_pose_bytes": 4 * 1024 * 1024,
                "max_event_bytes": 4 * 1024 * 1024,
                "max_adapter_bytes": 4 * 1024 * 1024,
                "max_retire_bytes": 4 * 1024 * 1024,
                "max_records": 100,
            },
        }
        self.generator_spec.write_bytes(canonical(spec))

    def generate(self, result: Path) -> dict[str, Any]:
        return generate(
            self.joined, self.spec, self.adapter, self.retire,
            self.generator_spec, result,
        )

    def inspect(self, result: Path) -> dict[str, Any]:
        return inspect(
            result, self.joined, self.spec, self.adapter, self.retire,
            self.generator_spec,
        )


def joined_events(path: Path) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(path / "events_pose_join.jsonl") if row.get("record_type") == "event"]


def normalize(vector):
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude <= 0.0 or not math.isfinite(magnitude):
        raise AssertionError("oracle vector is invalid")
    return tuple(value / magnitude for value in vector)


def slerp(before, after, alpha: float):
    left, right = normalize(before), normalize(after)
    dot = sum(left[i] * right[i] for i in range(4))
    if dot < 0.0:
        right = tuple(-value for value in right)
        dot = -dot
    dot = max(-1.0, min(1.0, dot))
    if dot > 0.9995:
        return normalize(tuple((1.0 - alpha) * left[i] + alpha * right[i] for i in range(4)))
    theta = math.acos(dot)
    sine = math.sin(theta)
    return normalize(tuple(
        math.sin((1.0 - alpha) * theta) / sine * left[i]
        + math.sin(alpha * theta) / sine * right[i]
        for i in range(4)
    ))


def quaternion_matrix(q):
    x, y, z, w = normalize(q)
    return (
        (1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)),
        (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)),
        (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)),
    )


def transpose(matrix):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def matmul(left, right):
    return tuple(tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3)) for row in range(3))


def matvec(matrix, vector):
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))


def distort(x: float, y: float, calibration):
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    r2 = x*x + y*y
    radial = 1.0 + k1*r2 + k2*r2*r2 + k3*r2*r2*r2
    return (
        x*radial + 2*p1*x*y + p2*(r2 + 2*x*x),
        y*radial + p1*(r2 + 2*y*y) + 2*p2*x*y,
    )


def undistort_newton(xd: float, yd: float, calibration):
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    x, y = xd, yd
    for _ in range(50):
        out_x, out_y = distort(x, y, calibration)
        rx, ry = out_x - xd, out_y - yd
        r2 = x*x + y*y
        radial = 1 + k1*r2 + k2*r2*r2 + k3*r2*r2*r2
        g = k1 + 2*k2*r2 + 3*k3*r2*r2
        drx, dry = 2*x*g, 2*y*g
        j00 = radial + x*drx + 2*p1*y + 6*p2*x
        j01 = x*dry + 2*p1*x + 2*p2*y
        j10 = y*drx + 2*p1*x + 2*p2*y
        j11 = radial + y*dry + 6*p1*y + 2*p2*x
        determinant = j00*j11 - j01*j10
        if abs(determinant) < 1e-18:
            raise AssertionError("oracle radtan Jacobian is singular")
        step_x = (j11*rx - j01*ry) / determinant
        step_y = (-j10*rx + j00*ry) / determinant
        x, y = x - step_x, y - step_y
        if max(abs(step_x), abs(step_y), abs(rx), abs(ry)) < 2e-15:
            return x, y
    raise AssertionError("oracle radtan inversion failed")


def pose_rows():
    rows = []
    for line in POSES.decode("ascii").splitlines():
        fields = line.split()
        rows.append((timestamp_ns(fields[0].encode("ascii")), tuple(map(float, fields[4:8]))))
    return rows


def quaternion_at(time_ns: int):
    poses = pose_rows()
    for (left_t, left_q), (right_t, right_q) in zip(poses, poses[1:]):
        if left_t <= time_ns < right_t:
            return slerp(left_q, right_q, (time_ns - left_t) / (right_t - left_t))
    raise AssertionError(f"oracle time lacks pose bracket: {time_ns}")


def raw_ray(x_raw: int, y_raw: int):
    calibration = tuple(float(value) for value in CALIBRATION.split())
    fx, fy, cx, cy = calibration[:4]
    xu, yu = undistort_newton((x_raw - cx) / fx, (y_raw - cy) / fy, calibration)
    return normalize((xu, yu, 1.0))


def rotated_ray(x_raw: int, y_raw: int, time_ns: int, *, wrong: bool = False):
    reference = quaternion_matrix(quaternion_at(START_NS))
    current = quaternion_matrix(quaternion_at(time_ns))
    current_to_reference = matmul(transpose(reference), current)
    rotation = transpose(current_to_reference) if wrong else current_to_reference
    return normalize(matvec(rotation, raw_ray(x_raw, y_raw)))


def angular_error(left, right) -> float:
    a, b = normalize(left), normalize(right)
    dot = max(-1.0, min(1.0, sum(a[i] * b[i] for i in range(3))))
    return math.acos(dot)


def output_rows(result: Path):
    return read_jsonl(result / CONTROLS_NAME)


class SixArmIndependentAcceptance(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root / "fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identity_conservation_six_arm_semantics_and_oracles(self) -> None:
        result = self.root / "result"
        receipt = self.fixture.generate(result)
        inspected = self.fixture.inspect(result)
        self.assertEqual({path.name for path in result.iterdir()}, INVENTORY)
        source = joined_events(self.fixture.joined)
        adapter_rows = read_jsonl(self.fixture.adapter / "events_mc_wtb_adapter.jsonl")[1:]
        retire_rows = read_jsonl(self.fixture.retire)[1:]
        records = output_rows(result)
        self.assertEqual(len(records), len(source))
        self.assertEqual(len(records), 5)
        for ordinal, (record, event, adapter, retired) in enumerate(zip(records, source, adapter_rows, retire_rows)):
            self.assertEqual(set(record["arms"]), ARMS)
            self.assertEqual(record["join_sequence_index"], ordinal)
            self.assertEqual(
                (record["dataset_event_index"], record["timestamp_ns"], record["x_raw"], record["y_raw"], record["polarity_01"]),
                (event["dataset_event_index"], event["timestamp_ns"], event["x"], event["y"], event["polarity_01"]),
            )
            raw, fixed = record["arms"]["RAW"], record["arms"]["SENSOR_FIXED"]
            correct, wrong = record["arms"]["MC_CORRECT"], record["arms"]["MC_WRONG"]
            delayed, retire = record["arms"]["MC_DELAYED"], record["arms"]["RETIRE_WARP"]
            self.assertIsNone(raw["pose_lookup_timestamp_ns"])
            self.assertEqual((raw["locality_x"], raw["locality_y"]), (float(event["x"]), float(event["y"])))
            self.assertEqual((fixed["locality_x"], fixed["locality_y"]), (float(event["x"]), float(event["y"])))
            for arm in (fixed, correct, wrong):
                self.assertEqual(arm["pose_lookup_timestamp_ns"], event["timestamp_ns"])
            self.assertEqual(delayed["pose_lookup_timestamp_ns"], event["timestamp_ns"] - DELAY_NS)
            self.assertEqual(retire["pose_lookup_timestamp_ns"], retired["retire_timestamp_ns"])
            expected = {
                "RAW": raw_ray(event["x"], event["y"]),
                "SENSOR_FIXED": rotated_ray(event["x"], event["y"], event["timestamp_ns"]),
                "MC_CORRECT": rotated_ray(event["x"], event["y"], event["timestamp_ns"]),
                "MC_WRONG": rotated_ray(event["x"], event["y"], event["timestamp_ns"], wrong=True),
                "MC_DELAYED": rotated_ray(event["x"], event["y"], event["timestamp_ns"] - DELAY_NS),
                "RETIRE_WARP": rotated_ray(event["x"], event["y"], retired["retire_timestamp_ns"]),
            }
            for name, ray in expected.items():
                self.assertLessEqual(angular_error(record["arms"][name]["reference_ray"], ray), 2e-12, name)
            self.assertEqual(correct["geometry_status"], adapter["geometry"]["status"])

        self.assertEqual(set(receipt), {
            "schema", "status", "evidence_class", "promotion_status",
            "input_binding", "parameters", "cohort", "arm_ledgers",
            "evaluator_result", "claim_scope", "artifact",
        })
        self.assertEqual(receipt["schema"], RECEIPT_SCHEMA)
        self.assertEqual(receipt["status"], FIXTURE_STATUS)
        self.assertEqual(receipt["cohort"]["record_count"], 5)
        self.assertEqual(inspected["record_count"], 5)

    def test_wrong_direction_and_exact_delayed_delta_are_distinct(self) -> None:
        result = self.root / "result"
        self.fixture.generate(result)
        records = output_rows(result)
        separations = []
        delayed_separations = []
        for record in records:
            arms = record["arms"]
            self.assertEqual(
                record["timestamp_ns"] - arms["MC_DELAYED"]["pose_lookup_timestamp_ns"],
                DELAY_NS,
            )
            separations.append(angular_error(arms["MC_CORRECT"]["reference_ray"], arms["MC_WRONG"]["reference_ray"]))
            delayed_separations.append(angular_error(arms["MC_CORRECT"]["reference_ray"], arms["MC_DELAYED"]["reference_ray"]))
        self.assertGreater(max(separations), math.radians(0.1))
        self.assertGreater(max(delayed_separations), 0.0)
        receipt = read_json(result / RECEIPT_NAME)
        self.assertEqual(receipt["parameters"]["mc_delayed_delta_ns"], DELAY_NS)

    def test_retire_receipt_provenance_and_input_tamper_are_fail_closed(self) -> None:
        result = self.root / "result"
        receipt = self.fixture.generate(result)
        binding = receipt["input_binding"]["retire_receipt"]
        self.assertEqual(binding["raw_sha256"], sha256(self.fixture.retire.read_bytes()))
        self.assertEqual(binding["basename"], self.fixture.retire.name)
        self.assertEqual(binding["provenance_class"], "SYNTHETIC_TEST_FIXTURE")

        tampered = self.root / "tampered-retire.jsonl"
        shutil.copy2(self.fixture.retire, tampered)
        tampered.write_bytes(tampered.read_bytes() + b" \n")
        rejected = self.root / "rejected"
        with self.assertRaises(GeneratorFailure):
            generate(
                self.fixture.joined, self.fixture.spec, self.fixture.adapter,
                tampered, self.fixture.generator_spec, rejected,
            )
        self.assertFalse((rejected / COMPLETE_NAME).exists())

    def test_output_tamper_and_coherent_rehash_are_rejected(self) -> None:
        simple = self.root / "simple"
        self.fixture.generate(simple)
        artifact = simple / CONTROLS_NAME
        artifact.chmod(0o644)
        artifact.write_bytes(artifact.read_bytes() + b" \n")
        with self.assertRaises(GeneratorFailure):
            self.fixture.inspect(simple)

        coherent = self.root / "coherent"
        self.fixture.generate(coherent)
        rows = output_rows(coherent)
        original_payload = (coherent / CONTROLS_NAME).read_bytes()
        mutated = next(
            row for row in rows
            if row["arms"]["MC_WRONG"]["reference_ray"]
            != row["arms"]["MC_CORRECT"]["reference_ray"]
        )
        mutated["arms"]["MC_WRONG"]["reference_ray"] = copy.deepcopy(
            mutated["arms"]["MC_CORRECT"]["reference_ray"]
        )
        artifact_payload = b"".join(canonical(row) for row in rows)
        self.assertNotEqual(artifact_payload, original_payload)
        (coherent / CONTROLS_NAME).chmod(0o644)
        (coherent / CONTROLS_NAME).write_bytes(artifact_payload)
        receipt = read_json(coherent / RECEIPT_NAME)
        receipt["artifact"].update({"size_bytes": len(artifact_payload), "sha256": sha256(artifact_payload)})
        receipt_payload = canonical(receipt)
        (coherent / RECEIPT_NAME).chmod(0o644)
        (coherent / RECEIPT_NAME).write_bytes(receipt_payload)
        completion = read_json(coherent / COMPLETE_NAME)
        completion["artifacts"][CONTROLS_NAME].update({"size_bytes": len(artifact_payload), "sha256": sha256(artifact_payload)})
        completion["artifacts"][RECEIPT_NAME].update({"size_bytes": len(receipt_payload), "sha256": sha256(receipt_payload)})
        (coherent / COMPLETE_NAME).chmod(0o644)
        (coherent / COMPLETE_NAME).write_bytes(canonical(completion))
        with self.assertRaises(GeneratorFailure):
            self.fixture.inspect(coherent)

    def test_source_free_inspection_cannot_return_pass(self) -> None:
        result = self.root / "result"
        self.fixture.generate(result)
        with self.assertRaises((GeneratorFailure, TypeError)):
            inspect(result)  # type: ignore[call-arg]

    def test_determinism_and_synthetic_fixture_cannot_claim_official_success(self) -> None:
        first, second = self.root / "first", self.root / "second"
        first_receipt = self.fixture.generate(first)
        second_receipt = self.fixture.generate(second)
        self.assertEqual(first_receipt, second_receipt)
        for name in INVENTORY:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        self.assertEqual(first_receipt["status"], FIXTURE_STATUS)
        self.assertNotEqual(first_receipt["status"], PRODUCTION_STATUS)
        self.assertEqual(first_receipt["promotion_status"], PROMOTION)
        claims = first_receipt["claim_scope"]
        self.assertFalse(claims["official_uzh_source_input"])
        self.assertFalse(claims["generated_artifact_official_uzh"])
        self.assertFalse(claims["source_bound_pose_join"])
        self.assertFalse(claims["source_bound_correct_adapter"])
        self.assertFalse(claims["actual_retire_receipt_bound"])
        self.assertFalse(claims["retire_timebase_mapping_validated"])
        for name in ("official_redred_traffic", "canonical_redred_traffic", "depth_or_plane_model_applied", "causal_hardware_claimed", "codec_evaluated", "bandwidth_measured", "compression_measured", "benefit_claimed", "rtl_or_ppa_evaluated"):
            self.assertFalse(claims[name], name)
        for name in ("six_controls_generated", "orientation_only", "translation_preserved_not_applied", "offline_future_bracket_slerp", "future_pose_lookahead_required"):
            self.assertTrue(claims[name], name)

    def test_a23_1019_of_1100_replay_is_negative_not_a_retire_authority(self) -> None:
        self.assertTrue(A23_EXPORT.is_file(), "canonical A23 negative evidence is absent")
        with tarfile.open(A23_EXPORT, "r:gz") as archive:
            for owner in ("a2", "a3"):
                member = f"run/artifacts/{owner}/none/public_projected_1x/summary.csv"
                extracted = archive.extractfile(member)
                self.assertIsNotNone(extracted)
                row = next(csv.DictReader(io.StringIO(extracted.read().decode("ascii"))))
                self.assertEqual(int(row["generated"]), 1_100)
                self.assertEqual(int(row["source_overrun"]), 81)
                self.assertEqual(int(row["accepted"]), 1_019)
                self.assertEqual(int(row["retired"]), 1_019)

        # Never fill the 81 absent retire times. A strict stream count mismatch
        # remains invalid even if every retained record is otherwise canonical.
        rows = read_jsonl(self.fixture.retire)
        incomplete = self.root / "incomplete-retire.jsonl"
        incomplete.write_bytes(b"".join(canonical(row) for row in rows[:-1]))
        with self.assertRaises(GeneratorFailure):
            generate(
                self.fixture.joined, self.fixture.spec, self.fixture.adapter,
                incomplete, self.fixture.generator_spec, self.root / "a23-rejected",
            )

    @unittest.skipUnless(
        os.environ.get("REDRED_RUN_SIXARM_OFFICIAL") == "1",
        "HOLD: set REDRED_RUN_SIXARM_OFFICIAL=1 only with an actual retire receipt",
    )
    def test_optional_official_full_cohort_requires_actual_retire_receipt(self) -> None:
        names = {
            "joined": "REDRED_UZH_JOINED_ROOT",
            "spec": "REDRED_UZH_JOIN_SPEC",
            "adapter": "REDRED_UZH_ADAPTER_ROOT",
            "retire": "REDRED_UZH_RETIRE_RECEIPT",
            "generator_spec": "REDRED_SIXARM_GENERATOR_SPEC",
        }
        values = {name: os.environ.get(variable) for name, variable in names.items()}
        missing = [names[name] for name, value in values.items() if not value or not Path(value).exists()]
        approved_generator_spec_sha256 = os.environ.get("REDRED_SIXARM_APPROVED_GENERATOR_SPEC_SHA256")
        if not approved_generator_spec_sha256:
            missing.append("REDRED_SIXARM_APPROVED_GENERATOR_SPEC_SHA256")
        if missing:
            self.skipTest("HOLD_ACTUAL_RETIRE_RECEIPT_MISSING: " + ", ".join(missing))
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "sixarm"
            receipt = generate(
                Path(values["joined"]), Path(values["spec"]),
                Path(values["adapter"]), Path(values["retire"]),
                Path(values["generator_spec"]), result,
            )
            self.assertEqual(receipt["status"], PRODUCTION_STATUS)
            self.assertEqual(receipt["promotion_status"], PROMOTION)
            self.assertEqual(receipt["cohort"]["record_count"], 1_100)
            self.assertTrue(receipt["claim_scope"]["actual_retire_receipt_bound"])
            self.assertTrue(receipt["claim_scope"]["retire_timebase_mapping_validated"])
            rows = output_rows(result)
            self.assertEqual(len(rows), 1_100)
            for row in rows:
                self.assertEqual(set(row["arms"]), ARMS)
                self.assertEqual(row["timestamp_ns"] - row["arms"]["MC_DELAYED"]["pose_lookup_timestamp_ns"], DELAY_NS)
            inspected = inspect(
                result, Path(values["joined"]), Path(values["spec"]),
                Path(values["adapter"]), Path(values["retire"]),
                Path(values["generator_spec"]),
            )
            self.assertEqual(inspected["status"], PRODUCTION_STATUS)
            self.assertEqual(inspected["record_count"], 1_100)


if __name__ == "__main__":
    unittest.main()
