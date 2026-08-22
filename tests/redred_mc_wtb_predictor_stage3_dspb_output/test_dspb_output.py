from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import inspect
import math
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import dspb_output
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_output import (
    CANDIDATE_OUTPUT_SCHEMA,
    DSPBOutputError,
    EXECUTABLE_MANIFEST_SCHEMA,
    ROUTE_CANDIDATE,
    ROUTE_CURRENT_CAV,
    ROUTE_FRESH_ZOH,
    ROUTE_SENSOR_FIXED,
    generate_dspb_candidate_output,
    locked_dspb_config_bytes,
    locked_dspb_config_sha256,
    locked_dspb_executable_manifest,
    locked_dspb_executable_sha256,
    verify_dspb_candidate_output,
)
from benchmarks.redred_mc_wtb_pose_recovery import rotate_sensor_ray_to_world
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
    pose_timestamp_to_cycle,
)


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


def _event(
    event_id,
    timestamp_ns,
    query_ns,
    angle_rad,
    pose_id,
    transform_guard_valid=True,
):
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
            transform_guard_valid,
        ),
        transform_guard_valid,
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


def _single_route_fixture(route):
    start = 10_000_000
    query = start + 50_000_000
    window_id = "route-%s" % route.lower()
    registry = (
        NeutralRegistryWindow(window_id, start, query, query + 500_000),
    )
    if route == ROUTE_FRESH_ZOH:
        poses = (_pose(0, query - 500_000, start, 0.1),)
    elif route == ROUTE_SENSOR_FIXED:
        poses = (_pose(0, start, start, 0.1),)
    else:
        raise AssertionError("unknown fixture route")
    events = (_event(0, query, query, 0.02, 0),)
    return registry, {window_id: events}, {window_id: poses}


def _signed_reset_fixture():
    start = 10_000_000
    query = start + 50_000_000
    window_id = "signed-reset"
    registry = (
        NeutralRegistryWindow(window_id, start, query, query + 500_000),
    )
    poses = (
        _pose(0, start - 500_000, start, -0.1),
        _pose(1, query - 500_000, start, 0.1),
    )
    events = (
        _event(0, start, query, 0.00, 0),
        _event(1, query, query, 0.02, 1),
    )
    return registry, {window_id: events}, {window_id: poses}


def _assert_sealed(test, output):
    body = dict(output)
    supplied = body.pop("aggregate_sha256")
    test.assertEqual(supplied, canonical_sha256(body))
    for window in output["windows"]:
        window_body = dict(window)
        window_sha256 = window_body.pop("window_sha256")
        test.assertEqual(window_sha256, canonical_sha256(window_body))
        test.assertEqual(
            window["events_sha256"], canonical_sha256(window["events"])
        )
        test.assertEqual(
            window["state_receipts_sha256"],
            canonical_sha256(window["state_receipts"]),
        )
        test.assertEqual(
            window["pose_receipts_sha256"],
            canonical_sha256(window["pose_receipts"]),
        )


class LockedDSPBOutputTests(unittest.TestCase):
    def test_cycle_replay_uses_fixed_stage3_logical_ingress_profile(self):
        registries, events, poses = _motion_fixture()
        with mock.patch.object(
            dspb_output, "run_cycle_model", wraps=dspb_output.run_cycle_model
        ) as replay:
            generate_dspb_candidate_output(
                registries, events, poses, ADAPTER_SHA256
            )
        self.assertTrue(replay.call_args_list)
        self.assertTrue(all(
            call.kwargs["ingress_profile"]
            == STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE
            for call in replay.call_args_list
        ))

    def test_replay_has_native_identity_hardened_schema_and_recursive_seals(self):
        registries, events, poses = _motion_fixture()
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )

        self.assertEqual(set(output), {
            "schema",
            "candidate_id",
            "adapter_aggregate_sha256",
            "neutral_input_sha256",
            "candidate_executable_sha256",
            "candidate_executable_manifest",
            "candidate_config_sha256",
            "candidate_config",
            "windows",
            "aggregate_sha256",
        })
        self.assertEqual(output["schema"], CANDIDATE_OUTPUT_SCHEMA)
        self.assertEqual(output["candidate_id"], DSPBConfig().candidate_id)
        self.assertEqual(output["adapter_aggregate_sha256"], ADAPTER_SHA256)
        self.assertEqual(
            output["candidate_executable_sha256"],
            locked_dspb_executable_sha256(),
        )
        self.assertEqual(
            output["candidate_config_sha256"], locked_dspb_config_sha256()
        )
        self.assertEqual(output["candidate_config"], DSPBConfig().to_mapping())
        self.assertEqual(
            output["candidate_executable_manifest"],
            locked_dspb_executable_manifest(),
        )
        _assert_sealed(self, output)

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
        self.assertEqual(first[0]["predictor_state_sha256"], first[1]["predictor_state_sha256"])
        self.assertEqual(first[0]["occurrence_cycle"], first[0]["decision_cycle"] - 1)
        self.assertNotIn(9, first[0]["used_pose_ids"])
        self.assertNotIn(9, first[1]["used_pose_ids"])
        self.assertIn(9, first[2]["used_pose_ids"])
        self.assertEqual(first[4]["predictor_state_version"], first[5]["predictor_state_version"])
        self.assertEqual(
            [row["predictor_state_version"] for row in first],
            [row["predictor_state_version"] for row in second],
        )
        first_window = output["windows"][0]
        self.assertEqual(first_window["state_receipts"][0]["effective_cycle"], 0)
        self.assertIsNone(first_window["state_receipts"][0]["parent_state_sha256"])
        self.assertIsNone(
            first_window["reset_receipt"]["previous_window_state_sha256"]
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
        self.assertEqual(
            reset_output["windows"][1]["reset_receipt"]["generation"][
                "excluded_pre_reset_pose_ids"
            ],
            [99],
        )

        signed_registry, signed_events, signed_poses = _signed_reset_fixture()
        signed = generate_dspb_candidate_output(
            signed_registry, signed_events, signed_poses, ADAPTER_SHA256
        )
        first_signed = signed["windows"][0]["events"][0]
        self.assertEqual(first_signed["decision_cycle"], 0)
        self.assertEqual(first_signed["occurrence_cycle"], -1)
        self.assertEqual(first_signed["predictor_state_version"], 0)
        self.assertEqual(first_signed["state_dependency_pose_ids"], [])

    def test_candidate_receipts_preserve_all_pose_dependencies_and_ray_derivation(self):
        registries, events, poses = _motion_fixture(1)
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        baseline = evaluate_current_cav_registry(registries, events, poses)
        rows = output["windows"][0]["events"]
        self.assertEqual(set(rows[0]), {
            "event_id",
            "event_content_sha256",
            "event_timestamp_ns",
            "is_query",
            "occurrence_cycle",
            "decision_cycle",
            "model_id",
            "geometry_expert_id",
            "predictor_state_version",
            "predictor_state_sha256",
            "state_dependency_pose_ids",
            "pose_receipt_chain_sha256",
            "used_pose_ids",
            "used_pose_evidence",
            "route",
            "route_reason",
            "candidate_attempted",
            "candidate_used",
            "candidate_failure_reason",
            "fallback_reason",
            "output_quaternion_xyzw",
            "world_ray",
            "ray_derivation_receipt",
            "native_decision_sha256",
            "prior_decision_sha256",
            "decision_sha256",
        })
        candidates = [row for row in rows if row["candidate_used"]]
        self.assertTrue(candidates)
        self.assertTrue(any(len(row["used_pose_ids"]) >= 3 for row in candidates))
        for row in candidates:
            norm = math.sqrt(math.fsum(value * value for value in row["world_ray"]))
            self.assertAlmostEqual(norm, 1.0, places=12)
            self.assertEqual(row["model_id"], DSPBConfig().candidate_id)
            self.assertEqual(row["route"], ROUTE_CANDIDATE)
            self.assertTrue(row["candidate_attempted"])
            self.assertIsNone(row["fallback_reason"])
            self.assertIsNone(row["candidate_failure_reason"])
            self.assertTrue(
                set(row["used_pose_ids"]).issubset(
                    row["state_dependency_pose_ids"]
                )
            )
            self.assertEqual(
                [item["pose_id"] for item in row["used_pose_evidence"]],
                row["used_pose_ids"],
            )
            baseline_row = next(
                record
                for record in baseline.windows[0].simulation.records
                if record.event_id == row["event_id"]
            )
            self.assertEqual(baseline_row.disposition_reason, "causal_cav")
            event = next(
                item for item in events["motion-0"] if item.event_id == row["event_id"]
            )
            expected_ray = rotate_sensor_ray_to_world(
                row["output_quaternion_xyzw"], event.sensor_ray
            )
            self.assertEqual(tuple(row["world_ray"]), expected_ray)
            ray_receipt = dict(row["ray_derivation_receipt"])
            ray_digest = ray_receipt.pop("ray_derivation_sha256")
            self.assertEqual(ray_digest, canonical_sha256(ray_receipt))

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
            self.assertTrue(row["candidate_attempted"])
            self.assertEqual(row["route"], ROUTE_CURRENT_CAV)
            self.assertEqual(row["model_id"], DSPBConfig().candidate_id)
            self.assertIsNotNone(row["world_ray"])
            self.assertIsNotNone(row["output_quaternion_xyzw"])
            self.assertTrue(row["candidate_failure_reason"])
            self.assertTrue(row["fallback_reason"])
            self.assertEqual(
                row["used_pose_ids"], sorted(set(baseline_row.used_pose_ids))
            )

    def test_fresh_zoh_and_sensor_fixed_are_distinct_unattempted_routes(self):
        for expected_route in (ROUTE_FRESH_ZOH, ROUTE_SENSOR_FIXED):
            with self.subTest(route=expected_route):
                registries, events, poses = _single_route_fixture(expected_route)
                output = generate_dspb_candidate_output(
                    registries, events, poses, ADAPTER_SHA256
                )
                row = output["windows"][0]["events"][0]
                self.assertEqual(row["route"], expected_route)
                self.assertFalse(row["candidate_attempted"])
                self.assertFalse(row["candidate_used"])
                self.assertIsNone(row["candidate_failure_reason"])
                self.assertEqual(row["model_id"], DSPBConfig().candidate_id)
                if expected_route == ROUTE_FRESH_ZOH:
                    self.assertEqual(row["route_reason"], "fresh_zoh_fallback")
                    self.assertIsNotNone(row["world_ray"])
                    self.assertEqual(row["used_pose_ids"], [0])
                else:
                    self.assertEqual(row["route_reason"], "stale_pose")
                    self.assertIsNone(row["world_ray"])
                    self.assertIsNone(row["output_quaternion_xyzw"])

    def test_state_pose_and_event_receipts_form_complete_hash_chains(self):
        registries, events, poses = _motion_fixture(1)
        output = generate_dspb_candidate_output(
            registries, events, poses, ADAPTER_SHA256
        )
        window = output["windows"][0]
        reset = dict(window["reset_receipt"])
        reset_digest = reset.pop("reset_receipt_sha256")
        self.assertEqual(reset_digest, canonical_sha256(reset))

        state_by_sha = {}
        for state in window["state_receipts"]:
            body = dict(state)
            digest = body.pop("state_sha256")
            self.assertEqual(digest, canonical_sha256(body))
            state_by_sha[digest] = state

        previous = reset_digest
        for receipt in window["pose_receipts"]:
            body = dict(receipt)
            digest = body.pop("pose_receipt_sha256")
            self.assertEqual(digest, canonical_sha256(body))
            self.assertEqual(receipt["previous_pose_receipt_sha256"], previous)
            self.assertIn(receipt["prior_state_sha256"], state_by_sha)
            self.assertIn(receipt["next_state_sha256"], state_by_sha)
            next_state = state_by_sha[receipt["next_state_sha256"]]
            self.assertEqual(
                next_state["parent_state_sha256"], receipt["prior_state_sha256"]
            )
            previous = digest

        prior_decision = None
        for row in window["events"]:
            body = dict(row)
            digest = body.pop("decision_sha256")
            self.assertEqual(digest, canonical_sha256(body))
            self.assertEqual(row["prior_decision_sha256"], prior_decision)
            self.assertIn(row["predictor_state_sha256"], state_by_sha)
            prior_decision = digest

        rows = window["events"]
        pose_receipts = window["pose_receipts"]
        self.assertEqual(
            rows[0]["pose_receipt_chain_sha256"],
            pose_receipts[8]["pose_receipt_sha256"],
        )
        self.assertEqual(
            rows[2]["pose_receipt_chain_sha256"],
            pose_receipts[9]["pose_receipt_sha256"],
        )
        self.assertEqual(rows[0]["state_dependency_pose_ids"], list(range(9)))

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
        manifest = locked_dspb_executable_manifest()
        body = dict(manifest)
        digest = body.pop("manifest_sha256")
        self.assertEqual(manifest["schema"], EXECUTABLE_MANIFEST_SCHEMA)
        self.assertEqual(digest, canonical_sha256(body))
        self.assertEqual(
            locked_dspb_executable_sha256(), digest
        )
        root = Path(dspb_output.__file__).resolve().parents[2]
        roles = set()
        for row in manifest["files"]:
            roles.add(row["role"])
            self.assertEqual(
                row["sha256"], hashlib.sha256((root / row["path"]).read_bytes()).hexdigest()
            )
        self.assertTrue({
            "producer",
            "candidate_model",
            "pose_recovery_implementation",
            "cycle_model_implementation",
            "neutral_projection",
        }.issubset(roles))
        self.assertEqual(
            locked_dspb_config_sha256(),
            hashlib.sha256(locked_dspb_config_bytes()).hexdigest(),
        )
        self.assertEqual(locked_dspb_config_sha256(), DSPBConfig().sha256)

        changed = deepcopy(manifest)
        changed["manifest_sha256"] = "f" * 64
        registries, events, poses = _motion_fixture(1)
        with mock.patch.object(
            dspb_output,
            "locked_dspb_executable_manifest",
            side_effect=(manifest, changed),
        ), self.assertRaisesRegex(DSPBOutputError, "changed during replay"):
            generate_dspb_candidate_output(
                registries, events, poses, ADAPTER_SHA256
            )

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
