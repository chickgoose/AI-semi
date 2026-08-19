from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from demos.mc_wtb.model import (
    InterfaceError,
    UNSUPPORTED_FEATURES,
    _require_uint,
    analyze_files,
)


ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "demos" / "mc_wtb" / "fixtures" / "events.jsonl"
KNOWN = ROOT / "demos" / "known_motion_coordinate" / "fixtures"
INTRINSICS = KNOWN / "intrinsics.json"
POSES = KNOWN / "poses.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


class StageOneModelTests(unittest.TestCase):
    def analyze(
        self,
        directory: str,
        *,
        records: list[dict] | None = None,
        maximum_age: int = 0,
        output_name: str = "result.json",
        events_path: Path | None = None,
        intrinsics_path: Path | None = None,
        poses_path: Path | None = None,
        time_bin_ns: int = 1000,
    ) -> tuple[dict, Path]:
        events = events_path or EVENTS
        if records is not None:
            events = Path(directory) / "events.jsonl"
            write_jsonl(events, records)
        output = Path(directory) / output_name
        result = analyze_files(
            events,
            intrinsics_path or INTRINSICS,
            poses_path or POSES,
            output,
            tile_width=8,
            tile_height=8,
            time_bin_ns=time_bin_ns,
            max_pose_age_ns=maximum_age,
        )
        return result, output

    def test_fixture_preserves_atomic_timestamp_pose_and_exact_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.analyze(directory)
        ledger = result["exact_input_count_ledger"]
        self.assertEqual(ledger["declared_input_events"], 24)
        self.assertEqual(ledger["parsed_input_events"], 24)
        self.assertEqual(ledger["atomic_timestamp_pose_bindings"], 24)
        self.assertEqual(ledger["sensor_fixed_assignments"], 24)
        self.assertEqual(ledger["pose_compensated_assignments"], 24)
        self.assertEqual(ledger["positive_events"], 12)
        self.assertEqual(ledger["negative_events"], 12)
        self.assertEqual(ledger["dropped_events"], 0)
        self.assertEqual(ledger["unaccounted_events"], 0)
        event = result["exact_event_ledger"][7]
        self.assertEqual(event["event_id"], 107)
        self.assertEqual(event["timestamp_ns"], 1000)
        self.assertEqual(event["pose_version"], "pan-right")
        self.assertEqual(event["pose_version_code"], 1)
        self.assertEqual(event["pose_timestamp_ns"], 1000)
        self.assertEqual(event["pose_age_ns"], 0)

    def test_compensation_improves_locality_and_discloses_multiplicity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.analyze(directory)
        fixed = result["representations"]["sensor_fixed"]
        compensated = result["representations"]["pose_compensated_reference"]
        self.assertEqual(fixed["metrics"]["occupied_tile_polarity_bins"], 7)
        self.assertEqual(compensated["metrics"]["occupied_tile_polarity_bins"], 2)
        self.assertEqual(fixed["metrics"]["same_tile_extra_events"], 17)
        self.assertEqual(compensated["metrics"]["same_tile_extra_events"], 22)
        self.assertEqual(
            compensated["occupancy_by_polarity"]["positive"]["event_count"], 12
        )
        self.assertEqual(
            compensated["occupancy_by_polarity"]["negative"]["event_count"], 12
        )
        self.assertEqual(
            compensated["occupancy_by_polarity"]["positive"]["occupied_tiles"], 1
        )
        self.assertEqual(
            compensated["occupancy_by_polarity"]["negative"]["occupied_tiles"], 1
        )
        motion = result["bottleneck_metrics"]["6_motion_reference_locality"]
        self.assertGreater(motion["spread_reduction"], 0.0)
        self.assertGreater(motion["same_tile_adjacency_gain"], 0.0)
        self.assertGreater(motion["concentration_gain"], 0.0)
        disclosure = result["same_tile_multiplicity_disclosure"]
        self.assertTrue(disclosure["occupancy_projection_is_lossy"])
        self.assertFalse(disclosure["reversible_codec_implemented"])
        self.assertTrue(disclosure["exact_event_ledger_remains_complete"])

    def test_fixed_logical_bit_accounting_is_exact_and_caveated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.analyze(directory)
        accounting = result["logical_bit_accounting"]
        self.assertEqual(
            accounting["format"]["format_id"], "redred.mc_wtb.logical_bits/fixed-v1"
        )
        self.assertEqual(
            accounting["sensor_fixed"]["raw_sensor_payload_width_bits"], 113
        )
        self.assertEqual(accounting["sensor_fixed"]["raw_sensor_payload_total_bits"], 2712)
        self.assertEqual(accounting["sensor_fixed"]["occupancy_packet_count"], 10)
        self.assertEqual(
            accounting["pose_compensated_reference"]["occupancy_packet_count"], 8
        )
        self.assertEqual(
            accounting["sensor_fixed"]["occupancy_projection_total_bits"], 1290
        )
        self.assertEqual(
            accounting["pose_compensated_reference"]["occupancy_projection_total_bits"], 1032
        )
        bottleneck = result["bottleneck_metrics"][
            "1_packet_key_projection_not_wire_bandwidth"
        ]
        self.assertEqual(bottleneck["projected_delta_bits"], 258)
        self.assertFalse(bottleneck["actual_wire_bandwidth_measured"])
        self.assertIn("cannot be quoted", bottleneck["caveat"])
        self.assertEqual(
            result["input_contract"]
            ["analysis_provenance_fields_excluded_from_raw_sensor_payload"],
            ["event_id", "sequence_index"],
        )

    def test_output_bytes_and_return_value_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, first_path = self.analyze(directory, output_name="first.json")
            second, second_path = self.analyze(directory, output_name="second.json")
            self.assertEqual(first, second)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertEqual(json.loads(first_path.read_text(encoding="utf-8")), first)

    def assert_fail_closed(
        self, records: list[dict], pattern: str, *, maximum_age: int = 0
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            with self.assertRaisesRegex(InterfaceError, pattern):
                self.analyze(directory, records=records, maximum_age=maximum_age)
            self.assertFalse(output.exists())

    def test_unknown_future_stale_and_out_of_fov_pose_use_fail_closed(self) -> None:
        records = read_jsonl(EVENTS)
        records[1]["pose_version"] = "missing-pose"
        self.assert_fail_closed(records, "unknown pose_version")

        records = read_jsonl(EVENTS)
        records[1]["pose_version"] = "pan-right"
        self.assert_fail_closed(records, "from the future")

        records = read_jsonl(EVENTS)
        records[-1]["timestamp_ns"] = 3001
        self.assert_fail_closed(records, "pose age 1 exceeds inclusive maximum 0")

        records = read_jsonl(EVENTS)
        records[-1]["pose_version"] = "identity"
        self.assert_fail_closed(records, "is not the deterministic latest pose")

        records = read_jsonl(EVENTS)
        records[7]["x"] = 0
        records[7]["y"] = 20
        self.assert_fail_closed(records, "out_of_fov")

    def test_count_identity_order_and_polarity_mutants_fail(self) -> None:
        records = read_jsonl(EVENTS)
        records[0]["declared_event_count"] = 23
        self.assert_fail_closed(records, "declared_event_count=23")

        records = read_jsonl(EVENTS)
        records[2]["event_id"] = records[1]["event_id"]
        self.assert_fail_closed(records, "duplicate event_id")

        records = read_jsonl(EVENTS)
        records[2]["sequence_index"] = 99
        self.assert_fail_closed(records, "sequence_index")

        for mutant in (0, 1.0, True, "1"):
            with self.subTest(polarity=repr(mutant)):
                records = read_jsonl(EVENTS)
                records[2]["polarity"] = mutant
                self.assert_fail_closed(records, "polarity must be JSON integer -1 or 1")

    def test_event_timebase_must_exactly_match_pose_timebase(self) -> None:
        mutations = {
            "clock_domain": "other_clock",
            "epoch": "other_epoch",
            "unit": "us",
        }
        for key, value in mutations.items():
            with self.subTest(field=key):
                records = read_jsonl(EVENTS)
                records[0]["timebase"][key] = value
                self.assert_fail_closed(records, "event timebase must exactly match")
        records = read_jsonl(EVENTS)
        del records[0]["timebase"]["unit"]
        self.assert_fail_closed(records, "timebase keys differ")
        records = read_jsonl(EVENTS)
        records[0]["timebase"]["scale"] = 1
        self.assert_fail_closed(records, "timebase keys differ")

    def test_output_may_not_alias_any_input_and_inputs_are_preserved(self) -> None:
        inputs = (EVENTS, INTRINSICS, POSES)
        for output in inputs:
            with self.subTest(exact_output=str(output)):
                before = {path: path.read_bytes() for path in inputs}
                with self.assertRaisesRegex(InterfaceError, "output aliases input path"):
                    analyze_files(
                        EVENTS,
                        INTRINSICS,
                        POSES,
                        output,
                        tile_width=8,
                        tile_height=8,
                        time_bin_ns=1000,
                        max_pose_age_ns=0,
                    )
                self.assertEqual(before, {path: path.read_bytes() for path in inputs})

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            events = temporary / "events.jsonl"
            intrinsics = temporary / "intrinsics.json"
            poses = temporary / "poses.jsonl"
            shutil.copyfile(EVENTS, events)
            shutil.copyfile(INTRINSICS, intrinsics)
            shutil.copyfile(POSES, poses)
            output = temporary / "hardlink-result.json"
            os.link(events, output)
            before = {path: path.read_bytes() for path in (events, intrinsics, poses)}
            with self.assertRaisesRegex(InterfaceError, "output aliases input inode"):
                analyze_files(
                    events,
                    intrinsics,
                    poses,
                    output,
                    tile_width=8,
                    tile_height=8,
                    time_bin_ns=1000,
                    max_pose_age_ns=0,
                )
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_all_declared_unsigned_widths_accept_boundary_and_reject_overflow(self) -> None:
        declared_fields = {
            "timestamp_ns": 64,
            "time_bin_start_ns": 64,
            "tile.x": 16,
            "tile.y": 16,
            "pose_version_code": 16,
            "multiplicity_count": 16,
        }
        for field, bits in declared_fields.items():
            with self.subTest(field=field, case="boundary"):
                self.assertEqual(_require_uint((1 << bits) - 1, bits, field), (1 << bits) - 1)
            with self.subTest(field=field, case="overflow"):
                with self.assertRaisesRegex(InterfaceError, f"unsigned {bits}-bit"):
                    _require_uint(1 << bits, bits, field)

        records = read_jsonl(EVENTS)
        records[-1]["timestamp_ns"] = (1 << 64) - 1
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.analyze(
                directory,
                records=records,
                maximum_age=(1 << 64) - 1,
                time_bin_ns=1,
            )
        last_packet_start = max(
            packet["time_bin_start_ns"]
            for packet in result["representations"]["sensor_fixed"]
            ["logical_occupancy_packets"]
        )
        self.assertEqual(last_packet_start, (1 << 64) - 1)

        records[-1]["timestamp_ns"] = 1 << 64
        self.assert_fail_closed(records, "unsigned 64-bit", maximum_age=(1 << 64) - 1)

    def test_exact_input_sha256_provenance_changes_with_each_file_bytes(self) -> None:
        hash_keys = (
            ("events_sha256", "events.jsonl"),
            ("intrinsics_sha256", "intrinsics.json"),
            ("poses_sha256", "poses.jsonl"),
        )
        for hash_key, filename in hash_keys:
            with self.subTest(input=hash_key), tempfile.TemporaryDirectory() as directory:
                temporary = Path(directory)
                events = temporary / "events.jsonl"
                intrinsics = temporary / "intrinsics.json"
                poses = temporary / "poses.jsonl"
                for source, target in (
                    (EVENTS, events),
                    (INTRINSICS, intrinsics),
                    (POSES, poses),
                ):
                    shutil.copyfile(source, target)
                baseline, _ = self.analyze(
                    directory,
                    events_path=events,
                    intrinsics_path=intrinsics,
                    poses_path=poses,
                    output_name="baseline.json",
                )
                target = temporary / filename
                target.write_bytes(target.read_bytes() + b"\n")
                mutated, _ = self.analyze(
                    directory,
                    events_path=events,
                    intrinsics_path=intrinsics,
                    poses_path=poses,
                    output_name="mutated.json",
                )
                self.assertNotEqual(
                    baseline["input_provenance"][hash_key],
                    mutated["input_provenance"][hash_key],
                )
                self.assertFalse(mutated["input_provenance"]["canonical_evidence_claimed"])

    def test_scope_explicitly_rejects_unimplemented_system_features(self) -> None:
        self.assertEqual(
            UNSUPPORTED_FEATURES,
            ["depth", "pose_estimation", "reversible_codec", "rtl", "translation"],
        )
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.analyze(directory)
        self.assertEqual(result["model_scope"]["unsupported"], UNSUPPORTED_FEATURES)
        self.assertTrue(result["model_scope"]["rotation_only"])
        self.assertFalse(
            result["bottleneck_metrics"]["5_timestamp_fidelity"]
            ["transport_timestamp_error_measured"]
        )
        self.assertFalse(
            result["bottleneck_metrics"]["6_motion_reference_locality"]
            ["world_reconstruction_error_measured"]
        )
        self.assertEqual(
            result["output_semantics"]["atomic_visibility"],
            "temporary file plus os.replace at destination",
        )
        self.assertFalse(result["output_semantics"]["crash_durability_guaranteed"])


class CliTests(unittest.TestCase):
    def command(self, output: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "demos.mc_wtb.cli",
            "--events",
            str(EVENTS),
            "--intrinsics",
            str(INTRINSICS),
            "--poses",
            str(POSES),
            "--tile-width",
            "8",
            "--tile-height",
            "8",
            "--time-bin-ns",
            "1000",
            "--max-pose-age-ns",
            "0",
            "--output",
            str(output),
        ]

    def test_cli_stdout_matches_deterministic_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            completed = subprocess.run(
                self.command(output), cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), json.loads(output.read_text()))

    def test_cli_failure_writes_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            command = self.command(output)
            command[command.index("0")] = "-1"
            completed = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("must be a non-negative integer", completed.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
