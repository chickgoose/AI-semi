from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_mc_wtb_stage4_assay import generate_score_free_inputs
from benchmarks.redred_mc_wtb_stage4_assay.source import (
    Calibration,
    EventSample,
    sensor_ray,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import Arm, run_cycle_model
from benchmarks.redred_mc_wtb_stage4_integration import adapter as integration
from benchmarks.redred_mc_wtb_stage4_integration import (
    IntegrationError,
    build_all_arm_window,
    build_window_cycle_inputs,
    load_assay_bundle,
)
from benchmarks.redred_mc_wtb_stage4_scoring import ScoreBoundaryEvidence
from tests.redred_mc_wtb_stage4_assay.test_generator import (
    build_fixture as build_assay_source_fixture,
)


HASH_A = "1" * 64
HASH_B = "2" * 64
HASH_C = "3" * 64
START = 1_000_000_000
END = START + 2_000_000
WINDOW = "synthetic_stage4_integration_window"


def pose_hash(pose_id, timestamp_ns, quaternion):
    return canonical_sha256({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion),
    })


def packet(pose_id, delta_ns, quaternion):
    timestamp = START + delta_ns
    commit = (delta_ns * 1000 + 6499) // 6500
    row = {
        "window_id": WINDOW,
        "source_pose_id": pose_id,
        "timestamp_ns": timestamp,
        "quaternion_xyzw": list(quaternion),
        "pose_value_sha256": pose_hash(pose_id, timestamp, quaternion),
        "arrival_cycle": commit,
        "commit_cycle": commit,
        "visible_cycle": commit + 1,
        "visible_at_window_start": commit + 1 <= 0,
    }
    row["packet_sha256"] = canonical_sha256(row)
    return row


def pack_event(event_id, ordinal, timestamp_ns, x, y, polarity, pose_id):
    fields = (
        (event_id % (1 << 24), 24),
        (ordinal, 11),
        (timestamp_ns, 36),
        (x, 8),
        (y, 8),
        (polarity, 1),
        (pose_id, 14),
    )
    value = 0
    shift = 0
    for field, width in fields:
        value |= field << shift
        shift += width
    assert shift == 102
    return "%026x" % value


def write_jsonl(path, rows):
    path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))


def artifact(path, rows):
    raw = path.read_bytes()
    return {
        "path": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": len(rows),
        "size_bytes": len(raw),
    }


def reseal_artifact_and_manifest(root, name, rows):
    path = root / name
    write_jsonl(path, rows)
    manifest_path = root / "stage4_input_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][name] = artifact(path, rows)
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    return hashlib.sha256(manifest_bytes).hexdigest()


def build_artifacts(
    root, *, include_limits=True, signed_history=True, event_id_offset=0
):
    calibration = Calibration(
        240,
        180,
        100.0,
        100.0,
        120.0,
        90.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    )
    q0 = (0.0, 0.0, 0.0, 1.0)
    q1 = (0.0, 0.0, math.sin(0.01), math.cos(0.01))
    q2 = (0.0, 0.0, math.sin(0.02), math.cos(0.02))
    q3 = (0.0, 0.0, math.sin(0.03), math.cos(0.03))
    dataset = [
        packet(10, -7 if signed_history else 0, q0),
        packet(11, 6, q1),
        packet(12, 20, q2),
        packet(13, 30, q3),
    ]
    dataset_path = root / "stage4_dataset_pose_packets.jsonl"
    write_jsonl(dataset_path, dataset)
    dataset_stream_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()

    oracle_pose_id = START // 1_000_000
    oracle = [{
        "oracle_pose_id": oracle_pose_id,
        "effective_timestamp_ns": START,
        "quaternion_xyzw": list(q1),
        "before_source_pose_id": 9,
        "before_timestamp_ns": START - 1,
        "after_source_pose_id": 10,
        "after_timestamp_ns": START + 1,
        "slerp_numerator_ns": 1,
        "slerp_denominator_ns": 2,
        "pose_value_sha256": pose_hash(oracle_pose_id, START, q1),
    }]
    oracle[0]["packet_sha256"] = canonical_sha256(oracle[0])
    oracle_schedule = [{
        "window_id": WINDOW,
        "oracle_pose_id": oracle_pose_id,
        "effective_timestamp_ns": START,
        "pose_value_sha256": oracle[0]["pose_value_sha256"],
        "packet_sha256": oracle[0]["packet_sha256"],
        "effective_cycle": 0,
        "commit_cycle": 1,
        "visible_cycle": 2,
    }]

    event_specs = (
        (event_id_offset + 100, 12, False, 2, 0, 2, 0),
        (event_id_offset + 101, 12, False, 2, 1, 2, 1),
        (event_id_offset + 102, 18, True, 3, 0, 3, 0),
        (event_id_offset + 103, 25, True, 4, 0, 4, 0),
    )
    snapshots = []
    snapshot_by_batch = {}
    for batch_id, occurrence, timestamp_range in (
        (0, 2, (START + 12, START + 12)),
        (1, 3, (START + 18, START + 18)),
        (2, 4, (START + 25, START + 25)),
    ):
        selected = dataset[:2]
        snapshot = {
            "schema": "redred.mc_wtb.stage4_occurrence_pose_snapshot/v1",
            "window_id": WINDOW,
            "occurrence_batch_id": batch_id,
            "occurrence_cycle": occurrence,
            "event_timestamp_range_ns": list(timestamp_range),
            "dataset_pose_packet_stream_sha256": dataset_stream_hash,
            "selection_rule": "two_latest_packets_with_commit_cycle_strictly_before_occurrence_cycle",
            "pose_packets": [
                {
                    "source_pose_id": row["source_pose_id"],
                    "timestamp_ns": row["timestamp_ns"],
                    "quaternion_xyzw": row["quaternion_xyzw"],
                    "pose_value_sha256": row["pose_value_sha256"],
                    "packet_sha256": row["packet_sha256"],
                    "commit_cycle": row["commit_cycle"],
                    "visible_cycle": row["visible_cycle"],
                }
                for row in selected
            ],
        }
        snapshot["pose_snapshot_sha256"] = canonical_sha256(snapshot)
        snapshots.append(snapshot)
        snapshot_by_batch[batch_id] = snapshot

    events = []
    batch_for_ordinal = (0, 0, 1, 2)
    batch_sizes = (2, 2, 1, 1)
    for ordinal, spec in enumerate(event_specs):
        event_id, delta, is_query, occurrence, lane, presentation, presentation_lane = spec
        batch_id = batch_for_ordinal[ordinal]
        x = 120 + ordinal
        y = 90
        event_sample = EventSample(event_id, START + delta, x, y, ordinal & 1)
        events.append({
            "window_id": WINDOW,
            "event_id": event_id,
            "event_sequence_tag": event_id % (1 << 24),
            "timestamp_ns": START + delta,
            "x": x,
            "y": y,
            "polarity": ordinal & 1,
            "sensor_ray": list(sensor_ray(event_sample, calibration)),
            "is_query": is_query,
            "window_event_ordinal": ordinal,
            "occurrence_cycle": occurrence,
            "equal_timestamp_cluster_id": 100 if ordinal < 2 else event_id,
            "equal_timestamp_cluster_size": 2 if ordinal < 2 else 1,
            "occurrence_batch_id": batch_id,
            "occurrence_lane": lane,
            "occurrence_batch_size": batch_sizes[ordinal],
            "occurrence_pose_snapshot_sha256": snapshot_by_batch[batch_id]["pose_snapshot_sha256"],
            "causal_pose_source_index": 11,
            "payload_hex": pack_event(
                event_id, ordinal, START + delta, x, y, ordinal & 1, 11
            ),
            "presentation_cycle": presentation,
            "presentation_lane": presentation_lane,
            "serializer_queue_cycles": presentation - occurrence,
        })
    batches = []
    for batch_id, member_indexes in ((0, (0, 1)), (1, (2,)), (2, (3,))):
        members = [events[index] for index in member_indexes]
        snapshots_row = snapshot_by_batch[batch_id]
        batches.append({
            "window_id": WINDOW,
            "occurrence_batch_id": batch_id,
            "occurrence_cycle": members[0]["occurrence_cycle"],
            "event_count": len(members),
            "event_ids": [row["event_id"] for row in members],
            "payload_hex": [row["payload_hex"] for row in members],
            "pose_snapshot": snapshots_row,
            "pose_snapshot_sha256": snapshots_row["pose_snapshot_sha256"],
        })

    rows_by_file = {
        "stage4_events.jsonl": events,
        "stage4_occurrence_batches.jsonl": batches,
        "stage4_occurrence_pose_snapshots.jsonl": snapshots,
        "stage4_dataset_pose_packets.jsonl": dataset,
        "oracle_resampled_groundtruth_1khz.jsonl": oracle,
        "stage4_oracle_window_schedule.jsonl": oracle_schedule,
    }
    for name, rows in rows_by_file.items():
        if name != "stage4_dataset_pose_packets.jsonl":
            write_jsonl(root / name, rows)
    artifacts = dict(
        (name, artifact(root / name, rows)) for name, rows in rows_by_file.items()
    )
    ordered_raw = b"".join((row["payload_hex"] + "\n").encode("ascii") for row in events)
    ordered_tags = [row["event_sequence_tag"] for row in events]
    min_source_event_id = min(row["event_id"] for row in events)
    max_source_event_id = max(row["event_id"] for row in events)
    source_event_id_span = max_source_event_id - min_source_event_id
    window_tag_evidence = [{
        "window_id": WINDOW,
        "min_source_event_id": min_source_event_id,
        "max_source_event_id": max_source_event_id,
        "source_event_id_span": source_event_id_span,
        "ordered_event_sequence_tags_sha256": canonical_sha256(ordered_tags),
    }]
    window_tag_evidence_sha256 = canonical_sha256(window_tag_evidence)
    authority = {
        "schema": "redred.mc_wtb.stage4_authoritative_input_binding/v1",
        "ordered_102bit_occurrence_records": {
            "serialization": "lowercase_26_hex_digits_plus_lf_in_source_event_order",
            "record_count": len(events),
            "sha256": hashlib.sha256(ordered_raw).hexdigest(),
            "ordered_event_ids_sha256": canonical_sha256([row["event_id"] for row in events]),
        },
        "event_sequence_tags": {
            "derivation": "event_id_mod_2^24",
            "bits": 24,
            "event_sequence_tag_count": len(ordered_tags),
            "event_sequence_tags_globally_unique": True,
            "ordered_event_sequence_tags_sha256": canonical_sha256(ordered_tags),
            "window_reset_domains": True,
            "window_source_event_id_span_limit_exclusive": 1 << 23,
            "window_source_event_id_evidence_sha256": (
                window_tag_evidence_sha256
            ),
        },
        "raw_source_streams": {
            "events.txt_sha256": HASH_A,
            "groundtruth.txt_sha256": HASH_B,
            "calib.txt_sha256": HASH_C,
        },
        "calibration_model": {
            "schema": "redred.mc_wtb.stage4_calibration_authority/v1",
            "source_path": "calib.txt",
            "source_sha256": HASH_C,
            "sensor_ray_generator_rule": (
                "radtan_inverse_newton_then_normalized_sensor_ray"
            ),
            "model": {
                name: getattr(calibration, name)
                for name in (
                    "width", "height", "fx", "fy", "cx", "cy",
                    "k1", "k2", "p1", "p2", "k3",
                )
            },
        },
        "dataset_pose_packet_stream": {
            "path": "stage4_dataset_pose_packets.jsonl",
            "sha256": artifacts["stage4_dataset_pose_packets.jsonl"]["sha256"],
            "record_count": len(dataset),
        },
        "occurrence_pose_snapshot_stream": {
            "path": "stage4_occurrence_pose_snapshots.jsonl",
            "sha256": artifacts["stage4_occurrence_pose_snapshots.jsonl"]["sha256"],
            "record_count": len(snapshots),
        },
        "oracle_pose_stream": {
            "path": "oracle_resampled_groundtruth_1khz.jsonl",
            "sha256": artifacts["oracle_resampled_groundtruth_1khz.jsonl"]["sha256"],
            "record_count": len(oracle),
            "packet_sha256_rule": (
                "canonical_sha256_of_record_without_packet_sha256"
            ),
            "ordered_packet_sha256": canonical_sha256(
                [row["packet_sha256"] for row in oracle]
            ),
        },
        "oracle_window_schedule_stream": {
            "path": "stage4_oracle_window_schedule.jsonl",
            "sha256": artifacts["stage4_oracle_window_schedule.jsonl"]["sha256"],
            "record_count": len(oracle_schedule),
        },
        "generator_code_sha256": {"fixture.py": HASH_A},
        "runtime": {"identity_sha256": HASH_B, "python_executable_sha256": HASH_C},
    }
    calibration_body = authority["calibration_model"]
    calibration_body["authority_sha256"] = canonical_sha256(calibration_body)
    authority["binding_sha256"] = canonical_sha256(authority)
    contract = load_comparison_contract()
    window = {
        "window_id": WINDOW,
        "selected_event_count": 4,
        "query_event_count": 2,
        "min_source_event_id": min_source_event_id,
        "max_source_event_id": max_source_event_id,
        "source_event_id_span": source_event_id_span,
        "ordered_event_sequence_tags_sha256": canonical_sha256(ordered_tags),
    }
    if include_limits:
        window.update(
            warmup_start_ns_inclusive=START,
            query_start_ns_inclusive=START + 15,
            query_end_ns_exclusive=END,
        )
    manifest = {
        "schema": "redred.mc_wtb.stage4_score_free_inputs/v2",
        "content_class": "DECISION_INPUTS_ONLY_NO_ARM_TRANSFORMS",
        "provenance_scope": "SYNTHETIC_FIXTURE_ONLY",
        "fixture_label": "stage4_integration_unit_fixture_v1",
        "comparison_contract_sha256": contract.canonical_sha256,
        "generator_runtime": {
            "generator_code_sha256": {"fixture.py": HASH_A},
            "runtime": {"identity_sha256": HASH_B, "python_executable_sha256": HASH_C},
        },
        "authoritative_input_binding": authority,
        "registry": {
            "window_count": 1,
            "sha256": contract.registry["sha256"],
            "query_event_count": 2,
            "forbidden_interval_ns": list(contract.registry["forbidden_interval_ns"]),
            "forbidden_interval_selected_records": 0,
        },
        "source": {
            "sequence": "SYNTHETIC_FIXTURE_ONLY",
            "events_sha256": HASH_A,
            "groundtruth_sha256": HASH_B,
            "calibration_sha256": HASH_C,
        },
        "event_inputs": {
            "ray_model": "radtan_inverse_newton_then_normalized_sensor_ray",
            "calibration_authority_sha256": authority["calibration_model"][
                "authority_sha256"
            ],
            "selected_event_count": len(events),
            "event_sequence_tag_count": len(ordered_tags),
            "event_sequence_tags_globally_unique": True,
            "ordered_event_sequence_tags_sha256": canonical_sha256(ordered_tags),
            "window_reset_domains": True,
            "window_source_event_id_span_limit_exclusive": 1 << 23,
            "window_source_event_id_evidence_sha256": (
                window_tag_evidence_sha256
            ),
            "occurrence_batch_count": len(batches),
            "ordered_selected_event_ids_sha256": canonical_sha256(
                [row["event_id"] for row in events]
            ),
            "ordered_query_event_ids_sha256": canonical_sha256(
                [row["event_id"] for row in events if row["is_query"]]
            ),
            "ordered_102bit_records_sha256": hashlib.sha256(ordered_raw).hexdigest(),
        },
        "windows": [window],
        "artifacts": artifacts,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (root / "stage4_input_manifest.json").write_bytes(manifest_bytes)
    return hashlib.sha256(manifest_bytes).hexdigest()


class IntegrationTests(unittest.TestCase):
    def test_loader_closes_every_fixture_artifact_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            self.assertEqual(len(bundle.events), 4)
            self.assertEqual(len(bundle.snapshots), 3)
            self.assertEqual(bundle.manifest_sha256, expected)
            with self.assertRaisesRegex(IntegrationError, "caller seal"):
                load_assay_bundle(root, expected_manifest_sha256="0" * 64)
            with (root / "stage4_events.jsonl").open("ab") as stream:
                stream.write(b" ")
            with self.assertRaisesRegex(IntegrationError, "artifact hash"):
                load_assay_bundle(root, expected_manifest_sha256=expected)

    def test_loader_requires_exact_event_fields_and_exact_bool_query_label(self):
        cases = (
            ("extra_field", 0, "event record field set differs"),
            ("is_query", 1, "is_query must be an exact bool"),
            ("event_sequence_tag", True, "must be an integer"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                build_artifacts(root)
                event_path = root / "stage4_events.jsonl"
                rows = [json.loads(line) for line in event_path.read_text().splitlines()]
                rows[0][field] = value
                expected = reseal_artifact_and_manifest(
                    root, "stage4_events.jsonl", rows
                )
                with self.assertRaisesRegex(IntegrationError, message):
                    load_assay_bundle(root, expected_manifest_sha256=expected)

    def test_payload_tag_wrap_retains_full_event_ids_in_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            offset = 1 << 24
            expected = build_artifacts(root, event_id_offset=offset)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            self.assertEqual(
                tuple(row["event_sequence_tag"] for row in bundle.events),
                (100, 101, 102, 103),
            )
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            integrated = build_all_arm_window(bundle, WINDOW)
            self.assertEqual(
                tuple(event.event_id for event in inputs.events),
                tuple(offset + value for value in (100, 101, 102, 103)),
            )
            for sealed in integrated.values():
                self.assertEqual(
                    tuple(record.event_id for record in sealed.query_records),
                    (offset + 102, offset + 103),
                )

    def test_window_rejects_duplicate_sequence_tags_after_bundle_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            rows = list(bundle.events)
            duplicate = dict(rows[1])
            duplicate["event_id"] = (1 << 24) + rows[0]["event_sequence_tag"]
            duplicate["event_sequence_tag"] = rows[0]["event_sequence_tag"]
            payload = int(duplicate["payload_hex"], 16)
            duplicate["payload_hex"] = "%026x" % (
                (payload & ~((1 << 24) - 1)) | duplicate["event_sequence_tag"]
            )
            rows[1] = duplicate
            with self.assertRaisesRegex(
                IntegrationError, "not globally unique"
            ):
                build_window_cycle_inputs(replace(bundle, events=tuple(rows)), WINDOW)

    def test_selected_tags_reject_cross_window_collision_and_window_span(self):
        first = {
            "window_id": "window-a",
            "event_id": 7,
            "event_sequence_tag": 7,
        }
        cross_window_collision = {
            "window_id": "window-b",
            "event_id": (1 << 24) + 7,
            "event_sequence_tag": 7,
        }
        with self.assertRaisesRegex(IntegrationError, "not globally unique"):
            integration._validate_window_event_tags(
                (first, cross_window_collision)
            )

        at_valid_boundary = (
            first,
            {
                "window_id": "window-a",
                "event_id": 7 + (1 << 23) - 1,
                "event_sequence_tag": 7 + (1 << 23) - 1,
            },
        )
        integration._validate_window_event_tags(at_valid_boundary)
        over_boundary = (
            first,
            {
                "window_id": "window-a",
                "event_id": 7 + (1 << 23),
                "event_sequence_tag": 7 + (1 << 23),
            },
        )
        with self.assertRaisesRegex(IntegrationError, r"less than 2\^23"):
            integration._validate_window_event_tags(over_boundary)

    def test_manifest_tag_count_hash_and_window_bounds_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            mutations = (
                (
                    ("event_inputs", "event_sequence_tag_count"),
                    3,
                    "manifest-wide",
                ),
                (
                    (
                        "event_inputs",
                        "ordered_event_sequence_tags_sha256",
                    ),
                    "0" * 64,
                    "manifest-wide",
                ),
                (
                    ("event_inputs", "window_source_event_id_evidence_sha256"),
                    "0" * 64,
                    "window event tag evidence hash",
                ),
                (
                    ("windows", 0, "source_event_id_span"),
                    1 << 23,
                    "per-window",
                ),
                (
                    ("windows", 0, "ordered_event_sequence_tags_sha256"),
                    "0" * 64,
                    "per-window",
                ),
                (
                    (
                        "authoritative_input_binding",
                        "event_sequence_tags",
                        "ordered_event_sequence_tags_sha256",
                    ),
                    "0" * 64,
                    "authoritative",
                ),
            )
            for path, value, message in mutations:
                with self.subTest(path=path):
                    manifest = json.loads(json.dumps(bundle.manifest))
                    target = manifest
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = value
                    with self.assertRaisesRegex(IntegrationError, message):
                        build_window_cycle_inputs(
                            replace(bundle, manifest=manifest), WINDOW
                        )

    def test_live_full_event_id_span_is_inclusive_and_count_is_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            result = run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.ZOH_FRESHNESS,
                events=inputs.events,
                poses=inputs.dataset_poses,
            )

            def with_span(span):
                event_ids = (0, span, 1, 2)
                return replace(
                    result,
                    cycle_receipts=tuple(
                        replace(receipt, event_id=event_ids[index])
                        for index, receipt in enumerate(result.cycle_receipts)
                    ),
                )

            integration._validate_live_event_id_scope(
                with_span((1 << 23) - 1), 1032, 1 << 23
            )
            with self.assertRaisesRegex(IntegrationError, r"less than 2\^23"):
                integration._validate_live_event_id_scope(
                    with_span(1 << 23), 1032, 1 << 23
                )
            with self.assertRaisesRegex(IntegrationError, "maximum live count"):
                integration._validate_live_event_id_scope(
                    with_span((1 << 23) - 1), 1, 1 << 23
                )

    def test_runtime_contract_accounting_validation_is_fail_closed(self):
        frozen = load_comparison_contract().as_dict()["score_free_accounting"]
        self.assertIs(
            integration._validate_score_free_accounting_contract(frozen), frozen
        )
        mutations = (
            (
                ("raw_reason_classification_by_arm", "delayed_exact", "invalid_pose"),
                "invalid_pose_bypass",
                "reason taxonomy",
            ),
            (
                ("common_state_envelope", "components_bits", "delayed_fifo_payload"),
                104447,
                "11-component state",
            ),
            (
                ("common_state_envelope", "maximum_simultaneous_live_references"),
                1033,
                "11-component state",
            ),
            (
                ("pose_interface", "pose_bandwidth_bits_per_second"),
                191999,
                "pose bandwidth",
            ),
            (
                ("query_event_bandwidth", "record_bits"),
                101,
                "102-bit event",
            ),
            (
                (
                    "delayed_fifo",
                    "minimum_zero_loss_buffer_entries",
                    "bounded_peak_authoritative_if_any_fifo_full_forced_bypass",
                ),
                True,
                "FIFO conditional rule",
            ),
        )
        for path, value, message in mutations:
            with self.subTest(path=path):
                changed = json.loads(json.dumps(frozen))
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(IntegrationError, message):
                    integration._validate_score_free_accounting_contract(changed)

    def test_runtime_event_identity_contract_retains_36bit_timestamp(self):
        timing = load_comparison_contract().as_dict()["timing"]
        identity = integration._validate_event_record_identity_contract(timing)
        self.assertEqual(identity["transport_sequence_tag_bits"], 24)
        self.assertEqual(identity["serial_number_half_range"], 1 << 23)
        self.assertEqual(identity["timestamp_bits"], 36)
        changed = json.loads(json.dumps(timing))
        changed["event_record_identity"]["timestamp_bits"] = 35
        with self.assertRaisesRegex(
            IntegrationError, "event record identity contract differs"
        ):
            integration._validate_event_record_identity_contract(changed)

    def test_pose_value_is_recomputed_after_outer_hashes_are_resealed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            pose_path = root / "stage4_dataset_pose_packets.jsonl"
            rows = [json.loads(line) for line in pose_path.read_text().splitlines()]
            rows[0]["quaternion_xyzw"] = [0.0, 0.0, 0.1, 0.995]
            body = dict(rows[0])
            body.pop("packet_sha256")
            rows[0]["packet_sha256"] = canonical_sha256(body)
            write_jsonl(pose_path, rows)
            manifest_path = root / "stage4_input_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            pose_artifact = artifact(pose_path, rows)
            manifest["artifacts"][pose_path.name] = pose_artifact
            authority = manifest["authoritative_input_binding"]
            authority["dataset_pose_packet_stream"]["sha256"] = pose_artifact["sha256"]
            authority_body = dict(authority)
            authority_body.pop("binding_sha256")
            authority["binding_sha256"] = canonical_sha256(authority_body)
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_path.write_bytes(manifest_bytes)
            expected = hashlib.sha256(manifest_bytes).hexdigest()
            with self.assertRaisesRegex(IntegrationError, "pose value hash differs"):
                load_assay_bundle(root, expected_manifest_sha256=expected)

    def test_oracle_packet_hash_is_recomputed_after_stream_reseal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            oracle_path = root / "oracle_resampled_groundtruth_1khz.jsonl"
            rows = [json.loads(line) for line in oracle_path.read_text().splitlines()]
            rows[0]["before_source_pose_id"] -= 1
            write_jsonl(oracle_path, rows)
            manifest_path = root / "stage4_input_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            oracle_artifact = artifact(oracle_path, rows)
            manifest["artifacts"][oracle_path.name] = oracle_artifact
            authority = manifest["authoritative_input_binding"]
            authority["oracle_pose_stream"]["sha256"] = oracle_artifact["sha256"]
            authority_body = dict(authority)
            authority_body.pop("binding_sha256")
            authority["binding_sha256"] = canonical_sha256(authority_body)
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_path.write_bytes(manifest_bytes)
            expected = hashlib.sha256(manifest_bytes).hexdigest()
            with self.assertRaisesRegex(
                IntegrationError, "oracle pose packet canonical hash differs"
            ):
                load_assay_bundle(root, expected_manifest_sha256=expected)

    def test_window_registry_bounds_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root, include_limits=False)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            with self.assertRaisesRegex(
                IntegrationError, "window summary lacks frozen registry bounds"
            ):
                build_window_cycle_inputs(bundle, WINDOW)

    def test_calibration_recovery_rejects_resealed_sensor_ray_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            events_path = root / "stage4_events.jsonl"
            rows = [json.loads(line) for line in events_path.read_text().splitlines()]
            rows[0]["sensor_ray"][0] += 0.001
            write_jsonl(events_path, rows)
            manifest_path = root / "stage4_input_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["artifacts"][events_path.name] = artifact(events_path, rows)
            manifest_bytes = canonical_json_bytes(manifest)
            manifest_path.write_bytes(manifest_bytes)
            expected = hashlib.sha256(manifest_bytes).hexdigest()
            with self.assertRaisesRegex(IntegrationError, "calibration recovery"):
                load_assay_bundle(root, expected_manifest_sha256=expected)

    def test_four_arm_signed_same_cycle_inputs_are_fully_sealed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            self.assertLess(inputs.dataset_poses[0].commit_cycle, 0)
            integrated = build_all_arm_window(bundle, WINDOW)
            self.assertEqual(set(integrated), set(Arm))
            for sealed in integrated.values():
                self.assertEqual(sealed.receipt.expected_events, 2)
                self.assertEqual(sealed.receipt.retired_records, 2)
                self.assertEqual(
                    sealed.query_projection_sha256,
                    sealed.receipt.decision_records_sha256,
                )
                self.assertEqual(
                    sealed.manifest.query_projection_sha256,
                    sealed.receipt.decision_records_sha256,
                )
                self.assertIsInstance(sealed.boundary_evidence, ScoreBoundaryEvidence)
                self.assertEqual(
                    sealed.boundary_evidence.digest_tuple(),
                    (
                        sealed.manifest.assay_authoritative_input_manifest_sha256,
                        sealed.manifest.full_cycle_result_sha256,
                        sealed.manifest.cycle_receipts_sha256,
                        sealed.manifest.query_projection_sha256,
                    ),
                )
                self.assertEqual(
                    sealed.simulation.cycle_receipts_sha256,
                    sealed.manifest.cycle_receipts_sha256,
                )
                if sealed.arm is not Arm.ORACLE_1KHZ:
                    self.assertTrue(
                        sealed.simulation.all_event_pose_indices_verified
                    )
                    self.assertTrue(all(
                        row.causal_pose_index_applicable
                        and row.causal_pose_index_verified
                        and row.event_causal_pose_index
                        == inputs.events[index].causal_pose_index
                        for index, row in enumerate(
                            sealed.simulation.cycle_receipts
                        )
                    ))
                else:
                    self.assertTrue(all(
                        not row.causal_pose_index_applicable
                        and not row.causal_pose_index_verified
                        and row.event_causal_pose_index is None
                        for row in sealed.simulation.cycle_receipts
                    ))
                self.assertEqual(sealed.accounting.incremental_state_bits, 108_799)
                self.assertEqual(
                    sealed.accounting.pose_bandwidth_bits_per_second, 192_000
                )

                accounting_evidence = sealed.accounting_evidence.to_mapping()
                self.assertEqual(accounting_evidence["state_total_bits"], 108_799)
                state_components = accounting_evidence["state_components_bits"]
                self.assertEqual(len(state_components), 11)
                self.assertEqual(
                    state_components["delayed_fifo_pointers_and_occupancy"], 31
                )
                self.assertEqual(
                    state_components["ingress_serializer_count_and_cursor"], 6
                )
                self.assertEqual(
                    state_components["pose_ring_write_pointer_and_valid_count"], 9
                )
                self.assertEqual(
                    state_components["pose_ring_live_reference_counters"], 176
                )
                self.assertEqual(state_components["transform_pipeline_payload"], 204)
                self.assertEqual(state_components["atomic_pose_ingress_staging"], 192)
                self.assertEqual(
                    state_components["global_cycle_and_deadline_counter"], 21
                )
                self.assertEqual(
                    state_components["expected_and_retired_receipt_counters"], 28
                )
                self.assertEqual(
                    accounting_evidence["pose_bandwidth_total_bps"], 192_000
                )
                expected_event_rate = (
                    2 * 102 * 1_000_000_000 + (END - (START + 15)) - 1
                ) // (END - (START + 15))
                self.assertEqual(
                    sealed.accounting.event_bandwidth_bits_per_second,
                    expected_event_rate,
                )
                receipts = sealed.simulation.cycle_receipts
                serializer_cycles = sum(
                    row.admission_cycle - row.occurrence_cycle for row in receipts
                )
                fifo_cycles = (
                    sum(row.retire_cycle - row.admission_cycle for row in receipts)
                    if sealed.arm is Arm.DELAYED_EXACT
                    else 0
                )
                self.assertEqual(
                    sealed.accounting.buffer_bit_cycles,
                    102 * (serializer_cycles + fifo_cycles),
                )
                self.assertEqual(
                    accounting_evidence["pose_ring_accounting_sha256"],
                    sealed.simulation.pose_ring_accounting_sha256,
                )
                self.assertEqual(
                    tuple(
                        receipt.admission_cycle
                        for receipt in sealed.simulation.cycle_receipts
                    ),
                    tuple(row["presentation_cycle"] for row in inputs.event_rows),
                )
                self.assertEqual(
                    sealed.full_cycle_evidence_sha256,
                    canonical_sha256(integration._full_cycle_evidence(sealed.simulation)),
                )
                full_cycle = integration._full_cycle_evidence(sealed.simulation)
                self.assertEqual(
                    full_cycle["pose_ring_accounting_sha256"],
                    sealed.simulation.pose_ring_accounting_sha256,
                )
                self.assertEqual(
                    full_cycle["cycle_receipts_sha256"],
                    sealed.simulation.cycle_receipts_sha256,
                )
                self.assertEqual(
                    dict(sealed.manifest.artifact_sha256)["sources"],
                    canonical_sha256({
                        "source": bundle.manifest["source"],
                        "assay_manifest_sha256": bundle.manifest_sha256,
                        "assay_authority_sha256": bundle.authority_sha256,
                    }),
                )
                package_root = Path(integration.__file__).resolve().parents[1]
                self.assertEqual(
                    dict(sealed.manifest.artifact_sha256)["generator"],
                    canonical_sha256({
                        "assay_generator_code_sha256": bundle.manifest[
                            "generator_runtime"
                        ]["generator_code_sha256"],
                        "integration_adapter_py_sha256": integration._sha256_file(
                            Path(integration.__file__).resolve()
                        ),
                        "pose_recovery_geometry_py_sha256": integration._sha256_file(
                            package_root / "redred_mc_wtb_pose_recovery" / "geometry.py"
                        ),
                    }),
                )
                self.assertEqual(
                    dict(sealed.manifest.artifact_sha256)["cycle_model"],
                    canonical_sha256({
                        "model_py_sha256": integration._sha256_file(
                            package_root / "redred_mc_wtb_stage4_cyclemodel" / "model.py"
                        ),
                        "full_cycle_evidence_sha256": (
                            sealed.full_cycle_evidence_sha256
                        ),
                        "score_free_accounting_evidence_sha256": (
                            sealed.accounting_evidence.canonical_sha256()
                        ),
                    }),
                )
            self.assertTrue(all(
                abs(math.sqrt(sum(value * value for value in shadow.ray)) - 1.0)
                < 1.0e-12
                for sealed in integrated.values()
                for event in sealed.ray_events
                for shadow in event.world_shadow_rays
            ))

        source = Path(integration.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score_window(", source)
        self.assertNotIn("EventLoss", source)

    def test_accounting_reason_taxonomy_matches_refrozen_contract(self):
        contract = load_comparison_contract()
        accounting = contract.as_dict()["score_free_accounting"]
        corrected = accounting["corrected_reason_allowlist_by_arm"]
        raw = accounting["raw_reason_classification_by_arm"]
        category_names = {
            "freshness_veto": "freshness",
            "invalid_pose_bypass": "invalid",
            "operational_waste": "operational",
        }
        for arm in Arm:
            policy = integration._ARM_CATEGORY_REASONS[arm]
            self.assertEqual(policy["corrected"], frozenset(corrected[arm.value]))
            for contract_name, implementation_name in category_names.items():
                self.assertEqual(
                    policy[implementation_name],
                    frozenset(
                        reason
                        for reason, category in raw[arm.value].items()
                        if category == contract_name
                    ),
                )

    def test_delayed_raw_shadow_uses_scorer_supported_offline_bracket(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            result = run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.DELAYED_EXACT,
                events=(replace(inputs.events[0], transform_guard_valid=False),),
                poses=inputs.dataset_poses,
            )
            record = integration._convert_record(result.records[0])
            self.assertEqual(record.disposition, "raw_bypass")
            shadow = integration._shadow_for_record(
                record,
                inputs.event_rows[0]["sensor_ray"],
                inputs.dataset_quaternions,
                inputs.dataset_poses,
            )
            self.assertEqual(shadow.transform, "delayed_slerp")
            self.assertEqual(len(shadow.pose_ids), 2)
            single_inputs = replace(
                inputs,
                event_rows=(dict(inputs.event_rows[2], is_query=True),),
                events=(replace(inputs.events[2], transform_guard_valid=False),),
            )
            invalid_result = run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.DELAYED_EXACT,
                events=single_inputs.events,
                poses=inputs.dataset_poses,
            )
            invalid_converted = tuple(
                integration._convert_record(row) for row in invalid_result.records
            )
            accounting, evidence = integration._derive_accounting(
                single_inputs, invalid_result, invalid_converted
            )
            self.assertEqual(accounting.operational_waste_event_ids, (102,))
            self.assertEqual(accounting.attempted_correction_event_ids, (102,))
            self.assertEqual(accounting.invalid_pose_bypass_event_ids, ())
            self.assertIn(
                "invalid_pose",
                dict(evidence.category_reason_policy)["operational"],
            )

            forced_record = replace(
                invalid_result.records[0],
                disposition_reason="fifo_full_forced_bypass",
            )
            forced_result = replace(invalid_result, records=(forced_record,))
            with self.assertRaisesRegex(
                IntegrationError,
                "UNBOUNDED_REPLAY_REQUIRED_FOR_MINIMUM_ZERO_LOSS_DEPTH",
            ):
                integration._derive_accounting(
                    single_inputs, forced_result, invalid_converted
                )
            with self.assertRaisesRegex(
                IntegrationError,
                "authoritative delayed offline bracket is unavailable",
            ):
                integration._shadow_for_record(
                    record,
                    inputs.event_rows[0]["sensor_ray"],
                    inputs.dataset_quaternions,
                )

    def test_actual_generated_assay_fixture_builds_all_four_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            pins = build_assay_source_fixture(dataset)
            output = root / "assay"
            generated = generate_score_free_inputs(
                dataset,
                output,
                source_pins=pins,
                fixture_label="stage4_integration_end_to_end_fixture_v1",
            )
            manifest_path = output / "stage4_input_manifest.json"
            expected = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            bundle = load_assay_bundle(
                output, expected_manifest_sha256=expected
            )
            window_id = generated["windows"][0]["window_id"]
            integrated = build_all_arm_window(bundle, window_id)
            self.assertEqual(set(integrated), set(Arm))
            self.assertTrue(all(
                sealed.query_projection_sha256
                == sealed.receipt.decision_records_sha256
                for sealed in integrated.values()
            ))
            self.assertTrue(all(
                sealed.simulation.all_event_pose_indices_verified
                for arm, sealed in integrated.items()
                if arm is not Arm.ORACLE_1KHZ
            ))


if __name__ == "__main__":
    unittest.main()
