from __future__ import annotations

import math
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    evaluate_current_cav_registry,
    load_neutral_registry,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


SHA_A = "a" * 64
SHA_B = "b" * 64


def z_rotation(angle_rad):
    return (0.0, 0.0, math.sin(angle_rad / 2.0), math.cos(angle_rad / 2.0))


def ray(angle_rad):
    return (math.cos(angle_rad), math.sin(angle_rad), 0.0)


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
        registry = NeutralRegistryWindow(
            "neutral-window", 0, 1_500_000, 1_800_000
        )
        poses = (
            NeutralPoseInput(
                10,
                0,
                pose_timestamp_to_cycle(0, 0),
                z_rotation(0.0),
                SHA_A,
            ),
            NeutralPoseInput(
                11,
                1_000_000,
                pose_timestamp_to_cycle(1_000_000, 0),
                z_rotation(0.1),
                SHA_B,
            ),
        )
        events = tuple(
            NeutralEventInput(
                event_id,
                timestamp_ns,
                0,
                timestamp_ns >= registry.query_start_ns_inclusive,
                ray(angle),
                11,
            )
            for event_id, timestamp_ns, angle in (
                (100, 1_400_000, 0.00),
                (101, 1_500_000, 0.10),
                (102, 1_600_000, 0.20),
            )
        )

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


if __name__ == "__main__":
    unittest.main()
