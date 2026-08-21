from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
    load_comparison_contract,
    validate_decision_records,
)
from benchmarks.redred_mc_wtb_causal_reference.development import window_registry
from benchmarks.redred_mc_wtb_stage4_cyclemodel import Arm, Event, run_cycle_model
from benchmarks.redred_mc_wtb_stage4_integration import adapter, sealing
from benchmarks.redred_mc_wtb_stage4_score_runner import runner
from benchmarks.redred_mc_wtb_stage4_scoring.scoring import (
    ArmAggregate,
    EventLoss,
    LatencySummary,
    ScoreInputManifest,
    WindowMetrics,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
ASSAY_SHA = "c" * 64
AUTHORITY_SHA = "d" * 64


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def ray_shadow(arm):
    if arm == "delayed_exact":
        return {
            "arm": arm,
            "ray": [0.0, 0.0, 1.0],
            "transform": "delayed_slerp",
            "pose_ids": [10, 11],
            "pose_timestamps_ns": [0, 200],
            "pose_commit_cycles": [0, 2],
            "pose_sha256": [HASH_A, HASH_B],
        }
    transform = "oracle_prefix" if arm.startswith("oracle_") else "occurrence_zoh"
    return {
        "arm": arm,
        "ray": [0.0, 0.0, 1.0],
        "transform": transform,
        "pose_ids": [10],
        "pose_timestamps_ns": [0],
        "pose_commit_cycles": [0],
        "pose_sha256": [HASH_A],
    }


class SyntheticSeal:
    def __init__(self, root):
        self.root = Path(root)
        self.contract = load_comparison_contract()
        self.window_rows = tuple(window_registry())
        self.window_order = tuple(row["window_id"] for row in self.window_rows)
        self._build()

    def _build(self):
        write_json(self.root / "assay-closure.json", {
            "schema": "redred.mc_wtb.stage4_score_free_assay_closure/v1",
            "assay_manifest_sha256": ASSAY_SHA,
            "assay_authority_sha256": AUTHORITY_SHA,
            "comparison_contract_sha256": self.contract.canonical_sha256,
            "registry_sha256": self.contract.registry["sha256"],
        })
        files = {
            "assay-closure.json": sealing._observe_file(
                self.root, "assay-closure.json", kind="object", record_count=1
            )
        }
        scorer_sha = sha256(Path(runner.scoring_module.__file__).read_bytes())
        for window_index, frozen in enumerate(self.window_rows):
            window_id = frozen["window_id"]
            event_id = 100_000 + window_index
            event = Event(
                event_id,
                frozen["query_start_ns_inclusive"],
                causal_pose_index=None,
            )
            inputs = adapter.WindowCycleInputs(
                window_id,
                frozen["warmup_start_ns_inclusive"],
                frozen["query_start_ns_inclusive"],
                frozen["query_end_ns_exclusive"],
                ({"is_query": True},),
                (event,),
                ((0.0, 0.0, 1.0),),
                (),
                (),
                {},
                {},
            )
            authoritative = sealing._authoritative_window_inputs(inputs)
            ray_values = [{
                "window_id": window_id,
                "event_id": event_id,
                "timestamp_ns": event.timestamp_ns,
                "polarity": 0,
                "is_query": True,
                "sensor_ray": [0.0, 0.0, 1.0],
                "world_shadow_rays": [
                    ray_shadow(arm) for arm in sorted(runner._ARM_ORDER)
                ],
            }]
            ray_relative = "windows/%s/ray-events.json" % window_id
            write_json(self.root / ray_relative, ray_values)
            files[ray_relative] = sealing._observe_file(
                self.root, ray_relative, kind="array", record_count=1
            )
            ray_sha = files[ray_relative]["sha256"]
            arms = {}
            for arm_value in runner._ARM_ORDER:
                arm = Arm(arm_value)
                result = run_cycle_model(
                    window_id=window_id,
                    window_start_ns=frozen["warmup_start_ns_inclusive"],
                    arm=arm,
                    events=(event,),
                    poses=(),
                    synthetic_test_mode=True,
                )
                records = tuple(adapter._convert_record(row) for row in result.records)
                receipt = validate_decision_records(
                    self.contract,
                    [event_id],
                    records,
                    expected_window_id=window_id,
                    expected_arm=arm_value,
                )
                accounting, accounting_evidence = adapter._derive_accounting(
                    inputs, result, records
                )
                full_sha = canonical_sha256(adapter._full_cycle_evidence(result))
                cycles_sha = result.cycle_receipts_sha256
                records_sha = receipt.decision_records_sha256
                manifest = ScoreInputManifest(
                    window_id,
                    arm_value,
                    receipt.canonical_sha256(),
                    accounting.canonical_sha256(),
                    ray_sha,
                    ASSAY_SHA,
                    full_sha,
                    cycles_sha,
                    records_sha,
                    tuple(sorted({
                        "protocol": self.contract.canonical_sha256,
                        "registry": self.contract.registry["sha256"],
                        "arm_parameters": canonical_sha256(
                            self.contract.arms[arm_value]
                        ),
                        "generator": "1" * 64,
                        "cycle_model": "2" * 64,
                        "scorer": scorer_sha,
                        "sources": "3" * 64,
                        "runtime": "4" * 64,
                    }.items())),
                )
                sealed = SimpleNamespace(
                    arm=arm,
                    simulation=result,
                    query_records=records,
                    receipt=receipt,
                    accounting=accounting,
                    accounting_evidence=accounting_evidence,
                    manifest=manifest,
                    delayed_unbounded_diagnostic=None,
                )
                leaf_relative = "windows/%s/arms/%s" % (window_id, arm_value)
                sealing._write_leaf_inputs(self.root, leaf_relative, sealed)
                arms[arm_value] = sealing._observe_leaf(
                    self.root,
                    leaf_relative,
                    sealed,
                    ASSAY_SHA,
                    ray_sha,
                    1,
                    authoritative,
                    files,
                )
            window_relative = "windows/%s/window-seal.json" % window_id
            write_json(self.root / window_relative, {
                "schema": runner._WINDOW_SCHEMA,
                "window_id": window_id,
                "warmup_start_ns_inclusive": frozen["warmup_start_ns_inclusive"],
                "query_start_ns_inclusive": frozen["query_start_ns_inclusive"],
                "query_end_ns_exclusive": frozen["query_end_ns_exclusive"],
                "selected_event_count": 1,
                "query_event_count": 1,
                "ordered_query_event_ids_sha256": canonical_sha256([event_id]),
                "ray_events_path": ray_relative,
                "ray_events_sha256": ray_sha,
                "authoritative_cycle_inputs": authoritative,
                "arms": arms,
            })
            files[window_relative] = sealing._observe_file(
                self.root, window_relative, kind="object", record_count=1
            )
        self.reseal()

    def reseal(self):
        for window_id in self.window_order:
            window_path = self.root / "windows" / window_id / "window-seal.json"
            window = json.loads(window_path.read_text(encoding="ascii"))
            for arm in runner._ARM_ORDER:
                leaf = self.root / "windows" / window_id / "arms" / arm
                window["arms"][arm]["score_input_manifest_sha256"] = sha256(
                    (leaf / "score-input-manifest.json").read_bytes()
                )
                window["arms"][arm]["score_boundary_evidence_sha256"] = sha256(
                    (leaf / "score-boundary-evidence.json").read_bytes()
                )
            write_json(window_path, window)
        files = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.name == runner._CAMPAIGN_FILE:
                continue
            relative = path.relative_to(self.root).as_posix()
            value = json.loads(path.read_text(encoding="ascii"))
            kind = "array" if isinstance(value, list) else "object"
            files[relative] = {
                "sha256": sha256(path.read_bytes()),
                "size_bytes": len(path.read_bytes()),
                "kind": kind,
                "record_count": len(value) if isinstance(value, list) else 1,
            }
        windows = []
        for window_id in self.window_order:
            relative = "windows/%s/window-seal.json" % window_id
            windows.append({
                "window_id": window_id,
                "path": relative,
                "sha256": files[relative]["sha256"],
            })
        campaign = {
            "schema": runner._CAMPAIGN_SCHEMA,
            "content_class": runner._CAMPAIGN_CONTENT,
            "assay_manifest_sha256": ASSAY_SHA,
            "assay_authority_sha256": AUTHORITY_SHA,
            "assay_closure_sha256": files["assay-closure.json"]["sha256"],
            "comparison_contract_sha256": self.contract.canonical_sha256,
            "registry_sha256": self.contract.registry["sha256"],
            "window_count": 24,
            "arm_count": 4,
            "arm_window_count": 96,
            "window_order": list(self.window_order),
            "arm_order": list(runner._ARM_ORDER),
            "windows": windows,
            "files": files,
        }
        write_json(self.root / runner._CAMPAIGN_FILE, campaign)
        self.global_sha256 = sha256(
            (self.root / runner._CAMPAIGN_FILE).read_bytes()
        )
        return self.global_sha256


def fake_contract(contract):
    registry = dict(contract.registry)
    registry["query_event_count"] = 24
    return SimpleNamespace(
        canonical_sha256=contract.canonical_sha256,
        registry=registry,
        arms=contract.arms,
    )


def fake_window_metric(call):
    receipt = call.args[1]
    delayed = receipt.arm == "delayed_exact"
    event_id = call.args[2][0].event_id
    event_loss = EventLoss(
        event_id,
        1.0,
        0.5,
        1.0,
        False,
        False,
        event_id,
        event_id,
        1,
        1,
    )
    return WindowMetrics(
        receipt.window_id,
        receipt.arm,
        call.kwargs["expected_manifest_sha256"],
        call.kwargs["expected_receipt_sha256"],
        call.kwargs["expected_accounting_sha256"],
        (event_loss,),
        0 if delayed else 1,
        0,
        1 if delayed else 0,
        1 if delayed else 0,
        1,
        1,
        102,
        192_000,
        102_000,
        108_799,
        0,
        0,
        0,
        0,
    )


def fake_aggregate(_contract, windows):
    arm = windows[0].arm
    delayed = arm == "delayed_exact"
    latency = LatencySummary(24, 1.0, 1, 1, 1, 1)
    return ArmAggregate(
        arm,
        tuple(windows),
        24,
        0,
        24 if delayed else 0,
        0 if delayed else 24,
        0,
        24 if delayed else 0,
        0,
        0.0,
        None,
        0,
        0.0,
        0.0 if delayed else 1.0,
        0.0,
        1.0 if delayed else None,
        None,
        latency,
        latency,
        1,
        1,
        24 * 102,
        192_000,
        102_000,
        108_799,
        0,
        0,
        0,
        0,
        "STOP",
        "STOP",
    )


class ScoreRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.seal = SyntheticSeal(self.root / "seal")
        self.output = self.root / "official-result.json"
        self.contract_patch = mock.patch.object(
            runner,
            "load_comparison_contract",
            return_value=fake_contract(self.seal.contract),
        )
        self.contract_patch.start()
        self.addCleanup(self.contract_patch.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def run_success(self):
        score_calls = []

        def score_side_effect(*args, **kwargs):
            call = SimpleNamespace(args=args, kwargs=kwargs)
            score_calls.append(call)
            return fake_window_metric(call)

        with mock.patch.object(
            runner, "score_window", side_effect=score_side_effect
        ) as score, mock.patch.object(
            runner, "aggregate_arm", side_effect=fake_aggregate
        ) as aggregate, mock.patch.object(
            runner,
            "validate_complete_comparison",
            side_effect=lambda values: tuple(sorted(values, key=lambda row: row.arm)),
        ) as complete:
            result = runner.run_official_score(
                self.seal.root,
                expected_global_seal_sha256=self.seal.global_sha256,
                output_path=self.output,
            )
        return result, score, aggregate, complete, score_calls

    def test_scores_exactly_96_unique_leaves_in_frozen_order_and_seals_once(self):
        result, score, aggregate, complete, calls = self.run_success()
        self.assertEqual(score.call_count, 96)
        self.assertEqual(aggregate.call_count, 4)
        complete.assert_called_once()
        keys = [(call.args[1].window_id, call.args[1].arm) for call in calls]
        self.assertEqual(keys, [
            (window_id, arm)
            for window_id in self.seal.window_order
            for arm in runner._ARM_ORDER
        ])
        self.assertEqual(len(set(keys)), 96)
        self.assertTrue(self.output.is_file())
        payload = self.output.read_bytes()
        observed = json.loads(payload)
        body = dict(observed)
        seal = body.pop("result_seal")
        self.assertEqual(seal["sha256"], canonical_sha256(body))
        self.assertEqual(result.output_sha256, sha256(payload))
        self.assertEqual(observed["execution"]["score_window_call_count"], 96)
        self.assertFalse(observed["execution"]["resume_supported"])
        self.assertEqual(
            observed["arm_semantic_limits"]["delayed_exact"]["label"],
            "DIAGNOSTIC_UPPER_BOUND",
        )
        self.assertEqual(
            observed["arm_semantic_limits"][
                "oracle_resampled_groundtruth_1khz"
            ]["label"],
            "INTERFACE_VALUE_ONLY",
        )
        self.assertEqual(
            observed["input_bindings"]["scorer_py_sha256"],
            sha256(Path(runner.scoring_module.__file__).read_bytes()),
        )
        self.assertEqual(
            observed["input_bindings"]["causal_reference_py_sha256"],
            sha256(Path(runner.reference_module.__file__).read_bytes()),
        )
        authoritative = [
            json.loads(
                (
                    self.seal.root
                    / "windows"
                    / window_id
                    / "window-seal.json"
                ).read_text(encoding="ascii")
            )["authoritative_cycle_inputs"]
            for window_id in self.seal.window_order
        ]
        self.assertEqual(
            observed["input_bindings"][
                "authoritative_window_cycle_inputs_sha256"
            ],
            canonical_sha256(authoritative),
        )

    def test_external_seal_mismatch_fails_before_scoring(self):
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "external seal"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256="0" * 64,
                    output_path=self.output,
                )
        score.assert_not_called()
        self.assertFalse(os.path.lexists(str(self.output)))

    def test_symlink_and_unindexed_content_fail_before_scoring(self):
        ray = self.seal.root / "windows" / self.seal.window_order[0] / "ray-events.json"
        outside = self.root / "outside.json"
        outside.write_bytes(ray.read_bytes())
        ray.unlink()
        ray.symlink_to(outside)
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "symlink"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()
        ray.unlink()
        ray.write_bytes(outside.read_bytes())
        ray.unlink()
        os.link(str(outside), str(ray))
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "hard-linked"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()
        ray.unlink()
        ray.write_bytes(outside.read_bytes())
        (self.seal.root / "unindexed.json").write_bytes(canonical_json_bytes({}))
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "inventory"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()

    def test_overwrite_and_resume_are_rejected_before_scoring(self):
        self.output.write_bytes(b"existing")
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "resume/overwrite"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_leaf_failure_is_not_retried_and_publishes_no_partial_result(self):
        calls = []

        def fail_once(*args, **kwargs):
            calls.append((args[1].window_id, args[1].arm))
            if len(calls) == 11:
                raise RuntimeError("synthetic leaf failure")
            return fake_window_metric(SimpleNamespace(args=args, kwargs=kwargs))

        with mock.patch.object(runner, "score_window", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "synthetic leaf failure"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        self.assertEqual(len(calls), 11)
        self.assertEqual(len(set(calls)), 11)
        self.assertFalse(os.path.lexists(str(self.output)))

    def test_resealed_wrong_scorer_binding_fails_before_scoring(self):
        window = self.seal.window_order[0]
        path = (
            self.seal.root
            / "windows"
            / window
            / "arms"
            / "zoh_freshness"
            / "score-input-manifest.json"
        )
        value = json.loads(path.read_text(encoding="ascii"))
        value["artifact_sha256"]["scorer"] = "f" * 64
        write_json(path, value)
        self.seal.reseal()
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "code, contract"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()

    def test_resealed_noncanonical_window_order_fails_before_scoring(self):
        campaign_path = self.seal.root / runner._CAMPAIGN_FILE
        campaign = json.loads(campaign_path.read_text(encoding="ascii"))
        campaign["window_order"][0], campaign["window_order"][1] = (
            campaign["window_order"][1],
            campaign["window_order"][0],
        )
        campaign["windows"][0], campaign["windows"][1] = (
            campaign["windows"][1],
            campaign["windows"][0],
        )
        write_json(campaign_path, campaign)
        external_sha = sha256(campaign_path.read_bytes())
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "frozen|24-by-4"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=external_sha,
                    output_path=self.output,
                )
        score.assert_not_called()

    def test_authoritative_window_binding_parser_is_exact(self):
        window_path = (
            self.seal.root
            / "windows"
            / self.seal.window_order[0]
            / "window-seal.json"
        )
        window = json.loads(window_path.read_text(encoding="ascii"))
        authoritative = window["authoritative_cycle_inputs"]
        parsed = runner._parse_authoritative_inputs(
            authoritative,
            window_id=window["window_id"],
            window_start_ns=window["warmup_start_ns_inclusive"],
            selected_count=window["selected_event_count"],
        )
        self.assertEqual(parsed, authoritative)
        mutant = dict(authoritative)
        mutant["unexpected"] = 1
        with self.assertRaisesRegex(runner.ScoreRunnerError, "fields differ"):
            runner._parse_authoritative_inputs(
                mutant,
                window_id=window["window_id"],
                window_start_ns=window["warmup_start_ns_inclusive"],
                selected_count=window["selected_event_count"],
            )

    def test_resealed_extra_receipt_field_fails_strict_reconstruction(self):
        window = self.seal.window_order[0]
        leaf = self.seal.root / "windows" / window / "arms" / "zoh_freshness"
        receipt_path = leaf / "decision-receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="ascii"))
        receipt["unexpected"] = 1
        write_json(receipt_path, receipt)
        manifest_path = leaf / "score-input-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
        manifest["decision_receipt_sha256"] = sha256(receipt_path.read_bytes())
        write_json(manifest_path, manifest)
        self.seal.reseal()
        with mock.patch.object(runner, "score_window") as score:
            with self.assertRaisesRegex(runner.ScoreRunnerError, "fields differ"):
                runner.run_official_score(
                    self.seal.root,
                    expected_global_seal_sha256=self.seal.global_sha256,
                    output_path=self.output,
                )
        score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
