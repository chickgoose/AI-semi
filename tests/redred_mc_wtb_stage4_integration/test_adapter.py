from __future__ import annotations

import hashlib
import math
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_decision_records,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import Arm, run_cycle_model
from benchmarks.redred_mc_wtb_stage4_integration import adapter as integration
from benchmarks.redred_mc_wtb_stage4_integration import (
    IntegrationError,
    build_all_arm_window,
    build_window_cycle_inputs,
    load_assay_bundle,
)
from benchmarks.redred_mc_wtb_stage4_scoring import RayEvent, ScoreInputManifest


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
        (event_id, 24),
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


def build_artifacts(root, *, include_limits=True, signed_history=False):
    q0 = (0.0, 0.0, 0.0, 1.0)
    q1 = (0.0, 0.0, math.sin(0.01), math.cos(0.01))
    q2 = (0.0, 0.0, math.sin(0.02), math.cos(0.02))
    q3 = (0.0, 0.0, math.sin(0.03), math.cos(0.03))
    dataset = [
        packet(10, 0, q0),
        packet(11, 6, q1),
        packet(12, 20, q2),
        packet(13, 30, q3),
    ]
    if signed_history:
        dataset[0] = dict(dataset[0])
        dataset[0]["timestamp_ns"] = START - 6
        dataset[0]["arrival_cycle"] = -1
        dataset[0]["commit_cycle"] = -1
        dataset[0]["visible_cycle"] = 0
        body = dict(dataset[0])
        body.pop("packet_sha256")
        dataset[0]["packet_sha256"] = canonical_sha256(body)
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
    oracle_schedule = [{
        "window_id": WINDOW,
        "oracle_pose_id": oracle_pose_id,
        "effective_timestamp_ns": START,
        "pose_value_sha256": oracle[0]["pose_value_sha256"],
        "effective_cycle": 0,
        "commit_cycle": 1,
        "visible_cycle": 2,
    }]

    event_specs = (
        (100, 12, False, 2, 0, 3, 0),
        (101, 12, False, 2, 1, 3, 1),
        (102, 18, True, 3, 0, 4, 0),
        (103, 25, True, 4, 0, 5, 0),
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
        events.append({
            "window_id": WINDOW,
            "event_id": event_id,
            "timestamp_ns": START + delta,
            "x": 120 + ordinal,
            "y": 90,
            "polarity": ordinal & 1,
            "sensor_ray": [0.0, 0.0, 1.0],
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
            "payload_hex": pack_event(event_id, ordinal, START + delta, 120 + ordinal, 90, ordinal & 1, 11),
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
    authority = {
        "schema": "redred.mc_wtb.stage4_authoritative_input_binding/v1",
        "ordered_102bit_occurrence_records": {
            "serialization": "lowercase_26_hex_digits_plus_lf_in_source_event_order",
            "record_count": len(events),
            "sha256": hashlib.sha256(ordered_raw).hexdigest(),
            "ordered_event_ids_sha256": canonical_sha256([row["event_id"] for row in events]),
        },
        "raw_source_streams": {
            "events.txt_sha256": HASH_A,
            "groundtruth.txt_sha256": HASH_B,
            "calib.txt_sha256": HASH_C,
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
        },
        "oracle_window_schedule_stream": {
            "path": "stage4_oracle_window_schedule.jsonl",
            "sha256": artifacts["stage4_oracle_window_schedule.jsonl"]["sha256"],
            "record_count": len(oracle_schedule),
        },
        "generator_code_sha256": {"fixture.py": HASH_A},
        "runtime": {"identity_sha256": HASH_B, "python_executable_sha256": HASH_C},
    }
    authority["binding_sha256"] = canonical_sha256(authority)
    contract = load_comparison_contract()
    window = {
        "window_id": WINDOW,
        "selected_event_count": 4,
        "query_event_count": 2,
    }
    if include_limits:
        window.update(window_start_ns=START, window_end_ns=END)
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
        "windows": [window],
        "artifacts": artifacts,
    }
    (root / "stage4_input_manifest.json").write_bytes(canonical_json_bytes(manifest))
    return events


class IntegrationTests(unittest.TestCase):
    def test_loader_closes_every_fixture_artifact_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            bundle = load_assay_bundle(root)
            self.assertEqual(len(bundle.events), 4)
            self.assertEqual(len(bundle.snapshots), 3)
            with (root / "stage4_events.jsonl").open("ab") as stream:
                stream.write(b" ")
            with self.assertRaisesRegex(IntegrationError, "artifact hash"):
                load_assay_bundle(root)

    def test_anticipated_window_limits_and_signed_history_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root, include_limits=False)
            bundle = load_assay_bundle(root)
            with self.assertRaisesRegex(
                IntegrationError, "UPSTREAM_WINDOW_LIMITS_NOT_SERIALIZED"
            ):
                build_window_cycle_inputs(bundle, WINDOW)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root, signed_history=True)
            bundle = load_assay_bundle(root)
            with self.assertRaisesRegex(
                IntegrationError, "UPSTREAM_SIGNED_HISTORY_CYCLE_UNSUPPORTED"
            ):
                build_window_cycle_inputs(bundle, WINDOW)

    def test_four_arm_components_build_receipt_accounting_shadows_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            bundle = load_assay_bundle(root)
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            results = {}
            converted = {}
            for arm in Arm:
                poses = inputs.oracle_poses if arm is Arm.ORACLE_1KHZ else inputs.dataset_poses
                result = run_cycle_model(
                    window_id=WINDOW,
                    window_start_ns=START,
                    arm=arm,
                    events=inputs.events,
                    poses=poses,
                )
                results[arm] = result
                converted[arm] = tuple(integration._convert_record(row) for row in result.records)
                if arm is not Arm.ORACLE_1KHZ:
                    integration._validate_assay_snapshot_projection(
                        bundle, inputs, converted[arm]
                    )

            ray_events = []
            for index, event_row in enumerate(inputs.event_rows):
                shadows = []
                for arm in Arm:
                    quaternions = (
                        inputs.oracle_quaternions
                        if arm is Arm.ORACLE_1KHZ
                        else inputs.dataset_quaternions
                    )
                    shadows.append(integration._shadow_for_record(
                        converted[arm][index], event_row["sensor_ray"], quaternions
                    ))
                ray_events.append(RayEvent(
                    WINDOW,
                    event_row["event_id"],
                    event_row["timestamp_ns"],
                    event_row["polarity"],
                    event_row["is_query"],
                    tuple(event_row["sensor_ray"]),
                    tuple(shadows),
                ))
            ray_digest = canonical_sha256([row.to_mapping() for row in ray_events])
            query_indexes = (2, 3)
            query_ids = (102, 103)
            contract = load_comparison_contract()
            for arm in Arm:
                query_records = tuple(converted[arm][index] for index in query_indexes)
                receipt = validate_decision_records(
                    contract,
                    query_ids,
                    query_records,
                    expected_window_id=WINDOW,
                    expected_arm=arm.value,
                )
                accounting = integration._derive_accounting(
                    inputs, results[arm], converted[arm]
                )
                evidence_hash = canonical_sha256(
                    integration._full_cycle_evidence(results[arm])
                )
                manifest = ScoreInputManifest(
                    WINDOW,
                    arm.value,
                    receipt.canonical_sha256(),
                    accounting.canonical_sha256(),
                    ray_digest,
                    integration._artifact_bindings(bundle, arm, evidence_hash),
                )
                self.assertEqual(receipt.expected_events, 2)
                self.assertEqual(receipt.retired_records, 2)
                self.assertEqual(
                    tuple(row[0] for row in accounting.baseline_retire_cycles),
                    query_ids,
                )
                self.assertEqual(manifest.ray_events_sha256, ray_digest)
                self.assertRegex(manifest.canonical_sha256(), r"^[0-9a-f]{64}$")
                self.assertRegex(evidence_hash, r"^[0-9a-f]{64}$")
            self.assertTrue(
                all(
                    abs(math.sqrt(sum(value * value for value in shadow.ray)) - 1.0)
                    < 1.0e-12
                    for event in ray_events
                    for shadow in event.world_shadow_rays
                )
            )

    def test_current_cycle_ingress_mismatch_is_named_and_no_scorer_is_called(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            bundle = load_assay_bundle(root)
            with self.assertRaisesRegex(
                IntegrationError, "UPSTREAM_CYCLEMODEL_INGRESS_SCHEDULE_MISMATCH"
            ):
                build_all_arm_window(bundle, WINDOW)
        source = Path(integration.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score_window(", source)
        self.assertNotIn("EventLoss", source)

    def test_delayed_raw_shadow_arity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_artifacts(root)
            bundle = load_assay_bundle(root)
            inputs = build_window_cycle_inputs(bundle, WINDOW)
            result = run_cycle_model(
                window_id=WINDOW,
                window_start_ns=START,
                arm=Arm.DELAYED_EXACT,
                events=inputs.events,
                poses=inputs.dataset_poses[:2],
            )
            record = integration._convert_record(result.records[0])
            self.assertEqual(record.disposition, "raw_bypass")
            with self.assertRaisesRegex(
                IntegrationError,
                "UPSTREAM_DELAYED_RAW_SHADOW_ARITY_UNREPRESENTABLE",
            ):
                integration._shadow_for_record(
                    record,
                    inputs.event_rows[0]["sensor_ray"],
                    inputs.dataset_quaternions,
                )


if __name__ == "__main__":
    unittest.main()
