from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_output, screen108
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_output import (
    CURRENT_CAV_MODEL_ID,
    RG3_CONFIG_BYTES,
    RG3_CONFIG_SHA256,
    RG3_EXECUTABLE_PATH,
    RG3_EXECUTABLE_SHA256,
    RG3_MODEL_PATH,
    RG3_MODEL_SHA256,
    RG3_OUTPUT_CANDIDATE_ID,
    RG3OutputError,
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
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import New108AdapterBundle
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ADAPTER_SHA256 = "a" * 64


def _rotation_z(angle: float):
    return (0.0, 0.0, math.sin(0.5 * angle), math.cos(0.5 * angle))


def _pose(pose_id: int, timestamp_ns: int, start_ns: int, angle: float):
    quaternion = _rotation_z(angle)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
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
        start = index * 100_000_000
        query = start + 50_000_000
        end = query + 2_000_000
        window_id = "rg3-output-%d" % index
        pose_base = index * 100
        event_base = index * 1000
        registry.append(NeutralRegistryWindow(window_id, start, query, end))
        # Three usable 7 ms cadence poses plus one pose committing on the first
        # query event's exact occurrence edge.  The latter must be invisible.
        pose_streams[window_id] = (
            _pose(pose_base, start + 35_000_000, start, 0.00),
            _pose(pose_base + 1, start + 42_000_000, start, 0.07),
            _pose(pose_base + 2, start + 49_000_000, start, 0.14),
            _pose(pose_base + 3, query, start, 1.50),
        )
        event_streams[window_id] = (
            _event(event_base, start + 36_000_000, False, pose_base),
            _event(
                event_base + 1,
                start + 49_500_000,
                False,
                pose_base + 2,
                ray_angle=0.10,
            ),
            _event(
                event_base + 2,
                query,
                True,
                pose_base + 2,
                ray_angle=0.20,
            ),
            _event(
                event_base + 3,
                query + 500_000,
                True,
                pose_base + 3,
                ray_angle=0.30,
                transform_guard_valid=False,
            ),
        )
    registry_tuple = tuple(registry)
    baseline = evaluate_current_cav_registry(
        registry_tuple, event_streams, pose_streams
    )
    bundle = New108AdapterBundle(
        {},
        registry_tuple,
        event_streams,
        pose_streams,
        {},
        {"aggregate_sha256": ADAPTER_SHA256},
    )
    return registry_tuple, event_streams, pose_streams, baseline, bundle


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
            self.bundle,
        ) = _fixture()

    def _generate(self):
        return generate_locked_rg3_output(
            self.registry,
            self.event_streams,
            self.pose_streams,
            ADAPTER_SHA256,
        )

    def test_replays_every_event_at_exact_occurrence_edge_with_honest_provenance(self):
        output = self._generate()

        self.assertEqual(set(output), {
            "schema",
            "candidate_id",
            "adapter_aggregate_sha256",
            "neutral_input_sha256",
            "candidate_executable_sha256",
            "candidate_config_sha256",
            "windows",
            "aggregate_sha256",
        })
        self.assertEqual(output["candidate_id"], RG3_OUTPUT_CANDIDATE_ID)
        self.assertEqual(output["neutral_input_sha256"], self.baseline.neutral_input_sha256)
        self.assertTrue(_verify_self_seals(output))
        for output_window, baseline_window in zip(output["windows"], self.baseline.windows):
            self.assertEqual(
                set(output_window), {"window_id", "events", "events_sha256"}
            )
            rows = output_window["events"]
            self.assertEqual(
                [row["event_id"] for row in rows],
                [event.event_id for event in baseline_window.input_events],
            )
            self.assertEqual(
                [row["decision_cycle"] for row in rows],
                [record.occurrence_cycle for record in baseline_window.simulation.records],
            )
            successful = rows[2]
            self.assertEqual(set(successful), {
                "event_id",
                "event_content_sha256",
                "decision_cycle",
                "model_id",
                "predictor_state_version",
                "used_pose_ids",
                "candidate_used",
                "fallback_reason",
                "world_ray",
                "decision_sha256",
            })
            pose_base = baseline_window.input_poses[0].pose_id
            self.assertTrue(successful["candidate_used"])
            self.assertEqual(successful["model_id"], RG3_OUTPUT_CANDIDATE_ID)
            self.assertEqual(
                successful["used_pose_ids"],
                [pose_base, pose_base + 1, pose_base + 2],
            )
            self.assertNotIn(pose_base + 3, successful["used_pose_ids"])
            self.assertIsNone(successful["fallback_reason"])
            self.assertAlmostEqual(
                math.sqrt(math.fsum(value * value for value in successful["world_ray"])),
                1.0,
                places=12,
            )

    def test_state_resets_at_each_exact_50ms_preroll(self):
        output = self._generate()

        first_versions = [
            window["events"][0]["predictor_state_version"]
            for window in output["windows"]
        ]
        query_versions = [
            window["events"][2]["predictor_state_version"]
            for window in output["windows"]
        ]
        self.assertEqual(first_versions, [1, 1])
        self.assertEqual(query_versions, [3, 3])

        bad_registry = list(self.registry)
        row = bad_registry[0]
        object.__setattr__(row, "warmup_start_ns_inclusive", 1)
        try:
            with self.assertRaisesRegex(RG3OutputError, "exact 50 ms pre-roll"):
                generate_locked_rg3_output(
                    tuple(bad_registry), self.event_streams, self.pose_streams, ADAPTER_SHA256
                )
        finally:
            object.__setattr__(row, "warmup_start_ns_inclusive", 0)

    def test_attempts_rg3_only_for_cycle_model_current_cav_and_falls_back_exactly(self):
        real = rg3_output.recover_rg3_cav
        with mock.patch.object(rg3_output, "recover_rg3_cav", wraps=real) as attempt:
            output = self._generate()
        expected_attempts = sum(
            record.disposition_reason == "causal_cav"
            for window in self.baseline.windows
            for record in window.simulation.records
        )
        self.assertEqual(attempt.call_count, expected_attempts)
        for output_window, baseline_window in zip(output["windows"], self.baseline.windows):
            early = output_window["events"][0]
            guarded = output_window["events"][3]
            for row, baseline_record in (
                (early, baseline_window.simulation.records[0]),
                (guarded, baseline_window.simulation.records[3]),
            ):
                self.assertFalse(row["candidate_used"])
                self.assertEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
                self.assertEqual(row["used_pose_ids"], list(baseline_record.used_pose_ids))
                self.assertIsNone(row["world_ray"])
                self.assertTrue(row["fallback_reason"])

    def test_same_edge_pose_never_enters_rg3_call(self):
        captured = []
        real = rg3_output.recover_rg3_cav

        def inspect(samples, timestamp_ns, edge):
            captured.append((samples, timestamp_ns, edge))
            return real(samples, timestamp_ns, edge)

        with mock.patch.object(rg3_output, "recover_rg3_cav", side_effect=inspect):
            self._generate()
        for samples, timestamp_ns, edge in captured:
            self.assertTrue(samples)
            self.assertTrue(all(sample.commit_cycle < edge for sample in samples))
            self.assertTrue(all(
                sample.measurement_timestamp_ns <= timestamp_ns for sample in samples
            ))

    def test_screen108_accepts_all_honest_causal_rg3_pose_dependencies(self):
        output = self._generate()
        candidate_id, checked = screen108._validate_candidate_output(
            output,
            self.bundle,
            self.baseline,
            RG3_EXECUTABLE_SHA256,
            RG3_CONFIG_SHA256,
        )
        self.assertEqual(candidate_id, RG3_OUTPUT_CANDIDATE_ID)
        self.assertEqual(len(checked), len(self.baseline.windows))

    def test_input_mutations_and_forbidden_fields_fail_closed(self):
        mutated = deepcopy(self.event_streams)
        object.__setattr__(mutated[self.registry[0].window_id][0], "sensor_ray", (0.0, 1.0, 0.0))
        with self.assertRaisesRegex(RG3OutputError, "event content digest differs"):
            generate_locked_rg3_output(
                self.registry, mutated, self.pose_streams, ADAPTER_SHA256
            )

        class Contaminated:
            pass

        contaminated = Contaminated()
        for key, value in vars(self.event_streams[self.registry[0].window_id][0]).items():
            setattr(contaminated, key, value)
        contaminated.score = 0.0
        changed = dict(self.event_streams)
        changed[self.registry[0].window_id] = (
            contaminated,
        ) + changed[self.registry[0].window_id][1:]
        with self.assertRaisesRegex(RG3OutputError, "field schema differs"):
            generate_locked_rg3_output(
                self.registry, changed, self.pose_streams, ADAPTER_SHA256
            )

    def test_config_and_executable_receipts_are_file_hashes(self):
        self.assertEqual(
            RG3_EXECUTABLE_SHA256,
            hashlib.sha256(RG3_EXECUTABLE_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            RG3_CONFIG_SHA256,
            hashlib.sha256(RG3_CONFIG_BYTES).hexdigest(),
        )
        self.assertEqual(
            RG3_MODEL_SHA256,
            hashlib.sha256(RG3_MODEL_PATH.read_bytes()).hexdigest(),
        )
        output = self._generate()
        self.assertEqual(output["candidate_executable_sha256"], RG3_EXECUTABLE_SHA256)
        self.assertEqual(output["candidate_config_sha256"], RG3_CONFIG_SHA256)

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
