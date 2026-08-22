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

from benchmarks.redred_mc_wtb_predictor_stage3 import screen108
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_predictor_stage3.screen108 import (
    Screen108Error,
    seal_candidate_output,
    validate_cncp,
    verify_screen108_result_envelope,
)
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import SO3PLLConfig
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
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ZERO_SHA = "0" * 64
EXECUTABLE = b"sealed candidate executable fixture\n"
CONFIG = b'{"fixture":true}\n'
EXECUTABLE_SHA = hashlib.sha256(EXECUTABLE).hexdigest()
CONFIG_SHA = hashlib.sha256(CONFIG).hexdigest()


def _rotation_z(angle):
    return (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0))


def _ray(angle):
    return (math.cos(angle), math.sin(angle), 0.0)


def _pose(pose_id, timestamp_ns, start_ns, angle):
    quaternion = _rotation_z(angle)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
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


def _fixture():
    registries = []
    event_streams = {}
    pose_streams = {}
    labels = {}
    for index, motion_bin in enumerate(("LOW", "MID", "HIGH")):
        start = index * 100_000_000
        query = start + 50_000_000
        end = query + 500_000
        window_id = "fixture-%s" % motion_bin
        pose0 = index * 10
        pose1 = pose0 + 1
        pose2 = pose0 + 2
        registries.append(NeutralRegistryWindow(window_id, start, query, end))
        pose_streams[window_id] = (
            _pose(pose0, start, start, 0.0),
            _pose(pose1, query - 2_000_000, start, 0.05),
            _pose(pose2, query - 1_000_000, start, 0.1),
        )
        event_base = index * 100
        event_streams[window_id] = (
            _event(event_base, query - 200_000, False, 0.00, pose2),
            _event(event_base + 1, query, True, 0.10, pose2),
            _event(event_base + 2, query + 100_000, True, 0.20, pose2),
        )
        labels[window_id] = {
            "axis": "X", "sign": "POSITIVE", "motion_bin": motion_bin,
            "rotation_vector_rad": [0.1, 0.0, 0.0], "purity": 1.0,
            "motion_proxy": 1.0, "rank_sha256": ZERO_SHA,
        }
    baseline = evaluate_current_cav_registry(
        tuple(registries), event_streams, pose_streams
    )
    seal = {
        "source_member_sha256": {
            "events": ZERO_SHA, "poses": ZERO_SHA, "calibration": ZERO_SHA,
        },
        "selector_registry_sha256": ZERO_SHA,
        "selector_implementation_sha256": ZERO_SHA,
        "projection_implementation_sha256": ZERO_SHA,
        "neutral_registry_sha256": canonical_sha256([
            row.to_mapping() for row in registries
        ]),
        "selector_labels_sidecar_sha256": canonical_sha256(labels),
        "window_count": 3,
        "selected_event_count": 9,
        "selected_pose_packet_count": 9,
        "aggregate_sha256": "1" * 64,
    }
    bundle = New108AdapterBundle(
        {}, tuple(registries), event_streams, pose_streams, labels, seal
    )
    unsealed_windows = []
    for base_window in baseline.windows:
        rows = []
        for event, decision in zip(
            base_window.input_events, base_window.simulation.records
        ):
            rows.append({
                "event_id": event.event_id,
                "event_content_sha256": event.event_content_sha256,
                "decision_cycle": decision.occurrence_cycle,
                "model_id": "CURRENT_CAV",
                "predictor_state_version": 0,
                "used_pose_ids": [],
                "candidate_used": False,
                "fallback_reason": "FIXTURE_FALLBACK",
                "world_ray": None,
            })
        unsealed_windows.append({
            "window_id": base_window.registry.window_id, "events": rows,
        })
    output = seal_candidate_output(
        "FIXTURE-CANDIDATE",
        seal["aggregate_sha256"],
        baseline.neutral_input_sha256,
        EXECUTABLE_SHA,
        CONFIG_SHA,
        unsealed_windows,
    )
    frozen = {
        "freeze_receipt": ZERO_SHA,
        "benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json": ZERO_SHA,
        "docs/MC_WTB_PREDICTOR_STAGE12_CONTRACT_20260822.md": ZERO_SHA,
        "docs/MC_WTB_STAGE12_ARCHITECTURE_CANDIDATES_20260822.md": ZERO_SHA,
    }
    return bundle, baseline, output, frozen


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
        "numeric_risk": "N2",
        "state_class": "S1",
        "compute_class": "C2",
        "pipeline_class": "P1",
        "endpoint_target_ns": 6.5,
        "event_lanes": 2,
    }


def _reseal_output(output):
    for window in output["windows"]:
        for event in window["events"]:
            body = dict(event)
            body.pop("decision_sha256", None)
            event["decision_sha256"] = canonical_sha256(body)
        window["events_sha256"] = canonical_sha256(window["events"])
    body = dict(output)
    body.pop("aggregate_sha256", None)
    output["aggregate_sha256"] = canonical_sha256(body)
    return output


def _candidate_use_output(output, candidate_id, used_pose_ids, event_index=1):
    changed = deepcopy(output)
    changed["candidate_id"] = candidate_id
    event = changed["windows"][0]["events"][event_index]
    event["candidate_used"] = True
    event["fallback_reason"] = None
    event["world_ray"] = [1.0, 0.0, 0.0]
    event["model_id"] = candidate_id
    event["used_pose_ids"] = list(used_pose_ids)
    return _reseal_output(changed)


class LockedScreen108Tests(unittest.TestCase):
    def _evaluate(self, output=None, cncp=None):
        bundle, baseline, original, frozen = _fixture()
        return screen108._evaluate_verified(
            bundle,
            baseline,
            original if output is None else output,
            EXECUTABLE_SHA,
            CONFIG_SHA,
            _cncp() if cncp is None else cncp,
            frozen,
            Path(__file__).resolve().parents[2],
        )

    def test_fallback_screen_reports_all_locked_metrics_without_promotion(self):
        result = self._evaluate()
        self.assertEqual(
            [row["group"] for row in result["groups"]],
            ["OVERALL", "LOW", "MID", "HIGH"],
        )
        overall = result["groups"][0]
        self.assertEqual(overall["query_event_count"], 6)
        self.assertEqual(overall["fallback_events"], 6)
        self.assertEqual(overall["fallback_reasons"], {"FIXTURE_FALLBACK": 6})
        self.assertAlmostEqual(overall["pooled"]["I_P_A"], 0.0)
        self.assertAlmostEqual(overall["equal_window"]["I_P_A"], 0.0)
        self.assertEqual(result["status"], screen108.STATUS_HOLD)
        self.assertEqual(
            result["gate"]["model_accuracy_verdict"],
            screen108.MODEL_ACCURACY_FAIL,
        )
        self.assertFalse(result["gate"]["model_accuracy_gate_pass"])
        self.assertEqual(result["cncp"]["evidence_grade"], "DECLARED_UNVERIFIED")
        self.assertEqual(result["cncp"]["verdict"], "CNCP_HOLD_UNVERIFIED")
        self.assertEqual(result["cncp"]["declared_values"], _cncp())
        self.assertFalse(result["gate"]["hardware_estimate_boundary_met"])
        self.assertFalse(result["gate"]["promotion_authorized"])
        self.assertFalse(result["gate"]["rtl_ppa_authorized"])
        self.assertFalse(result["claim_scope"]["candidate_executed_by_runner"])
        self.assertRegex(verify_screen108_result_envelope(result), r"^[0-9a-f]{64}$")

    def test_result_matches_exact_json_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "benchmarks/redred_mc_wtb_predictor_stage3/screen108_result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self._evaluate())

    def test_drop_duplicate_extra_and_reorder_fail_even_when_resealed(self):
        _, _, original, _ = _fixture()
        mutations = []
        dropped = deepcopy(original)
        dropped["windows"][0]["events"].pop()
        mutations.append(dropped)
        duplicated = deepcopy(original)
        duplicated["windows"][0]["events"][1] = deepcopy(
            duplicated["windows"][0]["events"][0]
        )
        mutations.append(duplicated)
        extra = deepcopy(original)
        extra["windows"][0]["events"].append(deepcopy(
            extra["windows"][0]["events"][-1]
        ))
        mutations.append(extra)
        reordered = deepcopy(original)
        reordered["windows"][0]["events"][0:2] = reversed(
            reordered["windows"][0]["events"][0:2]
        )
        mutations.append(reordered)
        for changed in mutations:
            with self.subTest(count=len(changed["windows"][0]["events"])), self.assertRaisesRegex(
                Screen108Error, "cardinality|order or content"
            ):
                self._evaluate(_reseal_output(changed))

    def test_fallback_substitution_label_injection_and_unknown_pose_fail_closed(self):
        _, _, original, _ = _fixture()
        substituted = deepcopy(original)
        substituted["windows"][0]["events"][0]["world_ray"] = [1.0, 0.0, 0.0]
        with self.assertRaisesRegex(Screen108Error, "fallback must not supply"):
            self._evaluate(_reseal_output(substituted))

        contaminated = deepcopy(original)
        contaminated["windows"][0]["events"][0]["motion_bin"] = "HIGH"
        with self.assertRaisesRegex(Screen108Error, "field schema"):
            self._evaluate(_reseal_output(contaminated))

        mislabeled_use = deepcopy(original)
        event = mislabeled_use["windows"][0]["events"][1]
        event["candidate_used"] = True
        event["fallback_reason"] = None
        event["world_ray"] = [1.0, 0.0, 0.0]
        event["used_pose_ids"] = [1]
        with self.assertRaisesRegex(Screen108Error, "baseline fallback"):
            self._evaluate(_reseal_output(mislabeled_use))

        unknown = deepcopy(original)
        event = unknown["windows"][0]["events"][1]
        event["candidate_used"] = True
        event["fallback_reason"] = None
        event["world_ray"] = [1.0, 0.0, 0.0]
        event["model_id"] = "FIXTURE-CANDIDATE"
        event["used_pose_ids"] = [999]
        with self.assertRaisesRegex(Screen108Error, "unavailable pose"):
            self._evaluate(_reseal_output(unknown))

    def test_candidate_may_use_any_time_eligible_neutral_pose(self):
        _, baseline, output, _ = _fixture()
        old_pose_id = baseline.windows[0].input_poses[0].pose_id
        occurrence_ids = baseline.windows[0].simulation.records[1].occurrence_pose_ids
        self.assertNotIn(old_pose_id, occurrence_ids)
        result = self._evaluate(_candidate_use_output(
            output, RG3_POLICY.candidate_id, [old_pose_id]
        ))
        self.assertEqual(result["candidate_id"], RG3_POLICY.candidate_id)
        self.assertEqual(result["windows"][0]["candidate_use_events"], 1)

    def test_same_edge_future_invalid_and_unknown_poses_fail_closed(self):
        bundle, baseline, output, _ = _fixture()
        base_window = baseline.windows[0]
        event = base_window.input_events[1]
        decision = base_window.simulation.records[1]
        start = base_window.registry.warmup_start_ns_inclusive
        invalid = replace(
            _pose(1000, event.timestamp_ns - 1_000, start, 0.2),
            value_valid=False,
        )
        same_edge = _pose(1001, event.timestamp_ns, start, 0.2)
        future = _pose(1002, event.timestamp_ns + 1_000, start, 0.2)
        self.assertEqual(same_edge.commit_cycle, decision.occurrence_cycle)
        changed_window = replace(
            base_window,
            input_poses=base_window.input_poses + (invalid, same_edge, future),
        )
        changed_baseline = replace(
            baseline, windows=(changed_window,) + baseline.windows[1:]
        )
        for pose_id in (same_edge.pose_id, future.pose_id, invalid.pose_id, 9999):
            with self.subTest(pose_id=pose_id), self.assertRaisesRegex(
                Screen108Error, "unavailable pose"
            ):
                screen108._validate_candidate_output(
                    _candidate_use_output(
                        output, RG3_POLICY.candidate_id, [pose_id]
                    ),
                    bundle,
                    changed_baseline,
                    EXECUTABLE_SHA,
                    CONFIG_SHA,
                )

    def test_frozen_rg3_and_so3_pll_candidate_and_model_ids_are_accepted(self):
        _, baseline, output, _ = _fixture()
        pose_id = baseline.windows[0].input_poses[-1].pose_id
        candidate_ids = (RG3_POLICY.candidate_id, SO3PLLConfig().candidate_id)
        results = []
        for candidate_id in candidate_ids:
            with self.subTest(candidate_id=candidate_id):
                result = self._evaluate(_candidate_use_output(
                    output, candidate_id, [pose_id]
                ))
                self.assertEqual(result["candidate_id"], candidate_id)
                self.assertRegex(
                    verify_screen108_result_envelope(result), r"^[0-9a-f]{64}$"
                )
                results.append(result)

        mismatched = _candidate_use_output(
            output, RG3_POLICY.candidate_id, [pose_id]
        )
        mismatched["windows"][0]["events"][1]["model_id"] = SO3PLLConfig().candidate_id
        with self.assertRaisesRegex(Screen108Error, "model identity"):
            self._evaluate(_reseal_output(mismatched))

        unsafe = deepcopy(output)
        unsafe["candidate_id"] = "RG3 unsafe"
        with self.assertRaisesRegex(Screen108Error, "canonical identifier"):
            self._evaluate(_reseal_output(unsafe))

        try:
            import jsonschema
        except ImportError:
            return
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "benchmarks/redred_mc_wtb_predictor_stage3/screen108_result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        for result in results:
            validator.validate(result)

    def test_output_and_executable_config_provenance_are_bound(self):
        bundle, baseline, output, _ = _fixture()
        changed = deepcopy(output)
        changed["adapter_aggregate_sha256"] = "2" * 64
        with self.assertRaisesRegex(Screen108Error, "adapter binding"):
            self._evaluate(_reseal_output(changed))
        with self.assertRaisesRegex(Screen108Error, "executable hash"):
            screen108._validate_candidate_output(
                output, bundle, baseline, "3" * 64, CONFIG_SHA
            )
        with self.assertRaisesRegex(Screen108Error, "config hash"):
            screen108._validate_candidate_output(
                output, bundle, baseline, EXECUTABLE_SHA, "4" * 64
            )

    def test_cncp_declaration_lint_charges_pipeline_and_preserves_endpoint(self):
        self.assertEqual(validate_cncp(_cncp())["state_class"], "S1")
        bad_state = _cncp()
        bad_state["pipeline_bits"] = 513
        with self.assertRaisesRegex(Screen108Error, "included in B_ff"):
            validate_cncp(bad_state)
        bad_class = _cncp()
        bad_class["state_class"] = "S0"
        with self.assertRaisesRegex(Screen108Error, "state class"):
            validate_cncp(bad_class)
        bad_endpoint = _cncp()
        bad_endpoint["event_lanes"] = 1
        with self.assertRaisesRegex(Screen108Error, "6.5 ns two-lane"):
            validate_cncp(bad_endpoint)
        undercharged = _cncp()
        undercharged["O_event"]["nonlinear"] = 1
        with self.assertRaisesRegex(Screen108Error, "undercharges"):
            validate_cncp(undercharged)

    def test_unverified_cncp_never_changes_model_verdict_or_meets_boundary(self):
        unfavorable_cncp = _cncp()
        unfavorable_cncp.update({
            "B_ff": 4097,
            "state_class": "S3",
            "compute_class": "C4",
            "critical_depth": 9,
            "pipeline_class": "P3",
            "II_event": 2,
            "numeric_risk": "N3",
        })
        unfavorable_cncp["O_event"]["nonlinear"] = 1
        summarize_group = screen108._summarize_group

        def passing_accuracy(group, windows):
            result = dict(summarize_group(group, windows))
            result["pooled"] = dict(result["pooled"], I_P_A=0.1)
            return result

        with mock.patch.object(
            screen108, "_summarize_group", side_effect=passing_accuracy
        ):
            favorable = self._evaluate(cncp=_cncp())
            unfavorable = self._evaluate(cncp=unfavorable_cncp)
        self.assertEqual(favorable["status"], unfavorable["status"])
        self.assertEqual(favorable["status"], screen108.STATUS_MEASURED)
        self.assertEqual(favorable["groups"], unfavorable["groups"])
        self.assertEqual(favorable["windows"], unfavorable["windows"])
        self.assertEqual(favorable["gate"], unfavorable["gate"])
        for result in (favorable, unfavorable):
            self.assertEqual(
                result["gate"]["model_accuracy_verdict"],
                screen108.MODEL_ACCURACY_PASS,
            )
            self.assertTrue(result["gate"]["model_accuracy_gate_pass"])
            self.assertEqual(result["cncp"]["evidence_grade"], "DECLARED_UNVERIFIED")
            self.assertEqual(result["cncp"]["verdict"], "CNCP_HOLD_UNVERIFIED")
            self.assertFalse(result["gate"]["hardware_estimate_boundary_met"])
            self.assertNotIn("cost_and_endpoint", result["gate"])
            self.assertNotIn("screen_metric_gate_pass", result["gate"])

    def test_resealed_result_cannot_claim_rtl_or_ppa_authorization(self):
        result = deepcopy(self._evaluate())
        result["gate"]["rtl_ppa_authorized"] = True
        body = dict(result)
        body.pop("result_sha256")
        result["result_sha256"] = canonical_sha256(body)
        with self.assertRaisesRegex(Screen108Error, "authorization boundary"):
            verify_screen108_result_envelope(result)

        result = deepcopy(self._evaluate())
        result["gate"]["hardware_estimate_boundary_met"] = True
        body = dict(result)
        body.pop("result_sha256")
        result["result_sha256"] = canonical_sha256(body)
        with self.assertRaisesRegex(Screen108Error, "authorization boundary"):
            verify_screen108_result_envelope(result)

        result = deepcopy(self._evaluate())
        result["cncp"]["verdict"] = "CNCP_PASS_BOUNDED"
        body = dict(result)
        body.pop("result_sha256")
        result["result_sha256"] = canonical_sha256(body)
        with self.assertRaisesRegex(Screen108Error, "CNCP evidence boundary"):
            verify_screen108_result_envelope(result)

    def test_public_runner_reads_but_does_not_execute_candidate(self):
        bundle, baseline, output, frozen = _fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_path = root / "candidate.json"
            executable_path = root / "candidate.bin"
            config_path = root / "config.json"
            output_path.write_text(json.dumps(output), encoding="utf-8")
            executable_path.write_bytes(EXECUTABLE)
            config_path.write_bytes(CONFIG)
            with mock.patch.object(
                screen108, "_verify_freeze", return_value=frozen
            ), mock.patch.object(
                screen108, "build_locked_new108_adapter", return_value=bundle
            ) as build, mock.patch.object(
                screen108, "verify_new108_adapter", return_value=bundle.provenance_seal["aggregate_sha256"]
            ) as verify, mock.patch.object(
                screen108, "evaluate_current_cav_registry", return_value=baseline
            ) as evaluate, mock.patch.multiple(
                screen108,
                EXPECTED_ADAPTER_AGGREGATE_SHA256=bundle.provenance_seal["aggregate_sha256"],
                EXPECTED_NEUTRAL_INPUT_SHA256=baseline.neutral_input_sha256,
                EXPECTED_NEUTRAL_REGISTRY_SHA256=bundle.provenance_seal["neutral_registry_sha256"],
                EXPECTED_LABEL_SIDECAR_SHA256=bundle.provenance_seal["selector_labels_sidecar_sha256"],
                EXPECTED_SELECTOR_REGISTRY_SHA256=bundle.provenance_seal["selector_registry_sha256"],
            ):
                result = screen108.run_locked_screen108(
                    root, output_path, executable_path, config_path, _cncp()
                )
            build.assert_called_once_with(root)
            verify.assert_called_once_with(bundle, root)
            evaluate.assert_called_once()
            self.assertFalse(result["claim_scope"]["candidate_executed_by_runner"])
            with mock.patch.object(
                screen108, "_verify_freeze", return_value=frozen
            ), mock.patch.object(
                screen108, "build_locked_new108_adapter", return_value=bundle
            ), mock.patch.object(
                screen108, "verify_new108_adapter",
                return_value=bundle.provenance_seal["aggregate_sha256"],
            ), self.assertRaisesRegex(Screen108Error, "locked NEW108"):
                screen108.run_locked_screen108(
                    root, output_path, executable_path, config_path, _cncp()
                )


if __name__ == "__main__":
    unittest.main()
