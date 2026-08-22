from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import campaign108, screen108
from benchmarks.redred_mc_wtb_predictor_stage3.campaign108 import (
    Campaign108Error,
    frozen_candidate_config_bytes,
    run_campaign108,
    verify_campaign108_receipt,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import New108AdapterBundle
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    pose_timestamp_to_cycle,
    timestamp_to_cycle,
)


ZERO_SHA = "0" * 64


def _quaternion(angle):
    return (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0))


def _ray(angle):
    return (math.cos(angle), math.sin(angle), 0.0)


def _pose(pose_id, timestamp_ns, angle, commit_cycle=None):
    quaternion = _quaternion(angle)
    cycle = (
        pose_timestamp_to_cycle(timestamp_ns, 0)
        if commit_cycle is None
        else commit_cycle
    )
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        cycle,
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(event_id, timestamp_ns, is_query, angle, pose_index):
    ray = _ray(angle)
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        0,
        is_query,
        ray,
        pose_index,
        canonical_event_content_sha256(
            event_id, timestamp_ns, 0, is_query, ray, pose_index
        ),
    )


class ForbiddenLabels(dict):
    def _forbidden(self, *args, **kwargs):
        raise AssertionError("label sidecar was accessed before candidate seal")

    __getitem__ = _forbidden
    __bool__ = _forbidden
    __contains__ = _forbidden
    __iter__ = _forbidden
    __len__ = _forbidden
    get = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden
    copy = _forbidden


def _fixture(same_edge_angle=None):
    registry = NeutralRegistryWindow("fixture-window", 0, 50_000_000, 51_000_000)
    poses = [
        _pose(0, 47_000_000, 0.000),
        _pose(1, 48_000_000, 0.010),
        _pose(2, 49_000_000, 0.021),
    ]
    if same_edge_angle is not None:
        poses.append(_pose(
            3,
            50_000_000,
            same_edge_angle,
            timestamp_to_cycle(50_000_000, 0),
        ))
    events = (
        _event(10, 49_500_000, False, 0.00, 2),
        _event(11, 50_000_000, True, 0.10, 2),
        _event(12, 50_100_000, True, 0.20, 3 if same_edge_angle is not None else 2),
    )
    poses_tuple = tuple(poses)
    baseline = evaluate_current_cav_registry(
        (registry,), {registry.window_id: events}, {registry.window_id: poses_tuple}
    )
    bundle = New108AdapterBundle(
        {},
        (registry,),
        {registry.window_id: events},
        {registry.window_id: poses_tuple},
        ForbiddenLabels(),
        {"aggregate_sha256": "1" * 64},
    )
    return bundle, baseline


def _cncp():
    return {
        "B_ff": 512,
        "B_sram": 0,
        "read_ports": 1,
        "write_ports": 1,
        "O_pose": {"add_compare": 4, "fixed_multiply": 2, "nonlinear": 1},
        "O_event": {"add_compare": 2, "fixed_multiply": 3, "nonlinear": 0},
        "II_event": 1,
        "critical_depth": 3,
        "pipeline_bits": 128,
        "max_wire_width": 32,
        "numeric_risk": "N3",
        "state_class": "S1",
        "compute_class": "C2",
        "pipeline_class": "P1",
        "endpoint_target_ns": 6.5,
        "event_lanes": 2,
    }


def _fixture_screen_result(
    candidate_id,
    candidate_output_sha256,
    candidate_executable_sha256,
    candidate_config_sha256,
    cncp,
):
    body = {
        "schema": "fixture-screen-result/v1",
        "status": "FIXTURE_ONLY",
        "candidate_id": candidate_id,
        "cncp": cncp,
        "provenance": {
            "candidate_output_sha256": candidate_output_sha256,
            "candidate_executable_sha256": candidate_executable_sha256,
            "candidate_config_sha256": candidate_config_sha256,
        },
    }
    return dict(body, result_sha256=canonical_sha256(body))


def _event_row(output, event_id):
    return next(
        row
        for window in output["windows"]
        for row in window["events"]
        if row["event_id"] == event_id
    )


def _bundle_with_event_ray(bundle, event_id, angle):
    event_streams = dict(bundle.event_streams)
    window_id = bundle.neutral_registry[0].window_id
    events = []
    for event in event_streams[window_id]:
        if event.event_id != event_id:
            events.append(event)
            continue
        ray = _ray(angle)
        events.append(NeutralEventInput(
            event.event_id,
            event.timestamp_ns,
            event.polarity,
            event.is_query,
            ray,
            event.causal_pose_source_index,
            canonical_event_content_sha256(
                event.event_id,
                event.timestamp_ns,
                event.polarity,
                event.is_query,
                ray,
                event.causal_pose_source_index,
                event.transform_guard_valid,
            ),
            event.transform_guard_valid,
        ))
    event_streams[window_id] = tuple(events)
    return New108AdapterBundle(
        bundle.selector_registry,
        bundle.neutral_registry,
        event_streams,
        bundle.pose_streams,
        ForbiddenLabels(),
        bundle.provenance_seal,
    )


def _reseal_output(value):
    output = deepcopy(value)
    for window in output["windows"]:
        for event in window["events"]:
            body = dict(event)
            body.pop("decision_sha256", None)
            event["decision_sha256"] = canonical_sha256(body)
        window["events_sha256"] = canonical_sha256(window["events"])
    unsigned = dict(output)
    unsigned.pop("aggregate_sha256", None)
    output["aggregate_sha256"] = canonical_sha256(unsigned)
    return output


class Campaign108Tests(unittest.TestCase):
    def _files(self, root, candidate_id):
        config = root / "config.json"
        cncp = root / "cncp.json"
        config.write_bytes(frozen_candidate_config_bytes(candidate_id))
        cncp.write_text(json.dumps(_cncp(), sort_keys=True), encoding="utf-8")
        return config, cncp

    def _screen(self, candidate_id, calls=None):
        def locked_screen(dataset, output, executable, config, cncp):
            if calls is not None:
                calls.append("screen")
            self.assertTrue(output.exists())
            generated = json.loads(output.read_text(encoding="utf-8"))
            return _fixture_screen_result(
                candidate_id,
                generated["aggregate_sha256"],
                hashlib.sha256(executable.read_bytes()).hexdigest(),
                hashlib.sha256(config.read_bytes()).hexdigest(),
                cncp,
            )

        return locked_screen

    def test_registry_dispatches_exact_tested_adapters_and_authorities(self):
        bundle, baseline = _fixture()
        neutral = campaign108._neutral_view(bundle)
        neutral_baseline = campaign108._neutral_baseline_view(baseline, neutral)
        for candidate_id in campaign108.FROZEN_CANDIDATE_IDS:
            with self.subTest(candidate_id=candidate_id):
                spec = campaign108._candidate(candidate_id)
                adapter_bytes, executable_bytes, model_bytes = (
                    campaign108._verify_authorities(spec)
                )
                self.assertEqual(
                    hashlib.sha256(adapter_bytes).hexdigest(),
                    spec.output_adapter_sha256,
                )
                self.assertEqual(
                    hashlib.sha256(executable_bytes).hexdigest(),
                    spec.candidate_executable_sha256,
                )
                self.assertEqual(
                    hashlib.sha256(model_bytes).hexdigest(), spec.model_sha256
                )
                self.assertEqual(
                    hashlib.sha256(spec.config_bytes).hexdigest(), spec.config_sha256
                )
                output = spec.adapter(neutral, neutral_baseline)
                self.assertEqual(output["candidate_id"], candidate_id)
                self.assertEqual(
                    output["candidate_executable_sha256"],
                    spec.candidate_executable_sha256,
                )
                self.assertEqual(output["candidate_config_sha256"], spec.config_sha256)
        with self.assertRaisesRegex(Campaign108Error, "frozen Stage3 registry"):
            campaign108._candidate("RG3-TUNED-AFTER-RESULT")

    def test_same_edge_pose_mutation_cannot_change_same_edge_decision(self):
        left_bundle, left_baseline = _fixture(same_edge_angle=0.4)
        right_bundle, right_baseline = _fixture(same_edge_angle=-0.7)
        left_neutral = campaign108._neutral_view(left_bundle)
        right_neutral = campaign108._neutral_view(right_bundle)
        left_baseline = campaign108._neutral_baseline_view(
            left_baseline, left_neutral
        )
        right_baseline = campaign108._neutral_baseline_view(
            right_baseline, right_neutral
        )
        for candidate_id in campaign108.FROZEN_CANDIDATE_IDS:
            with self.subTest(candidate_id=candidate_id):
                spec = campaign108._candidate(candidate_id)
                left = spec.adapter(left_neutral, left_baseline)
                right = spec.adapter(right_neutral, right_baseline)
                self.assertEqual(_event_row(left, 11), _event_row(right, 11))

    def test_unrelated_event_ray_cannot_change_prior_event_decision(self):
        left_bundle, left_baseline = _fixture()
        right_bundle = _bundle_with_event_ray(left_bundle, 12, 1.7)
        right_baseline = evaluate_current_cav_registry(
            right_bundle.neutral_registry,
            right_bundle.event_streams,
            right_bundle.pose_streams,
        )
        left_neutral = campaign108._neutral_view(left_bundle)
        right_neutral = campaign108._neutral_view(right_bundle)
        left_baseline = campaign108._neutral_baseline_view(
            left_baseline, left_neutral
        )
        right_baseline = campaign108._neutral_baseline_view(
            right_baseline, right_neutral
        )
        for candidate_id in campaign108.FROZEN_CANDIDATE_IDS:
            with self.subTest(candidate_id=candidate_id):
                spec = campaign108._candidate(candidate_id)
                left = spec.adapter(
                    left_neutral, left_baseline
                )
                right = spec.adapter(
                    right_neutral, right_baseline
                )
                self.assertEqual(_event_row(left, 11), _event_row(right, 11))

    def test_future_v2_row_fields_are_preserved_opaquely(self):
        bundle, baseline = _fixture()
        neutral = campaign108._neutral_view(bundle)
        neutral_baseline = campaign108._neutral_baseline_view(baseline, neutral)
        spec = campaign108._candidate(campaign108.RG3_ID)
        output = deepcopy(spec.adapter(neutral, neutral_baseline))
        event = output["windows"][0]["events"][0]
        event["occurrence_cycle"] = event["decision_cycle"] - 1
        event["state_sha256"] = "2" * 64
        output = _reseal_output(output)
        checked = campaign108._validate_adapter_output(
            output, spec, neutral, neutral_baseline
        )
        self.assertEqual(
            checked["windows"][0]["events"][0]["occurrence_cycle"],
            event["decision_cycle"] - 1,
        )
        self.assertEqual(
            checked["windows"][0]["events"][0]["state_sha256"], "2" * 64
        )

    def test_single_attempt_persists_adapter_output_before_locked_screen(self):
        bundle, baseline = _fixture()
        candidate_id = campaign108.RG3_ID
        calls = []
        original = campaign108._candidate(candidate_id)

        def adapter(supplied_bundle, supplied_baseline):
            self.assertIs(type(supplied_bundle), campaign108.NeutralAdapterView)
            self.assertFalse(hasattr(supplied_bundle, "selector_labels"))
            self.assertIs(
                type(supplied_baseline), campaign108.NeutralBaselineView
            )
            self.assertFalse(hasattr(supplied_baseline.windows[0], "query_events"))
            calls.append("adapter")
            return original.adapter(supplied_bundle, supplied_baseline)

        spec = replace(original, adapter=adapter)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108,
                "run_locked_screen108",
                side_effect=self._screen(candidate_id, calls),
            ) as screen, mock.patch.object(
                screen108,
                "verify_screen108_result_envelope",
                side_effect=lambda value: value["result_sha256"],
            ):
                receipt = run_campaign108(
                    candidate_id,
                    root / "dataset-not-opened",
                    config,
                    cncp,
                    campaign_dir,
                )
            self.assertEqual(calls, ["adapter", "adapter", "screen"])
            screen.assert_called_once()
            paths = campaign108._artifact_paths(campaign_dir, candidate_id)
            output = json.loads(paths["candidate_output"].read_text(encoding="utf-8"))
            direct_neutral = campaign108._neutral_view(bundle)
            direct = original.adapter(
                direct_neutral,
                campaign108._neutral_baseline_view(baseline, direct_neutral),
            )
            self.assertEqual(output, direct)
            self.assertEqual(receipt["candidate_id"], output["candidate_id"])
            self.assertEqual(
                receipt["bindings"]["output_adapter_sha256"],
                spec.output_adapter_sha256,
            )
            self.assertEqual(receipt["policy"]["attempt_count"], 1)
            self.assertEqual(receipt["policy"]["adapter_execution_count"], 2)
            self.assertEqual(receipt["policy"]["verification_replay_count"], 1)
            self.assertFalse(
                receipt["policy"]["verification_replay_is_tuning"]
            )
            self.assertFalse(
                receipt["policy"]["verification_replay_output_scored"]
            )
            replay = json.loads(paths["replay"].read_text(encoding="utf-8"))
            self.assertTrue(replay["candidate_output_byte_identical"])
            self.assertTrue(replay["evidence_byte_identical"])
            self.assertEqual(
                receipt["bindings"]["model_sha256"], spec.model_sha256
            )
            self.assertRegex(
                receipt["bindings"]["dependency_manifest_sha256"],
                r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                receipt["bindings"]["campaign_runner_sha256"],
                r"^[0-9a-f]{64}$",
            )
            with mock.patch.object(
                screen108,
                "verify_screen108_result_envelope",
                side_effect=lambda value: value["result_sha256"],
            ):
                self.assertRegex(
                    verify_campaign108_receipt(receipt, campaign_dir),
                    r"^[0-9a-f]{64}$",
                )

    def test_semantically_equal_but_nonexact_config_bytes_are_rejected(self):
        candidate_id = campaign108.SO3_PLL_ID
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            parsed = json.loads(config.read_text(encoding="utf-8"))
            config.write_text(json.dumps(parsed, indent=2, sort_keys=True), encoding="utf-8")
            with mock.patch.object(
                campaign108, "build_locked_new108_adapter"
            ) as build, self.assertRaisesRegex(Campaign108Error, "config bytes"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, root / "campaign"
                )
            build.assert_not_called()
            self.assertFalse((root / "campaign").exists())

    def test_output_adapter_and_candidate_executable_drift_fail_preflight(self):
        candidate_id = campaign108.DSPB_ID
        spec = campaign108._candidate(candidate_id)
        real_read = campaign108._read_bytes
        for drift_where in ("output adapter", "candidate executable"):
            with self.subTest(drift_where=drift_where), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config, cncp = self._files(root, candidate_id)

                def drifting_read(path, where):
                    payload = real_read(path, where)
                    return payload + b"fixture-drift" if where == drift_where else payload

                with mock.patch.object(
                    campaign108, "_read_bytes", side_effect=drifting_read
                ), mock.patch.object(
                    campaign108, "build_locked_new108_adapter"
                ) as build, self.assertRaisesRegex(Campaign108Error, "frozen ID"):
                    run_campaign108(
                        candidate_id,
                        root / "dataset",
                        config,
                        cncp,
                        root / "campaign",
                    )
                build.assert_not_called()
                self.assertFalse((root / "campaign").exists())
        self.assertNotEqual(spec.output_adapter_path, spec.candidate_executable_path)

    def test_dependency_source_substitution_fails_before_attempt(self):
        candidate_id = campaign108.RG3_ID
        real_read = campaign108._read_bytes
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)

            def substituted_read(path, where):
                payload = real_read(path, where)
                if where == "pose_geometry dependency":
                    return payload + b"fixture-source-substitution"
                return payload

            with mock.patch.object(
                campaign108, "_read_bytes", side_effect=substituted_read
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter"
            ) as build, self.assertRaisesRegex(Campaign108Error, "frozen manifest"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "campaign",
                )
            build.assert_not_called()
            self.assertFalse((root / "campaign").exists())

    def test_neutral_source_substitution_after_production_fails_before_replay(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        substituted = _bundle_with_event_ray(bundle, 12, 1.4)
        original = campaign108._candidate(candidate_id)
        adapter = mock.Mock()

        def mutate_source(neutral, supplied_baseline):
            output = original.adapter(neutral, supplied_baseline)
            bundle.event_streams.clear()
            bundle.event_streams.update(substituted.event_streams)
            return output

        adapter.side_effect = mutate_source
        spec = replace(original, adapter=adapter)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "neutral source changed"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "campaign",
                )
            self.assertEqual(adapter.call_count, 1)
            screen.assert_not_called()

    def test_native_candidate_id_substitution_is_rejected_before_replay(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        original = campaign108._candidate(candidate_id)

        def substitute_id(neutral, supplied_baseline):
            output = deepcopy(original.adapter(neutral, supplied_baseline))
            output["candidate_id"] = campaign108.DSPB_ID
            unsigned = dict(output)
            unsigned.pop("aggregate_sha256")
            output["aggregate_sha256"] = canonical_sha256(unsigned)
            return output

        spec = replace(original, adapter=mock.Mock(side_effect=substitute_id))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "frozen binding"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "campaign",
                )
            self.assertEqual(spec.adapter.call_count, 1)
            screen.assert_not_called()

    def test_nondeterministic_adapter_replay_is_rejected_before_screen(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        original = campaign108._candidate(candidate_id)
        calls = 0

        def nondeterministic(neutral, supplied_baseline):
            nonlocal calls
            calls += 1
            output = original.adapter(neutral, supplied_baseline)
            if calls == 2:
                changed = deepcopy(output)
                changed["windows"][0]["events"][0]["v2_replay_nonce"] = 1
                return _reseal_output(changed)
            return output

        spec = replace(original, adapter=nondeterministic)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "not byte-identical"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            self.assertEqual(calls, 2)
            screen.assert_not_called()
            self.assertTrue(
                campaign108._artifact_paths(campaign_dir, candidate_id)[
                    "candidate_output"
                ].exists()
            )
            self.assertFalse(
                campaign108._artifact_paths(campaign_dir, candidate_id)[
                    "replay"
                ].exists()
            )

    def test_nondeterministic_replay_evidence_is_rejected_before_screen(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        original_evidence = campaign108._dispatch_evidence
        calls = 0

        def nondeterministic_evidence(*args, **kwargs):
            nonlocal calls
            calls += 1
            evidence = deepcopy(original_evidence(*args, **kwargs))
            if calls == 2:
                evidence["windows"][0]["v2_replay_nonce"] = 1
                unsigned = dict(evidence)
                unsigned.pop("aggregate_sha256")
                evidence["aggregate_sha256"] = canonical_sha256(unsigned)
            return evidence

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            with mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                campaign108, "_dispatch_evidence", side_effect=nondeterministic_evidence
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "evidence"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "campaign",
                )
            self.assertEqual(calls, 2)
            screen.assert_not_called()

    def test_config_race_fails_after_adapter_seal_without_screen_or_retry(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        original = campaign108._candidate(candidate_id)
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"

            def racing_adapter(supplied_bundle, supplied_baseline):
                calls.append("adapter")
                output = original.adapter(supplied_bundle, supplied_baseline)
                config.write_bytes(b"{}")
                return output

            spec = replace(original, adapter=racing_adapter)
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "changed during"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            self.assertEqual(calls, ["adapter"])
            screen.assert_not_called()
            self.assertTrue(
                campaign108._artifact_paths(campaign_dir, candidate_id)[
                    "candidate_output"
                ].exists()
            )

    def test_adapter_failure_consumes_attempt_and_cannot_retry(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        adapter = mock.Mock(side_effect=RuntimeError("fixture adapter failure"))
        spec = replace(campaign108._candidate(candidate_id), adapter=adapter)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ):
                with self.assertRaisesRegex(RuntimeError, "fixture adapter"):
                    run_campaign108(
                        candidate_id, root / "dataset", config, cncp, campaign_dir
                    )
                with self.assertRaisesRegex(Campaign108Error, "attempt marker"):
                    run_campaign108(
                        candidate_id, root / "dataset", config, cncp, campaign_dir
                    )
            self.assertEqual(adapter.call_count, 1)

    def test_preexisting_candidate_output_is_not_overwritten(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            campaign_dir.mkdir()
            output = campaign108._artifact_paths(campaign_dir, candidate_id)[
                "candidate_output"
            ]
            output.write_bytes(b"fixture-existing-output\n")
            with mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "candidate output"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            self.assertEqual(output.read_bytes(), b"fixture-existing-output\n")
            screen.assert_not_called()

    def test_resealed_receipt_cannot_claim_retry_or_rtl(self):
        receipt = {
            "schema": campaign108.CAMPAIGN_SCHEMA,
            "status": "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED",
            "candidate_id": campaign108.RG3_ID,
            "model_candidate_id": campaign108._candidate(
                campaign108.RG3_ID
            ).model_candidate_id,
            "attempt_sha256": ZERO_SHA,
            "bindings": {},
            "artifacts": {},
            "policy": {
                "attempt_count": 2,
                "retry_performed": True,
                "tuning_performed": False,
                "labels_accessed_before_candidate_output_seal": False,
                "source_selection_changed": False,
                "external_data_accessed": False,
                "rtl_or_ppa_evaluated": True,
            },
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        with self.assertRaisesRegex(Campaign108Error, "policy boundary"):
            verify_campaign108_receipt(receipt, Path("unused"))


if __name__ == "__main__":
    unittest.main()
