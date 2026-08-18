from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from unittest import mock
import subprocess
import sys
import tempfile
import unittest

from demos.known_motion_coordinate.model import (
    CANONICAL_COMMON_SUITE,
    InterfaceError,
    Intrinsics,
    euler_world_to_sensor,
    transform_files,
    warp_pixel,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "demos" / "known_motion_coordinate" / "fixtures"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def matrix_multiply(left, right):
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


class RotationWarpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.camera = Intrinsics(
            "test-k", "test-camera", 1001, 1001, 400.0, 420.0, 500.0, 500.0
        )

    def assertPixelAlmostEqual(self, result: dict, x: float, y: float) -> None:
        self.assertEqual(result["status"], "in_fov")
        self.assertAlmostEqual(float(result["x_float"]), x, places=10)
        self.assertAlmostEqual(float(result["y_float"]), y, places=10)

    def test_identity_forward_and_inverse(self) -> None:
        rotation = euler_world_to_sensor(0.0, 0.0, 0.0)
        self.assertPixelAlmostEqual(
            warp_pixel(370.0, 610.0, self.camera, rotation, "world-to-sensor"),
            370.0,
            610.0,
        )
        self.assertPixelAlmostEqual(
            warp_pixel(370.0, 610.0, self.camera, rotation, "sensor-to-world"),
            370.0,
            610.0,
        )

    def test_pan_tilt_and_roll_sign_goldens(self) -> None:
        angle = math.degrees(math.atan(0.1))
        pan = warp_pixel(
            500.0,
            500.0,
            self.camera,
            euler_world_to_sensor(angle, 0.0, 0.0),
            "world-to-sensor",
        )
        self.assertPixelAlmostEqual(pan, 540.0, 500.0)
        tilt = warp_pixel(
            500.0,
            500.0,
            self.camera,
            euler_world_to_sensor(0.0, angle, 0.0),
            "world-to-sensor",
        )
        self.assertPixelAlmostEqual(tilt, 500.0, 458.0)
        roll = warp_pixel(
            540.0,
            500.0,
            self.camera,
            euler_world_to_sensor(0.0, 0.0, 90.0),
            "world-to-sensor",
        )
        self.assertPixelAlmostEqual(roll, 500.0, 542.0)

    def test_compound_noncommuting_numerical_golden_and_reversed_order_mutant(self) -> None:
        rotation = euler_world_to_sensor(13.0, -7.0, 19.0)
        expected_matrix = (
            (0.9302103286303524, -0.3231414188034184, 0.1740355364950670),
            (0.2913028149482985, 0.9384708235164863, 0.1855132971284953),
            (-0.2232743032966611, -0.1218693434051475, 0.9671072580770909),
        )
        for actual_row, expected_row in zip(rotation, expected_matrix):
            for actual, expected in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual, expected, places=14)
        result = warp_pixel(560.0, 470.0, self.camera, rotation, "world-to-sensor")
        self.assertPixelAlmostEqual(result, 642.901874091145, 572.2827281600142)

        pan, tilt, roll = map(math.radians, (13.0, -7.0, 19.0))
        rp = (
            (math.cos(pan), 0.0, math.sin(pan)),
            (0.0, 1.0, 0.0),
            (-math.sin(pan), 0.0, math.cos(pan)),
        )
        rt = (
            (1.0, 0.0, 0.0),
            (0.0, math.cos(tilt), -math.sin(tilt)),
            (0.0, math.sin(tilt), math.cos(tilt)),
        )
        rr = (
            (math.cos(roll), -math.sin(roll), 0.0),
            (math.sin(roll), math.cos(roll), 0.0),
            (0.0, 0.0, 1.0),
        )
        reversed_order_mutant = matrix_multiply(rp, matrix_multiply(rt, rr))
        mutant = warp_pixel(
            560.0, 470.0, self.camera, reversed_order_mutant, "world-to-sensor"
        )
        self.assertPixelAlmostEqual(mutant, 665.0468554460639, 546.5450094528826)
        self.assertNotAlmostEqual(result["x_float"], mutant["x_float"], places=6)

    def test_compound_forward_inverse_round_trip(self) -> None:
        rotation = euler_world_to_sensor(13.0, -7.0, 19.0)
        forward = warp_pixel(560.0, 470.0, self.camera, rotation, "world-to-sensor")
        inverse = warp_pixel(
            forward["x_float"],
            forward["y_float"],
            self.camera,
            rotation,
            "sensor-to-world",
        )
        self.assertPixelAlmostEqual(inverse, 560.0, 470.0)


class InterfaceTests(unittest.TestCase):
    def run_fixture(
        self,
        directory: str,
        *,
        event_records: list[dict] | None = None,
        pose_records: list[dict] | None = None,
        intrinsics: dict | None = None,
        maximum_age: int = 800,
    ) -> tuple[dict, Path, Path]:
        base = Path(directory)
        events = FIXTURES / "retired_events.jsonl"
        poses = FIXTURES / "poses.jsonl"
        camera = FIXTURES / "intrinsics.json"
        if event_records is not None:
            events = base / "events.jsonl"
            write_jsonl(events, event_records)
        if pose_records is not None:
            poses = base / "poses.jsonl"
            write_jsonl(poses, pose_records)
        if intrinsics is not None:
            camera = base / "intrinsics.json"
            camera.write_text(json.dumps(intrinsics) + "\n", encoding="utf-8")
        output = base / "transformed.jsonl"
        summary_path = base / "summary.json"
        summary = transform_files(
            events,
            camera,
            poses,
            output,
            summary_path,
            "world-to-sensor",
            maximum_age,
        )
        return summary, output, summary_path

    def test_fixture_is_synthetic_post_retire_and_keeps_transport_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, output, _ = self.run_fixture(directory)
            self.assertEqual(summary["provenance"]["evidence_class"], "SYNTHETIC_DEMO")
            self.assertEqual(summary["counts"]["coordinate_out_of_fov"], 1)
            self.assertEqual(summary["aer_transport_accounting"]["source_overrun"], 1)
            self.assertEqual(summary["aer_transport_accounting"]["accepted_missing"], 0)
            records = read_jsonl(output)
            event = records[2]
            self.assertEqual(event["tb_only_event_id"], 20)
            self.assertEqual(event["retire_sequence_index"], 1)
            self.assertEqual(event["logical_source"], 1)
            self.assertEqual(event["address"], 1)
            self.assertEqual(event["capture_time"]["value"], 1000)
            self.assertEqual(event["retire_time"]["value"], 1200)
            self.assertEqual(event["pose_lookup_time_field"], "capture_time")
            self.assertEqual(event["pose_lookup_timestamp"]["value"], 1000)

    def test_noncontiguous_tb_ids_are_preserved_with_contiguous_retire_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, output, _ = self.run_fixture(directory)
            events = read_jsonl(output)[1:]
            self.assertEqual(
                [event["tb_only_event_id"] for event in events],
                [10, 20, 40, 70, 105],
            )
            self.assertEqual(
                [event["retire_sequence_index"] for event in events],
                [0, 1, 2, 3, 4],
            )

    def test_exact_pose_timestamp_uses_latest_pose_at_or_before(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, output, _ = self.run_fixture(directory)
            event = read_jsonl(output)[2]
            self.assertEqual(event["pose_id"], "pan-right")
            self.assertEqual(event["pose_age_ns"], 0)

    def test_declared_occurrence_time_not_retire_or_capture_drives_pose(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["pose_lookup_time"] = "occurrence_time"
        with tempfile.TemporaryDirectory() as directory:
            _, output, _ = self.run_fixture(
                directory, event_records=events, maximum_age=1000
            )
            result = read_jsonl(output)[2]
            self.assertEqual(result["pose_id"], "identity")
            self.assertEqual(result["pose_lookup_timestamp"]["value"], 900)
            self.assertEqual(result["capture_time"]["value"], 1000)
            self.assertEqual(result["retire_time"]["value"], 1200)

    def test_pose_age_limit_is_inclusive_and_one_beyond_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary, _, _ = self.run_fixture(directory, maximum_age=800)
            self.assertEqual(summary["pose_handling"]["maximum_observed_pose_age_ns"], 800)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "exceeds inclusive maximum 799"):
                self.run_fixture(directory, maximum_age=799)

    def test_before_first_pose_fails_closed(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        poses = read_jsonl(FIXTURES / "poses.jsonl")
        events[1]["pose_id"] = None
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "no pose at or before"):
                self.run_fixture(directory, event_records=events, pose_records=[poses[0], *poses[2:]])

    def test_future_explicit_pose_fails_closed(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1]["pose_id"] = "pan-right"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "explicit pose is in the future"):
                self.run_fixture(directory, event_records=events, maximum_age=2000)

    def test_accepted_missing_is_hard_failure_with_no_outputs(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["transport_accounting"].update(generated=7, accepted=6, retired=5)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "transformed.jsonl"
            summary = Path(directory) / "summary.json"
            with self.assertRaisesRegex(
                InterfaceError, "accepted == retired == event-record count"
            ):
                self.run_fixture(directory, event_records=events)
            self.assertFalse(output.exists())
            self.assertFalse(summary.exists())

    def test_unknown_fields_duplicate_keys_ids_and_pose_timestamps_fail(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1]["free_field"] = "not allowed"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "unknown=.*free_field"):
                self.run_fixture(directory, event_records=events)

        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[2]["tb_only_event_id"] = 10
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "duplicate tb_only_event_id"):
                self.run_fixture(directory, event_records=events)

        poses = read_jsonl(FIXTURES / "poses.jsonl")
        poses[2]["timestamp"] = copy.deepcopy(poses[1]["timestamp"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "duplicate pose ID or pose timestamp"):
                self.run_fixture(directory, pose_records=poses)

        raw = (FIXTURES / "retired_events.jsonl").read_text(encoding="utf-8").splitlines()
        raw[1] = raw[1].replace(
            '"record_type":"event"',
            '"record_type":"event","record_type":"event"',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            events_path = Path(directory) / "events.jsonl"
            events_path.write_text("\n".join(raw) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(InterfaceError, "duplicate JSON key"):
                transform_files(
                    events_path,
                    FIXTURES / "intrinsics.json",
                    FIXTURES / "poses.jsonl",
                    Path(directory) / "out.jsonl",
                    Path(directory) / "summary.json",
                    "world-to-sensor",
                    800,
                )

    def test_swapped_retirement_order_and_address_fail(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1], events[2] = events[2], events[1]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "retire_sequence_index"):
                self.run_fixture(directory, event_records=events)

        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1]["address"], events[2]["address"] = (
            events[2]["address"],
            events[1]["address"],
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "address must equal logical_source"):
                self.run_fixture(directory, event_records=events)

    def test_changed_domain_epoch_and_header_clock_label_fail(self) -> None:
        mutations = []
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1]["capture_time"]["clock_domain"] = "unbound_clock"
        mutations.append(events)
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[1]["accept_time"]["epoch"] = "different_epoch"
        mutations.append(events)
        for index, events in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(InterfaceError, "exact absolute timebase"):
                    self.run_fixture(directory, event_records=events)

        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["clock_domains"]["retire_time"] = "unbound_clock"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "absolute clock domain"):
                self.run_fixture(directory, event_records=events)

    def test_cross_stage_time_order_mutants_fail(self) -> None:
        mutations = (
            ("occurrence_time", 101),
            ("capture_time", 121),
            ("accept_time", 201),
        )
        for field, value in mutations:
            events = read_jsonl(FIXTURES / "retired_events.jsonl")
            events[1][field]["value"] = value
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(
                    InterfaceError, "occurrence <= capture <= accept <= retire"
                ):
                    self.run_fixture(directory, event_records=events)

    def test_matrix_direction_and_machine_convention_mutants_fail(self) -> None:
        poses = read_jsonl(FIXTURES / "poses.jsonl")
        poses[3]["matrix_direction"] = "sensor_to_world"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "must be world_to_sensor"):
                self.run_fixture(directory, pose_records=poses)

        intrinsics = json.loads((FIXTURES / "intrinsics.json").read_text(encoding="utf-8"))
        intrinsics["convention"]["camera_axes"]["handedness"] = "left_handed"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "machine-bound contract"):
                self.run_fixture(directory, intrinsics=intrinsics)

    def test_zero_digests_fail(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["provenance"]["manifest_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "must be nonzero"):
                self.run_fixture(directory, event_records=events)

        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["provenance"]["content_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "must be nonzero"):
                self.run_fixture(directory, event_records=events)

    def test_every_canonical_claim_is_hold_without_reading_sidecar(self) -> None:
        for receipt_reference in (
            None,
            {"path": "untrusted-or-symlinked-sidecar.json", "sha256": "f" * 64},
        ):
            events = read_jsonl(FIXTURES / "retired_events.jsonl")
            events[0]["evidence_class"] = CANONICAL_COMMON_SUITE
            events[0]["provenance"]["transport_receipt"] = receipt_reference
            with self.subTest(receipt=receipt_reference), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "untrusted-sidecar-target.json"
                target.write_text('{"untrusted":true}\n', encoding="utf-8")
                sidecar = base / "untrusted-or-symlinked-sidecar.json"
                sidecar.symlink_to(target)
                original_is_symlink = Path.is_symlink
                original_resolve = Path.resolve

                def reject_sidecar_inspection(path: Path) -> bool:
                    if path.name == sidecar.name:
                        raise AssertionError("canonical sidecar was inspected")
                    return original_is_symlink(path)

                def reject_sidecar_resolution(path: Path, *args, **kwargs) -> Path:
                    if path.name == sidecar.name:
                        raise AssertionError("canonical sidecar was resolved")
                    return original_resolve(path, *args, **kwargs)

                with mock.patch.object(Path, "resolve", reject_sidecar_resolution), \
                     mock.patch.object(Path, "is_symlink", reject_sidecar_inspection):
                    with self.assertRaisesRegex(
                        InterfaceError,
                        "CANONICAL_COMMON_SUITE is HOLD/unsupported",
                    ):
                        self.run_fixture(directory, event_records=events)
                self.assertFalse((base / "transformed.jsonl").exists())
                self.assertFalse((base / "summary.json").exists())

    def test_synthetic_claim_with_any_sidecar_is_rejected(self) -> None:
        events = read_jsonl(FIXTURES / "retired_events.jsonl")
        events[0]["provenance"]["transport_receipt"] = {
            "path": "fake.json",
            "sha256": "f" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InterfaceError, "rejects transport receipts"):
                self.run_fixture(directory, event_records=events)

    def test_each_primary_input_is_read_once_and_exact_bytes_are_hashed(self) -> None:
        original = Path.read_bytes
        counts: dict[Path, int] = {}

        def counted(path: Path) -> bytes:
            resolved = path.resolve()
            counts[resolved] = counts.get(resolved, 0) + 1
            return original(path)

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(Path, "read_bytes", counted):
                summary, _, _ = self.run_fixture(directory)
        for name, provenance_key in (
            ("retired_events.jsonl", "events_input_sha256"),
            ("intrinsics.json", "intrinsics_input_sha256"),
            ("poses.jsonl", "poses_input_sha256"),
        ):
            path = (FIXTURES / name).resolve()
            self.assertEqual(counts[path], 1)
            self.assertEqual(
                summary["provenance"][provenance_key], hashlib.sha256(original(path)).hexdigest()
            )


class CliTests(unittest.TestCase):
    def command(self, directory: str) -> list[str]:
        return [
            sys.executable,
            "-m",
            "demos.known_motion_coordinate.cli",
            "--events",
            str(FIXTURES / "retired_events.jsonl"),
            "--intrinsics",
            str(FIXTURES / "intrinsics.json"),
            "--poses",
            str(FIXTURES / "poses.jsonl"),
            "--mode",
            "world-to-sensor",
            "--max-pose-age-ns",
            "800",
            "--output",
            str(Path(directory) / "events.jsonl"),
            "--summary",
            str(Path(directory) / "summary.json"),
        ]

    def test_cli_writes_outputs_and_reports_synthetic_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                self.command(directory), cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["provenance"]["evidence_class"], "SYNTHETIC_DEMO")

    def test_cli_requires_maximum_pose_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = self.command(directory)
            index = command.index("--max-pose-age-ns")
            del command[index:index + 2]
            completed = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("--max-pose-age-ns", completed.stderr)


if __name__ == "__main__":
    unittest.main()
