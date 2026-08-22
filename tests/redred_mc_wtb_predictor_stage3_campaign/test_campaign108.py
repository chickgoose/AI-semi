from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import campaign108
from benchmarks.redred_mc_wtb_predictor_stage3.campaign108 import (
    Campaign108Error,
    GeneratedCandidate,
    GenerationInput,
    GenerationWindow,
    frozen_candidate_config,
    run_campaign108,
    verify_campaign108_receipt,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import screen108
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


def _pose(pose_id, timestamp_ns, angle):
    quaternion = _quaternion(angle)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(event_id, timestamp_ns, is_query, angle, pose_id):
    ray = _ray(angle)
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        0,
        is_query,
        ray,
        pose_id,
        canonical_event_content_sha256(
            event_id, timestamp_ns, 0, is_query, ray, pose_id
        ),
    )


class ForbiddenLabels(dict):
    def _forbidden(self, *args, **kwargs):
        raise AssertionError("label sidecar was accessed before sealing")

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


def _fixture():
    registry = NeutralRegistryWindow("fixture-window", 0, 50_000_000, 51_000_000)
    poses = (
        _pose(0, 47_000_000, 0.000),
        _pose(1, 48_000_000, 0.010),
        _pose(2, 49_000_000, 0.021),
    )
    events = (
        _event(10, 49_500_000, False, 0.00, 2),
        _event(11, 50_000_000, True, 0.10, 2),
        _event(12, 50_100_000, True, 0.20, 2),
    )
    baseline = evaluate_current_cav_registry(
        (registry,), {registry.window_id: events}, {registry.window_id: poses}
    )
    seal = {
        "aggregate_sha256": "1" * 64,
    }
    bundle = New108AdapterBundle(
        {}, (registry,), {registry.window_id: events},
        {registry.window_id: poses}, ForbiddenLabels(), seal,
    )
    neutral = GenerationInput((GenerationWindow(registry, events, poses),))
    return bundle, baseline, neutral


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


def _fake_generated(candidate_id, neutral):
    windows = []
    evidence_windows = []
    for window in neutral.windows:
        rows = []
        for event in window.events:
            rows.append({
                "event_id": event.event_id,
                "event_content_sha256": event.event_content_sha256,
                "decision_cycle": timestamp_to_cycle(
                    event.timestamp_ns,
                    window.registry.warmup_start_ns_inclusive,
                ),
                "model_id": "CURRENT_CAV",
                "predictor_state_version": 0,
                "used_pose_ids": [],
                "candidate_used": False,
                "fallback_reason": "fixture_fallback",
                "world_ray": None,
            })
        windows.append({"window_id": window.registry.window_id, "events": rows})
        evidence_windows.append({
            "window_id": window.registry.window_id,
            "event_count": len(rows),
        })
    spec = campaign108._candidate(candidate_id)
    evidence = campaign108._seal_generator_evidence(
        candidate_id, spec.model_candidate_id, evidence_windows
    )
    return GeneratedCandidate(candidate_id, tuple(windows), evidence)


def _fixture_screen_result(
    candidate_id=campaign108.RG3_ID,
    candidate_output_sha256=ZERO_SHA,
    candidate_executable_sha256=campaign108._EXECUTABLE_SHA256[campaign108.RG3_ID],
    candidate_config_sha256=ZERO_SHA,
    cncp=None,
):
    body = {
        "schema": "fixture-screen-result/v1",
        "status": "FIXTURE_ONLY",
        "candidate_id": candidate_id,
        "cncp": _cncp() if cncp is None else cncp,
        "provenance": {
            "candidate_output_sha256": candidate_output_sha256,
            "candidate_executable_sha256": candidate_executable_sha256,
            "candidate_config_sha256": candidate_config_sha256,
        },
    }
    return dict(body, result_sha256=canonical_sha256(body))


class Campaign108Tests(unittest.TestCase):
    def _files(self, root, candidate_id=campaign108.RG3_ID):
        config = root / "config.json"
        cncp = root / "cncp.json"
        config.write_text(
            json.dumps(frozen_candidate_config(candidate_id), sort_keys=True),
            encoding="utf-8",
        )
        cncp.write_text(json.dumps(_cncp(), sort_keys=True), encoding="utf-8")
        return config, cncp

    def test_frozen_registry_routes_each_exact_id_to_its_generator(self):
        _, _, neutral = _fixture()
        for candidate_id in campaign108.FROZEN_CANDIDATE_IDS:
            with self.subTest(candidate_id=candidate_id):
                spec = campaign108._candidate(candidate_id)
                self.assertEqual(
                    hashlib.sha256(spec.executable_path.read_bytes()).hexdigest(),
                    spec.executable_sha256,
                )
                generated = spec.generator(neutral)
                self.assertEqual(generated.candidate_id, candidate_id)
                self.assertEqual(len(generated.windows), 1)
                self.assertEqual(len(generated.windows[0]["events"]), 3)
                evidence = dict(generated.evidence)
                digest = evidence.pop("aggregate_sha256")
                self.assertEqual(digest, canonical_sha256(evidence))
        with self.assertRaisesRegex(Campaign108Error, "frozen Stage3 registry"):
            campaign108._candidate("RG3-TUNED-AFTER-RESULT")

    def test_single_attempt_writes_output_before_screen_and_binds_digests(self):
        bundle, baseline, _ = _fixture()
        candidate_id = campaign108.RG3_ID
        calls = []

        def generator(neutral):
            calls.append("generator")
            return _fake_generated(candidate_id, neutral)

        def locked_screen(dataset, output, executable, config, cncp):
            calls.append("screen")
            self.assertTrue(output.exists())
            self.assertTrue((output.parent / "rg3-cav-a3-v1.generator-evidence.json").exists())
            candidate_output = json.loads(output.read_text(encoding="utf-8"))
            return _fixture_screen_result(
                candidate_id,
                candidate_output["aggregate_sha256"],
                hashlib.sha256(executable.read_bytes()).hexdigest(),
                hashlib.sha256(config.read_bytes()).hexdigest(),
                cncp,
            )

        spec = replace(campaign108._candidate(candidate_id), generator=generator)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            campaign_dir = root / "campaign"
            with mock.patch.dict(campaign108._CANDIDATES, {candidate_id: spec}), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108", side_effect=locked_screen
            ) as screen, mock.patch.object(
                screen108, "verify_screen108_result_envelope",
                side_effect=lambda value: value["result_sha256"],
            ):
                receipt = run_campaign108(
                    candidate_id, root / "dataset-not-opened", config, cncp,
                    campaign_dir,
                )
            self.assertEqual(calls, ["generator", "screen"])
            screen.assert_called_once()
            self.assertEqual(receipt["policy"]["attempt_count"], 1)
            self.assertFalse(receipt["policy"]["retry_performed"])
            self.assertFalse(receipt["policy"]["tuning_performed"])
            self.assertFalse(
                receipt["policy"]["labels_accessed_before_candidate_output_seal"]
            )
            with mock.patch.object(
                screen108, "verify_screen108_result_envelope",
                side_effect=lambda value: value["result_sha256"],
            ):
                self.assertRegex(
                    verify_campaign108_receipt(receipt, campaign_dir),
                    r"^[0-9a-f]{64}$",
                )
                forged = json.loads(json.dumps(receipt))
                forged["bindings"]["cncp_semantic_sha256"] = ZERO_SHA
                forged_unsigned = dict(forged)
                forged_unsigned.pop("receipt_sha256")
                forged["receipt_sha256"] = canonical_sha256(forged_unsigned)
                with self.assertRaisesRegex(Campaign108Error, "digest binding"):
                    verify_campaign108_receipt(forged, campaign_dir)
            self.assertEqual(
                receipt["bindings"]["candidate_config_sha256"],
                hashlib.sha256(config.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["bindings"]["cncp_sha256"],
                hashlib.sha256(cncp.read_bytes()).hexdigest(),
            )

    def test_same_campaign_path_cannot_retry(self):
        bundle, baseline, _ = _fixture()
        candidate_id = campaign108.RG3_ID
        generator = mock.Mock(side_effect=lambda neutral: _fake_generated(candidate_id, neutral))
        spec = replace(campaign108._candidate(candidate_id), generator=generator)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            campaign_dir = root / "campaign"
            patches = (
                mock.patch.dict(campaign108._CANDIDATES, {candidate_id: spec}),
                mock.patch.object(campaign108, "build_locked_new108_adapter", return_value=bundle),
                mock.patch.object(campaign108, "evaluate_current_cav_registry", return_value=baseline),
                mock.patch.object(screen108, "run_locked_screen108", return_value=_fixture_screen_result()),
                mock.patch.object(screen108, "verify_screen108_result_envelope", return_value=_fixture_screen_result()["result_sha256"]),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
                with self.assertRaisesRegex(Campaign108Error, "attempt marker"):
                    run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
            self.assertEqual(generator.call_count, 1)

    def test_preexisting_candidate_output_is_never_overwritten(self):
        bundle, baseline, _ = _fixture()
        candidate_id = campaign108.RG3_ID
        generator = mock.Mock(
            side_effect=lambda neutral: _fake_generated(candidate_id, neutral)
        )
        spec = replace(campaign108._candidate(candidate_id), generator=generator)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            campaign_dir = root / "campaign"
            campaign_dir.mkdir()
            output = campaign_dir / "rg3-cav-a3-v1.candidate-output.json"
            output.write_bytes(b"fixture-existing-output\n")
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
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
            self.assertEqual(generator.call_count, 1)
            screen.assert_not_called()

    def test_generator_failure_consumes_attempt_and_is_not_retried(self):
        candidate_id = campaign108.RG3_ID
        bundle, _, _ = _fixture()
        generator = mock.Mock(side_effect=RuntimeError("fixture generator failure"))
        spec = replace(campaign108._candidate(candidate_id), generator=generator)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            campaign_dir = root / "campaign"
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ):
                with self.assertRaisesRegex(RuntimeError, "fixture generator"):
                    run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
                self.assertTrue((campaign_dir / "rg3-cav-a3-v1.attempt.json").exists())
                with self.assertRaisesRegex(Campaign108Error, "attempt marker"):
                    run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
            self.assertEqual(generator.call_count, 1)

    def test_config_tuning_is_rejected_before_attempt_or_source_access(self):
        candidate_id = campaign108.RG3_ID
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            changed = json.loads(config.read_text(encoding="utf-8"))
            changed["parameters"]["maximum_rate_change_ratio"] = 0.75
            config.write_text(json.dumps(changed), encoding="utf-8")
            campaign_dir = root / "campaign"
            with mock.patch.object(
                campaign108, "build_locked_new108_adapter"
            ) as build, self.assertRaisesRegex(Campaign108Error, "frozen ID"):
                run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
            build.assert_not_called()
            self.assertFalse(campaign_dir.exists())

    def test_config_race_fails_after_seal_without_screen_or_retry(self):
        candidate_id = campaign108.RG3_ID
        bundle, baseline, _ = _fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, cncp = self._files(root)
            campaign_dir = root / "campaign"

            def racing_generator(neutral):
                result = _fake_generated(candidate_id, neutral)
                config.write_text("{}", encoding="utf-8")
                return result

            spec = replace(
                campaign108._candidate(candidate_id), generator=racing_generator
            )
            with mock.patch.dict(
                campaign108._CANDIDATES, {candidate_id: spec}
            ), mock.patch.object(
                campaign108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                campaign108, "evaluate_current_cav_registry", return_value=baseline
            ), mock.patch.object(
                screen108, "run_locked_screen108"
            ) as screen, self.assertRaisesRegex(Campaign108Error, "changed during"):
                run_campaign108(candidate_id, root / "dataset", config, cncp, campaign_dir)
            screen.assert_not_called()
            self.assertTrue(
                (campaign_dir / "rg3-cav-a3-v1.candidate-output.json").exists()
            )

    def test_receipt_reseal_cannot_claim_retry_or_rtl(self):
        policy = {
            "attempt_count": 2,
            "retry_performed": True,
            "tuning_performed": False,
            "labels_accessed_before_candidate_output_seal": False,
            "source_selection_changed": False,
            "external_data_accessed": False,
            "rtl_or_ppa_evaluated": True,
        }
        receipt = {
            "schema": campaign108.CAMPAIGN_SCHEMA,
            "status": "SCREEN108_SINGLE_ATTEMPT_COMPLETE",
            "candidate_id": campaign108.RG3_ID,
            "model_candidate_id": campaign108._candidate(campaign108.RG3_ID).model_candidate_id,
            "attempt_sha256": ZERO_SHA,
            "bindings": {},
            "artifacts": {},
            "policy": policy,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        with self.assertRaisesRegex(Campaign108Error, "policy boundary"):
            verify_campaign108_receipt(receipt, Path("unused"))


if __name__ == "__main__":
    unittest.main()
