"""Independent analytical acceptance tests for the UZH geometry adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from typing import Any, Iterable

from benchmarks.redred_uzh_mc_wtb_adapter import AdapterFailure, adapt, inspect
from benchmarks.redred_uzh_shapes_pose_join import import_join


ROOT = Path(os.environ.get("REDRED_ADAPTER_TEST_ROOT", Path(__file__).resolve().parents[2]))
JOIN_SPEC = ROOT / "benchmarks" / "redred_uzh_shapes_pose_join" / "join_spec.json"

START_NS = 41_321_000_000
END_NS = 41_322_000_000
EVENTS_NAME = "events_mc_wtb_adapter.jsonl"
RECEIPT_NAME = "receipt.json"
COMPLETE_NAME = "COMPLETE.json"
EXPECTED_INVENTORY = {EVENTS_NAME, RECEIPT_NAME, COMPLETE_NAME}
EXPECTED_STATUS = "PASS_POSE_JOIN_TO_ROTATION_GEOMETRY_ADAPTER_SCOPED"
EXPECTED_PROMOTION = "HOLD_MC_WTB_REAL_DATA_BENEFIT"
OFFICIAL_JOIN_RECEIPT_SHA256 = (
    "85c182e1daa2f380dffa34a559ae2093835b1052c3d9d9a7f5a1f014a9974f87"
)

WORLD = "WORLD_REFERENCE_EVENT"
RAW_ESCAPE = "RAW_ESCAPE_GEOMETRIC_OOF"
RAW_BYPASS = "RAW_BYPASS_INVALID_GEOMETRY"
DISPOSITIONS = {WORLD, RAW_ESCAPE, RAW_BYPASS}
STATUS_FOR_DISPOSITION = {
    WORLD: {"in_fov"},
    RAW_ESCAPE: {"outside_reference_image", "behind_reference"},
    RAW_BYPASS: {"invalid_distortion"},
}

LICENSE = (
    b"Creative Commons Legal Code\n\n"
    b"Attribution-NonCommercial-ShareAlike 3.0 Unported\n"
    b"Deterministic test fixture excerpt; not an official license artifact.\n"
)
CALIBRATION = (
    b"199.092366542 198.82882047 132.192071378 110.712660011 "
    b"-0.368436311798 0.150947243557 -0.000296130534385 "
    b"-0.000759431726241 0.0\n"
)
POSES = (
    b"41.317287872 4.51993979847 1.6330859119 1.45572595687 "
    b"0.479481806428 -0.469260100441 0.563446446301 -0.48209984193\n"
    b"41.322286058 4.52071289347 1.63355211383 1.45157713355 "
    b"0.491469933232 -0.480481456472 0.549361934187 -0.475180323649\n"
)
EVENTS = (
    b"41.321000000 217 16 0\n"
    b"41.321000000 217 16 0\n"
    b"41.321224001 160 179 1\n"
    b"41.321336001 140 179 1\n"
    b"41.321474001 159 179 1\n"
    b"41.321742001 140 178 1\n"
    b"41.321804000 160 178 1\n"
    b"41.321815001 158 179 1\n"
    b"41.321999000 25 50 1\n"
)

# These coordinates come from an independent double-precision derivation and
# are deliberately not imported from production geometry helpers.
SIX_GOLDENS = {
    2: (159.90546143120395, 179.38763147091402),
    3: (139.85314912050305, 179.57989532623980),
    4: (158.79953733064860, 179.82018220773630),
    5: (139.67589651436072, 179.28111642373105),
    6: (159.66073298245410, 179.39180347041838),
    7: (157.65458003836017, 180.40998450073613),
}
OFFICIAL_SIX_GOLDENS = {
    13_856_524: SIX_GOLDENS[2],
    13_856_654: SIX_GOLDENS[3],
    13_856_794: SIX_GOLDENS[4],
    13_857_092: SIX_GOLDENS[5],
    13_857_160: SIX_GOLDENS[6],
    13_857_171: SIX_GOLDENS[7],
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ) + "\n").encode("ascii")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="ascii"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]
    if not all(isinstance(value, dict) for value in values):
        raise AssertionError(f"expected JSON-object lines: {path}")
    return values


def timestamp_ns(token: bytes) -> int:
    whole, fraction = token.split(b".", 1)
    if len(fraction) != 9:
        raise AssertionError(f"non-canonical fixture timestamp: {token!r}")
    return int(whole) * 1_000_000_000 + int(fraction)


def selected_event_lines(events: bytes) -> list[tuple[int, bytes]]:
    selected = []
    for index, line in enumerate(events.splitlines(keepends=True)):
        time_ns = timestamp_ns(line.split(b" ", 1)[0])
        if START_NS <= time_ns < END_NS:
            selected.append((index, line))
    return selected


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


class JoinedFixture:
    """Build a valid source-join package without invoking adapter code."""

    def __init__(
        self,
        root: Path,
        *,
        events: bytes = EVENTS,
        poses: bytes = POSES,
        calibration: bytes = CALIBRATION,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = events
        self.poses = poses
        self.calibration = calibration
        self.spec_value = copy.deepcopy(json.loads(JOIN_SPEC.read_text(encoding="ascii")))
        self.archive = root / self.spec_value["source_archive"]["basename"]
        self.license = root / self.spec_value["license"]["basename"]
        self.spec = root / "fixture_join_spec.json"
        self.joined = root / "joined"
        self.license.write_bytes(LICENSE)
        self._build()

    def _build(self) -> None:
        members = [
            ("events.txt", self.events),
            ("groundtruth.txt", self.poses),
            ("calib.txt", self.calibration),
        ]
        archive_bytes = build_zip(self.archive, members)
        self.spec_value["source_archive"].update({
            "size_bytes": len(archive_bytes),
            "sha256": sha256(archive_bytes),
            "expected_entry_count": len(members),
        })
        self.spec_value["license"].update({
            "size_bytes": len(LICENSE),
            "sha256": sha256(LICENSE),
        })
        with zipfile.ZipFile(self.archive) as archive:
            update_member(self.spec_value["required_members"]["events"], archive.getinfo("events.txt"), self.events)
            update_member(self.spec_value["required_members"]["poses"], archive.getinfo("groundtruth.txt"), self.poses)
            update_member(self.spec_value["required_members"]["calibration"], archive.getinfo("calib.txt"), self.calibration)
        selected = selected_event_lines(self.events)
        raw = b"".join(line for _, line in selected)
        first, last = selected[0], selected[-1]
        self.spec_value["selection"].update({
            "start_timestamp_ns_inclusive": START_NS,
            "end_timestamp_ns_exclusive": END_NS,
            "expected_event_count": len(selected),
            "expected_first_dataset_event_index": first[0],
            "expected_last_dataset_event_index": last[0],
            "expected_first_timestamp_ns": timestamp_ns(first[1].split(b" ", 1)[0]),
            "expected_last_timestamp_ns": timestamp_ns(last[1].split(b" ", 1)[0]),
            "selected_raw_lines_sha256": sha256(raw),
        })
        self.spec.write_bytes(canonical_json(self.spec_value))
        import_join(self.archive, self.license, self.spec, self.joined)

    def run_adapter(self, result: Path) -> dict[str, Any]:
        return adapt(self.joined, self.spec, result)


def artifact_name(result: Path) -> str:
    return str(read_json(result / RECEIPT_NAME)["artifact"]["name"])


def event_rows(result: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = read_jsonl(result / artifact_name(result))
    if not rows or rows[0].get("record_type") != "header":
        raise AssertionError("adapter artifact must begin with one header")
    return rows[0], rows[1:]


def joined_event_records(joined: Path) -> list[dict[str, Any]]:
    return [
        row for row in read_jsonl(joined / "events_pose_join.jsonl")
        if row.get("record_type") == "event"
    ]


def normalize(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    magnitude = math.sqrt(sum(value * value for value in q))
    if not math.isfinite(magnitude) or magnitude <= 0.0:
        raise AssertionError("invalid oracle quaternion")
    return tuple(value / magnitude for value in q)  # type: ignore[return-value]


def slerp(
    before: tuple[float, float, float, float],
    after: tuple[float, float, float, float],
    alpha: float,
) -> tuple[float, float, float, float]:
    left, right = normalize(before), normalize(after)
    cosine = sum(a * b for a, b in zip(left, right))
    if cosine < 0.0:
        right = tuple(-value for value in right)  # type: ignore[assignment]
        cosine = -cosine
    cosine = min(1.0, max(-1.0, cosine))
    if cosine > 0.9995:
        return normalize(tuple(
            (1.0 - alpha) * left[i] + alpha * right[i] for i in range(4)
        ))  # type: ignore[arg-type]
    theta = math.acos(cosine)
    sine = math.sin(theta)
    return normalize(tuple(
        math.sin((1.0 - alpha) * theta) / sine * left[i]
        + math.sin(alpha * theta) / sine * right[i]
        for i in range(4)
    ))  # type: ignore[arg-type]


def quaternion_matrix(q: tuple[float, float, float, float]):
    x, y, z, w = normalize(q)
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def transpose(matrix):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def matmul(left, right):
    return tuple(tuple(
        sum(left[row][k] * right[k][column] for k in range(3))
        for column in range(3)
    ) for row in range(3))


def matvec(matrix, vector):
    return tuple(sum(matrix[row][k] * vector[k] for k in range(3)) for row in range(3))


def assert_matrix(test: unittest.TestCase, actual, expected, delta: float = 2e-12) -> None:
    for actual_row, expected_row in zip(actual, expected):
        for actual_value, expected_value in zip(actual_row, expected_row):
            test.assertAlmostEqual(actual_value, expected_value, delta=delta)


def distort(x: float, y: float, calibration: tuple[float, ...]) -> tuple[float, float]:
    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    radius2 = x * x + y * y
    radial = 1.0 + k1 * radius2 + k2 * radius2**2 + k3 * radius2**3
    return (
        x * radial + 2.0 * p1 * x * y + p2 * (radius2 + 2.0 * x * x),
        y * radial + p1 * (radius2 + 2.0 * y * y) + 2.0 * p2 * x * y,
    )


def undistort_newton(xd: float, yd: float, calibration: tuple[float, ...]) -> tuple[float, float]:
    """Independent analytic-Jacobian inverse, unlike production fixed point."""

    _, _, _, _, k1, k2, p1, p2, k3 = calibration
    x, y = xd, yd
    for _ in range(50):
        fx_value, fy_value = distort(x, y, calibration)
        rx, ry = fx_value - xd, fy_value - yd
        radius2 = x * x + y * y
        radial = 1.0 + k1 * radius2 + k2 * radius2**2 + k3 * radius2**3
        radial_gradient = k1 + 2.0 * k2 * radius2 + 3.0 * k3 * radius2**2
        dr_dx, dr_dy = 2.0 * x * radial_gradient, 2.0 * y * radial_gradient
        j00 = radial + x * dr_dx + 2.0 * p1 * y + 6.0 * p2 * x
        j01 = x * dr_dy + 2.0 * p1 * x + 2.0 * p2 * y
        j10 = y * dr_dx + 2.0 * p1 * x + 2.0 * p2 * y
        j11 = radial + y * dr_dy + 6.0 * p1 * y + 2.0 * p2 * x
        determinant = j00 * j11 - j01 * j10
        if not math.isfinite(determinant) or abs(determinant) < 1e-18:
            raise AssertionError("independent radtan inverse is singular")
        step_x = (j11 * rx - j01 * ry) / determinant
        step_y = (-j10 * rx + j00 * ry) / determinant
        x -= step_x
        y -= step_y
        if max(abs(step_x), abs(step_y), abs(rx), abs(ry)) < 2e-15:
            return x, y
    raise AssertionError("independent radtan inverse did not converge")


def oracle_warp(x_raw: int, y_raw: int, calibration: tuple[float, ...], rotation):
    fx, fy, cx, cy = calibration[:4]
    xu, yu = undistort_newton((x_raw - cx) / fx, (y_raw - cy) / fy, calibration)
    ray = matvec(rotation, (xu, yu, 1.0))
    if ray[2] <= 0.0:
        return "behind_reference", None, None, ray[2], None, None
    xd, yd = distort(ray[0] / ray[2], ray[1] / ray[2], calibration)
    x_float, y_float = fx * xd + cx, fy * yd + cy
    if not (0.0 <= x_float <= 239.0 and 0.0 <= y_float <= 179.0):
        return "outside_reference_image", x_float, y_float, ray[2], None, None
    return (
        "in_fov", x_float, y_float, ray[2],
        math.floor(x_float + 0.5), math.floor(y_float + 0.5),
    )


def decimal_vector(value: list[str]) -> tuple[float, ...]:
    return tuple(float(component) for component in value)


def source_identity(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_event_index": source["dataset_event_index"],
        "join_sequence_index": source["join_sequence_index"],
        "timestamp_ns": source["timestamp_ns"],
        "timestamp_seconds_lexeme": source["timestamp_seconds_lexeme"],
        "x_sensor": source["x"],
        "y_sensor": source["y"],
        "polarity_01": source["polarity_01"],
    }


class AdapterAcceptanceTests(unittest.TestCase):
    maxDiff = None

    def assert_rejected(self, callable_value, result: Path | None = None) -> None:
        with self.assertRaises(AdapterFailure):
            callable_value()
        if result is not None:
            self.assertFalse((result / COMPLETE_NAME).exists())

    def assert_status_and_offline_scope(
        self,
        result: Path,
        receipt: dict[str, Any],
        header: dict[str, Any],
    ) -> None:
        """Enforce the accepted native ABI's merge-blocking semantic labels."""

        completion = read_json(result / COMPLETE_NAME)
        for value in (receipt, header, completion):
            self.assertEqual(value["status"], EXPECTED_STATUS)
            self.assertEqual(value["promotion_status"], EXPECTED_PROMOTION)
        for value in (receipt, header):
            claims = value["claim_scope"]
            self.assertIs(claims["offline_future_bracket_slerp"], True)
            self.assertIs(claims["future_pose_lookahead_required"], True)
            self.assertIs(claims["causal_hardware_claimed"], False)
            self.assertIs(claims["clock_alignment_validated"], False)

    def test_event_conservation_exclusivity_and_raw_escape_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root)
            result = root / "adapter"
            receipt = fixture.run_adapter(result)
            inspected = inspect(result, fixture.joined, fixture.spec)
            artifact = receipt["artifact"]
            self.assertEqual(artifact["name"], EVENTS_NAME)
            self.assertEqual({path.name for path in result.iterdir()}, EXPECTED_INVENTORY)

            source = joined_event_records(fixture.joined)
            header, records = event_rows(result)
            self.assert_status_and_offline_scope(result, receipt, header)
            self.assertEqual(inspected["status"], EXPECTED_STATUS)
            self.assertEqual(inspected["promotion_status"], EXPECTED_PROMOTION)
            self.assertEqual(len(records), len(source))
            self.assertEqual(len(records), 9)
            source_by_sequence = {row["join_sequence_index"]: row for row in source}
            seen_occurrences = set()
            for ordinal, record in enumerate(records):
                self.assertEqual(record["record_type"], "event_disposition")
                self.assertEqual(record["source_event"]["join_sequence_index"], ordinal)
                disposition = record["disposition"]
                self.assertIn(disposition, DISPOSITIONS)
                self.assertIn(record["geometry"]["status"], STATUS_FOR_DISPOSITION[disposition])
                original = source_by_sequence[ordinal]
                self.assertEqual(record["source_event"], source_identity(original))
                self.assertEqual(
                    record["source_identity_sha256"],
                    sha256(canonical_json(record["source_event"])),
                )
                occurrence = (
                    record["source_event"]["dataset_event_index"],
                    record["source_event"]["join_sequence_index"],
                )
                self.assertNotIn(occurrence, seen_occurrences)
                seen_occurrences.add(occurrence)
                self.assertFalse(record["geometry"]["translation_applied_to_pixel_warp"])

            self.assertEqual(
                {row["source_event"]["dataset_event_index"] for row in records},
                set(range(9)),
            )
            self.assertEqual(receipt["conservation"], {
                "input_joined_events": 9,
                "output_dispositions": 9,
                "world_reference_events": 3,
                "raw_escape_geometric_oof": 6,
                "raw_bypass_invalid_geometry": 0,
                "dropped_events": 0,
                "duplicate_events": 0,
                "reordered_events": 0,
                "equation": "input_joined_events == world_reference_events + raw_escape_geometric_oof + raw_bypass_invalid_geometry",
            })
            self.assertEqual(inspected["record_count"], 9)
            self.assertEqual(inspected["world_reference_events"], 3)
            self.assertEqual(inspected["raw_escape_geometric_oof"], 6)

    def test_source_and_spec_free_inspection_cannot_return_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root)
            result = root / "adapter"
            fixture.run_adapter(result)
            with self.assertRaises((AdapterFailure, TypeError)):
                inspect(result)

    def test_frame_transpose_radtan_and_exact_six_oof_coordinates(self) -> None:
        calibration = tuple(float(value) for value in CALIBRATION.split())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root)
            result = root / "adapter"
            fixture.run_adapter(result)
            header, records = event_rows(result)
            reference_q = decimal_vector(header["reference_pose"]["quaternion_xyzw_decimal"])
            reference_matrix = quaternion_matrix(reference_q)  # T_WC0 camera-to-world

            wrong_direction_separation = []
            for record in records:
                current_q = decimal_vector(record["event_pose"]["quaternion_xyzw_decimal"])
                current_matrix = quaternion_matrix(current_q)  # T_WCt camera-to-world
                reference_to_current = matmul(transpose(current_matrix), reference_matrix)
                sensor_to_reference = matmul(transpose(reference_matrix), current_matrix)
                assert_matrix(self, sensor_to_reference, transpose(reference_to_current), 1e-15)
                assert_matrix(self, matmul(sensor_to_reference, reference_to_current), (
                    (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
                ))

                source = record["source_event"]
                expected = oracle_warp(
                    source["x_sensor"], source["y_sensor"], calibration,
                    sensor_to_reference,
                )
                geometry = record["geometry"]
                self.assertEqual(geometry["status"], expected[0])
                self.assertAlmostEqual(float(geometry["ray_z_decimal"]), expected[3], delta=2e-12)
                if expected[1] is not None:
                    self.assertAlmostEqual(float(geometry["x_reference_float_decimal"]), expected[1], delta=2e-11)
                    self.assertAlmostEqual(float(geometry["y_reference_float_decimal"]), expected[2], delta=2e-11)
                self.assertEqual(geometry["x_reference"], expected[4])
                self.assertEqual(geometry["y_reference"], expected[5])

                wrong = oracle_warp(
                    source["x_sensor"], source["y_sensor"], calibration,
                    reference_to_current,
                )
                if expected[1] is not None and wrong[1] is not None:
                    wrong_direction_separation.append(math.hypot(expected[1] - wrong[1], expected[2] - wrong[2]))

            self.assertGreater(max(wrong_direction_separation), 1.0)
            by_index = {row["source_event"]["dataset_event_index"]: row for row in records}
            self.assertEqual(
                {index for index, row in by_index.items() if row["disposition"] == RAW_ESCAPE},
                set(SIX_GOLDENS),
            )
            for index, expected in SIX_GOLDENS.items():
                geometry = by_index[index]["geometry"]
                self.assertEqual(geometry["status"], "outside_reference_image")
                self.assertIsNone(geometry["x_reference"])
                self.assertIsNone(geometry["y_reference"])
                self.assertAlmostEqual(float(geometry["x_reference_float_decimal"]), expected[0], delta=2e-11)
                self.assertAlmostEqual(float(geometry["y_reference_float_decimal"]), expected[1], delta=2e-11)

            last_translation = decimal_vector(
                by_index[8]["geometry"]["translation_current_in_reference_m_decimal"]
            )
            self.assertAlmostEqual(
                math.sqrt(sum(value * value for value in last_translation)),
                0.0008486407832504547,
                delta=2e-12,
            )

    def test_true_trigonometric_shortest_arc_slerp(self) -> None:
        half = math.sqrt(0.5)
        poses = (
            b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
            + f"41.322500000 0.0 0.0 0.0 0.0 0.0 {-half:.17g} {-half:.17g}\n".encode("ascii")
        )
        events = b"41.321250000 120 90 1\n"
        calibration = b"100.0 100.0 120.0 90.0 0.0 0.0 0.0 0.0 0.0\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root, events=events, poses=poses, calibration=calibration)
            result = root / "adapter"
            fixture.run_adapter(result)
            header, records = event_rows(result)
            expected_reference = slerp((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, -half, -half), 0.4)
            expected_event = slerp((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, -half, -half), 0.5)
            for actual, expected in zip(
                decimal_vector(header["reference_pose"]["quaternion_xyzw_decimal"]),
                expected_reference,
            ):
                self.assertAlmostEqual(actual, expected, delta=2e-15)
            for actual, expected in zip(
                decimal_vector(records[0]["event_pose"]["quaternion_xyzw_decimal"]),
                expected_event,
            ):
                self.assertAlmostEqual(actual, expected, delta=2e-15)
            self.assertEqual(records[0]["event_pose"]["source_bracket"]["alpha_numerator_ns"], 1_250_000)
            self.assertEqual(records[0]["event_pose"]["source_bracket"]["alpha_denominator_ns"], 2_500_000)

    def test_invalid_distortion_is_raw_bypass_with_identity_preserved(self) -> None:
        events = b"41.321000000 1 0 1\n"
        poses = (
            b"41.320000000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
            b"41.322500000 0.0 0.0 0.0 0.0 0.0 0.0 1.0\n"
        )
        calibration = b"1.0 1.0 0.0 0.0 -1.0 0.0 0.0 0.0 0.0\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root, events=events, poses=poses, calibration=calibration)
            result = root / "adapter"
            receipt = fixture.run_adapter(result)
            _, records = event_rows(result)
            record = records[0]
            original = joined_event_records(fixture.joined)[0]
            self.assertEqual(record["disposition"], RAW_BYPASS)
            self.assertEqual(record["geometry"]["status"], "invalid_distortion")
            self.assertEqual(record["source_event"], source_identity(original))
            self.assertEqual(record["source_event"]["x_sensor"], 1)
            self.assertEqual(record["source_event"]["y_sensor"], 0)
            self.assertIsNone(record["geometry"]["x_reference_float_decimal"])
            self.assertIsNone(record["geometry"]["y_reference_float_decimal"])
            self.assertEqual(receipt["conservation"]["raw_bypass_invalid_geometry"], 1)
            self.assertEqual(receipt["conservation"]["raw_escape_geometric_oof"], 0)

    def test_join_tamper_and_bound_spec_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root / "tamper")
            event_path = fixture.joined / "events_pose_join.jsonl"
            event_path.write_bytes(event_path.read_bytes() + b" \n")
            tampered_result = root / "tampered-result"
            self.assert_rejected(lambda: fixture.run_adapter(tampered_result), tampered_result)

            fixture = JoinedFixture(root / "spec")
            wrong_value = copy.deepcopy(fixture.spec_value)
            wrong_value["selection"]["start_timestamp_ns_inclusive"] -= 1
            wrong_spec = fixture.root / "wrong_spec.json"
            wrong_spec.write_bytes(canonical_json(wrong_value))
            wrong_result = root / "wrong-result"
            self.assert_rejected(
                lambda: adapt(fixture.joined, wrong_spec, wrong_result), wrong_result,
            )

    def test_output_tamper_and_coherent_rehash_mutant_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root)

            simple = root / "simple"
            fixture.run_adapter(simple)
            artifact = simple / artifact_name(simple)
            artifact.chmod(0o644)
            artifact.write_bytes(artifact.read_bytes() + b" \n")
            self.assert_rejected(lambda: inspect(simple, fixture.joined, fixture.spec))

            coherent = root / "coherent"
            fixture.run_adapter(coherent)
            name = artifact_name(coherent)
            rows = read_jsonl(coherent / name)
            target = next(row for row in rows[1:] if row["disposition"] == WORLD)
            target["disposition"] = RAW_ESCAPE
            artifact_bytes = b"".join(canonical_json(row) for row in rows)
            (coherent / name).chmod(0o644)
            (coherent / name).write_bytes(artifact_bytes)

            receipt = read_json(coherent / RECEIPT_NAME)
            receipt["artifact"].update({
                "size_bytes": len(artifact_bytes), "sha256": sha256(artifact_bytes),
            })
            receipt["conservation"]["world_reference_events"] -= 1
            receipt["conservation"]["raw_escape_geometric_oof"] += 1
            receipt_bytes = canonical_json(receipt)
            (coherent / RECEIPT_NAME).chmod(0o644)
            (coherent / RECEIPT_NAME).write_bytes(receipt_bytes)

            completion = read_json(coherent / COMPLETE_NAME)
            completion["artifacts"][name].update({
                "size_bytes": len(artifact_bytes), "sha256": sha256(artifact_bytes),
            })
            completion["artifacts"][RECEIPT_NAME].update({
                "size_bytes": len(receipt_bytes), "sha256": sha256(receipt_bytes),
            })
            (coherent / COMPLETE_NAME).chmod(0o644)
            (coherent / COMPLETE_NAME).write_bytes(canonical_json(completion))
            self.assert_rejected(lambda: inspect(coherent, fixture.joined, fixture.spec))

    def test_published_bytes_are_deterministic_and_claims_remain_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = JoinedFixture(root)
            first, second = root / "first", root / "second"
            first_receipt = fixture.run_adapter(first)
            second_receipt = fixture.run_adapter(second)
            self.assertEqual(first_receipt, second_receipt)
            self.assertEqual({path.name for path in first.iterdir()}, EXPECTED_INVENTORY)
            for name in EXPECTED_INVENTORY:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)

            claims = first_receipt["claim_scope"]
            self.assertTrue(claims["orientation_only"])
            self.assertTrue(claims["translation_preserved_not_applied"])
            self.assertFalse(claims["depth_or_plane_model_applied"])
            for name in (
                "official_redred_traffic", "canonical_redred_traffic",
                "raw_packet_fifo_or_decoder_implemented", "controls_implemented",
                "codec_or_wire_benefit_claimed", "rtl_timing_power_or_ppa_claimed",
            ):
                self.assertFalse(claims[name], name)
            self.assertEqual(first_receipt["status"], EXPECTED_STATUS)
            self.assertEqual(first_receipt["promotion_status"], EXPECTED_PROMOTION)

    @unittest.skipUnless(
        os.environ.get("REDRED_RUN_UZH_ADAPTER_OFFICIAL") == "1",
        "set REDRED_RUN_UZH_ADAPTER_OFFICIAL=1 with a completed official join",
    )
    def test_optional_official_joined_artifact_exact_1094_plus_6(self) -> None:
        joined_value = os.environ.get("REDRED_UZH_JOINED_ROOT")
        spec_value = os.environ.get("REDRED_UZH_JOIN_SPEC")
        if not joined_value or not spec_value:
            self.skipTest("official joined root and its bound spec are required")
        joined, spec = Path(joined_value), Path(spec_value)
        self.assertTrue(joined.is_dir())
        self.assertTrue(spec.is_file())
        self.assertEqual(sha256((joined / RECEIPT_NAME).read_bytes()), OFFICIAL_JOIN_RECEIPT_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "adapter"
            receipt = adapt(joined, spec, result)
            header, records = event_rows(result)
            self.assert_status_and_offline_scope(result, receipt, header)
            self.assertEqual(len(records), 1_100)
            counts = receipt["conservation"]
            self.assertEqual(counts["world_reference_events"], 1_094)
            self.assertEqual(counts["raw_escape_geometric_oof"], 6)
            self.assertEqual(counts["raw_bypass_invalid_geometry"], 0)
            self.assertEqual(counts["dropped_events"], 0)
            self.assertEqual(counts["duplicate_events"], 0)

            escapes = {
                row["source_event"]["dataset_event_index"]: row
                for row in records if row["disposition"] == RAW_ESCAPE
            }
            self.assertEqual(set(escapes), set(OFFICIAL_SIX_GOLDENS))
            source_by_index = {
                row["dataset_event_index"]: row for row in joined_event_records(joined)
            }
            for index, expected in OFFICIAL_SIX_GOLDENS.items():
                record = escapes[index]
                self.assertEqual(record["source_event"], source_identity(source_by_index[index]))
                self.assertAlmostEqual(
                    float(record["geometry"]["x_reference_float_decimal"]),
                    expected[0], delta=2e-11,
                )
                self.assertAlmostEqual(
                    float(record["geometry"]["y_reference_float_decimal"]),
                    expected[1], delta=2e-11,
                )
            inspected = inspect(result, joined, spec)
            self.assertEqual(inspected["record_count"], 1_100)
            self.assertEqual(inspected["world_reference_events"], 1_094)
            self.assertEqual(inspected["raw_escape_geometric_oof"], 6)


if __name__ == "__main__":
    unittest.main()
