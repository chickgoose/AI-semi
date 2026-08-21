from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from benchmarks.redred_mc_wtb_stage4_assay import generator as generator_module
from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_assay import (
    AssayInputError,
    SourcePins,
    canonicalize_quaternion,
    generate_score_free_inputs,
    shortest_arc_slerp,
    timestamp_to_cycle,
)


def timestamp_text(timestamp_ns: int) -> str:
    return "%d.%09d" % divmod(timestamp_ns, 1_000_000_000)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def build_fixture(root: Path) -> SourcePins:
    rows = tuple(window_registry())
    event_lines = []
    remaining = 8914
    for index, row in enumerate(rows):
        warmup = row["warmup_start_ns_inclusive"]
        event_lines.append("%s 120 90 0\n" % timestamp_text(warmup))
        count = 372 if index < 10 else 371
        remaining -= count
        for offset in range(count):
            if index == 0 and offset < 5:
                timestamp = row["query_start_ns_inclusive"]
            else:
                adjusted = offset - 5 if index == 0 else offset
                denominator = count - 5 if index == 0 else count
                timestamp = (
                    row["query_start_ns_inclusive"]
                    + 10_000
                    + (adjusted * 980_000) // denominator
                )
            x = 100 + offset % 40
            y = 70 + offset % 30
            event_lines.append(
                "%s %d %d %d\n" % (timestamp_text(timestamp), x, y, offset & 1)
            )
        if index == 17:
            event_lines.append("43.321000000 1 1 0\n")
    assert remaining == 0
    events = root / "events.txt"
    events.write_text("".join(event_lines), encoding="ascii")

    pose_lines = []
    pose_id = 0
    for row in rows:
        start = row["warmup_start_ns_inclusive"]
        for delta in (
            -2_500_000,
            -1_500_000,
            -500_000,
            1_000_000,
            1_500_000,
            3_500_000,
            8_500_000,
        ):
            timestamp = start + delta
            angle = pose_id * 0.0001
            qy = math.sin(angle / 2.0)
            qw = math.cos(angle / 2.0)
            pose_lines.append(
                "%s 0 0 0 0 %.17g 0 %.17g\n"
                % (timestamp_text(timestamp), qy, qw)
            )
            pose_id += 1
    groundtruth = root / "groundtruth.txt"
    groundtruth.write_text("".join(pose_lines), encoding="ascii")
    calibration = root / "calib.txt"
    calibration.write_text("100 100 120 90 0 0 0 0 0\n", encoding="ascii")
    return SourcePins(
        events_sha256=sha256(events),
        groundtruth_sha256=sha256(groundtruth),
        calibration_sha256=sha256(calibration),
        events_size_bytes=events.stat().st_size,
        events_line_count=len(event_lines),
    )


class GeneratorTests(unittest.TestCase):
    def test_integer_cycle_mapping_handles_edges_and_negative_history(self) -> None:
        self.assertEqual(timestamp_to_cycle(1_000_000, 1_000_000, 6500), 0)
        self.assertEqual(timestamp_to_cycle(1_000_006, 1_000_000, 6500), 1)
        self.assertEqual(timestamp_to_cycle(1_000_007, 1_000_000, 6500), 2)
        self.assertEqual(timestamp_to_cycle(999_994, 1_000_000, 6500), 0)
        self.assertEqual(timestamp_to_cycle(999_993, 1_000_000, 6500), -1)

    def test_quaternion_sign_canonicalization_and_slerp_are_encoding_invariant(self) -> None:
        before = (0.0, 0.0, 0.0, 1.0)
        after = (0.0, math.sin(0.1), 0.0, math.cos(0.1))
        negated_before = tuple(-value for value in before)
        negated_after = tuple(-value for value in after)
        self.assertEqual(canonicalize_quaternion(before), canonicalize_quaternion(negated_before))
        expected = shortest_arc_slerp(before, after, 1, 2)
        actual = shortest_arc_slerp(negated_before, negated_after, 1, 2)
        for left, right in zip(expected, actual):
            self.assertAlmostEqual(left, right)

    def test_six_entry_serializer_rejects_atomic_overflow_without_external_queue(self) -> None:
        records = []
        batches = []
        for cycle in (0, 1):
            event_ids = []
            for lane in range(6):
                event_id = cycle * 6 + lane
                event_ids.append(event_id)
                records.append({"event_id": event_id, "occurrence_cycle": cycle})
            batches.append({"occurrence_cycle": cycle, "event_ids": event_ids})
        with self.assertRaisesRegex(AssayInputError, "serializer overflow"):
            generator_module._schedule_staging_serializer(records, batches)

    def test_pose_packet_stream_and_per_packet_hashes_fail_closed(self) -> None:
        packet = {
            "window_id": "fixture",
            "source_pose_id": 7,
            "timestamp_ns": 100,
            "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            "pose_value_sha256": "1" * 64,
            "arrival_cycle": 2,
            "commit_cycle": 2,
            "visible_cycle": 3,
            "visible_at_window_start": False,
        }
        packet["packet_sha256"] = canonical_sha256(packet)
        payload = generator_module._jsonl_bytes([packet])
        stream_hash = hashlib.sha256(payload).hexdigest()
        generator_module._validate_dataset_pose_packet_stream([packet], stream_hash)
        with self.assertRaisesRegex(AssayInputError, "stream hash"):
            generator_module._validate_dataset_pose_packet_stream([packet], "0" * 64)
        mutated = dict(packet)
        mutated["commit_cycle"] = 1
        mutated_stream_hash = hashlib.sha256(
            generator_module._jsonl_bytes([mutated])
        ).hexdigest()
        with self.assertRaisesRegex(AssayInputError, "packet hash"):
            generator_module._validate_dataset_pose_packet_stream(
                [mutated], mutated_stream_hash
            )

    def test_fixture_generation_is_deterministic_score_free_and_excludes_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            pins = build_fixture(dataset)
            first = root / "first"
            second = root / "second"
            manifest = generate_score_free_inputs(
                dataset, first, source_pins=pins, fixture_label="stage4_unit_fixture_v1"
            )
            repeated = generate_score_free_inputs(
                dataset, second, source_pins=pins, fixture_label="stage4_unit_fixture_v1"
            )
            self.assertEqual(manifest, repeated)
            self.assertEqual(manifest["registry"]["window_count"], 24)
            self.assertEqual(manifest["registry"]["query_event_count"], 8914)
            self.assertEqual(manifest["registry"]["forbidden_interval_selected_records"], 0)
            self.assertEqual(manifest["provenance_scope"], "SYNTHETIC_FIXTURE_ONLY")
            self.assertEqual(manifest["timing"]["occurrence_ingress_lanes"], 6)
            self.assertEqual(manifest["timing"]["presentation_lanes"], 2)
            self.assertEqual(manifest["timing"]["event_payload_pose_index_bits"], 14)
            self.assertEqual(manifest["staging_serializer"]["entries"], 6)
            self.assertEqual(manifest["staging_serializer"]["payload_state_bits"], 612)
            self.assertLessEqual(manifest["staging_serializer"]["peak_occupancy"], 6)
            binding = manifest["authoritative_input_binding"]
            binding_body = dict(binding)
            binding_hash = binding_body.pop("binding_sha256")
            self.assertEqual(binding_hash, canonical_sha256(binding_body))
            for name, artifact in manifest["artifacts"].items():
                self.assertEqual(sha256(first / name), artifact["sha256"])
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

            events = jsonl(first / "stage4_events.jsonl")
            self.assertEqual(sum(record["is_query"] for record in events), 8914)
            self.assertTrue(all(0 <= record["presentation_lane"] < 2 for record in events))
            self.assertTrue(
                any(
                    right["event_id"] > left["event_id"] + 1
                    for left, right in zip(events, events[1:])
                )
            )
            self.assertTrue(
                all(
                    not 43320750000 <= record["timestamp_ns"] < 43322000000
                    for record in events
                )
            )
            for record in events[:20]:
                self.assertAlmostEqual(
                    math.sqrt(sum(value * value for value in record["sensor_ray"])), 1.0
                )
            self.assertEqual(events[0]["sensor_ray"], [0.0, 0.0, 1.0])

            batches = jsonl(first / "stage4_occurrence_batches.jsonl")
            snapshots = jsonl(first / "stage4_occurrence_pose_snapshots.jsonl")
            self.assertTrue(any(batch["event_count"] == 5 for batch in batches))
            self.assertTrue(all(1 <= batch["event_count"] <= 6 for batch in batches))
            self.assertEqual(len(snapshots), len(batches))
            by_batch = {}
            by_timestamp = {}
            for event in events:
                key = (event["window_id"], event["occurrence_batch_id"])
                by_batch.setdefault(key, []).append(event)
                by_timestamp.setdefault(
                    (event["window_id"], event["timestamp_ns"]), set()
                ).add(event["occurrence_pose_snapshot_sha256"])
                self.assertGreaterEqual(
                    event["presentation_cycle"], event["occurrence_cycle"] + 1
                )
                packed = int(event["payload_hex"], 16)
                self.assertEqual(packed & ((1 << 24) - 1), event["event_id"])
                self.assertEqual(
                    (packed >> 24) & ((1 << 11) - 1),
                    event["window_event_ordinal"],
                )
                packed_pose_index = (packed >> 88) & ((1 << 14) - 1)
                self.assertEqual(packed_pose_index, event["causal_pose_source_index"])
            for members in by_batch.values():
                self.assertEqual(
                    len({member["occurrence_pose_snapshot_sha256"] for member in members}),
                    1,
                )
                self.assertEqual(
                    [member["occurrence_lane"] for member in members],
                    list(range(len(members))),
                )
            self.assertTrue(all(len(hashes) == 1 for hashes in by_timestamp.values()))
            five_event_batch = next(batch for batch in batches if batch["event_count"] == 5)
            five_members = by_batch[
                (five_event_batch["window_id"], five_event_batch["occurrence_batch_id"])
            ]
            first_presentation = five_event_batch["occurrence_cycle"] + 1
            self.assertEqual(
                [member["presentation_cycle"] for member in five_members],
                [first_presentation, first_presentation, first_presentation + 1,
                 first_presentation + 1, first_presentation + 2],
            )

            dataset_poses = jsonl(first / "stage4_dataset_pose_packets.jsonl")
            self.assertTrue(all(packet["visible_cycle"] == packet["commit_cycle"] + 1 for packet in dataset_poses))
            self.assertTrue(
                all(
                    not 43320750000 <= packet["timestamp_ns"] < 43322000000
                    for packet in dataset_poses
                )
            )
            dataset_pose_stream_hash = sha256(
                first / "stage4_dataset_pose_packets.jsonl"
            )
            pose_packets_by_id = {}
            for packet in dataset_poses:
                packet_body = dict(packet)
                packet_hash = packet_body.pop("packet_sha256")
                self.assertEqual(packet_hash, canonical_sha256(packet_body))
                pose_packets_by_id[(packet["window_id"], packet["source_pose_id"])] = packet
            snapshots_by_batch = {}
            for snapshot_record in snapshots:
                snapshot = dict(snapshot_record)
                snapshot_hash = snapshot.pop("pose_snapshot_sha256")
                self.assertEqual(snapshot_hash, canonical_sha256(snapshot))
                self.assertEqual(len(snapshot["pose_packets"]), 2)
                self.assertEqual(
                    snapshot["dataset_pose_packet_stream_sha256"],
                    dataset_pose_stream_hash,
                )
                eligible = [
                    packet
                    for packet in dataset_poses
                    if packet["window_id"] == snapshot["window_id"]
                    and packet["commit_cycle"] < snapshot["occurrence_cycle"]
                ]
                self.assertGreaterEqual(len(eligible), 2)
                self.assertEqual(
                    [pose["source_pose_id"] for pose in snapshot["pose_packets"]],
                    [packet["source_pose_id"] for packet in eligible[-2:]],
                )
                for pose in snapshot["pose_packets"]:
                    packet = pose_packets_by_id[
                        (snapshot["window_id"], pose["source_pose_id"])
                    ]
                    self.assertEqual(pose["packet_sha256"], packet["packet_sha256"])
                    self.assertLess(pose["commit_cycle"], snapshot["occurrence_cycle"])
                    self.assertLessEqual(pose["visible_cycle"], snapshot["occurrence_cycle"])
                snapshots_by_batch[
                    (snapshot["window_id"], snapshot["occurrence_batch_id"])
                ] = snapshot_record
            for batch in batches:
                authoritative = snapshots_by_batch[
                    (batch["window_id"], batch["occurrence_batch_id"])
                ]
                self.assertEqual(batch["pose_snapshot"], authoritative)
                self.assertEqual(
                    batch["pose_snapshot_sha256"],
                    authoritative["pose_snapshot_sha256"],
                )

            same_edge_packet = next(
                packet
                for packet in dataset_poses
                if packet["window_id"] == five_event_batch["window_id"]
                and packet["timestamp_ns"]
                == five_members[0]["timestamp_ns"]
            )
            self.assertEqual(
                same_edge_packet["commit_cycle"], five_event_batch["occurrence_cycle"]
            )
            five_snapshot = snapshots_by_batch[
                (five_event_batch["window_id"], five_event_batch["occurrence_batch_id"])
            ]
            self.assertNotIn(
                same_edge_packet["source_pose_id"],
                [pose["source_pose_id"] for pose in five_snapshot["pose_packets"]],
            )

            oracle = jsonl(first / "oracle_resampled_groundtruth_1khz.jsonl")
            self.assertTrue(oracle)
            self.assertTrue(
                all(packet["effective_timestamp_ns"] % 1_000_000 == 0 for packet in oracle)
            )
            self.assertTrue(
                all(
                    packet["before_timestamp_ns"]
                    <= packet["effective_timestamp_ns"]
                    < packet["after_timestamp_ns"]
                    for packet in oracle
                )
            )
            schedule = jsonl(first / "stage4_oracle_window_schedule.jsonl")
            self.assertTrue(all(row["commit_cycle"] == row["effective_cycle"] + 1 for row in schedule))
            self.assertTrue(all(row["visible_cycle"] == row["commit_cycle"] + 1 for row in schedule))

            ordered_payload = b"".join(
                (record["payload_hex"] + "\n").encode("ascii") for record in events
            )
            ordered_binding = binding["ordered_102bit_occurrence_records"]
            self.assertEqual(
                hashlib.sha256(ordered_payload).hexdigest(), ordered_binding["sha256"]
            )
            self.assertEqual(
                binding["raw_source_streams"],
                {
                    "events.txt_sha256": pins.events_sha256,
                    "groundtruth.txt_sha256": pins.groundtruth_sha256,
                    "calib.txt_sha256": pins.calibration_sha256,
                },
            )
            for key in (
                "dataset_pose_packet_stream",
                "occurrence_pose_snapshot_stream",
                "oracle_pose_stream",
                "oracle_window_schedule_stream",
            ):
                stream = binding[key]
                self.assertEqual(stream["sha256"], sha256(first / stream["path"]))
            for relative, digest in binding["generator_code_sha256"].items():
                if relative == "generator.py":
                    path = Path(generator_module.__file__)
                elif relative == "source.py":
                    path = Path(generator_module.__file__).with_name("source.py")
                else:
                    path = Path(generator_module.__file__).resolve().parents[1] / relative
                self.assertEqual(digest, sha256(path))
            self.assertRegex(
                binding["runtime"]["python_executable_sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertEqual(
                binding["runtime"]["python_executable_sha256"],
                sha256(Path(sys.executable).resolve()),
            )

            forbidden_words = ("loss", "score", "result")
            for path in first.iterdir():
                text = path.read_text(encoding="ascii").lower()
                if path.name != "stage4_input_manifest.json":
                    self.assertTrue(all(word not in text for word in forbidden_words))

    def test_hash_mutation_fails_before_output_and_nonofficial_pins_need_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            pins = build_fixture(dataset)
            with self.assertRaisesRegex(AssayInputError, "fixture label"):
                generate_score_free_inputs(dataset, root / "unlabeled", source_pins=pins)
            with (dataset / "calib.txt").open("a", encoding="ascii") as stream:
                stream.write(" ")
            output = root / "mutated"
            with self.assertRaisesRegex(AssayInputError, "provenance"):
                generate_score_free_inputs(
                    dataset,
                    output,
                    source_pins=pins,
                    fixture_label="stage4_unit_fixture_v1",
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
