from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    PosePacket,
    run_cycle_model,
    run_delayed_unbounded_diagnostic,
)
from benchmarks.redred_mc_wtb_stage4_integration import adapter
from benchmarks.redred_mc_wtb_stage4_integration import sealing


HASH_A = "a" * 64
HASH_B = "b" * 64
ASSAY_SHA = "c" * 64
RAY_SHA = "d" * 64


class MappingValue:
    def __init__(self, value):
        self.value = value

    def to_mapping(self):
        return self.value


def timestamp_for_cycle(cycle):
    return (cycle * 6_500) // 1_000


def pressure_fixture():
    window_id = "synthetic-sealing-pressure"
    events = []
    event_id = 0
    for cycle in range(2, 515):
        timestamp_ns = timestamp_for_cycle(cycle)
        events.extend((
            Event(event_id, timestamp_ns, causal_pose_index=0),
            Event(event_id + 1, timestamp_ns, causal_pose_index=0),
        ))
        event_id += 2
    events = tuple(events)
    poses = (
        PosePacket.dataset(0, 0, 0, HASH_A),
        PosePacket.dataset(1, timestamp_for_cycle(2_000), 0, HASH_B),
    )
    bounded = run_cycle_model(
        window_id=window_id,
        window_start_ns=0,
        arm=Arm.DELAYED_EXACT,
        events=events,
        poses=poses,
        synthetic_test_mode=False,
    )
    diagnostic = run_delayed_unbounded_diagnostic(
        window_id=window_id,
        window_start_ns=0,
        events=events,
        poses=poses,
        synthetic_test_mode=False,
    )
    inputs = adapter.WindowCycleInputs(
        window_id,
        0,
        0,
        timestamp_for_cycle(2_001),
        tuple({"is_query": True} for _ in events),
        events,
        tuple((0.0, 0.0, 1.0) for _ in events),
        poses,
        (),
        {},
        {},
    )
    converted = tuple(adapter._convert_record(record) for record in bounded.records)
    accounting, accounting_evidence = adapter._derive_accounting(
        inputs, bounded, converted, diagnostic
    )
    full_cycle = adapter._full_cycle_evidence(bounded)
    receipt_mapping = {
        "decision_records_sha256": canonical_sha256(
            [record.to_mapping() for record in converted]
        ),
        "expected_events": len(converted),
        "retired_records": len(converted),
    }
    accounting_mapping = accounting.to_mapping()
    manifest_mapping = {
        "assay_authoritative_input_manifest_sha256": ASSAY_SHA,
        "full_cycle_result_sha256": canonical_sha256(full_cycle),
        "cycle_receipts_sha256": canonical_sha256(
            [receipt.to_mapping() for receipt in bounded.cycle_receipts]
        ),
        "query_projection_sha256": canonical_sha256(
            [record.to_mapping() for record in converted]
        ),
        "decision_receipt_sha256": canonical_sha256(receipt_mapping),
        "score_free_accounting_sha256": canonical_sha256(accounting_mapping),
        "ray_events_sha256": RAY_SHA,
    }
    sealed = SimpleNamespace(
        arm=Arm.DELAYED_EXACT,
        simulation=bounded,
        query_records=converted,
        receipt=MappingValue(receipt_mapping),
        accounting=MappingValue(accounting_mapping),
        accounting_evidence=accounting_evidence,
        manifest=MappingValue(manifest_mapping),
        delayed_unbounded_diagnostic=diagnostic,
    )
    return sealed, diagnostic, inputs


class SealingTests(unittest.TestCase):
    def test_delayed_diagnostic_is_a_separate_observed_leaf(self):
        sealed, _diagnostic, inputs = pressure_fixture()
        authoritative = sealing._authoritative_window_inputs(inputs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leaf = "windows/%s/arms/delayed_exact" % inputs.window_id
            sealing._write_leaf_inputs(root, leaf, sealed)
            files = {}
            with mock.patch(
                "benchmarks.redred_mc_wtb_stage4_scoring.scoring.score_window"
            ) as score_window:
                binding = sealing._observe_leaf(
                    root,
                    leaf,
                    sealed,
                    ASSAY_SHA,
                    RAY_SHA,
                    len(sealed.query_records),
                    authoritative,
                    files,
                )
            score_window.assert_not_called()

            diagnostic_relative = "%s/%s" % (
                leaf, sealing._DELAYED_DIAGNOSTIC_FILE
            )
            self.assertEqual(
                files[diagnostic_relative]["kind"],
                sealing._DELAYED_DIAGNOSTIC_KIND,
            )
            self.assertEqual(
                binding["delayed_unbounded_depth_diagnostic"]["path"],
                diagnostic_relative,
            )
            diagnostic_binding = binding[
                "delayed_unbounded_depth_diagnostic"
            ]
            self.assertEqual(
                diagnostic_binding["schema"],
                "redred.mc_wtb.stage4_delayed_unbounded_depth_diagnostic/v3",
            )
            self.assertEqual(
                diagnostic_binding["config_schema"],
                "redred.mc_wtb.stage4_delayed_unbounded_depth_config/v2",
            )
            self.assertEqual(
                diagnostic_binding["termination_guard_rule"],
                "iterations<=10*input_count+2*pose_count+32",
            )
            self.assertEqual(
                diagnostic_binding["queue_bound_rule"],
                "fifo_occupancy<=input_event_count_at_all_times",
            )
            self.assertTrue(diagnostic_binding["termination_proven"])
            self.assertTrue(
                diagnostic_binding["queue_never_exceeded_input_count"]
            )
            self.assertTrue(
                diagnostic_binding["exact_once_ordered_conservation"]
            )
            self.assertLessEqual(
                diagnostic_binding["simulation_iterations"],
                diagnostic_binding["termination_iteration_bound"],
            )
            self.assertEqual(
                diagnostic_binding["input_count"],
                diagnostic_binding["retired_count"],
            )
            boundary_path = root / leaf / "score-boundary-evidence.json"
            boundary = sealing._read_json(boundary_path)[0]
            self.assertEqual(set(boundary), {
                "schema",
                "assay_authoritative_input_manifest_sha256",
                "full_cycle_result_sha256",
                "cycle_receipts_sha256",
                "query_projection_sha256",
            })
            self.assertNotIn("diagnostic", canonical_json_bytes(boundary).decode("ascii"))
            self.assertEqual(
                hashlib.sha256(boundary_path.read_bytes()).hexdigest(),
                "e465c981a5a0c6122a093f1d7ab853984f3ce477ccb6017a5c98d199c60ed35b",
            )
            self.assertEqual(
                binding["delayed_unbounded_depth_diagnostic"][
                    "bounded_full_cycle_result_sha256"
                ],
                boundary["full_cycle_result_sha256"],
            )
            self.assertEqual(
                binding["delayed_unbounded_depth_diagnostic"][
                    "bounded_cycle_receipts_sha256"
                ],
                boundary["cycle_receipts_sha256"],
            )

            window_mapping = {
                "window_id": diagnostic_binding["window_id"],
                "ray_events_sha256": RAY_SHA,
                "query_event_count": len(sealed.query_records),
            }
            sealing._verify_reopened_leaf(
                root,
                files,
                window_mapping,
                Arm.DELAYED_EXACT.value,
                binding,
                authoritative,
                ASSAY_SHA,
            )
            mutant = deepcopy(binding)
            mutant["delayed_unbounded_depth_diagnostic"][
                "bounded_cycle_receipts_sha256"
            ] = "e" * 64
            with self.assertRaisesRegex(
                sealing.SealingError, "bounded-evidence binding"
            ):
                sealing._verify_reopened_leaf(
                    root,
                    files,
                    window_mapping,
                    Arm.DELAYED_EXACT.value,
                    mutant,
                    authoritative,
                    ASSAY_SHA,
                )

    def test_independent_replay_rejects_resealed_peak_mutant(self):
        _sealed, diagnostic, _inputs = pressure_fixture()
        mutant = deepcopy(diagnostic.to_mapping())
        mutant["peak_fifo_depth"] += 1
        body = dict(mutant)
        del body["evidence_sha256"]
        mutant["evidence_sha256"] = canonical_sha256(body)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutant.json"
            path.write_bytes(canonical_json_bytes(mutant))
            with self.assertRaisesRegex(
                sealing.SealingError, "progress or queue proof differs"
            ):
                sealing._observe_file(
                    path.parent,
                    path.name,
                    kind=sealing._DELAYED_DIAGNOSTIC_KIND,
                    record_count=1,
                )

    def test_v3_config_and_progress_mutants_fail_closed(self):
        _sealed, diagnostic, _inputs = pressure_fixture()
        for field, value, expected in (
            ("termination_proven", False, "progress or queue proof differs"),
            (
                "queue_never_exceeded_input_count",
                False,
                "progress or queue proof differs",
            ),
            (
                "simulation_iterations",
                diagnostic.termination_iteration_bound + 1,
                "progress or queue proof differs",
            ),
        ):
            with self.subTest(field=field):
                mutant = deepcopy(diagnostic.to_mapping())
                mutant[field] = value
                body = dict(mutant)
                del body["evidence_sha256"]
                mutant["evidence_sha256"] = canonical_sha256(body)
                with self.assertRaisesRegex(sealing.SealingError, expected):
                    sealing._validate_delayed_diagnostic_mapping(mutant, field)

        mutant = deepcopy(diagnostic.to_mapping())
        mutant["config"]["termination_guard_rule"] = "iterations_unbounded"
        mutant["config_identity_sha256"] = canonical_sha256(mutant["config"])
        body = dict(mutant)
        del body["evidence_sha256"]
        mutant["evidence_sha256"] = canonical_sha256(body)
        with self.assertRaisesRegex(sealing.SealingError, "wrong diagnostic config"):
            sealing._validate_delayed_diagnostic_mapping(mutant, "config")

    def test_diagnostic_hash_is_body_hash_not_file_hash(self):
        _sealed, diagnostic, _inputs = pressure_fixture()
        mapping = diagnostic.to_mapping()
        payload = canonical_json_bytes(mapping)
        self.assertEqual(mapping["evidence_sha256"], diagnostic.evidence_sha256)
        self.assertNotEqual(hashlib.sha256(payload).hexdigest(), diagnostic.evidence_sha256)

    def test_stable_observer_rejects_symlink_hardlink_and_toctou(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / (root.name + "-outside.json")
            outside.write_bytes(canonical_json_bytes({"value": 1}))
            try:
                (root / "symlink.json").symlink_to(outside)
                with self.assertRaisesRegex(sealing.SealingError, "regular file|unsafe"):
                    sealing._observe_file(
                        root, "symlink.json", kind="object", record_count=1
                    )

                target = root / "target.json"
                target.write_bytes(canonical_json_bytes({"value": 1}))
                os.link(str(target), str(root / "hardlink.json"))
                with self.assertRaisesRegex(sealing.SealingError, "hard-linked"):
                    sealing._observe_file(
                        root, "target.json", kind="object", record_count=1
                    )

                target.unlink()
                (root / "hardlink.json").unlink()
                race = root / "race.json"
                race.write_bytes(canonical_json_bytes({"value": 1}))
                real_read = sealing.os.read
                mutated = [False]

                def racing_read(descriptor, size):
                    payload = real_read(descriptor, size)
                    if payload and not mutated[0]:
                        mutated[0] = True
                        race.write_bytes(canonical_json_bytes({"value": 2}))
                    return payload

                with mock.patch.object(sealing.os, "read", side_effect=racing_read):
                    with self.assertRaisesRegex(sealing.SealingError, "changed while reading"):
                        sealing._observe_file(
                            root, "race.json", kind="object", record_count=1
                        )
            finally:
                if outside.exists():
                    outside.unlink()

    def test_record_count_is_exact_int_not_bool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "object.json").write_bytes(canonical_json_bytes({"x": 1}))
            with self.assertRaisesRegex(sealing.SealingError, "record_count"):
                sealing._observe_file(
                    root, "object.json", kind="object", record_count=True
                )

    def test_duplicate_window_and_missing_arm_mutations_fail_closed(self):
        frozen = (
            {
                "window_id": "w0",
                "warmup_start_ns_inclusive": 0,
                "query_start_ns_inclusive": 1,
                "query_end_ns_exclusive": 2,
            },
            {
                "window_id": "w1",
                "warmup_start_ns_inclusive": 3,
                "query_start_ns_inclusive": 4,
                "query_end_ns_exclusive": 5,
            },
        )
        authoritative = {
            "schema": "redred.mc_wtb.stage4_authoritative_window_cycle_inputs/v1",
            "window_id": "w0",
            "window_start_ns": 0,
            "input_events_sha256": HASH_A,
            "input_poses_sha256": HASH_B,
            "input_event_ids_sha256": "c" * 64,
            "input_count": 1,
            "input_pose_count": 1,
        }
        arm_binding = {
            "score_input_manifest_path": "unused",
            "score_input_manifest_sha256": HASH_A,
            "score_boundary_evidence_path": "unused",
            "score_boundary_evidence_sha256": HASH_B,
            "delayed_unbounded_depth_diagnostic": None,
        }
        window = {
            "schema": "redred.mc_wtb.stage4_score_free_window_seal/v1",
            "window_id": "w0",
            "warmup_start_ns_inclusive": 0,
            "query_start_ns_inclusive": 1,
            "query_end_ns_exclusive": 2,
            "selected_event_count": 1,
            "query_event_count": 1,
            "ordered_query_event_ids_sha256": "d" * 64,
            "ray_events_path": "windows/w0/ray-events.json",
            "ray_events_sha256": "e" * 64,
            "authoritative_cycle_inputs": authoritative,
            "arms": dict((arm.value, arm_binding) for arm in Arm),
        }
        pointer = {
            "window_id": "w0",
            "path": "windows/w0/window-seal.json",
            "sha256": "f" * 64,
        }
        campaign = {
            "assay_manifest_sha256": ASSAY_SHA,
            "windows": [pointer, deepcopy(pointer)],
            "files": {
                pointer["path"]: {
                    "sha256": pointer["sha256"],
                    "kind": "object",
                    "record_count": 1,
                },
                window["ray_events_path"]: {
                    "sha256": window["ray_events_sha256"],
                    "kind": "array",
                    "record_count": 1,
                },
            },
        }
        with mock.patch.object(sealing, "window_registry", return_value=frozen), mock.patch.object(
            sealing, "_read_indexed_json", return_value=(window, b"window")
        ), mock.patch.object(sealing, "_verify_reopened_leaf", return_value=()):
            with self.assertRaisesRegex(sealing.SealingError, "exact and unique"):
                sealing._verify_delayed_diagnostic_links(Path("unused"), campaign)

        one_window = deepcopy(window)
        del one_window["arms"][Arm.ORACLE_1KHZ.value]
        one_campaign = dict(campaign)
        one_campaign["windows"] = [pointer]
        with mock.patch.object(sealing, "window_registry", return_value=frozen[:1]), mock.patch.object(
            sealing, "_read_indexed_json", return_value=(one_window, b"window")
        ):
            with self.assertRaisesRegex(sealing.SealingError, "exactly four arms"):
                sealing._verify_delayed_diagnostic_links(Path("unused"), one_campaign)

    def test_unindexed_regular_file_is_rejected_by_full_tree_observer(self):
        contract = sealing.load_comparison_contract()
        frozen = tuple(sealing.window_registry())
        ids = [row["window_id"] for row in frozen]
        campaign = {
            "schema": "redred.mc_wtb.stage4_score_free_campaign_seal/v1",
            "content_class": "SCORE_FREE_OBSERVER_EVIDENCE_ONLY",
            "assay_manifest_sha256": ASSAY_SHA,
            "assay_authority_sha256": HASH_A,
            "assay_closure_sha256": HASH_B,
            "comparison_contract_sha256": contract.canonical_sha256,
            "registry_sha256": contract.registry["sha256"],
            "window_count": len(frozen),
            "arm_count": len(tuple(Arm)),
            "arm_window_count": len(frozen) * len(tuple(Arm)),
            "window_order": ids,
            "arm_order": [arm.value for arm in Arm],
            "windows": [
                {
                    "window_id": window_id,
                    "path": "windows/%s/window-seal.json" % window_id,
                    "sha256": HASH_A,
                }
                for window_id in ids
            ],
            "files": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = canonical_json_bytes(campaign)
            (root / "stage4-score-free-seal-manifest.json").write_bytes(payload)
            unindexed = root / "unindexed.json"
            unindexed.write_bytes(canonical_json_bytes({"x": 1}))
            with self.assertRaisesRegex(sealing.SealingError, "unindexed"):
                sealing.verify_score_free_seal(
                    root,
                    expected_seal_manifest_sha256=hashlib.sha256(payload).hexdigest(),
                )
            unindexed.unlink()
            outside = root.parent / (root.name + "-unindexed-target.json")
            outside.write_bytes(canonical_json_bytes({"x": 1}))
            try:
                (root / "unindexed-symlink.json").symlink_to(outside)
                with self.assertRaisesRegex(sealing.SealingError, "symlink"):
                    sealing.verify_score_free_seal(
                        root,
                        expected_seal_manifest_sha256=hashlib.sha256(payload).hexdigest(),
                    )
            finally:
                if outside.exists():
                    outside.unlink()

    def test_authoritative_input_hash_count_and_time_mutations_fail(self):
        sealed, _diagnostic, inputs = pressure_fixture()
        authoritative = sealing._authoritative_window_inputs(inputs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leaf = "windows/%s/arms/delayed_exact" % inputs.window_id
            sealing._write_leaf_inputs(root, leaf, sealed)
            files = {}
            binding = sealing._observe_leaf(
                root, leaf, sealed, ASSAY_SHA, RAY_SHA,
                len(sealed.query_records), authoritative, files,
            )
            window = {
                "window_id": inputs.window_id,
                "ray_events_sha256": RAY_SHA,
                "query_event_count": len(sealed.query_records),
            }
            mutations = (
                ("input_events_sha256", "e" * 64),
                ("input_poses_sha256", "f" * 64),
                ("input_event_ids_sha256", "0" * 64),
                ("input_count", authoritative["input_count"] + 1),
                ("input_pose_count", authoritative["input_pose_count"] + 1),
                ("window_start_ns", authoritative["window_start_ns"] + 1),
            )
            for field, value in mutations:
                with self.subTest(field=field):
                    mutant = dict(authoritative)
                    mutant[field] = value
                    with self.assertRaisesRegex(
                        sealing.SealingError, "authoritative"
                    ):
                        sealing._verify_reopened_leaf(
                            root, files, window, Arm.DELAYED_EXACT.value,
                            binding, mutant, ASSAY_SHA,
                        )

    def test_minimum_depth_and_bounded_pressure_mutations_fail(self):
        sealed, _diagnostic, inputs = pressure_fixture()
        authoritative = sealing._authoritative_window_inputs(inputs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leaf = "windows/%s/arms/delayed_exact" % inputs.window_id
            sealing._write_leaf_inputs(root, leaf, sealed)
            files = {}
            binding = sealing._observe_leaf(
                root, leaf, sealed, ASSAY_SHA, RAY_SHA,
                len(sealed.query_records), authoritative, files,
            )
            window = {
                "window_id": inputs.window_id,
                "ray_events_sha256": RAY_SHA,
                "query_event_count": len(sealed.query_records),
            }
            accounting_path = "%s/score-free-accounting-evidence.json" % leaf
            original_accounting = sealed.accounting_evidence.to_mapping()
            cases = (
                ("basis", "bounded_peak_no_full_pressure"),
                ("bounded_peak_buffer_entries", 1023),
                ("fifo_full_forced_bypass_event_ids", []),
                ("bounded_decision_records_sha256", "e" * 64),
                ("bounded_cycle_receipts_sha256", "f" * 64),
                ("unbounded_diagnostic_evidence_sha256", "0" * 64),
            )
            for field, value in cases:
                with self.subTest(field=field):
                    mutant = deepcopy(original_accounting)
                    mutant["minimum_depth_evidence"][field] = value
                    mutant["minimum_depth_evidence_sha256"] = canonical_sha256(
                        mutant["minimum_depth_evidence"]
                    )
                    (root / accounting_path).write_bytes(canonical_json_bytes(mutant))
                    files[accounting_path] = sealing._observe_file(
                        root, accounting_path, kind="object", record_count=1
                    )
                    with self.assertRaisesRegex(
                        sealing.SealingError, "minimum-depth"
                    ):
                        sealing._verify_reopened_leaf(
                            root, files, window, Arm.DELAYED_EXACT.value,
                            binding, authoritative, ASSAY_SHA,
                        )

            (root / accounting_path).write_bytes(
                canonical_json_bytes(original_accounting)
            )
            files[accounting_path] = sealing._observe_file(
                root, accounting_path, kind="object", record_count=1
            )
            full_path = "%s/full-cycle-result.json" % leaf
            full = deepcopy(adapter._full_cycle_evidence(sealed.simulation))
            full["peak_buffer_occupancy"] -= 1
            (root / full_path).write_bytes(canonical_json_bytes(full))
            files[full_path] = sealing._observe_file(
                root, full_path, kind="object", record_count=1
            )
            with self.assertRaisesRegex(sealing.SealingError, "peak"):
                sealing._verify_reopened_leaf(
                    root, files, window, Arm.DELAYED_EXACT.value,
                    binding, authoritative, ASSAY_SHA,
                )

    def test_runner_adds_diagnostic_only_after_explicit_pressure_signal(self):
        inputs = SimpleNamespace(
            window_id="w0",
            window_start_ns=7,
            events=("events",),
            dataset_poses=("poses",),
        )
        diagnostic = object()
        completed = {Arm.DELAYED_EXACT: object()}
        with mock.patch.object(
            sealing.integration_adapter,
            "build_all_arm_window",
            side_effect=(
                adapter.IntegrationError(
                    "%s: diagnostic required" % sealing._REPLAY_REQUIRED
                ),
                completed,
            ),
        ) as build, mock.patch.object(
            sealing.integration_adapter,
            "build_window_cycle_inputs",
            return_value=inputs,
        ), mock.patch.object(
            sealing,
            "run_delayed_unbounded_diagnostic",
            return_value=diagnostic,
        ) as replay:
            observed = sealing._build_window_with_required_diagnostic(
                object(), "w0"
            )
        self.assertIs(observed, completed)
        replay.assert_called_once_with(
            window_id="w0",
            window_start_ns=7,
            events=("events",),
            poses=("poses",),
            synthetic_test_mode=False,
        )
        self.assertEqual(build.call_count, 2)
        self.assertIs(
            build.call_args_list[1].kwargs["delayed_unbounded_diagnostic"],
            diagnostic,
        )

    def test_runner_does_not_mask_unrelated_adapter_failure(self):
        with mock.patch.object(
            sealing.integration_adapter,
            "build_all_arm_window",
            side_effect=adapter.IntegrationError("unrelated failure"),
        ), mock.patch.object(
            sealing.integration_adapter, "build_window_cycle_inputs"
        ) as inputs:
            with self.assertRaisesRegex(adapter.IntegrationError, "unrelated"):
                sealing._build_window_with_required_diagnostic(object(), "w0")
        inputs.assert_not_called()

    def test_sealing_module_has_no_score_or_metric_entrypoint(self):
        source = Path(sealing.__file__).read_text(encoding="utf-8")
        self.assertNotIn("score_window", source)
        self.assertNotIn("EventLoss", source)
        self.assertNotIn("metric", source.lower())


if __name__ == "__main__":
    unittest.main()
