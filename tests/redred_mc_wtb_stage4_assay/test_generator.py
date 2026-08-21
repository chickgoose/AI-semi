from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_stage4_assay import generator as generator_module
from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    run_cycle_model,
)
from benchmarks.redred_mc_wtb_stage4_assay import (
    AssayInputError,
    SourcePins,
    canonicalize_quaternion,
    generate_score_free_inputs,
    shortest_arc_slerp,
    timestamp_to_cycle,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import (
    Calibration,
    EventSample,
    load_calibration,
    sensor_ray,
)


def timestamp_text(timestamp_ns: int) -> str:
    return "%d.%09d" % divmod(timestamp_ns, 1_000_000_000)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="ascii").splitlines()]


def synthetic_event_record(
    event_id: int,
    window_id: str,
    ordinal: int,
    calibration: Calibration,
):
    timestamp_ns = 1_000_000 + ordinal
    event = EventSample(event_id, timestamp_ns, 120, 90, ordinal & 1)
    record = {
        "window_id": window_id,
        "event_id": event_id,
        "event_sequence_tag": event_id % (1 << 24),
        "timestamp_ns": timestamp_ns,
        "x": event.x,
        "y": event.y,
        "polarity": event.polarity,
        "sensor_ray": list(sensor_ray(event, calibration)),
        "is_query": True,
        "window_event_ordinal": ordinal,
        "occurrence_cycle": ordinal,
        "equal_timestamp_cluster_id": event_id,
        "equal_timestamp_cluster_size": 1,
        "occurrence_batch_id": ordinal,
        "occurrence_lane": 0,
        "occurrence_batch_size": 1,
        "occurrence_pose_snapshot_sha256": "1" * 64,
        "causal_pose_source_index": 7,
        "presentation_cycle": ordinal,
        "presentation_lane": 0,
        "serializer_queue_cycles": 0,
    }
    record["payload_hex"] = generator_module._pack_event_payload(record)
    return record


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

    def test_serializer_cycles_and_lanes_equal_cyclemodel_admissions(self) -> None:
        for batch_sizes in ((5,), (4, 2)):
            with self.subTest(batch_sizes=batch_sizes):
                records = []
                batches = []
                events = []
                next_id = 100
                for offset, size in enumerate(batch_sizes):
                    cycle = 2 + offset
                    timestamp_ns = (cycle * 6_500) // 1_000
                    batch_ids = []
                    for _ in range(size):
                        batch_ids.append(next_id)
                        records.append({
                            "event_id": next_id,
                            "occurrence_cycle": cycle,
                        })
                        events.append(Event(next_id, timestamp_ns))
                        next_id += 1
                    batches.append({
                        "occurrence_cycle": cycle,
                        "event_ids": batch_ids,
                    })

                accounting = generator_module._schedule_staging_serializer(
                    records, batches
                )
                cycle_result = run_cycle_model(
                    window_id="serializer-equivalence",
                    window_start_ns=0,
                    arm=Arm.ZOH_FRESHNESS,
                    events=tuple(events),
                    poses=(),
                    synthetic_test_mode=True,
                )

                self.assertEqual(
                    [record["presentation_cycle"] for record in records],
                    [receipt.admission_cycle for receipt in cycle_result.cycle_receipts],
                )
                self.assertEqual(
                    [record["presentation_lane"] for record in records],
                    [receipt.admission_lane for receipt in cycle_result.cycle_receipts],
                )
                self.assertEqual(
                    [record["serializer_queue_cycles"] for record in records],
                    list(cycle_result.common_serializer_cycles),
                )
                self.assertEqual(
                    accounting["peak_staging_occupancy"],
                    cycle_result.peak_ingress_staging_occupancy,
                )
                self.assertEqual(
                    [record["event_id"] for record in records],
                    [receipt.event_id for receipt in cycle_result.cycle_receipts],
                )

    def test_event_sequence_tag_boundaries_wrap_and_full_official_range_id(self) -> None:
        calibration = Calibration(240, 180, 100.0, 100.0, 120.0, 90.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        source_sha256 = "2" * 64
        authority = generator_module._calibration_authority(
            calibration, source_sha256
        )
        limit = 1 << 24
        official_range_id = generator_module.OFFICIAL_SOURCE_PINS.events_line_count - 1
        self.assertGreater(official_range_id, limit)
        event_ids = (limit - 1, limit, limit + 1, official_range_id)
        records = [
            synthetic_event_record(event_id, "one-window", ordinal, calibration)
            for ordinal, event_id in enumerate(event_ids)
        ]

        generator_module._validate_sensor_ray_records(
            records, authority, calibration, source_sha256
        )
        self.assertEqual(
            [record["event_sequence_tag"] for record in records],
            [event_id % limit for event_id in event_ids],
        )
        self.assertEqual([record["event_id"] for record in records], list(event_ids))
        for record in records:
            self.assertEqual(
                int(record["payload_hex"], 16) & (limit - 1),
                record["event_sequence_tag"],
            )
            self.assertEqual(len(record["payload_hex"]), 26)
            self.assertLess(int(record["payload_hex"], 16), 1 << 102)

    def test_event_sequence_tag_mutations_and_exact_field_set_fail_closed(self) -> None:
        calibration = Calibration(240, 180, 100.0, 100.0, 120.0, 90.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        source_sha256 = "3" * 64
        authority = generator_module._calibration_authority(
            calibration, source_sha256
        )
        record = synthetic_event_record((1 << 24) + 9, "mutation-window", 0, calibration)

        tag_only = dict(record)
        tag_only["event_sequence_tag"] = 10
        with self.assertRaisesRegex(AssayInputError, "full event_id modulo"):
            generator_module._validate_sensor_ray_records(
                [tag_only], authority, calibration, source_sha256
            )

        resealed = dict(tag_only)
        resealed["payload_hex"] = generator_module._pack_event_payload(resealed)
        with self.assertRaisesRegex(AssayInputError, "full event_id modulo"):
            generator_module._validate_sensor_ray_records(
                [resealed], authority, calibration, source_sha256
            )

        bool_tag = dict(record)
        bool_tag["event_sequence_tag"] = True
        with self.assertRaisesRegex(AssayInputError, "exact int, not bool"):
            generator_module._validate_sensor_ray_records(
                [bool_tag], authority, calibration, source_sha256
            )

        missing = dict(record)
        missing.pop("event_sequence_tag")
        with self.assertRaisesRegex(AssayInputError, "missing or unexpected fields"):
            generator_module._validate_sensor_ray_records(
                [missing], authority, calibration, source_sha256
            )

    def test_event_sequence_tag_uniqueness_is_per_window(self) -> None:
        calibration = Calibration(240, 180, 100.0, 100.0, 120.0, 90.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        source_sha256 = "4" * 64
        authority = generator_module._calibration_authority(
            calibration, source_sha256
        )
        first = synthetic_event_record(5, "window-a", 0, calibration)
        wrapped = synthetic_event_record((1 << 24) + 5, "window-a", 1, calibration)
        with self.assertRaisesRegex(AssayInputError, "unique within its window"):
            generator_module._validate_sensor_ray_records(
                [first, wrapped], authority, calibration, source_sha256
            )

        other_window = dict(wrapped)
        other_window["window_id"] = "window-b"
        generator_module._validate_sensor_ray_records(
            [first, other_window], authority, calibration, source_sha256
        )

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

        oracle_packet = {
            "oracle_pose_id": 11,
            "effective_timestamp_ns": 11_000_000,
            "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            "before_source_pose_id": 20,
            "before_timestamp_ns": 10_500_000,
            "after_source_pose_id": 21,
            "after_timestamp_ns": 11_500_000,
            "slerp_numerator_ns": 500_000,
            "slerp_denominator_ns": 1_000_000,
            "pose_value_sha256": "2" * 64,
        }
        oracle_packet["packet_sha256"] = canonical_sha256(oracle_packet)
        oracle_stream_hash = hashlib.sha256(
            generator_module._jsonl_bytes([oracle_packet])
        ).hexdigest()
        generator_module._validate_oracle_pose_packet_stream(
            [oracle_packet], oracle_stream_hash
        )
        with self.assertRaisesRegex(AssayInputError, "oracle pose packet stream hash"):
            generator_module._validate_oracle_pose_packet_stream(
                [oracle_packet], "0" * 64
            )
        mutated_oracle = dict(oracle_packet)
        mutated_oracle["before_source_pose_id"] = 19
        mutated_oracle_stream_hash = hashlib.sha256(
            generator_module._jsonl_bytes([mutated_oracle])
        ).hexdigest()
        with self.assertRaisesRegex(AssayInputError, "oracle pose packet hash"):
            generator_module._validate_oracle_pose_packet_stream(
                [mutated_oracle], mutated_oracle_stream_hash
            )
        with self.assertRaisesRegex(AssayInputError, "oracle pose packet hash"):
            generator_module._oracle_schedule(
                [mutated_oracle],
                mutated_oracle_stream_hash,
                [
                    {
                        "window_id": "fixture",
                        "warmup_start_ns_inclusive": 10_000_000,
                        "query_end_ns_exclusive": 12_000_000,
                    }
                ],
                6_500,
                1_000_000,
                1,
                1,
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
            self.assertEqual(
                manifest["event_payload_layout_lsb_first"][0],
                {
                    "field": "event_sequence_tag",
                    "source": "event_id_mod_2^24",
                    "bits": 24,
                },
            )
            self.assertEqual(manifest["staging_serializer"]["entries"], 6)
            self.assertEqual(manifest["staging_serializer"]["payload_state_bits"], 612)
            self.assertLessEqual(manifest["staging_serializer"]["peak_occupancy"], 6)
            summaries_by_id = {
                summary["window_id"]: summary for summary in manifest["windows"]
            }
            self.assertEqual(len(summaries_by_id), len(tuple(window_registry())))
            for registry_row in window_registry():
                summary = summaries_by_id[registry_row["window_id"]]
                for bound in (
                    "warmup_start_ns_inclusive",
                    "query_start_ns_inclusive",
                    "query_end_ns_exclusive",
                ):
                    self.assertEqual(summary[bound], registry_row[bound])
            binding = manifest["authoritative_input_binding"]
            binding_body = dict(binding)
            binding_hash = binding_body.pop("binding_sha256")
            self.assertEqual(binding_hash, canonical_sha256(binding_body))
            calibration_authority = binding["calibration_model"]
            calibration_body = dict(calibration_authority)
            calibration_hash = calibration_body.pop("authority_sha256")
            self.assertEqual(calibration_hash, canonical_sha256(calibration_body))
            self.assertEqual(calibration_body["source_path"], "calib.txt")
            self.assertEqual(calibration_body["source_sha256"], pins.calibration_sha256)
            self.assertEqual(
                calibration_body["sensor_ray_generator_rule"],
                "radtan_inverse_newton_then_normalized_sensor_ray",
            )
            self.assertEqual(
                tuple(calibration_body["model"]),
                (
                    "width",
                    "height",
                    "fx",
                    "fy",
                    "cx",
                    "cy",
                    "k1",
                    "k2",
                    "p1",
                    "p2",
                    "k3",
                ),
            )
            parsed_calibration = load_calibration(dataset / "calib.txt")
            authority_calibration = Calibration(**calibration_body["model"])
            self.assertEqual(authority_calibration, parsed_calibration)
            self.assertEqual(
                manifest["event_inputs"]["calibration_authority_sha256"],
                calibration_hash,
            )
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
            for record in events:
                payload = int(record["payload_hex"], 16)
                payload_event = EventSample(
                    event_id=record["event_id"],
                    timestamp_ns=(payload >> 35) & ((1 << 36) - 1),
                    x=(payload >> 71) & ((1 << 8) - 1),
                    y=(payload >> 79) & ((1 << 8) - 1),
                    polarity=(payload >> 87) & 1,
                )
                self.assertEqual(record["x"], payload_event.x)
                self.assertEqual(record["y"], payload_event.y)
                self.assertEqual(
                    record["sensor_ray"],
                    list(sensor_ray(payload_event, authority_calibration)),
                )

            mutated_events = list(events)
            mutated_event = dict(mutated_events[0])
            mutated_event["sensor_ray"] = list(mutated_event["sensor_ray"])
            mutated_event["sensor_ray"][0] += 0.001
            mutated_events[0] = mutated_event
            with self.assertRaisesRegex(AssayInputError, "sensor ray differs"):
                generator_module._validate_sensor_ray_records(
                    mutated_events,
                    calibration_authority,
                    parsed_calibration,
                    pins.calibration_sha256,
                )

            for field, bool_alias in (("x", True), ("polarity", False)):
                with self.subTest(bool_alias_field=field):
                    bool_events = list(events)
                    bool_event = dict(bool_events[0])
                    bool_event[field] = bool_alias
                    bool_events[0] = bool_event
                    with self.assertRaisesRegex(AssayInputError, "exact int, not bool"):
                        generator_module._validate_sensor_ray_records(
                            bool_events,
                            calibration_authority,
                            parsed_calibration,
                            pins.calibration_sha256,
                        )

            for invalid_component in (True, 0, float("inf")):
                with self.subTest(invalid_ray_component=invalid_component):
                    invalid_ray_events = list(events)
                    invalid_ray_event = dict(invalid_ray_events[0])
                    invalid_ray_event["sensor_ray"] = list(
                        invalid_ray_event["sensor_ray"]
                    )
                    invalid_ray_event["sensor_ray"][0] = invalid_component
                    invalid_ray_events[0] = invalid_ray_event
                    with self.assertRaisesRegex(
                        AssayInputError, "finite floats, not bool"
                    ):
                        generator_module._validate_sensor_ray_records(
                            invalid_ray_events,
                            calibration_authority,
                            parsed_calibration,
                            pins.calibration_sha256,
                        )

            bool_payload_events = list(events)
            bool_payload_event = dict(bool_payload_events[0])
            bool_payload_event["payload_hex"] = True
            bool_payload_events[0] = bool_payload_event
            with self.assertRaisesRegex(AssayInputError, "canonical 102-bit hex"):
                generator_module._validate_sensor_ray_records(
                    bool_payload_events,
                    calibration_authority,
                    parsed_calibration,
                    pins.calibration_sha256,
                )

            payload_mismatch_events = list(events)
            payload_mismatch_event = dict(payload_mismatch_events[0])
            payload_mismatch_event["payload_hex"] = "%026x" % (
                int(payload_mismatch_event["payload_hex"], 16) ^ (1 << 71)
            )
            payload_mismatch_events[0] = payload_mismatch_event
            with self.assertRaisesRegex(AssayInputError, "payload-bound value"):
                generator_module._validate_sensor_ray_records(
                    payload_mismatch_events,
                    calibration_authority,
                    parsed_calibration,
                    pins.calibration_sha256,
                )

            for unexpected_field in ("score", "decision", "quality"):
                with self.subTest(unexpected_field=unexpected_field):
                    unexpected_events = list(events)
                    unexpected_event = dict(unexpected_events[0])
                    unexpected_event[unexpected_field] = 0.0
                    unexpected_events[0] = unexpected_event
                    with self.assertRaisesRegex(AssayInputError, "unexpected fields"):
                        generator_module._validate_sensor_ray_records(
                            unexpected_events,
                            calibration_authority,
                            parsed_calibration,
                            pins.calibration_sha256,
                        )

            mutated_calibration_authority = json.loads(
                json.dumps(calibration_authority)
            )
            mutated_calibration_authority["model"]["fx"] += 1.0
            mutated_calibration_body = dict(mutated_calibration_authority)
            mutated_calibration_body.pop("authority_sha256")
            mutated_calibration_authority["authority_sha256"] = canonical_sha256(
                mutated_calibration_body
            )
            with self.assertRaisesRegex(AssayInputError, "parsed source model"):
                generator_module._validate_sensor_ray_records(
                    events,
                    mutated_calibration_authority,
                    parsed_calibration,
                    pins.calibration_sha256,
                )

            substituted_source_authority = json.loads(
                json.dumps(calibration_authority)
            )
            substituted_source_authority["source_sha256"] = "0" * 64
            substituted_source_body = dict(substituted_source_authority)
            substituted_source_body.pop("authority_sha256")
            substituted_source_authority["authority_sha256"] = canonical_sha256(
                substituted_source_body
            )
            with self.assertRaisesRegex(AssayInputError, "parsed source model"):
                generator_module._validate_sensor_ray_records(
                    events,
                    substituted_source_authority,
                    parsed_calibration,
                    pins.calibration_sha256,
                )

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
                    event["presentation_cycle"], event["occurrence_cycle"]
                )
                packed = int(event["payload_hex"], 16)
                self.assertEqual(
                    packed & ((1 << 24) - 1), event["event_sequence_tag"]
                )
                self.assertEqual(
                    event["event_sequence_tag"], event["event_id"] % (1 << 24)
                )
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
            first_presentation = five_event_batch["occurrence_cycle"]
            self.assertEqual(
                [member["presentation_cycle"] for member in five_members],
                [first_presentation, first_presentation, first_presentation + 1,
                 first_presentation + 1, first_presentation + 2],
            )

            dataset_poses = jsonl(first / "stage4_dataset_pose_packets.jsonl")
            self.assertTrue(all(packet["visible_cycle"] == packet["commit_cycle"] + 1 for packet in dataset_poses))
            self.assertTrue(any(packet["commit_cycle"] < 0 for packet in dataset_poses))
            self.assertTrue(
                all(packet["arrival_cycle"] == packet["commit_cycle"] for packet in dataset_poses)
            )
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
            oracle_by_id = {}
            for packet in oracle:
                packet_body = dict(packet)
                packet_hash = packet_body.pop("packet_sha256")
                self.assertEqual(packet_hash, canonical_sha256(packet_body))
                oracle_by_id[packet["oracle_pose_id"]] = packet
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
            self.assertTrue(
                all(
                    row["packet_sha256"]
                    == oracle_by_id[row["oracle_pose_id"]]["packet_sha256"]
                    for row in schedule
                )
            )

            ordered_payload = b"".join(
                (record["payload_hex"] + "\n").encode("ascii") for record in events
            )
            ordered_binding = binding["ordered_102bit_occurrence_records"]
            self.assertEqual(
                hashlib.sha256(ordered_payload).hexdigest(), ordered_binding["sha256"]
            )
            self.assertEqual(
                manifest["staging_serializer"]["cycle_order"],
                "atomically_capture_occurrence_batch_then_present_up_to_two_staged",
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
            ordered_oracle_packet_sha256 = canonical_sha256(
                [packet["packet_sha256"] for packet in oracle]
            )
            self.assertEqual(
                binding["oracle_pose_stream"]["packet_sha256_rule"],
                "canonical_sha256_of_record_without_packet_sha256",
            )
            self.assertEqual(
                binding["oracle_pose_stream"]["ordered_packet_sha256"],
                ordered_oracle_packet_sha256,
            )
            self.assertEqual(
                manifest["oracle_resampled_groundtruth_1khz"][
                    "ordered_packet_sha256"
                ],
                ordered_oracle_packet_sha256,
            )
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

            forbidden_output_fields = {
                "loss",
                "losses",
                "score",
                "scores",
                "result",
                "results",
                "decision",
                "decisions",
                "quality",
                "qualities",
                "ranking",
                "disposition",
            }

            def assert_no_forbidden_output_fields(value) -> None:
                if isinstance(value, dict):
                    for key, child in value.items():
                        self.assertNotIn(key.lower(), forbidden_output_fields)
                        assert_no_forbidden_output_fields(child)
                elif isinstance(value, list):
                    for child in value:
                        assert_no_forbidden_output_fields(child)

            for path in first.iterdir():
                if path.name == "stage4_input_manifest.json":
                    content = json.loads(path.read_text(encoding="ascii"))
                else:
                    content = jsonl(path)
                assert_no_forbidden_output_fields(content)

    def test_calibration_hash_and_parse_share_one_immutable_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            pins = build_fixture(dataset)
            original_validate_sources = generator_module.validate_sources
            captured_sources = []

            def capture_then_replace(dataset_dir, source_pins):
                sources = original_validate_sources(dataset_dir, source_pins)
                captured_sources.append(sources)
                (Path(dataset_dir) / "calib.txt").write_bytes(b"mutated after capture\n")
                return sources

            with mock.patch.object(
                generator_module,
                "validate_sources",
                side_effect=capture_then_replace,
            ):
                manifest = generate_score_free_inputs(
                    dataset,
                    root / "output",
                    source_pins=pins,
                    fixture_label="calibration_capture_fixture_v1",
                )
            authority = manifest["authoritative_input_binding"]["calibration_model"]
            self.assertEqual(len(captured_sources), 1)
            captured = captured_sources[0]
            self.assertIs(type(captured.calibration_bytes), bytes)
            captured_sha256 = hashlib.sha256(captured.calibration_bytes).hexdigest()
            self.assertEqual(captured.calibration_sha256, captured_sha256)
            self.assertEqual(authority["source_sha256"], captured_sha256)
            self.assertEqual(captured_sha256, pins.calibration_sha256)
            self.assertEqual(
                authority["model"],
                {
                    "width": 240,
                    "height": 180,
                    "fx": 100.0,
                    "fy": 100.0,
                    "cx": 120.0,
                    "cy": 90.0,
                    "k1": 0.0,
                    "k2": 0.0,
                    "p1": 0.0,
                    "p2": 0.0,
                    "k3": 0.0,
                },
            )

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
