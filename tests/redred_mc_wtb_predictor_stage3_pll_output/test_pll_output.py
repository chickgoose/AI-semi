from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import screen108
from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
    CANDIDATE_ID,
    MODEL_ID,
    PLLOutputError,
    generate_locked_pll_output,
    generator_executable_sha256,
    locked_config_sha256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


def _rotation_z(angle):
    return (0.0, 0.0, math.sin(0.5 * angle), math.cos(0.5 * angle))


def _ray(angle):
    return (math.cos(angle), math.sin(angle), 0.0)


def _pose(pose_id, timestamp_ns, start_ns, angle, valid=True):
    quaternion = _rotation_z(angle)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
        valid,
        valid,
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


class _LabelTrapBundle:
    def __init__(self, registry, events, poses, aggregate):
        self.neutral_registry = registry
        self.event_streams = events
        self.pose_streams = poses
        self.provenance_seal = {"aggregate_sha256": aggregate}

    @property
    def selector_labels(self):
        raise AssertionError("PLL output generator read selector labels")


def _fixture(window_count=2, pre_roll_ns=50_000_000):
    registry = []
    event_streams = {}
    pose_streams = {}
    for window_index in range(window_count):
        start = window_index * 100_000_000
        query = start + pre_roll_ns
        end = query + 2_000
        window_id = "pll-window-%d" % window_index
        pose_base = 10 * window_index
        event_base = 100 * window_index
        registry.append(NeutralRegistryWindow(window_id, start, query, end))
        pose_streams[window_id] = (
            _pose(pose_base, start + 30_000_000, start, 0.0),
            _pose(pose_base + 1, start + 40_000_000, start, 0.1),
            _pose(pose_base + 2, query, start, 0.2),
        )
        event_streams[window_id] = (
            _event(event_base, query - 1_000_000, False, 0.0, pose_base + 1),
            _event(event_base + 1, query, True, 0.1, pose_base + 1),
            _event(event_base + 2, query + 1_000, True, 0.2, pose_base + 2),
        )
    baseline = evaluate_current_cav_registry(
        tuple(registry), event_streams, pose_streams
    )
    aggregate = "a" * 64
    bundle = _LabelTrapBundle(
        tuple(registry), event_streams, pose_streams, aggregate
    )
    return bundle, baseline


class LockedPLLOutputTests(unittest.TestCase):
    def test_output_passes_screen108_contract_and_is_fully_sealed(self):
        bundle, baseline = _fixture()
        output = generate_locked_pll_output(bundle, baseline)
        candidate_id, checked = screen108._validate_candidate_output(
            output,
            bundle,
            baseline,
            generator_executable_sha256(),
            locked_config_sha256(),
        )
        self.assertEqual(candidate_id, CANDIDATE_ID)
        self.assertEqual(
            tuple(checked),
            tuple(row.window_id for row in bundle.neutral_registry),
        )
        unsigned = dict(output)
        supplied = unsigned.pop("aggregate_sha256")
        self.assertEqual(supplied, canonical_sha256(unsigned))

    def test_same_edge_uses_old_state_then_future_edge_uses_locked_pll(self):
        bundle, baseline = _fixture(1)
        rows = generate_locked_pll_output(bundle, baseline)["windows"][0]["events"]
        same_edge = rows[1]
        future_edge = rows[2]
        self.assertFalse(same_edge["candidate_used"])
        self.assertEqual(same_edge["model_id"], "CURRENT_CAV")
        self.assertIsNone(same_edge["world_ray"])
        self.assertIn("pll_unlocked", same_edge["fallback_reason"])
        self.assertTrue(future_edge["candidate_used"])
        self.assertEqual(future_edge["model_id"], MODEL_ID)
        self.assertEqual(future_edge["used_pose_ids"], [2])
        self.assertGreater(
            future_edge["predictor_state_version"],
            same_edge["predictor_state_version"],
        )
        norm = math.sqrt(
            sum(component * component for component in future_edge["world_ray"])
        )
        self.assertAlmostEqual(norm, 1.0, places=12)

    def test_every_window_resets_at_its_50ms_preroll(self):
        bundle, baseline = _fixture(2)
        output = generate_locked_pll_output(bundle, baseline)
        for window in output["windows"]:
            rows = window["events"]
            self.assertEqual(rows[0]["predictor_state_version"], 1)
            self.assertFalse(rows[1]["candidate_used"])
            self.assertTrue(rows[2]["candidate_used"])
            self.assertEqual(rows[2]["predictor_state_version"], 2)

    def test_invalid_pose_does_not_publish_state_or_supply_geometry(self):
        bundle, baseline = _fixture(1)
        window_id = bundle.neutral_registry[0].window_id
        poses = list(bundle.pose_streams[window_id])
        poses[2] = _pose(2, 50_000_000, 0, 0.2, valid=False)
        pose_streams = {window_id: tuple(poses)}
        baseline = evaluate_current_cav_registry(
            bundle.neutral_registry, bundle.event_streams, pose_streams
        )
        changed = _LabelTrapBundle(
            bundle.neutral_registry,
            bundle.event_streams,
            pose_streams,
            bundle.provenance_seal["aggregate_sha256"],
        )
        rows = generate_locked_pll_output(changed, baseline)["windows"][0]["events"]
        self.assertFalse(rows[2]["candidate_used"])
        self.assertEqual(rows[2]["model_id"], "CURRENT_CAV")
        self.assertIsNone(rows[2]["world_ray"])

    def test_non_50ms_window_and_neutral_baseline_mismatch_fail_closed(self):
        bundle, baseline = _fixture(1)
        short_bundle, short_baseline = _fixture(1, pre_roll_ns=49_000_000)
        with self.assertRaisesRegex(PLLOutputError, "locked 50 ms pre-roll"):
            generate_locked_pll_output(short_bundle, short_baseline)

        changed_events = dict(bundle.event_streams)
        changed_events["pll-window-0"] = changed_events["pll-window-0"][:-1]
        changed = SimpleNamespace(
            neutral_registry=bundle.neutral_registry,
            event_streams=changed_events,
            pose_streams=bundle.pose_streams,
            provenance_seal=bundle.provenance_seal,
        )
        with self.assertRaisesRegex(PLLOutputError, "event inputs differ"):
            generate_locked_pll_output(changed, baseline)


if __name__ == "__main__":
    unittest.main()
