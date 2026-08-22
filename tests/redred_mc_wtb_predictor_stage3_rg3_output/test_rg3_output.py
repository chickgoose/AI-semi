from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_output
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_output import (
    CURRENT_CAV_MODEL_ID,
    RG3_CONFIG,
    RG3_CONFIG_BYTES,
    RG3_CONFIG_SHA256,
    RG3_EXECUTABLE_MANIFEST,
    RG3_EXECUTABLE_MANIFEST_BYTES,
    RG3_EXECUTABLE_SHA256,
    RG3_MODEL_PATH,
    RG3_MODEL_SHA256,
    RG3_OUTPUT_CANDIDATE_ID,
    RG3OutputError,
    ROUTE_CANDIDATE,
    ROUTE_CURRENT_CAV,
    ROUTE_FRESH_ZOH,
    ROUTE_SENSOR_FIXED,
    build_rg3_executable_manifest,
    generate_locked_rg3_output,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    pose_timestamp_to_cycle,
)


ADAPTER_SHA256 = "a" * 64
ROW_FIELDS = {
    "event_id",
    "event_content_sha256",
    "occurrence_cycle",
    "decision_cycle",
    "model_id",
    "predictor_state_version",
    "used_pose_ids",
    "candidate_attempted",
    "candidate_used",
    "route",
    "fallback_reason",
    "world_ray",
    "decision_sha256",
}
MANIFEST_PATHS = (
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/rg3_output.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)


def _rotation_z(angle: float):
    return (0.0, 0.0, math.sin(0.5 * angle), math.cos(0.5 * angle))


def _pose(
    pose_id: int,
    timestamp_ns: int,
    start_ns: int,
    angle: float,
    *,
    commit_cycle=None,
):
    quaternion = _rotation_z(angle)
    commit = (
        pose_timestamp_to_cycle(timestamp_ns, start_ns)
        if commit_cycle is None
        else commit_cycle
    )
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        commit,
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(
    event_id: int,
    timestamp_ns: int,
    is_query: bool,
    pose_id: int,
    *,
    ray_angle: float = 0.0,
    transform_guard_valid: bool = True,
):
    ray = (math.cos(ray_angle), math.sin(ray_angle), 0.0)
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        0,
        is_query,
        ray,
        pose_id,
        canonical_event_content_sha256(
            event_id,
            timestamp_ns,
            0,
            is_query,
            ray,
            pose_id,
            transform_guard_valid,
        ),
        transform_guard_valid,
    )


def _fixture():
    registry = []
    event_streams = {}
    pose_streams = {}
    for index in range(2):
        start = 10_000_000 + index * 100_000_000
        query = start + 50_000_000
        end = query + 2_000_000
        window_id = "rg3-output-%d" % index
        pose_base = index * 100
        event_base = index * 1000
        registry.append(NeutralRegistryWindow(window_id, start, query, end))
        # The first pose is authoritative baseline evidence committed before
        # the per-window reset.  The adapter must never publish it to RG3.
        pose_streams[window_id] = (
            _pose(pose_base, start - 10, start, -0.01),
            _pose(pose_base + 1, start + 35_000_000, start, 0.00),
            _pose(pose_base + 2, start + 42_000_000, start, 0.07),
            _pose(pose_base + 3, start + 49_000_000, start, 0.14),
            # This pose commits on the query event's decision edge.
            _pose(pose_base + 4, query, start, 1.50),
        )
        event_streams[window_id] = (
            _event(event_base, start, False, pose_base),
            _event(event_base + 1, start + 36_000_000, False, pose_base + 1),
            _event(
                event_base + 2,
                start + 49_500_000,
                False,
                pose_base + 3,
                ray_angle=0.10,
            ),
            _event(
                event_base + 3,
                query,
                True,
                pose_base + 3,
                ray_angle=0.20,
            ),
            _event(
                event_base + 4,
                query + 500_000,
                True,
                pose_base + 4,
                ray_angle=0.30,
                transform_guard_valid=False,
            ),
            _event(
                event_base + 5,
                query + 1_500_000,
                True,
                pose_base + 4,
                ray_angle=0.40,
            ),
        )
    registry_tuple = tuple(registry)
    baseline = evaluate_current_cav_registry(
        registry_tuple, event_streams, pose_streams
    )
    return registry_tuple, event_streams, pose_streams, baseline


def _verify_self_seals(output):
    for window in output["windows"]:
        for event in window["events"]:
            body = dict(event)
            supplied = body.pop("decision_sha256")
            if supplied != canonical_sha256(body):
                return False
        if window["events_sha256"] != canonical_sha256(window["events"]):
            return False
    body = dict(output)
    supplied = body.pop("aggregate_sha256")
    return supplied == canonical_sha256(body)


class RG3LockedOutputTests(unittest.TestCase):
    def setUp(self):
        (
            self.registry,
            self.event_streams,
            self.pose_streams,
            self.baseline,
        ) = _fixture()

    def _generate(self):
        return generate_locked_rg3_output(
            self.registry,
            self.event_streams,
            self.pose_streams,
            ADAPTER_SHA256,
        )

    def test_cycle_replay_uses_fixed_stage3_logical_ingress_profile(self):
        with mock.patch.object(
            rg3_output,
            "run_stage3_logical_cycle_model",
            wraps=rg3_output.run_stage3_logical_cycle_model,
        ) as replay:
            self._generate()
        self.assertTrue(replay.call_args_list)
        self.assertTrue(all(
            "ingress_profile" not in call.kwargs
            for call in replay.call_args_list
        ))
        self.assertEqual(
            rg3_output.STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
            STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
        )

    def test_exact_signed_row_contract_native_identity_and_event_conservation(self):
        output = self._generate()

        self.assertEqual(output["candidate_id"], RG3_POLICY.candidate_id)
        self.assertEqual(RG3_OUTPUT_CANDIDATE_ID, RG3_POLICY.candidate_id)
        self.assertIn("/", RG3_OUTPUT_CANDIDATE_ID)
        self.assertNotEqual(
            RG3_OUTPUT_CANDIDATE_ID, RG3_POLICY.candidate_id.replace("/", ".")
        )
        self.assertEqual(output["neutral_input_sha256"], self.baseline.neutral_input_sha256)
        self.assertTrue(_verify_self_seals(output))
        for output_window, baseline_window in zip(output["windows"], self.baseline.windows):
            rows = output_window["events"]
            self.assertEqual(
                [row["event_id"] for row in rows],
                [event.event_id for event in baseline_window.input_events],
            )
            for row, record in zip(rows, baseline_window.simulation.records):
                self.assertEqual(set(row), ROW_FIELDS)
                self.assertEqual(row["decision_cycle"], record.occurrence_cycle)
                self.assertEqual(row["occurrence_cycle"], row["decision_cycle"] - 1)
            self.assertEqual(rows[0]["decision_cycle"], 0)
            self.assertEqual(rows[0]["occurrence_cycle"], -1)

    def test_route_matrix_attempt_semantics_pose_ids_and_exact_taxonomy(self):
        output = self._generate()
        observed_routes = set()
        for output_window, baseline_window in zip(output["windows"], self.baseline.windows):
            for row, record in zip(
                output_window["events"], baseline_window.simulation.records
            ):
                observed_routes.add(row["route"])
                self.assertEqual(
                    row["candidate_attempted"],
                    record.disposition_reason == "causal_cav",
                )
                self.assertEqual(row["candidate_used"], row["route"] == ROUTE_CANDIDATE)
                if row["route"] == ROUTE_CANDIDATE:
                    self.assertEqual(row["model_id"], RG3_POLICY.candidate_id)
                    self.assertEqual(len(row["used_pose_ids"]), 3)
                    self.assertIsNone(row["fallback_reason"])
                    self.assertAlmostEqual(
                        math.sqrt(math.fsum(value * value for value in row["world_ray"])),
                        1.0,
                        places=12,
                    )
                elif row["route"] == ROUTE_FRESH_ZOH:
                    self.assertEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
                    self.assertEqual(row["used_pose_ids"], list(record.used_pose_ids))
                    self.assertEqual(row["fallback_reason"], "fresh_zoh_fallback")
                    self.assertIsNone(row["world_ray"])
                elif row["route"] == ROUTE_SENSOR_FIXED:
                    self.assertEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
                    self.assertEqual(row["used_pose_ids"], list(record.used_pose_ids))
                    self.assertEqual(row["fallback_reason"], record.disposition_reason)
                    self.assertIsNone(row["world_ray"])
        self.assertEqual(
            observed_routes,
            {
                ROUTE_CANDIDATE,
                ROUTE_CURRENT_CAV,
                ROUTE_FRESH_ZOH,
                ROUTE_SENSOR_FIXED,
            },
        )

    def test_failed_rg3_routes_to_exact_current_cav(self):
        real = rg3_output.recover_rg3_cav

        def forced_gate(samples, timestamp_ns, edge):
            decision = real(samples, timestamp_ns, edge)
            return replace(
                decision,
                candidate_used=False,
                quaternion_xyzw=decision.baseline_decision.quaternion_xyzw,
                reason="rate_change_gate",
            )

        with mock.patch.object(rg3_output, "recover_rg3_cav", side_effect=forced_gate):
            output = self._generate()
        for output_window, baseline_window in zip(output["windows"], self.baseline.windows):
            for row, record in zip(output_window["events"], baseline_window.simulation.records):
                if record.disposition_reason != "causal_cav":
                    self.assertFalse(row["candidate_attempted"])
                    continue
                self.assertTrue(row["candidate_attempted"])
                self.assertFalse(row["candidate_used"])
                self.assertEqual(row["route"], ROUTE_CURRENT_CAV)
                self.assertEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
                self.assertEqual(row["used_pose_ids"], list(record.used_pose_ids))
                self.assertEqual(row["fallback_reason"], "rate_change_gate")
                self.assertIsNone(row["world_ray"])

    def test_reset_excludes_negative_and_same_edge_poses_from_rg3(self):
        captured = []
        real = rg3_output.recover_rg3_cav

        def inspect(samples, timestamp_ns, edge):
            captured.append((samples, timestamp_ns, edge))
            return real(samples, timestamp_ns, edge)

        with mock.patch.object(rg3_output, "recover_rg3_cav", side_effect=inspect):
            output = self._generate()
        self.assertTrue(captured)
        for samples, timestamp_ns, edge in captured:
            self.assertTrue(all(sample.commit_cycle >= 0 for sample in samples))
            self.assertTrue(all(sample.commit_cycle < edge for sample in samples))
            self.assertTrue(all(
                sample.measurement_timestamp_ns <= timestamp_ns for sample in samples
            ))
        for window_index, output_window in enumerate(output["windows"]):
            pose_base = window_index * 100
            rows = output_window["events"]
            self.assertEqual(rows[0]["predictor_state_version"], 0)
            self.assertEqual(rows[3]["predictor_state_version"], 3)
            candidate_ids = {
                pose_id
                for row in rows
                if row["route"] == ROUTE_CANDIDATE
                for pose_id in row["used_pose_ids"]
            }
            self.assertNotIn(pose_base, candidate_ids)
            self.assertNotIn(pose_base + 4, rows[3]["used_pose_ids"])

    def test_candidate_pose_ids_equal_actual_ordered_rg3_inputs(self):
        timestamp_to_id = {
            pose.timestamp_ns: pose.pose_id
            for poses in self.pose_streams.values()
            for pose in poses
        }
        actual = []
        real = rg3_output.recover_rg3_cav

        def capture(samples, timestamp_ns, edge):
            actual.append([timestamp_to_id[sample.measurement_timestamp_ns] for sample in samples])
            return real(samples, timestamp_ns, edge)

        with mock.patch.object(rg3_output, "recover_rg3_cav", side_effect=capture):
            output = self._generate()
        attempted_rows = [
            row
            for window in output["windows"]
            for row in window["events"]
            if row["candidate_attempted"]
        ]
        self.assertEqual(len(actual), len(attempted_rows))
        for supplied, row in zip(actual, attempted_rows):
            if row["route"] == ROUTE_CANDIDATE:
                self.assertEqual(row["used_pose_ids"], supplied)
            else:
                self.assertEqual(row["route"], ROUTE_CURRENT_CAV)
                self.assertEqual(len(row["used_pose_ids"]), 2)

    def test_attempts_rg3_if_and_only_if_baseline_is_causal_cav(self):
        real = rg3_output.recover_rg3_cav
        with mock.patch.object(rg3_output, "recover_rg3_cav", wraps=real) as attempt:
            output = self._generate()
        expected = sum(
            record.disposition_reason == "causal_cav"
            for window in self.baseline.windows
            for record in window.simulation.records
        )
        self.assertEqual(attempt.call_count, expected)
        self.assertEqual(
            sum(
                row["candidate_attempted"]
                for window in output["windows"]
                for row in window["events"]
            ),
            expected,
        )

    def test_dependency_closed_manifest_and_receipt_binding(self):
        manifest = build_rg3_executable_manifest()
        self.assertEqual(manifest, RG3_EXECUTABLE_MANIFEST)
        self.assertEqual(tuple(row["path"] for row in manifest["files"]), MANIFEST_PATHS)
        self.assertEqual(MANIFEST_PATHS, tuple(sorted(set(MANIFEST_PATHS))))
        repository = Path(rg3_output.__file__).resolve().parents[2]
        for row in manifest["files"]:
            self.assertEqual(set(row), {"path", "sha256"})
            self.assertEqual(
                row["sha256"],
                hashlib.sha256((repository / row["path"]).read_bytes()).hexdigest(),
            )
        self.assertEqual(RG3_EXECUTABLE_MANIFEST_BYTES, canonical_json_bytes(manifest))
        self.assertEqual(
            RG3_EXECUTABLE_SHA256,
            hashlib.sha256(RG3_EXECUTABLE_MANIFEST_BYTES).hexdigest(),
        )
        self.assertEqual(RG3_CONFIG["candidate_id"], RG3_POLICY.candidate_id)
        self.assertEqual(RG3_CONFIG["executable_manifest_sha256"], RG3_EXECUTABLE_SHA256)
        self.assertEqual(RG3_CONFIG_SHA256, hashlib.sha256(RG3_CONFIG_BYTES).hexdigest())
        self.assertEqual(RG3_MODEL_SHA256, hashlib.sha256(RG3_MODEL_PATH.read_bytes()).hexdigest())
        output = self._generate()
        self.assertEqual(output["candidate_executable_sha256"], RG3_EXECUTABLE_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "rg3-executable-manifest.json"
            artifact.write_bytes(RG3_EXECUTABLE_MANIFEST_BYTES)
            self.assertEqual(
                hashlib.sha256(artifact.read_bytes()).hexdigest(),
                output["candidate_executable_sha256"],
            )

    def test_manifest_drop_extra_reorder_and_digest_mutations_fail_lock(self):
        variants = []
        dropped = deepcopy(RG3_EXECUTABLE_MANIFEST)
        dropped["files"].pop()
        variants.append(dropped)
        extra = deepcopy(RG3_EXECUTABLE_MANIFEST)
        extra["files"].append({"path": "extra.py", "sha256": "0" * 64})
        variants.append(extra)
        reordered = deepcopy(RG3_EXECUTABLE_MANIFEST)
        reordered["files"] = list(reversed(reordered["files"]))
        variants.append(reordered)
        changed = deepcopy(RG3_EXECUTABLE_MANIFEST)
        changed["files"][0]["sha256"] = "f" * 64
        variants.append(changed)
        for manifest in variants:
            self.assertNotEqual(
                hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
                RG3_EXECUTABLE_SHA256,
            )
            with mock.patch.object(
                rg3_output, "build_rg3_executable_manifest", return_value=manifest
            ):
                with self.assertRaisesRegex(RG3OutputError, "dependency changed"):
                    self._generate()

    def test_input_mutations_and_forbidden_fields_fail_closed(self):
        mutated = deepcopy(self.event_streams)
        first_window = self.registry[0].window_id
        object.__setattr__(mutated[first_window][0], "sensor_ray", (0.0, 1.0, 0.0))
        with self.assertRaisesRegex(RG3OutputError, "event content digest differs"):
            generate_locked_rg3_output(
                self.registry, mutated, self.pose_streams, ADAPTER_SHA256
            )

        class Contaminated:
            pass

        contaminated = Contaminated()
        for key, value in vars(self.event_streams[first_window][0]).items():
            setattr(contaminated, key, value)
        contaminated.score = 0.0
        changed = dict(self.event_streams)
        changed[first_window] = (contaminated,) + changed[first_window][1:]
        with self.assertRaisesRegex(RG3OutputError, "field schema differs"):
            generate_locked_rg3_output(
                self.registry, changed, self.pose_streams, ADAPTER_SHA256
            )

    def test_generator_import_boundary_has_no_evaluator_screen_selector_or_score(self):
        source = Path(rg3_output.__file__).read_text(encoding="utf-8")
        imported = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        forbidden = ("evaluator", "screen108", "selector", "scoring", "score_runner")
        self.assertFalse(any(
            token in name for name in imported for token in forbidden
        ))


if __name__ == "__main__":
    unittest.main()
