from __future__ import annotations

import math
from pathlib import Path
import ast
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
    load_neutral_registry,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


def z_rotation(angle_rad):
    return (0.0, 0.0, math.sin(angle_rad / 2.0), math.cos(angle_rad / 2.0))


def ray(angle_rad):
    return (math.cos(angle_rad), math.sin(angle_rad), 0.0)


def event_input(event_id, timestamp_ns, is_query, angle):
    sensor_ray = ray(angle)
    digest = canonical_event_content_sha256(
        event_id, timestamp_ns, 0, is_query, sensor_ray, 11
    )
    return NeutralEventInput(
        event_id, timestamp_ns, 0, is_query, sensor_ray, 11, digest
    )


def pose_input(pose_id, timestamp_ns, angle):
    quaternion = z_rotation(angle)
    digest = canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        digest,
    )


def synthetic_inputs():
    registry = NeutralRegistryWindow("neutral-window", 0, 1_500_000, 1_800_000)
    poses = (pose_input(10, 0, 0.0), pose_input(11, 1_000_000, 0.1))
    events = (
        event_input(100, 1_400_000, False, 0.00),
        event_input(101, 1_500_000, True, 0.10),
        event_input(102, 1_600_000, True, 0.20),
    )
    return registry, events, poses


class NeutralRegistryTests(unittest.TestCase):
    def test_registry_accepts_only_identity_and_time_bounds(self):
        row = {
            "window_id": "neutral-window",
            "warmup_start_ns_inclusive": 0,
            "query_start_ns_inclusive": 1_500_000,
            "query_end_ns_exclusive": 1_800_000,
        }
        registry = load_neutral_registry((row,))
        self.assertEqual(registry[0].to_mapping(), row)

        for forbidden in ("axis_label", "axis_threshold", "selector_feature"):
            with self.subTest(forbidden=forbidden):
                contaminated = dict(row)
                contaminated[forbidden] = "not evaluator input"
                with self.assertRaisesRegex(CurrentCAVEvaluationError, "bounds only"):
                    load_neutral_registry((contaminated,))

    def test_registry_rejects_overlap_and_non_registry_stream_keys(self):
        first = NeutralRegistryWindow("first", 0, 10, 20)
        second = NeutralRegistryWindow("second", 19, 30, 40)
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "overlap"):
            load_neutral_registry((first.to_mapping(), second.to_mapping()))
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "stream window IDs"):
            evaluate_current_cav_registry((first,), {"selector-row": ()}, {"first": ()})


class CurrentCAVEvaluatorTests(unittest.TestCase):
    def test_generic_window_uses_current_cav_and_past_only_references(self):
        registry, events, poses = synthetic_inputs()

        result = evaluate_current_cav_registry(
            (registry,), {registry.window_id: events}, {registry.window_id: poses}
        )

        self.assertEqual(result.accepted_events, 2)
        self.assertEqual(result.enabled_events, 2)
        self.assertEqual(
            [row.decision.disposition_reason for row in result.query_events],
            ["causal_cav", "causal_cav"],
        )
        self.assertEqual(
            [row.sensor_reference_event_id for row in result.query_events],
            [100, 101],
        )
        self.assertEqual(
            [row.world_reference_event_id for row in result.query_events],
            [100, 101],
        )
        self.assertTrue(result.windows[0].simulation.all_event_pose_indices_verified)
        self.assertFalse(result.windows[0].simulation.synthetic_test_mode)

    def test_exact_dataclass_types_and_fields_are_enforced(self):
        class RegistrySubclass(NeutralRegistryWindow):
            pass

        with self.assertRaisesRegex(CurrentCAVEvaluationError, "exact dataclass type"):
            RegistrySubclass("subclass", 0, 1, 2)

        registry, events, poses = synthetic_inputs()
        object.__setattr__(registry, "axis_label", "forbidden")
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "field set differs"):
            evaluate_current_cav_registry(
                (registry,), {registry.window_id: events}, {registry.window_id: poses}
            )

        registry, events, poses = synthetic_inputs()
        object.__setattr__(events[0], "axis_label", "forbidden")
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "field set differs"):
            evaluate_current_cav_registry(
                (registry,), {registry.window_id: events}, {registry.window_id: poses}
            )

        registry, events, poses = synthetic_inputs()
        object.__setattr__(poses[0], "selector_threshold", 0.5)
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "field set differs"):
            evaluate_current_cav_registry(
                (registry,), {registry.window_id: events}, {registry.window_id: poses}
            )

    def test_event_and_pose_content_mutations_fail_closed(self):
        original_ray = ray(0.1)
        original_event_digest = canonical_event_content_sha256(
            101, 1_500_000, 0, True, original_ray, 11
        )
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "event content digest"):
            NeutralEventInput(
                101,
                1_500_000,
                0,
                True,
                ray(0.2),
                11,
                original_event_digest,
            )

        original_pose_digest = canonical_pose_value_sha256(
            11, 1_000_000, z_rotation(0.1)
        )
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "pose content digest"):
            NeutralPoseInput(
                11,
                1_000_000,
                pose_timestamp_to_cycle(1_000_000, 0),
                z_rotation(0.2),
                original_pose_digest,
            )

        registry, events, poses = synthetic_inputs()
        object.__setattr__(events[1], "sensor_ray", ray(0.3))
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "event content digest"):
            evaluate_current_cav_registry(
                (registry,), {registry.window_id: events}, {registry.window_id: poses}
            )

    def test_aggregate_digest_binds_all_neutral_input_content(self):
        registry, events, poses = synthetic_inputs()
        original = evaluate_current_cav_registry(
            (registry,), {registry.window_id: events}, {registry.window_id: poses}
        )
        changed_events = (
            events[0],
            event_input(101, 1_500_000, True, 0.11),
            events[2],
        )
        changed = evaluate_current_cav_registry(
            (registry,),
            {registry.window_id: changed_events},
            {registry.window_id: poses},
        )
        self.assertNotEqual(
            original.neutral_input_sha256, changed.neutral_input_sha256
        )

    def test_sources_parse_with_python38_grammar(self):
        root = Path(__file__).resolve().parents[2]
        for relative in (
            "benchmarks/redred_mc_wtb_so3_axis_audit/evaluator.py",
            "benchmarks/redred_mc_wtb_so3_axis_audit/compatibility.py",
            "tests/redred_mc_wtb_so3_axis_audit/test_evaluator.py",
            "tests/redred_mc_wtb_so3_axis_audit/test_compatibility.py",
        ):
            with self.subTest(relative=relative):
                source = (root / relative).read_text(encoding="utf-8")
                ast.parse(source, filename=relative, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
