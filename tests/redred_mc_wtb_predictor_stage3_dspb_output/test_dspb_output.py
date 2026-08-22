from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import math
from pathlib import Path
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import dspb_output
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_output import (
    CURRENT_CAV_MODEL_ID,
    DSPBOutputError,
    generate_dspb_candidate_output,
    locked_dspb_config_bytes,
    locked_dspb_config_sha256,
    locked_dspb_executable_sha256,
    verify_dspb_candidate_output,
)
from benchmarks.redred_mc_wtb_predictor_stage3.screen108 import (
    CANDIDATE_OUTPUT_SCHEMA,
    _validate_candidate_output,
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
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ADAPTER_SHA256 = "1" * 64


def _rotation_z(angle_rad):
    return (
        0.0,
        0.0,
        math.sin(0.5 * angle_rad),
        math.cos(0.5 * angle_rad),
    )


def _ray(angle_rad):
    return (math.cos(angle_rad), math.sin(angle_rad), 0.0)


def _pose(pose_id, timestamp_ns, start_ns, angle_rad):
    quaternion = _rotation_z(angle_rad)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(event_id, timestamp_ns, query_ns, angle_rad, pose_id):
    sensor_ray = _ray(angle_rad)
    is_query = timestamp_ns >= query_ns
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


def _sealed_bundle(registries, event_streams, pose_streams):
    seal = {
        "aggregate_sha256": ADAPTER_SHA256,
    }
    return New108AdapterBundle(
        {}, tuple(registries), event_streams, pose_streams, {}, seal
    )


def _motion_fixture(window_count=2):
    registries = []
    event_streams = {}
    pose_streams = {}
    angles = tuple(
        math.radians(value) for value in (0, 1, 3, 6, 10, 15, 21, 28, 36, 45)
    )
    for window_index in range(window_count):
        start = window_index * 100_000_000
        query = start + 50_000_000
        end = query + 500_000
        window_id = "motion-%d" % window_index
        pose_base = window_index * 100
        event_base = window_index * 1000
        registries.append(NeutralRegistryWindow(window_id, start, query, end))
        poses = tuple(
            _pose(
                pose_base + index,
                start + index * 5_000_000,
                start,
                angle,
            )
            for index, angle in enumerate(angles)
        )
        # The two 45 ms records share the commit edge of pose 9, and therefore
        # bind pose 8.  Later records bind pose 9.
        events = (
            _event(event_base, start + 45_000_000, query, 0.00, pose_base + 8),
            _event(event_base + 1, start + 45_000_000, query, 0.01, pose_base + 8),
            _event(event_base + 2, start + 49_200_000, query, 0.02, pose_base + 9),
            _event(event_base + 3, start + 49_800_000, query, 0.03, pose_base + 9),
            _event(event_base + 4, query, query, 0.04, pose_base + 9),
            _event(event_base + 5, query, query, 0.05, pose_base + 9),
        )
        event_streams[window_id] = events
        pose_streams[window_id] = poses
    return tuple(registries), event_streams, pose_streams


def _fallback_fixture():
    start = 0
    query = 50_000_000
    registry = (NeutralRegistryWindow("fallback", start, query, query + 500_000),)
    poses = (
        _pose(0, query - 1_000_000, start, 0.0),
        _pose(1, query - 500_000, start, 0.1),
    )
    events = (
        _event(0, query - 200_000, query, 0.00, 1),
        _event(1, query - 100_000, query, 0.01, 1),
        _event(2, query, query, 0.02, 1),
    )
    return registry, {"fallback": events}, {"fallback": poses}


def _screen_validate(registries, event_streams, pose_streams, output):
    baseline = evaluate_current_cav_registry(
        registries, event_streams, pose_streams
    )
    bundle = _sealed_bundle(registries, event_streams, pose_streams)
    return _validate_candidate_output(
        output,
        bundle,
        baseline,
        locked_dspb_executable_sha256(),
        locked_dspb_config_sha256(),
    )


class LockedDSPBOutputTests(unittest.TestCase):
    def test_replay_is_screen108_compatible_and_sealed(self):
        registries, events, poses = _motion_fixture()
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        candidate_id, checked = _screen_validate(
            registries, events, poses, output
        )

        self.assertEqual(output["schema"], CANDIDATE_OUTPUT_SCHEMA)
        self.assertEqual(output["candidate_id"], DSPBConfig().candidate_id)
        self.assertEqual(candidate_id, DSPBConfig().candidate_id)
        self.assertEqual(output["adapter_aggregate_sha256"], ADAPTER_SHA256)
        self.assertEqual(
            output["candidate_executable_sha256"],
            locked_dspb_executable_sha256(),
        )
        self.assertEqual(
            output["candidate_config_sha256"], locked_dspb_config_sha256()
        )
        body = dict(output)
        supplied = body.pop("aggregate_sha256")
        self.assertEqual(supplied, canonical_sha256(body))
        self.assertEqual(set(checked), {row.window_id for row in registries})

    def test_commit_edge_old_state_equal_time_atomicity_and_window_reset(self):
        registries, events, poses = _motion_fixture()
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        first = output["windows"][0]["events"]
        second = output["windows"][1]["events"]

        self.assertEqual(first[0]["predictor_state_version"], 9)
        self.assertEqual(first[1]["predictor_state_version"], 9)
        self.assertEqual(first[2]["predictor_state_version"], 10)
        self.assertNotIn(9, first[0]["used_pose_ids"])
        self.assertNotIn(9, first[1]["used_pose_ids"])
        self.assertIn(9, first[2]["used_pose_ids"])
        self.assertEqual(first[4]["predictor_state_version"], first[5]["predictor_state_version"])
        self.assertEqual(
            [row["predictor_state_version"] for row in first],
            [row["predictor_state_version"] for row in second],
        )
        self.assertEqual(
            [row["candidate_used"] for row in first],
            [row["candidate_used"] for row in second],
        )

        with_pre_reset_pose = dict(poses)
        with_pre_reset_pose["motion-1"] = (
            _pose(99, 99_000_000, 100_000_000, -0.5),
        ) + poses["motion-1"]
        reset_output = generate_dspb_candidate_output(
            registries, events, with_pre_reset_pose, ADAPTER_SHA256
        )
        self.assertEqual(
            [row["predictor_state_version"] for row in first],
            [
                row["predictor_state_version"]
                for row in reset_output["windows"][1]["events"]
            ],
        )

    def test_candidate_receipts_use_normalized_world_rays_without_hidden_poses(self):
        registries, events, poses = _motion_fixture(1)
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        baseline = evaluate_current_cav_registry(registries, events, poses)
        rows = output["windows"][0]["events"]
        candidates = [row for row in rows if row["candidate_used"]]
        self.assertTrue(candidates)
        for row in candidates:
            norm = math.sqrt(math.fsum(value * value for value in row["world_ray"]))
            self.assertAlmostEqual(norm, 1.0, places=12)
            self.assertNotEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
            self.assertIsNone(row["fallback_reason"])
            baseline_row = next(
                record
                for record in baseline.windows[0].simulation.records
                if record.event_id == row["event_id"]
            )
            self.assertTrue(set(row["used_pose_ids"]).issubset(
                baseline_row.occurrence_pose_ids
            ))
        _screen_validate(registries, events, poses, output)

    def test_untrained_bank_delegates_exactly_to_current_cav(self):
        registries, events, poses = _fallback_fixture()
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        baseline = evaluate_current_cav_registry(registries, events, poses)
        rows = output["windows"][0]["events"]
        for row, baseline_row in zip(rows, baseline.windows[0].simulation.records):
            self.assertEqual(baseline_row.disposition, "corrected_world_ray")
            self.assertFalse(row["candidate_used"])
            self.assertEqual(row["model_id"], CURRENT_CAV_MODEL_ID)
            self.assertIsNone(row["world_ray"])
            self.assertTrue(row["fallback_reason"])
            self.assertEqual(
                row["used_pose_ids"], sorted(set(baseline_row.used_pose_ids))
            )
        _screen_validate(registries, events, poses, output)

    def test_output_is_deterministic_and_verifier_reconstructs_everything(self):
        registries, events, poses = _motion_fixture(1)
        first = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        second = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        self.assertEqual(first, second)
        self.assertEqual(
            verify_dspb_candidate_output(
                first, registries, events, poses, ADAPTER_SHA256
            ),
            first["aggregate_sha256"],
        )
        changed = deepcopy(first)
        changed["windows"][0]["events"][0]["decision_cycle"] += 1
        with self.assertRaisesRegex(DSPBOutputError, "locked replay"):
            verify_dspb_candidate_output(
                changed, registries, events, poses, ADAPTER_SHA256
            )

    def test_locked_authority_hashes_bind_exact_source_and_config_bytes(self):
        source = Path(dspb_output.__file__).with_name("dspb.py").read_bytes()
        self.assertEqual(
            locked_dspb_executable_sha256(), hashlib.sha256(source).hexdigest()
        )
        self.assertEqual(
            locked_dspb_config_sha256(),
            hashlib.sha256(locked_dspb_config_bytes()).hexdigest(),
        )
        self.assertEqual(locked_dspb_config_sha256(), DSPBConfig().sha256)

    def test_inputs_fail_closed_on_preroll_order_digest_and_commit_alias(self):
        registries, events, poses = _motion_fixture(1)
        bad_registry = (
            NeutralRegistryWindow("motion-0", 0, 49_000_000, 50_500_000),
        )
        with self.assertRaisesRegex(DSPBOutputError, "exact 50 ms"):
            generate_dspb_candidate_output(
                bad_registry, events, poses, ADAPTER_SHA256
            )

        reordered = {"motion-0": tuple(reversed(events["motion-0"]))}
        with self.assertRaisesRegex(DSPBOutputError, "nondecreasing"):
            generate_dspb_candidate_output(
                registries, reordered, poses, ADAPTER_SHA256
            )

        corrupted = deepcopy(events)
        object.__setattr__(corrupted["motion-0"][0], "timestamp_ns", 44_000_000)
        with self.assertRaisesRegex(DSPBOutputError, "content digest"):
            generate_dspb_candidate_output(
                registries, corrupted, poses, ADAPTER_SHA256
            )

        aliased = list(poses["motion-0"])
        object.__setattr__(aliased[1], "commit_cycle", aliased[0].commit_cycle)
        with self.assertRaisesRegex(DSPBOutputError, "commit cycles"):
            generate_dspb_candidate_output(
                registries, events, {"motion-0": tuple(aliased)}, ADAPTER_SHA256
            )

    def test_public_api_has_no_label_score_filter_or_outcome_channel(self):
        parameters = set(inspect.signature(generate_dspb_candidate_output).parameters)
        self.assertEqual(
            parameters,
            {"registry", "event_streams", "pose_streams", "adapter_aggregate_sha256"},
        )
        forbidden = {"label", "score", "filter", "loss", "quality", "outcome"}
        self.assertFalse(parameters.intersection(forbidden))

    def test_source_parses_with_python38_grammar(self):
        source = Path(dspb_output.__file__).read_text(encoding="utf-8")
        ast.parse(source, filename="dspb_output.py", feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
