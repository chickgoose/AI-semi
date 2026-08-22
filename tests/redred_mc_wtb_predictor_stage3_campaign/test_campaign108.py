from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    campaign108,
    candidate_authority,
    screen108,
)
from benchmarks.redred_mc_wtb_predictor_stage3.campaign108 import (
    Campaign108Error,
    run_campaign108,
    verify_campaign108_receipt,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import New108AdapterBundle
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    pose_timestamp_to_cycle,
    timestamp_to_cycle,
)


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
        raise AssertionError("label sidecar was accessed before screen")

    __getitem__ = _forbidden
    __bool__ = _forbidden
    __contains__ = _forbidden
    __iter__ = _forbidden
    __len__ = _forbidden
    get = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


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
    pose_values = tuple(poses)
    baseline = evaluate_current_cav_registry(
        (registry,), {registry.window_id: events}, {registry.window_id: pose_values}
    )
    bundle = New108AdapterBundle(
        {},
        (registry,),
        {registry.window_id: events},
        {registry.window_id: pose_values},
        ForbiddenLabels(),
        {"aggregate_sha256": "1" * 64},
    )
    return bundle, baseline


def _current_cav_fallback_fixture():
    bundle, unused = _fixture()
    del unused
    window_id = bundle.neutral_registry[0].window_id
    poses = {window_id: tuple(bundle.pose_streams[window_id][1:])}
    baseline = evaluate_current_cav_registry(
        bundle.neutral_registry, bundle.event_streams, poses
    )
    fallback_bundle = New108AdapterBundle(
        bundle.selector_registry,
        bundle.neutral_registry,
        bundle.event_streams,
        poses,
        bundle.selector_labels,
        bundle.provenance_seal,
    )
    return fallback_bundle, baseline


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


@dataclass(frozen=True)
class FakeProjection:
    screen_output: object
    projection_receipt: object
    executable_artifact_bytes: bytes
    config_bytes: bytes


def _fake_projection(native_output, config_bytes=None):
    candidate_id = native_output["candidate_id"]
    executable = ("fixture-executable:%s\n" % candidate_id).encode("ascii")
    if config_bytes is None:
        config_bytes = campaign108.frozen_candidate_config_bytes(candidate_id)
    event_fields = screen108._OUTPUT_EVENT_FIELDS - frozenset(("decision_sha256",))
    windows = []
    for native_window in native_output["windows"]:
        events = []
        for native_event in native_window["events"]:
            row = {field: native_event[field] for field in event_fields}
            row["route"] = native_event["route"].lower()
            if not native_event["candidate_used"]:
                row["model_id"] = "CURRENT_CAV"
                row["world_ray"] = None
                if row["route"] == "current_cav":
                    row["fallback_reason"] = "candidate_failure"
            events.append(row)
        windows.append({"window_id": native_window["window_id"], "events": events})
    screen_output = screen108.seal_candidate_output(
        candidate_id,
        native_output["adapter_aggregate_sha256"],
        native_output["neutral_input_sha256"],
        hashlib.sha256(executable).hexdigest(),
        hashlib.sha256(config_bytes).hexdigest(),
        windows,
    )
    receipt_windows = []
    for native_window, projected_window in zip(
        native_output["windows"], screen_output["windows"]
    ):
        event_bindings = [
            {
                "event_id": native_event["event_id"],
                "source_decision_sha256": native_event["decision_sha256"],
                "projected_decision_sha256": projected_event["decision_sha256"],
            }
            for native_event, projected_event in zip(
                native_window["events"], projected_window["events"]
            )
        ]
        window_body = {
            "window_id": native_window["window_id"],
            "source_window_sha256": native_window.get(
                "window_sha256", canonical_sha256(native_window)
            ),
            "source_events_sha256": native_window["events_sha256"],
            "projected_events_sha256": projected_window["events_sha256"],
            "event_bindings": event_bindings,
            "event_bindings_sha256": canonical_sha256(event_bindings),
        }
        receipt_windows.append(dict(
            window_body,
            window_projection_sha256=canonical_sha256(window_body),
        ))
    receipt_body = {
        "schema": "fixture-screen-projection/v1",
        "candidate_id": candidate_id,
        "native_schema": native_output["schema"],
        "native_aggregate_sha256": native_output["aggregate_sha256"],
        "projected_aggregate_sha256": screen_output["aggregate_sha256"],
        "candidate_executable_sha256": hashlib.sha256(executable).hexdigest(),
        "candidate_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "windows": receipt_windows,
    }
    receipt = dict(
        receipt_body,
        projection_receipt_sha256=canonical_sha256(receipt_body),
    )
    return FakeProjection(screen_output, receipt, executable, config_bytes)


def _reseal(value):
    result = deepcopy(value)
    unsigned = dict(result)
    unsigned.pop("aggregate_sha256", None)
    result["aggregate_sha256"] = canonical_sha256(unsigned)
    return result


class Campaign108Tests(unittest.TestCase):
    def _files(self, root, candidate_id):
        config = root / "config.json"
        cncp = root / "cncp.json"
        config.write_bytes(campaign108.frozen_candidate_config_bytes(candidate_id))
        cncp.write_text(json.dumps(_cncp(), sort_keys=True), encoding="utf-8")
        return config, cncp

    def _authority(self, spec):
        selected = {
            "candidate": spec.authority_name,
            "native_candidate_id": spec.candidate_id,
            "config_sha256": candidate_authority.candidate_config_sha256(
                spec.authority_name
            ),
            "manifest_sha256": hashlib.sha256(
                ("manifest:" + spec.authority_name).encode("ascii")
            ).hexdigest(),
        }
        body = {
            "schema": candidate_authority.CAMPAIGN_SCHEMA,
            "candidate_order": list(candidate_authority.CANDIDATE_NAMES),
            "candidates": [selected],
        }
        return dict(body, aggregate_sha256=canonical_sha256(body)), selected, ()

    def _screen(self, candidate_id, calls=None):
        def run(dataset, output_path, executable_path, config_path, cncp):
            if calls is not None:
                calls.append("screen")
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                output["candidate_executable_sha256"],
                hashlib.sha256(executable_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                output["candidate_config_sha256"],
                hashlib.sha256(config_path.read_bytes()).hexdigest(),
            )
            body = {
                "schema": "fixture-screen-result/v1",
                "status": "FIXTURE_ONLY",
                "candidate_id": candidate_id,
                "cncp": {"declared_values": cncp},
                "provenance": {
                    "candidate_output_sha256": output["aggregate_sha256"],
                    "candidate_executable_sha256": output[
                        "candidate_executable_sha256"
                    ],
                    "candidate_config_sha256": output["candidate_config_sha256"],
                },
            }
            return dict(body, result_sha256=canonical_sha256(body))

        return run

    def _run_patches(self, stack, spec, bundle, baseline, projector, screen=None):
        stack.enter_context(mock.patch.object(
            campaign108, "_CANDIDATES", {spec.candidate_id: spec}
        ))
        stack.enter_context(mock.patch.object(
            campaign108, "_campaign_authority", return_value=self._authority(spec)
        ))
        stack.enter_context(mock.patch.object(
            campaign108, "_check_authority_unchanged", return_value=None
        ))
        stack.enter_context(mock.patch.object(
            campaign108,
            "build_locked_stage3_new108_adapter",
            return_value=bundle,
        ))
        stack.enter_context(mock.patch.object(
            campaign108,
            "verify_stage3_new108_adapter",
            return_value=bundle.provenance_seal["aggregate_sha256"],
        ))
        stack.enter_context(mock.patch.object(
            campaign108, "evaluate_current_cav_registry", return_value=baseline
        ))
        stack.enter_context(mock.patch.object(
            campaign108, "_project_native_output", side_effect=projector
        ))
        stack.enter_context(mock.patch.object(
            screen108,
            "run_locked_screen108",
            side_effect=screen or self._screen(spec.candidate_id),
        ))
        stack.enter_context(mock.patch.object(
            screen108,
            "verify_screen108_result_envelope",
            side_effect=lambda value: value["result_sha256"],
        ))

    def test_current_authority_ids_and_source_closures_are_the_registry(self):
        authority = candidate_authority.build_campaign_authority()
        self.assertRegex(
            candidate_authority.verify_campaign_authority(authority), r"^[0-9a-f]{64}$"
        )
        observed = tuple(row["native_candidate_id"] for row in authority["candidates"])
        self.assertEqual(observed, campaign108.FROZEN_CANDIDATE_IDS)
        for candidate_id in observed:
            spec = campaign108._candidate(candidate_id)
            self.assertEqual(spec.candidate_id, candidate_id)
            self.assertNotIn("/", spec.artifact_stem)
            self.assertNotIn(":", spec.artifact_stem)

    def test_no_stale_literal_authority_tables_remain(self):
        source = Path(campaign108.__file__).read_text(encoding="utf-8")
        self.assertNotIn("_EXPECTED_AUTHORITIES", source)
        self.assertNotIn("_DEPENDENCY_AUTHORITIES", source)
        self.assertIn("build_campaign_authority", source)
        self.assertIn("verify_campaign_authority", source)

    def test_actual_adapters_replay_rich_output_then_project_before_screen(self):
        bundle, baseline = _fixture()
        for candidate_id in campaign108.FROZEN_CANDIDATE_IDS:
            with self.subTest(candidate_id=candidate_id), tempfile.TemporaryDirectory() as tmp:
                original = campaign108._candidate(candidate_id)
                calls = []

                def adapter(neutral, neutral_baseline, original=original):
                    self.assertIs(type(neutral), campaign108.NeutralAdapterView)
                    self.assertFalse(hasattr(neutral, "selector_labels"))
                    self.assertFalse(hasattr(neutral_baseline.windows[0], "query_events"))
                    calls.append("adapter")
                    return original.adapter(neutral, neutral_baseline)

                spec = replace(original, adapter=adapter)

                def projector(native):
                    calls.append("projector")
                    self.assertNotEqual(native["schema"], screen108.CANDIDATE_OUTPUT_SCHEMA)
                    return _fake_projection(native)

                root = Path(tmp)
                config, cncp = self._files(root, candidate_id)
                campaign_dir = root / "campaign"
                with ExitStack() as stack:
                    self._run_patches(
                        stack, spec, bundle, baseline, projector,
                        self._screen(candidate_id, calls),
                    )
                    receipt = run_campaign108(
                        candidate_id, root / "dataset-not-opened", config, cncp,
                        campaign_dir,
                    )
                self.assertEqual(calls, ["adapter", "adapter", "projector", "screen"])
                paths = campaign108._artifact_paths(campaign_dir, candidate_id)
                native = json.loads(paths["native_output"].read_text(encoding="utf-8"))
                projected = json.loads(paths["screen_output"].read_text(encoding="utf-8"))
                self.assertEqual(native["schema"], spec.native_schema)
                self.assertEqual(projected["schema"], screen108.CANDIDATE_OUTPUT_SCHEMA)
                self.assertTrue(paths["projection_receipt"].exists())
                self.assertTrue(paths["executable_artifact"].exists())
                self.assertTrue(paths["replay"].exists())
                self.assertEqual(receipt["policy"]["attempt_count"], 1)
                self.assertFalse(receipt["policy"]["verification_replay_is_tuning"])
                self.assertEqual(receipt["campaign_epoch"], 1)
                self.assertEqual(receipt["predecessor_failures"], [])
                self.assertEqual(
                    receipt["bindings"]["predecessor_failures_sha256"],
                    canonical_sha256([]),
                )

    def test_receipt_reopens_all_native_projection_and_screen_bindings(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            self._run_patches(stack, spec, bundle, baseline, _fake_projection)
            receipt = run_campaign108(
                candidate_id, root / "dataset", config, cncp, campaign_dir
            )
            self.assertRegex(
                verify_campaign108_receipt(receipt, campaign_dir), r"^[0-9a-f]{64}$"
            )

    def test_nonexact_and_projection_substituted_configs_fail_closed(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            config.write_bytes(config.read_bytes() + b"\n")
            with mock.patch.object(
                campaign108, "build_locked_stage3_new108_adapter"
            ) as build:
                with self.assertRaisesRegex(Campaign108Error, "config bytes"):
                    run_campaign108(
                        candidate_id, root / "dataset", config, cncp, root / "campaign"
                    )
            build.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)

            def wrong_config(native):
                return _fake_projection(native, b"{}\n")

            self._run_patches(stack, spec, bundle, baseline, wrong_config)
            with self.assertRaisesRegex(Campaign108Error, "caller config bytes"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, root / "campaign"
                )

    def test_expected_screen_event_normalizes_detailed_current_cav_failures(self):
        base = {
            "event_id": 7,
            "event_content_sha256": "2" * 64,
            "occurrence_cycle": 10,
            "decision_cycle": 11,
            "model_id": campaign108.RG3_ID,
            "predictor_state_version": 3,
            "used_pose_ids": [1, 2],
            "route": "CURRENT_CAV",
            "candidate_attempted": True,
            "candidate_used": False,
            "fallback_reason": "rate_change_gate",
            "world_ray": [1.0, 0.0, 0.0],
        }
        for detailed_reason in (
            "rate_change_gate",
            "pll_unlocked",
            "selected_expert_invalid:causal_cav",
        ):
            with self.subTest(reason=detailed_reason):
                mutated = dict(base, fallback_reason=detailed_reason)
                projected = campaign108._expected_screen_event(
                    mutated, campaign108.RG3_ID
                )
                self.assertEqual(projected["fallback_reason"], "candidate_failure")
                self.assertIsNone(projected["world_ray"])

    def test_expected_screen_event_does_not_relabel_other_fallback_routes(self):
        base = {
            "event_id": 7,
            "event_content_sha256": "2" * 64,
            "occurrence_cycle": 10,
            "decision_cycle": 11,
            "model_id": "CURRENT_CAV",
            "predictor_state_version": 3,
            "used_pose_ids": [2],
            "candidate_attempted": False,
            "candidate_used": False,
            "world_ray": None,
        }
        cases = (
            ("FRESH_ZOH", "fresh_zoh_fallback"),
            ("SENSOR_FIXED", "no_occurrence_pose"),
            ("SENSOR_FIXED", "invalid_pose"),
            ("SENSOR_FIXED", "stale_pose"),
        )
        for route, reason in cases:
            with self.subTest(route=route, reason=reason):
                projected = campaign108._expected_screen_event(
                    dict(base, route=route, fallback_reason=reason),
                    campaign108.RG3_ID,
                )
                self.assertEqual(projected["fallback_reason"], reason)

    def test_stage3_adapter_verifier_digest_and_prewarm_are_mandatory(self):
        bundle, unused = _fixture()
        del unused
        root = Path("synthetic-stage3-root")
        with mock.patch.object(
            campaign108, "build_locked_stage3_new108_adapter", None
        ), mock.patch.object(
            campaign108, "verify_stage3_new108_adapter", None
        ), self.assertRaisesRegex(Campaign108Error, "API is unavailable"):
            campaign108._build_verified_stage3_adapter(root)

        with mock.patch.object(
            campaign108, "build_locked_stage3_new108_adapter", return_value=bundle
        ) as build, mock.patch.object(
            campaign108,
            "verify_stage3_new108_adapter",
            return_value=bundle.provenance_seal["aggregate_sha256"],
        ) as verify:
            self.assertIs(campaign108._build_verified_stage3_adapter(root), bundle)
        build.assert_called_once_with(root)
        verify.assert_called_once_with(bundle, root)

        with mock.patch.object(
            campaign108, "build_locked_stage3_new108_adapter", return_value=bundle
        ), mock.patch.object(
            campaign108, "verify_stage3_new108_adapter", return_value="f" * 64
        ), self.assertRaisesRegex(Campaign108Error, "authority differs"):
            campaign108._build_verified_stage3_adapter(root)

        short_registry = NeutralRegistryWindow(
            "fixture-window", 1, 50_000_000, 51_000_000
        )
        short_bundle = New108AdapterBundle(
            bundle.selector_registry,
            (short_registry,),
            bundle.event_streams,
            bundle.pose_streams,
            bundle.selector_labels,
            bundle.provenance_seal,
        )
        with mock.patch.object(
            campaign108,
            "build_locked_stage3_new108_adapter",
            return_value=short_bundle,
        ), mock.patch.object(
            campaign108,
            "verify_stage3_new108_adapter",
            return_value=short_bundle.provenance_seal["aggregate_sha256"],
        ), self.assertRaisesRegex(Campaign108Error, "50 ms pre-roll"):
            campaign108._build_verified_stage3_adapter(root)

    def test_campaign_accepts_normalized_current_cav_and_rejects_detail_leak(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _current_cav_fallback_fixture()
        real_projector = campaign108._project_native_output

        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            self._run_patches(
                stack, spec, bundle, baseline, real_projector,
                self._screen(candidate_id),
            )
            run_campaign108(
                candidate_id, root / "dataset", config, cncp, campaign_dir
            )
            output = json.loads(
                campaign108._artifact_paths(campaign_dir, candidate_id)[
                    "screen_output"
                ].read_text(encoding="utf-8")
            )
            current_rows = [
                event
                for window in output["windows"]
                for event in window["events"]
                if event["route"] == "current_cav"
            ]
            self.assertTrue(current_rows)
            self.assertEqual(
                {event["fallback_reason"] for event in current_rows},
                {"candidate_failure"},
            )

        def leaking_projector(native):
            projection = real_projector(native)
            output = deepcopy(projection.screen_output)
            native_rows = {
                event["event_id"]: event
                for window in native["windows"] for event in window["events"]
            }
            event = next(
                event
                for window in output["windows"] for event in window["events"]
                if event["route"] == "current_cav"
            )
            event["fallback_reason"] = native_rows[event["event_id"]][
                "fallback_reason"
            ]
            event_body = dict(event)
            event_body.pop("decision_sha256")
            event["decision_sha256"] = canonical_sha256(event_body)
            window = output["windows"][0]
            window["events_sha256"] = canonical_sha256(window["events"])
            output = _reseal(output)
            return replace(projection, screen_output=output)

        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            screen = mock.Mock()
            self._run_patches(
                stack, spec, bundle, baseline, leaking_projector, screen
            )
            with self.assertRaisesRegex(
                Campaign108Error, "decision substitution"
            ):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp,
                    root / "campaign",
                )
            screen.assert_not_called()

    def test_native_id_substitution_fails_before_replay(self):
        candidate_id = campaign108.RG3_ID
        original = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()

        def substitute(neutral, neutral_baseline):
            value = deepcopy(original.adapter(neutral, neutral_baseline))
            value["candidate_id"] = campaign108.DSPB_ID
            return _reseal(value)

        spec = replace(original, adapter=mock.Mock(side_effect=substitute))
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            self._run_patches(stack, spec, bundle, baseline, _fake_projection)
            with self.assertRaisesRegex(Campaign108Error, "frozen binding"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, root / "campaign"
                )
            self.assertEqual(spec.adapter.call_count, 1)

    def test_source_authority_failure_and_source_race_fail_closed(self):
        spec = campaign108._candidate(campaign108.RG3_ID)
        with mock.patch.object(
            candidate_authority, "build_campaign_authority", return_value={}
        ), mock.patch.object(
            candidate_authority,
            "verify_campaign_authority",
            side_effect=candidate_authority.CandidateAuthorityError("stale source"),
        ):
            with self.assertRaisesRegex(Campaign108Error, "source authority"):
                campaign108._campaign_authority(spec)

        bundle, baseline = _fixture()
        adapter = mock.Mock(side_effect=spec.adapter)
        raced = replace(spec, adapter=adapter)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, spec.candidate_id)
            self._run_patches(stack, raced, bundle, baseline, _fake_projection)
            stack.enter_context(mock.patch.object(
                campaign108,
                "_check_authority_unchanged",
                side_effect=Campaign108Error("candidate source changed"),
            ))
            with self.assertRaisesRegex(Campaign108Error, "source changed"):
                run_campaign108(
                    spec.candidate_id, root / "dataset", config, cncp,
                    root / "campaign",
                )
            self.assertEqual(adapter.call_count, 1)

    def test_nondeterministic_native_replay_fails_before_projection_and_screen(self):
        candidate_id = campaign108.RG3_ID
        original = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        count = 0

        def nondeterministic(neutral, neutral_baseline):
            nonlocal count
            count += 1
            value = original.adapter(neutral, neutral_baseline)
            if count == 2:
                value = deepcopy(value)
                value["replay_nonce"] = 1
                value = _reseal(value)
            return value

        spec = replace(original, adapter=nondeterministic)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            projector = mock.Mock(side_effect=_fake_projection)
            screen = mock.Mock()
            self._run_patches(stack, spec, bundle, baseline, projector, screen)
            with self.assertRaisesRegex(Campaign108Error, "not byte-identical"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, root / "campaign"
                )
            self.assertEqual(count, 2)
            projector.assert_not_called()
            screen.assert_not_called()

    def test_projection_id_executable_config_and_receipt_substitution_fail(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()

        def wrong_id(native):
            projection = _fake_projection(native)
            output = deepcopy(projection.screen_output)
            output["candidate_id"] = campaign108.DSPB_ID
            output = _reseal(output)
            return replace(projection, screen_output=output)

        def wrong_executable(native):
            projection = _fake_projection(native)
            return replace(projection, executable_artifact_bytes=b"substituted\n")

        def wrong_receipt(native):
            projection = _fake_projection(native)
            receipt = deepcopy(projection.projection_receipt)
            receipt["candidate_id"] = campaign108.DSPB_ID
            return replace(projection, projection_receipt=receipt)

        def coordinated_decision_substitution(native):
            projection = _fake_projection(native)
            output = deepcopy(projection.screen_output)
            event = output["windows"][0]["events"][0]
            event["world_ray"] = [0.0, 1.0, 0.0]
            event_body = dict(event)
            event_body.pop("decision_sha256")
            event["decision_sha256"] = canonical_sha256(event_body)
            window = output["windows"][0]
            window["events_sha256"] = canonical_sha256(window["events"])
            output = _reseal(output)

            receipt = deepcopy(projection.projection_receipt)
            receipt_window = receipt["windows"][0]
            receipt_window["projected_events_sha256"] = window["events_sha256"]
            receipt_window["event_bindings"][0]["projected_decision_sha256"] = (
                event["decision_sha256"]
            )
            receipt_window["event_bindings_sha256"] = canonical_sha256(
                receipt_window["event_bindings"]
            )
            receipt_window_body = dict(receipt_window)
            receipt_window_body.pop("window_projection_sha256")
            receipt_window["window_projection_sha256"] = canonical_sha256(
                receipt_window_body
            )
            receipt["projected_aggregate_sha256"] = output["aggregate_sha256"]
            receipt_body = dict(receipt)
            receipt_body.pop("projection_receipt_sha256")
            receipt["projection_receipt_sha256"] = canonical_sha256(receipt_body)
            return replace(
                projection, screen_output=output, projection_receipt=receipt
            )

        cases = (
            (wrong_id, "projection binding"),
            (wrong_executable, "projection binding"),
            (wrong_receipt, "receipt seal"),
            (coordinated_decision_substitution, "decision substitution"),
        )
        for projector, message in cases:
            with self.subTest(projector=projector.__name__), tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
                root = Path(tmp)
                config, cncp = self._files(root, candidate_id)
                screen = mock.Mock()
                self._run_patches(stack, spec, bundle, baseline, projector, screen)
                with self.assertRaisesRegex(Campaign108Error, message):
                    run_campaign108(
                        candidate_id, root / "dataset", config, cncp,
                        root / "campaign",
                    )
                screen.assert_not_called()

    def test_config_and_neutral_races_stop_after_first_native_seal(self):
        candidate_id = campaign108.RG3_ID
        original = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)

            def race(neutral, neutral_baseline):
                value = original.adapter(neutral, neutral_baseline)
                config.write_bytes(b"{}")
                return value

            spec = replace(original, adapter=mock.Mock(side_effect=race))
            screen = mock.Mock()
            self._run_patches(stack, spec, bundle, baseline, _fake_projection, screen)
            with self.assertRaisesRegex(Campaign108Error, "changed during"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, root / "campaign"
                )
            self.assertEqual(spec.adapter.call_count, 1)
            screen.assert_not_called()

    def test_append_only_attempt_native_output_and_safe_artifact_names(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            campaign_dir.mkdir()
            paths = campaign108._artifact_paths(campaign_dir, candidate_id)
            self.assertTrue(all(path.parent == campaign_dir for path in paths.values()))
            paths["native_output"].write_bytes(b"existing-native\n")
            self._run_patches(stack, spec, bundle, baseline, _fake_projection)
            with self.assertRaisesRegex(Campaign108Error, "rich native output"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            self.assertEqual(paths["native_output"].read_bytes(), b"existing-native\n")
            self.assertTrue(paths["attempt"].exists())

    def test_failed_attempt_cannot_retry(self):
        candidate_id = campaign108.RG3_ID
        original = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        adapter = mock.Mock(side_effect=RuntimeError("fixture adapter failure"))
        spec = replace(original, adapter=adapter)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "campaign"
            self._run_patches(stack, spec, bundle, baseline, _fake_projection)
            with self.assertRaisesRegex(RuntimeError, "fixture adapter"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            with self.assertRaisesRegex(Campaign108Error, "attempt marker"):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
            self.assertEqual(adapter.call_count, 1)
            paths = campaign108._artifact_paths(campaign_dir, candidate_id)
            failure_bytes = paths["failure_receipt"].read_bytes()
            failure = json.loads(failure_bytes)
            self.assertEqual(failure["failure_stage"], "PRODUCTION_ADAPTER")
            self.assertFalse(failure["native_output_sealed"])
            self.assertFalse(failure["screen_started"])
            self.assertFalse(failure["score_computed"])
            self.assertFalse(failure["labels_accessed"])
            self.assertFalse(failure["retry_allowed"])
            self.assertEqual(
                campaign108.verify_campaign108_failure_receipt(
                    failure, campaign_dir
                ),
                failure["failure_receipt_sha256"],
            )
            self.assertEqual(paths["failure_receipt"].read_bytes(), failure_bytes)

    def test_pre_score_failure_opens_only_bound_next_epoch(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            failed_dir = root / "epoch1"
            with ExitStack() as stack:
                self._run_patches(
                    stack, spec, bundle, baseline, _fake_projection
                )
                stack.enter_context(mock.patch.object(
                    campaign108,
                    "_build_verified_stage3_adapter",
                    side_effect=Campaign108Error("shared input mount unavailable"),
                ))
                with self.assertRaisesRegex(Campaign108Error, "input mount"):
                    run_campaign108(
                        candidate_id,
                        root / "dataset",
                        config,
                        cncp,
                        failed_dir,
                    )
            failed_paths = campaign108._artifact_paths(failed_dir, candidate_id)
            failure_path = failed_paths["failure_receipt"]
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertEqual(failure["campaign_epoch"], 1)
            self.assertEqual(failure["failure_stage"], "INPUT_BUILD")
            self.assertFalse(failure["native_output_sealed"])
            self.assertFalse(failure["screen_started"])
            self.assertFalse(failure["score_computed"])
            self.assertFalse(failure["labels_accessed"])

            with self.assertRaisesRegex(
                Campaign108Error, "requires predecessor"
            ):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "epoch2-missing-lineage",
                    campaign_epoch=2,
                )

            campaign_dir = root / "epoch2"
            with ExitStack() as stack:
                self._run_patches(
                    stack, spec, bundle, baseline, _fake_projection
                )
                receipt = run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    campaign_dir,
                    campaign_epoch=2,
                    predecessor_failure_receipts=(failure_path,),
                )
                self.assertEqual(
                    verify_campaign108_receipt(
                        receipt, campaign_dir, (failure_path,)
                    ),
                    receipt["receipt_sha256"],
                )
            self.assertEqual(receipt["campaign_epoch"], 2)
            self.assertEqual(len(receipt["predecessor_failures"]), 1)
            self.assertEqual(
                receipt["predecessor_failures"][0]["failure_receipt_sha256"],
                failure["failure_receipt_sha256"],
            )
            attempt = json.loads(
                campaign108._artifact_paths(campaign_dir, candidate_id)[
                    "attempt"
                ].read_text(encoding="utf-8")
            )
            self.assertEqual(attempt["campaign_epoch"], 2)
            self.assertEqual(attempt["attempt_index"], 1)
            self.assertFalse(attempt["retry_allowed"])

    def test_post_native_or_label_access_failure_cannot_open_new_epoch(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            failed_dir = root / "epoch1"
            screen = mock.Mock(side_effect=Campaign108Error("screen unavailable"))
            self._run_patches(
                stack, spec, bundle, baseline, _fake_projection, screen
            )
            with self.assertRaisesRegex(Campaign108Error, "screen unavailable"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    failed_dir,
                )
            failure_path = campaign108._artifact_paths(
                failed_dir, candidate_id
            )["failure_receipt"]
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            self.assertTrue(failure["native_output_sealed"])
            self.assertTrue(failure["screen_started"])
            self.assertFalse(failure["score_computed"])
            self.assertTrue(failure["labels_accessed"])
            with self.assertRaisesRegex(
                Campaign108Error, "not an eligible pre-score"
            ):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    root / "epoch2",
                    campaign_epoch=2,
                    predecessor_failure_receipts=(failure_path,),
                )

    def test_post_score_validation_failure_records_score_computed(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            campaign_dir = root / "epoch1"
            self._run_patches(
                stack, spec, bundle, baseline, _fake_projection
            )
            stack.enter_context(mock.patch.object(
                screen108,
                "verify_screen108_result_envelope",
                side_effect=Campaign108Error("result envelope invalid"),
            ))
            with self.assertRaisesRegex(Campaign108Error, "envelope invalid"):
                run_campaign108(
                    candidate_id,
                    root / "dataset",
                    config,
                    cncp,
                    campaign_dir,
                )
            failure = json.loads(campaign108._artifact_paths(
                campaign_dir, candidate_id
            )["failure_receipt"].read_text(encoding="utf-8"))
            self.assertEqual(
                failure["failure_stage"], "SCREEN_RESULT_VALIDATE"
            )
            self.assertTrue(failure["native_output_sealed"])
            self.assertTrue(failure["screen_started"])
            self.assertTrue(failure["score_computed"])
            self.assertTrue(failure["labels_accessed"])

    def test_predecessor_alias_and_duplicate_fail_closed(self):
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        bundle, baseline = _fixture()
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            config, cncp = self._files(root, candidate_id)
            failed_dir = root / "epoch1"
            self._run_patches(
                stack, spec, bundle, baseline, _fake_projection
            )
            stack.enter_context(mock.patch.object(
                campaign108,
                "_build_verified_stage3_adapter",
                side_effect=Campaign108Error("shared preflight failure"),
            ))
            with self.assertRaises(Campaign108Error):
                run_campaign108(
                    candidate_id, root / "dataset", config, cncp, failed_dir
                )
            failure_path = campaign108._artifact_paths(
                failed_dir, candidate_id
            )["failure_receipt"]
            alias = root / "failure-alias.json"
            alias.symlink_to(failure_path)
            for predecessors, message in (
                ((failure_path, failure_path), "duplicate predecessor"),
                ((alias,), "missing or aliased"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(
                    Campaign108Error, message
                ):
                    run_campaign108(
                        candidate_id,
                        root / "dataset",
                        config,
                        cncp,
                        root / ("epoch2-" + message.replace(" ", "-")),
                        campaign_epoch=2,
                        predecessor_failure_receipts=predecessors,
                    )

    def test_resealed_receipt_cannot_claim_retry_or_rtl(self):
        body = {
            "schema": campaign108.CAMPAIGN_SCHEMA,
            "status": "SCREEN108_SINGLE_ATTEMPT_REPLAY_VERIFIED",
            "candidate_id": campaign108.RG3_ID,
            "authority_name": "RG3",
            "campaign_epoch": 1,
            "attempt_sha256": "0" * 64,
            "predecessor_failures": [],
            "bindings": {
                "predecessor_failures_sha256": canonical_sha256([]),
            },
            "artifacts": {},
            "policy": {
                "attempt_count": 2,
                "adapter_execution_count": 2,
                "verification_replay_count": 1,
                "verification_replay_is_tuning": False,
                "verification_replay_output_scored": False,
                "retry_performed": True,
                "tuning_performed": False,
                "labels_accessed_before_screen_output_seal": False,
                "source_selection_changed": False,
                "external_data_accessed": False,
                "rtl_or_ppa_evaluated": True,
            },
        }
        receipt = dict(body, receipt_sha256=canonical_sha256(body))
        with self.assertRaisesRegex(Campaign108Error, "policy boundary"):
            verify_campaign108_receipt(receipt, Path("unused"))


if __name__ == "__main__":
    unittest.main()
