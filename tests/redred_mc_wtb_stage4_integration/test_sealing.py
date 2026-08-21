from __future__ import annotations

from copy import deepcopy
import hashlib
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
    return sealed, diagnostic


class SealingTests(unittest.TestCase):
    def test_delayed_diagnostic_is_a_separate_observed_leaf(self):
        sealed, _diagnostic = pressure_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            leaf = "windows/w0/arms/delayed_exact"
            sealing._write_leaf_inputs(root, leaf, sealed)
            files = {}
            binding = sealing._observe_leaf(
                root,
                leaf,
                sealed,
                ASSAY_SHA,
                RAY_SHA,
                len(sealed.query_records),
                files,
            )

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

            arms = {}
            for arm in Arm:
                arm_leaf = root / "windows" / "w0" / "arms" / arm.value
                arm_leaf.mkdir(parents=True, exist_ok=True)
                receipt_path = arm_leaf / "cycle-receipts.json"
                if not receipt_path.exists():
                    receipt_path.write_bytes(canonical_json_bytes([]))
                arms[arm.value] = {
                    "delayed_unbounded_depth_diagnostic": (
                        binding["delayed_unbounded_depth_diagnostic"]
                        if arm is Arm.DELAYED_EXACT
                        else None
                    )
                }
            window_mapping = {
                "window_id": diagnostic_binding["window_id"],
                "warmup_start_ns_inclusive": diagnostic_binding[
                    "window_start_ns"
                ],
                "arms": arms,
            }
            window_relative = "windows/w0/window-seal.json"
            window_path = root / window_relative
            window_path.write_bytes(canonical_json_bytes(window_mapping))
            files[window_relative] = sealing._observe_file(
                root, window_relative, kind="object", record_count=1
            )
            campaign = {
                "assay_manifest_sha256": ASSAY_SHA,
                "files": files,
                "windows": [{
                    "path": window_relative,
                    "sha256": hashlib.sha256(window_path.read_bytes()).hexdigest(),
                }],
            }
            sealing._verify_delayed_diagnostic_links(root, campaign)
            mutant = deepcopy(campaign)
            mutant["windows"] = list(mutant["windows"])
            mutated_window = deepcopy(window_mapping)
            mutated_window["arms"][Arm.DELAYED_EXACT.value][
                "delayed_unbounded_depth_diagnostic"
            ]["bounded_cycle_receipts_sha256"] = "e" * 64
            window_path.write_bytes(canonical_json_bytes(mutated_window))
            mutant["windows"][0]["sha256"] = hashlib.sha256(
                window_path.read_bytes()
            ).hexdigest()
            mutant["files"][window_relative]["sha256"] = mutant["windows"][0][
                "sha256"
            ]
            mutant["files"][window_relative]["size_bytes"] = len(
                window_path.read_bytes()
            )
            with self.assertRaisesRegex(
                sealing.SealingError, "bounded-evidence binding"
            ):
                sealing._verify_delayed_diagnostic_links(root, mutant)

    def test_independent_replay_rejects_resealed_peak_mutant(self):
        _sealed, diagnostic = pressure_fixture()
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
        _sealed, diagnostic = pressure_fixture()
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
        _sealed, diagnostic = pressure_fixture()
        mapping = diagnostic.to_mapping()
        payload = canonical_json_bytes(mapping)
        self.assertEqual(mapping["evidence_sha256"], diagnostic.evidence_sha256)
        self.assertNotEqual(hashlib.sha256(payload).hexdigest(), diagnostic.evidence_sha256)

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
