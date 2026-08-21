from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

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


def build_artifacts(root, *, include_limits=True, signed_history=True):
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
        (100, 12, False, 2, 0, 2, 0),
        (101, 12, False, 2, 1, 2, 1),
        (102, 18, True, 3, 0, 3, 0),
        (103, 25, True, 4, 0, 4, 0),
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
        window.update(
            window_start_ns=START,
            query_start_ns=START + 15,
            window_end_ns=END,
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

    def test_window_limits_remain_a_named_fail_closed_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = build_artifacts(root, include_limits=False)
            bundle = load_assay_bundle(root, expected_manifest_sha256=expected)
            with self.assertRaisesRegex(
                IntegrationError, "UPSTREAM_WINDOW_LIMITS_NOT_SERIALIZED"
            ):
                build_window_cycle_inputs(bundle, WINDOW)

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
                self.assertEqual(sealed.accounting.incremental_state_bits, 108_799)
                self.assertEqual(
                    sealed.accounting.pose_bandwidth_bits_per_second, 192_000
                )
                accounting_evidence = sealed.accounting_evidence.to_mapping()
                self.assertEqual(accounting_evidence["state_total_bits"], 108_799)
                state_components = accounting_evidence["state_components_bits"]
                self.assertEqual(len(state_components), 11)
                self.assertEqual(state_components["fifo_read_write_pointers_and_count"], 31)
                self.assertEqual(state_components["ingress_count_and_cursor"], 6)
                self.assertEqual(state_components["pose_ring_pointer_and_valid"], 9)
                self.assertEqual(state_components["pose_ring_16x11_live_references"], 176)
                self.assertEqual(state_components["two_lane_102bit_pipeline"], 204)
                self.assertEqual(state_components["pose_ingress_register"], 192)
                self.assertEqual(state_components["global_cycle_counter"], 21)
                self.assertEqual(state_components["status_counters"], 28)
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

    def test_delayed_raw_shadow_uses_offline_bracket_or_named_blocker(self):
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
                "UPSTREAM_DELAYED_RAW_SHADOW_ARITY_UNREPRESENTABLE",
            ):
                integration._shadow_for_record(
                    record,
                    inputs.event_rows[0]["sensor_ray"],
                    inputs.dataset_quaternions,
                )


if __name__ == "__main__":
    unittest.main()
