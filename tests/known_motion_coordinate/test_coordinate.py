from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from demos.known_motion_coordinate.model import (
    InterfaceError,
    Intrinsics,
    euler_world_to_sensor,
    transform_files,
    warp_pixel,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "demos" / "known_motion_coordinate" / "fixtures"


class RotationWarpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.camera = Intrinsics("test-k", "test-camera", 101, 101, 100.0, 100.0, 50.0, 50.0)

    def assertPixelAlmostEqual(self, result: dict[str, object], x: float, y: float) -> None:
        self.assertEqual(result["status"], "in_fov")
        self.assertAlmostEqual(float(result["x_float"]), x, places=10)
        self.assertAlmostEqual(float(result["y_float"]), y, places=10)

    def test_identity_forward_and_inverse(self) -> None:
        rotation = euler_world_to_sensor(0.0, 0.0, 0.0)
        self.assertPixelAlmostEqual(warp_pixel(37.0, 61.0, self.camera, rotation, "world-to-sensor"), 37.0, 61.0)
        self.assertPixelAlmostEqual(warp_pixel(37.0, 61.0, self.camera, rotation, "sensor-to-world"), 37.0, 61.0)

    def test_positive_pan_moves_center_right_and_inverse_recovers(self) -> None:
        angle = math.degrees(math.atan(0.1))
        rotation = euler_world_to_sensor(angle, 0.0, 0.0)
        forward = warp_pixel(50.0, 50.0, self.camera, rotation, "world-to-sensor")
        self.assertPixelAlmostEqual(forward, 60.0, 50.0)
        inverse = warp_pixel(float(forward["x_float"]), 50.0, self.camera, rotation, "sensor-to-world")
        self.assertPixelAlmostEqual(inverse, 50.0, 50.0)

    def test_positive_tilt_moves_center_up(self) -> None:
        angle = math.degrees(math.atan(0.1))
        rotation = euler_world_to_sensor(0.0, angle, 0.0)
        self.assertPixelAlmostEqual(
            warp_pixel(50.0, 50.0, self.camera, rotation, "world-to-sensor"), 50.0, 40.0
        )

    def test_positive_roll_moves_right_hand_ray_down(self) -> None:
        rotation = euler_world_to_sensor(0.0, 0.0, 90.0)
        self.assertPixelAlmostEqual(
            warp_pixel(60.0, 50.0, self.camera, rotation, "world-to-sensor"), 50.0, 60.0
        )


class InterfaceAndCliTests(unittest.TestCase):
    def test_fixture_timestamp_pose_matrix_and_loss_accounting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "events.jsonl"
            summary_path = Path(directory) / "summary.json"
            summary = transform_files(
                FIXTURES / "retired_events.jsonl",
                FIXTURES / "intrinsics.json",
                FIXTURES / "poses.jsonl",
                output,
                summary_path,
                "world-to-sensor",
            )
            self.assertEqual(summary["counts"]["input_retired_events"], 5)
            self.assertEqual(summary["counts"]["transformed_in_fov"], 4)
            self.assertEqual(summary["counts"]["coordinate_out_of_fov"], 1)
            self.assertEqual(summary["aer_transport_accounting"]["source_overrun"], 1)
            self.assertEqual(summary["aer_transport_accounting"]["accepted_missing"], 0)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[2]["pose_id"], "pan-right")
            self.assertEqual(records[2]["pose_selection"], "timestamp_zero_order_hold")
            self.assertEqual(records[3]["pose_id"], "tilt-up-matrix")
            self.assertEqual(records[-1]["output"]["status"], "out_of_fov")
            self.assertEqual(len(records[0]["provenance"]["events_sha256"]), 64)

    def test_stale_pose_limit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "events.jsonl"
            summary_path = Path(directory) / "summary.json"
            with self.assertRaisesRegex(InterfaceError, "pose age"):
                transform_files(
                    FIXTURES / "retired_events.jsonl",
                    FIXTURES / "intrinsics.json",
                    FIXTURES / "poses.jsonl",
                    output,
                    summary_path,
                    "world-to-sensor",
                    max_pose_age_ns=99,
                )
            self.assertFalse(output.exists())
            self.assertFalse(summary_path.exists())

    def test_cli_writes_both_outputs_and_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "events.jsonl"
            summary = Path(directory) / "summary.json"
            command = [
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
                "--output",
                str(output),
                "--summary",
                str(summary),
            ]
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            stdout_summary = json.loads(completed.stdout)
            disk_summary = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(stdout_summary, disk_summary)
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
