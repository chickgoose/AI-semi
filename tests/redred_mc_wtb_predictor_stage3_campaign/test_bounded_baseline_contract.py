"""Synthetic lifetime and byte-identity contracts for bounded Stage3 baselines."""

from __future__ import annotations

from contextlib import ExitStack
import gc
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import weakref

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    campaign108,
    candidate_authority,
    logical_cav_evaluator,
    screen108,
)
from tests.redred_mc_wtb_predictor_stage3_campaign.test_campaign108 import (
    _fake_projection,
)
from tests.redred_mc_wtb_predictor_stage3_screen.test_screen108 import (
    CONFIG,
    CONFIG_SHA,
    EXECUTABLE,
    EXECUTABLE_SHA,
    _cncp,
    _fixture,
)


def _json_bytes(value):
    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _campaign_authority(spec):
    selected = {
        "candidate": spec.authority_name,
        "native_candidate_id": spec.candidate_id,
        "config_sha256": candidate_authority.candidate_config_sha256(
            spec.authority_name
        ),
        "manifest_sha256": "a" * 64,
    }
    body = {
        "schema": candidate_authority.CAMPAIGN_SCHEMA,
        "candidate_order": list(candidate_authority.CANDIDATE_NAMES),
        "candidates": [selected],
    }
    authority = dict(
        body,
        aggregate_sha256=campaign108.canonical_sha256(body),
    )
    return authority, selected, ()


def _fixture_screen(candidate_id):
    def run(dataset, output_path, executable_path, config_path, cncp):
        del dataset
        output = json.loads(output_path.read_text(encoding="utf-8"))
        body = {
            "schema": "fixture-screen-result/v1",
            "status": "FIXTURE_ONLY",
            "candidate_id": candidate_id,
            "cncp": {"declared_values": cncp},
            "provenance": {
                "candidate_output_sha256": output["aggregate_sha256"],
                "candidate_executable_sha256": campaign108._sha256_bytes(
                    executable_path.read_bytes()
                ),
                "candidate_config_sha256": campaign108._sha256_bytes(
                    config_path.read_bytes()
                ),
            },
        }
        return dict(body, result_sha256=campaign108.canonical_sha256(body))

    return run


class _SingletonLifetimeProbe:
    def __init__(self, testcase, registry):
        self.testcase = testcase
        self.expected_ids = [row.window_id for row in registry]
        self.calls = []
        self.partial_refs = []
        self.real_evaluate = logical_cav_evaluator.evaluate_current_cav_registry

    def __call__(self, registry, event_streams, pose_streams):
        gc.collect()
        if self.partial_refs:
            prior, prior_simulation = self.partial_refs[-1]
            self.testcase.assertIsNone(
                prior(), "previous rich singleton evaluation remained live"
            )
            self.testcase.assertIsNone(
                prior_simulation(), "previous rich cycle simulation remained live"
            )
        self.testcase.assertEqual(len(registry), 1)
        window_id = registry[0].window_id
        self.testcase.assertEqual(list(event_streams), [window_id])
        self.testcase.assertEqual(list(pose_streams), [window_id])
        self.calls.append(window_id)
        result = self.real_evaluate(registry, event_streams, pose_streams)
        self.partial_refs.append((
            weakref.ref(result), weakref.ref(result.windows[0].simulation)
        ))
        return result

    def assert_complete(self, repetitions=1):
        gc.collect()
        self.testcase.assertEqual(self.calls, self.expected_ids * repetitions)
        self.testcase.assertTrue(all(
            partial() is None and simulation() is None
            for partial, simulation in self.partial_refs
        ))


class BoundedBaselineContractTests(unittest.TestCase):
    def test_shared_builder_and_verifier_are_ordered_bounded_and_exact(self):
        bundle, full, output, frozen = _fixture()
        build = getattr(
            logical_cav_evaluator, "evaluate_current_cav_registry_bounded"
        )
        verify = getattr(
            logical_cav_evaluator,
            "verify_current_cav_evaluation_integrity_bounded",
        )

        build_probe = _SingletonLifetimeProbe(self, bundle.neutral_registry)
        with mock.patch.object(
            logical_cav_evaluator,
            "evaluate_current_cav_registry",
            side_effect=build_probe,
        ):
            bounded = build(
                bundle.neutral_registry,
                bundle.event_streams,
                bundle.pose_streams,
            )
        build_probe.assert_complete()

        verify_probe = _SingletonLifetimeProbe(self, bundle.neutral_registry)
        with mock.patch.object(
            logical_cav_evaluator,
            "evaluate_current_cav_registry",
            side_effect=verify_probe,
        ):
            self.assertEqual(verify(bounded), full.neutral_input_sha256)
        verify_probe.assert_complete()

        control_result = screen108._evaluate_verified(
            bundle, full, output, EXECUTABLE_SHA, CONFIG_SHA, _cncp(), frozen,
            Path(__file__).resolve().parents[2],
        )
        bounded_result = screen108._evaluate_verified(
            bundle, bounded, output, EXECUTABLE_SHA, CONFIG_SHA, _cncp(), frozen,
            Path(__file__).resolve().parents[2],
        )
        self.assertEqual(_json_bytes(bounded_result), _json_bytes(control_result))

    def test_campaign_path_uses_shared_singleton_builder_and_preserves_receipt(self):
        bundle, full, unused_output, unused_frozen = _fixture()
        del unused_output, unused_frozen
        candidate_id = campaign108.RG3_ID
        spec = campaign108._candidate(candidate_id)
        neutral = campaign108._neutral_view(bundle)
        control_baseline = campaign108._neutral_baseline_view(full, neutral)
        expected_native = campaign108._json_bytes(
            spec.adapter(neutral, control_baseline)
        )
        shared_builder = getattr(
            logical_cav_evaluator, "evaluate_current_cav_registry_bounded"
        )
        probe = _SingletonLifetimeProbe(self, bundle.neutral_registry)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            cncp = root / "cncp.json"
            config.write_bytes(campaign108.frozen_candidate_config_bytes(candidate_id))
            cncp.write_bytes(_json_bytes(_cncp()))
            campaign_dir = root / "campaign"
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    campaign108, "_CANDIDATES", {candidate_id: spec}
                ))
                stack.enter_context(mock.patch.object(
                    campaign108,
                    "_campaign_authority",
                    return_value=_campaign_authority(spec),
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
                bounded_call = stack.enter_context(mock.patch.object(
                    campaign108,
                    "evaluate_current_cav_registry_bounded",
                    wraps=shared_builder,
                ))
                stack.enter_context(mock.patch.object(
                    logical_cav_evaluator,
                    "evaluate_current_cav_registry",
                    side_effect=probe,
                ))
                stack.enter_context(mock.patch.object(
                    campaign108,
                    "_project_native_output",
                    side_effect=_fake_projection,
                ))
                stack.enter_context(mock.patch.object(
                    screen108,
                    "run_locked_screen108",
                    side_effect=_fixture_screen(candidate_id),
                ))
                stack.enter_context(mock.patch.object(
                    screen108,
                    "verify_screen108_result_envelope",
                    side_effect=lambda value: value["result_sha256"],
                ))
                receipt = campaign108.run_campaign108(
                    candidate_id, root / "dataset", config, cncp, campaign_dir
                )
                self.assertRegex(
                    campaign108.verify_campaign108_receipt(receipt, campaign_dir),
                    r"^[0-9a-f]{64}$",
                )

            bounded_call.assert_called_once()
            probe.assert_complete()
            paths = campaign108._artifact_paths(campaign_dir, candidate_id)
            self.assertEqual(paths["native_output"].read_bytes(), expected_native)
            replay = json.loads(paths["replay"].read_text(encoding="utf-8"))
            self.assertEqual(
                replay["production_native_bytes_sha256"],
                replay["replay_native_bytes_sha256"],
            )
            self.assertEqual(
                paths["campaign_receipt"].read_bytes(),
                campaign108._json_bytes(receipt),
            )

    def test_screen_path_uses_shared_singleton_builder_and_verifier_exactly(self):
        bundle, full, output, frozen = _fixture()
        control_result = screen108._evaluate_verified(
            bundle, full, output, EXECUTABLE_SHA, CONFIG_SHA, _cncp(), frozen,
            Path(__file__).resolve().parents[2],
        )
        shared_builder = getattr(
            logical_cav_evaluator, "evaluate_current_cav_registry_bounded"
        )
        shared_verifier = getattr(
            logical_cav_evaluator,
            "verify_current_cav_evaluation_integrity_bounded",
        )
        probe = _SingletonLifetimeProbe(self, bundle.neutral_registry)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_path = root / "candidate.json"
            executable_path = root / "candidate.bin"
            config_path = root / "config.json"
            output_path.write_bytes(_json_bytes(output))
            executable_path.write_bytes(EXECUTABLE)
            config_path.write_bytes(CONFIG)
            with mock.patch.object(
                screen108, "_verify_freeze", return_value=frozen
            ), mock.patch.object(
                screen108,
                "build_locked_stage3_new108_adapter",
                return_value=bundle,
            ), mock.patch.object(
                screen108,
                "verify_stage3_new108_adapter",
                return_value=bundle.provenance_seal["aggregate_sha256"],
            ), mock.patch.object(
                screen108,
                "evaluate_current_cav_registry_bounded",
                wraps=shared_builder,
            ) as build, mock.patch.object(
                screen108,
                "verify_current_cav_evaluation_integrity_bounded",
                wraps=shared_verifier,
            ) as verify, mock.patch.object(
                logical_cav_evaluator,
                "evaluate_current_cav_registry",
                side_effect=probe,
            ), mock.patch.multiple(
                screen108,
                EXPECTED_LABEL_SIDECAR_SHA256=bundle.provenance_seal[
                    "selector_labels_sidecar_sha256"
                ],
                EXPECTED_SELECTOR_REGISTRY_SHA256=bundle.provenance_seal[
                    "selector_registry_sha256"
                ],
                EXPECTED_EVALUATOR_SHA256=screen108._file_sha256(
                    Path(logical_cav_evaluator.__file__), "fixture evaluator"
                ),
            ):
                result = screen108.run_locked_screen108(
                    root, output_path, executable_path, config_path, _cncp()
                )

        build.assert_called_once()
        verify.assert_called_once()
        probe.assert_complete(repetitions=2)
        self.assertEqual(_json_bytes(result), _json_bytes(control_result))


if __name__ == "__main__":
    unittest.main()
