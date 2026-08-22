"""Known answers for decision edge zero across production output adapters."""

from __future__ import annotations

from copy import deepcopy
import math
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    dspb_output,
    pll_output,
    rg3_output,
    screen108,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ADAPTER_SHA256 = "a" * 64
CURRENT_CAV_MODEL_ID = "CURRENT_CAV"
UINT64_MAX = (1 << 64) - 1
ROUTE_BY_REASON = {
    "causal_cav": "CURRENT_CAV",
    "fresh_zoh_fallback": "FRESH_ZOH",
    "stale_pose": "SENSOR_FIXED",
}


def _rotation_z(angle_rad):
    return (
        0.0,
        0.0,
        math.sin(0.5 * angle_rad),
        math.cos(0.5 * angle_rad),
    )


def _pose(pose_id, timestamp_ns, window_start_ns, angle_rad):
    quaternion = _rotation_z(angle_rad)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, window_start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(event_id, timestamp_ns, pose_id, is_query, ray_angle=0.0):
    sensor_ray = (math.cos(ray_angle), math.sin(ray_angle), 0.0)
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        0,
        is_query,
        sensor_ray,
        pose_id,
        canonical_event_content_sha256(
            event_id,
            timestamp_ns,
            0,
            is_query,
            sensor_ray,
            pose_id,
        ),
    )


def _fixture(same_edge_angle=0.9):
    registry = []
    event_streams = {}
    pose_streams = {}
    same_edge_pose_ids = {}
    kinds = ("cav", "zoh", "sensor")

    for index, kind in enumerate(kinds):
        start_ns = 10_000_000 + index * 100_000_000
        query_ns = start_ns + 50_000_000
        window_id = "edge-zero-%s" % kind
        registry.append(NeutralRegistryWindow(
            window_id,
            start_ns,
            query_ns,
            query_ns + 1,
        ))

        if kind == "cav":
            poses = (
                _pose(0, start_ns - 2_000_000, start_ns, 0.0),
                _pose(1, start_ns - 1_000_000, start_ns, 0.1),
                _pose(2, start_ns, start_ns, same_edge_angle),
            )
            edge_zero_source = 1
            post_reset_source = 2
        elif kind == "zoh":
            poses = (
                _pose(10, start_ns - 500_000, start_ns, 0.1),
                _pose(11, start_ns, start_ns, same_edge_angle),
            )
            edge_zero_source = 10
            post_reset_source = 11
        else:
            poses = (
                _pose(20, start_ns - 2_000_000, start_ns, 0.1),
                _pose(21, start_ns, start_ns, same_edge_angle),
            )
            edge_zero_source = 20
            post_reset_source = 21

        event_base = 100 + index * 3
        event_streams[window_id] = (
            _event(event_base, start_ns, edge_zero_source, False),
            _event(
                event_base + 1,
                query_ns - 1_000_000,
                post_reset_source,
                False,
                0.1,
            ),
            _event(event_base + 2, query_ns, post_reset_source, True, 0.2),
        )
        pose_streams[window_id] = poses
        same_edge_pose_ids[window_id] = post_reset_source

    registry = tuple(registry)
    baseline = evaluate_current_cav_registry(
        registry, event_streams, pose_streams
    )
    bundle = New108AdapterBundle(
        {},
        registry,
        event_streams,
        pose_streams,
        {},
        {"aggregate_sha256": ADAPTER_SHA256},
    )
    return (
        registry,
        event_streams,
        pose_streams,
        baseline,
        bundle,
        same_edge_pose_ids,
    )


def _outputs(fixture):
    registry, events, poses, baseline, bundle, unused_ids = fixture
    del unused_ids
    return {
        "RG3": rg3_output.generate_locked_rg3_output(
            registry, events, poses, ADAPTER_SHA256
        ),
        "DSPB": dspb_output.generate_dspb_candidate_output(
            registry, events, poses, ADAPTER_SHA256
        ),
        "PLL": pll_output.generate_locked_pll_output(bundle, baseline),
    }


def _project_for_screen(output):
    windows = []
    for rich_window in output["windows"]:
        events = []
        for rich_row in rich_window["events"]:
            candidate_used = bool(rich_row["candidate_used"])
            events.append({
                "event_id": rich_row["event_id"],
                "event_content_sha256": rich_row["event_content_sha256"],
                "occurrence_cycle": rich_row["occurrence_cycle"],
                "decision_cycle": rich_row["decision_cycle"],
                "model_id": (
                    output["candidate_id"]
                    if candidate_used
                    else CURRENT_CAV_MODEL_ID
                ),
                "predictor_state_version": rich_row["predictor_state_version"],
                "used_pose_ids": list(rich_row["used_pose_ids"]),
                "route": str(rich_row["route"]).lower(),
                "candidate_attempted": bool(rich_row["candidate_attempted"]),
                "candidate_used": candidate_used,
                "fallback_reason": (
                    None if candidate_used else rich_row["fallback_reason"]
                ),
                "world_ray": rich_row["world_ray"] if candidate_used else None,
            })
        windows.append({"window_id": rich_window["window_id"], "events": events})
    return windows


def _seal_projection(output, baseline, windows):
    return screen108.seal_candidate_output(
        output["candidate_id"],
        ADAPTER_SHA256,
        baseline.neutral_input_sha256,
        output["candidate_executable_sha256"],
        output["candidate_config_sha256"],
        windows,
    )


class DecisionEdgeZeroKnownAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _fixture()
        cls.outputs = _outputs(cls.fixture)

    def test_all_rich_outputs_keep_signed_minus_one_and_exact_baseline_routes(self):
        _, _, poses, baseline, _, same_edge_ids = self.fixture
        expected_reasons = ("causal_cav", "fresh_zoh_fallback", "stale_pose")

        for candidate_name, output in self.outputs.items():
            for rich_window, baseline_window, expected_reason in zip(
                output["windows"], baseline.windows, expected_reasons
            ):
                with self.subTest(
                    candidate=candidate_name,
                    window=rich_window["window_id"],
                ):
                    row = rich_window["events"][0]
                    baseline_row = baseline_window.simulation.records[0]
                    same_edge_pose_id = same_edge_ids[rich_window["window_id"]]

                    self.assertEqual(baseline_row.occurrence_cycle, 0)
                    self.assertEqual(baseline_row.disposition_reason, expected_reason)
                    self.assertEqual(row["decision_cycle"], 0)
                    self.assertEqual(row["occurrence_cycle"], -1)
                    self.assertIs(type(row["occurrence_cycle"]), int)
                    self.assertLess(row["occurrence_cycle"], row["decision_cycle"])
                    self.assertNotEqual(row["occurrence_cycle"], UINT64_MAX)
                    self.assertEqual(row["route"], ROUTE_BY_REASON[expected_reason])
                    self.assertEqual(
                        row["candidate_attempted"], expected_reason == "causal_cav"
                    )
                    self.assertFalse(row["candidate_used"])
                    self.assertEqual(
                        row["used_pose_ids"], list(baseline_row.used_pose_ids)
                    )
                    self.assertNotIn(same_edge_pose_id, row["used_pose_ids"])
                    self.assertEqual(row["predictor_state_version"], 0)
                    if "state_dependency_pose_ids" in row:
                        self.assertEqual(row["state_dependency_pose_ids"], [])

                    pose_by_id = {
                        pose.pose_id: pose
                        for pose in poses[rich_window["window_id"]]
                    }
                    self.assertTrue(all(
                        pose_by_id[pose_id].commit_cycle < row["decision_cycle"]
                        for pose_id in row["used_pose_ids"]
                    ))

    def test_same_edge_pose_value_cannot_change_edge_zero_decision(self):
        changed_outputs = _outputs(_fixture(same_edge_angle=-0.7))
        for candidate_name in self.outputs:
            for original_window, changed_window in zip(
                self.outputs[candidate_name]["windows"],
                changed_outputs[candidate_name]["windows"],
            ):
                with self.subTest(
                    candidate=candidate_name,
                    window=original_window["window_id"],
                ):
                    self.assertEqual(
                        original_window["events"][0], changed_window["events"][0]
                    )

    def test_screen_projection_accepts_minus_one_and_rejects_unsigned_wrap(self):
        _, _, _, baseline, bundle, _ = self.fixture
        for candidate_name, output in self.outputs.items():
            projected = _project_for_screen(output)
            sealed = _seal_projection(output, baseline, projected)
            with self.subTest(candidate=candidate_name, mutation="known-answer"):
                self.assertEqual(
                    sealed["windows"][0]["events"][0]["occurrence_cycle"], -1
                )
                screen108._validate_candidate_output(
                    sealed,
                    bundle,
                    baseline,
                    output["candidate_executable_sha256"],
                    output["candidate_config_sha256"],
                )

            wrapped = deepcopy(projected)
            wrapped[0]["events"][0]["occurrence_cycle"] = UINT64_MAX
            wrapped_seal = _seal_projection(output, baseline, wrapped)
            with self.subTest(candidate=candidate_name, mutation="uint64-wrap"):
                with self.assertRaisesRegex(
                    screen108.Screen108Error,
                    "occurrence edge must equal decision edge minus one",
                ):
                    screen108._validate_candidate_output(
                        wrapped_seal,
                        bundle,
                        baseline,
                        output["candidate_executable_sha256"],
                        output["candidate_config_sha256"],
                    )


if __name__ == "__main__":
    unittest.main()
