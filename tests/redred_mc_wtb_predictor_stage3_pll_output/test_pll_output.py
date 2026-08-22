from __future__ import annotations

from copy import deepcopy
import math
from types import SimpleNamespace
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
    CANDIDATE_ID,
    LOCKED_PLL_CONFIG,
    PLLOutputError,
    ROUTE_CANDIDATE,
    ROUTE_CURRENT_CAV,
    ROUTE_FRESH_ZOH,
    ROUTE_SENSOR_FIXED,
    executable_dependency_manifest,
    generate_locked_pll_output,
    generator_executable_sha256,
    locked_config_sha256,
    verify_locked_pll_output,
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
    def __init__(self, registry, events, poses, aggregate="a" * 64):
        self.neutral_registry = registry
        self.event_streams = events
        self.pose_streams = poses
        self.provenance_seal = {"aggregate_sha256": aggregate}

    @property
    def selector_labels(self):
        raise AssertionError("PLL output producer read selector labels")


def _candidate_fixture(window_count=1, pre_roll_ns=50_000_000):
    registry = []
    event_streams = {}
    pose_streams = {}
    for index in range(window_count):
        start = index * 100_000_000
        query = start + pre_roll_ns
        end = query + 2_000
        window_id = "pll-candidate-%d" % index
        pose_base = 10 * index
        event_base = 100 * index
        registry.append(NeutralRegistryWindow(window_id, start, query, end))
        pose_streams[window_id] = tuple(
            _pose(
                pose_base + offset,
                query - (3 - offset) * 1_000_000,
                start,
                0.01 * offset,
            )
            for offset in range(4)
        )
        event_streams[window_id] = (
            _event(event_base, query - 500_000, False, 0.0, pose_base + 2),
            _event(event_base + 1, query, True, 0.1, pose_base + 2),
            _event(event_base + 2, query + 1_000, True, 0.2, pose_base + 3),
        )
    baseline = evaluate_current_cav_registry(
        tuple(registry), event_streams, pose_streams
    )
    return _LabelTrapBundle(tuple(registry), event_streams, pose_streams), baseline


def _fallback_fixture():
    start = 0
    query = 50_000_000
    end = 52_000_001
    registry = (NeutralRegistryWindow("pll-fallback", start, query, end),)
    poses = {"pll-fallback": (
        _pose(0, 49_200_000, start, 0.0),
        _pose(1, 49_300_000, start, 0.001),
        _pose(2, 49_400_000, start, 0.002),
    )}
    events = {"pll-fallback": (
        _event(0, 49_450_000, False, 0.0, 2),
        _event(1, 50_000_000, True, 0.1, 2),
        _event(2, 52_000_000, True, 0.2, 2),
    )}
    baseline = evaluate_current_cav_registry(registry, events, poses)
    return _LabelTrapBundle(registry, events, poses), baseline


def _cav_unlocked_fixture():
    start = 0
    query = 50_000_000
    end = query + 2_000
    registry = (NeutralRegistryWindow("pll-cav", start, query, end),)
    poses = {"pll-cav": (
        _pose(0, 48_000_000, start, 0.0),
        _pose(1, 49_500_000, start, 0.1),
    )}
    events = {"pll-cav": (
        _event(0, 49_750_000, False, 0.0, 1),
        _event(1, query, True, 0.1, 1),
        _event(2, query + 1_000, True, 0.2, 1),
    )}
    baseline = evaluate_current_cav_registry(registry, events, poses)
    return _LabelTrapBundle(registry, events, poses), baseline


class LockedPLLOutputTests(unittest.TestCase):
    def test_native_parameter_complete_identity_and_dependency_closure(self):
        self.assertEqual(CANDIDATE_ID, LOCKED_PLL_CONFIG.candidate_id)
        self.assertIn(":", CANDIDATE_ID)
        manifest = executable_dependency_manifest()
        paths = {row["path"] for row in manifest["files"]}
        self.assertIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py", paths
        )
        self.assertIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py", paths
        )
        self.assertIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/framework.py", paths
        )
        self.assertIn(
            "benchmarks/redred_mc_wtb_pose_recovery/geometry.py", paths
        )
        self.assertEqual(generator_executable_sha256(), canonical_sha256(manifest))

    def test_candidate_rows_bind_edges_attempt_state_and_more_than_two_poses(self):
        bundle, baseline = _candidate_fixture()
        output = generate_locked_pll_output(bundle, baseline)
        self.assertEqual(output["candidate_id"], LOCKED_PLL_CONFIG.candidate_id)
        window = output["windows"][0]
        rows = window["events"]
        for row in rows:
            self.assertEqual(row["occurrence_cycle"], row["decision_cycle"] - 1)
            self.assertTrue(row["candidate_attempted"])
            self.assertEqual(row["route"], ROUTE_CANDIDATE)
            self.assertTrue(row["candidate_used"])
            self.assertIsNone(row["fallback_reason"])
            self.assertEqual(row["model_id"], CANDIDATE_ID)
            self.assertEqual(row["configuration_sha256"], locked_config_sha256())
            self.assertEqual(
                row["decision_sha256"],
                canonical_sha256({
                    key: value for key, value in row.items()
                    if key != "decision_sha256"
                }),
            )
        same_edge, future = rows[1:]
        self.assertEqual(same_edge["used_pose_ids"], [0, 1, 2])
        self.assertNotIn(3, same_edge["used_pose_ids"])
        self.assertEqual(future["used_pose_ids"], [0, 1, 2, 3])
        self.assertGreater(len(future["used_pose_ids"]), 2)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in future["world_ray"])),
            1.0,
            places=12,
        )
        self.assertEqual(window["events_sha256"], canonical_sha256(rows))
        self.assertEqual(
            window["baseline_decisions_sha256"],
            canonical_sha256([
                row.to_mapping()
                for row in baseline.windows[0].simulation.records
            ]),
        )
        unsigned_window = dict(window)
        supplied_window_sha = unsigned_window.pop("window_sha256")
        self.assertEqual(supplied_window_sha, canonical_sha256(unsigned_window))
        unsigned_output = dict(output)
        supplied_output_sha = unsigned_output.pop("aggregate_sha256")
        self.assertEqual(supplied_output_sha, canonical_sha256(unsigned_output))

    def test_exact_cav_fallback_preserves_route_pose_ids_and_reason(self):
        bundle, baseline = _cav_unlocked_fixture()
        output = generate_locked_pll_output(bundle, baseline)
        rows = output["windows"][0]["events"]
        for row, native in zip(rows, baseline.windows[0].simulation.records):
            self.assertTrue(row["candidate_attempted"])
            self.assertFalse(row["candidate_used"])
            self.assertEqual(row["route"], ROUTE_CURRENT_CAV)
            self.assertEqual(row["used_pose_ids"], list(native.used_pose_ids))
            self.assertEqual(row["fallback_reason"], native.disposition_reason)
            self.assertIn("pll_unlocked", row["candidate_failure_reason"])

    def test_zoh_and_sensor_routes_are_not_candidate_attempts(self):
        bundle, baseline = _fallback_fixture()
        rows = generate_locked_pll_output(bundle, baseline)["windows"][0]["events"]
        native = baseline.windows[0].simulation.records
        self.assertEqual([row["route"] for row in rows], [
            ROUTE_CANDIDATE,
            ROUTE_FRESH_ZOH,
            ROUTE_SENSOR_FIXED,
        ])
        self.assertTrue(rows[0]["candidate_attempted"])
        self.assertTrue(rows[0]["candidate_used"])
        for row, baseline_row in zip(rows[1:], native[1:]):
            self.assertFalse(row["candidate_attempted"])
            self.assertFalse(row["candidate_used"])
            self.assertEqual(row["used_pose_ids"], list(baseline_row.used_pose_ids))
            self.assertEqual(row["fallback_reason"], baseline_row.disposition_reason)
            self.assertIsNone(row["candidate_failure_reason"])
            self.assertIsNone(row["world_ray"])

    def test_reset_and_state_transition_chain_is_sealed_and_future_only(self):
        bundle, baseline = _candidate_fixture(window_count=2)
        output = generate_locked_pll_output(bundle, baseline)
        for generation, window in enumerate(output["windows"]):
            reset = window["reset"]
            self.assertEqual(reset["reset_generation"], generation)
            self.assertEqual(reset["reset_cycle"], 0)
            self.assertEqual(reset["window_id"], window["window_id"])
            self.assertEqual(
                reset["warmup_start_ns"],
                bundle.neutral_registry[generation].warmup_start_ns_inclusive,
            )
            self.assertIsNone(reset["prior_window_state_sha256"])
            self.assertEqual(
                reset["initial_state_sha256"],
                canonical_sha256(reset["initial_state"]),
            )
            parent = reset["initial_state_sha256"]
            state_hashes = {parent}
            for transition in window["state_transitions"]:
                self.assertEqual(transition["parent_state_sha256"], parent)
                if transition["accepted"]:
                    published = transition["published_state"]
                    unsigned_state = dict(published)
                    supplied_state_sha = unsigned_state.pop("state_sha256")
                    self.assertEqual(
                        supplied_state_sha, canonical_sha256(unsigned_state)
                    )
                    self.assertEqual(
                        transition["published_state_sha256"], supplied_state_sha
                    )
                    self.assertEqual(
                        transition["effective_cycle"],
                        transition["commit_cycle"] + 1,
                    )
                    self.assertEqual(
                        transition["publication_cycle"],
                        transition["effective_cycle"],
                    )
                    parent = transition["published_state_sha256"]
                    state_hashes.add(parent)
            self.assertEqual(
                window["state_transitions_sha256"],
                canonical_sha256(window["state_transitions"]),
            )
            for row in window["events"]:
                self.assertEqual(row["reset_generation"], generation)
                self.assertIn(row["state_sha256"], state_hashes)
                self.assertLessEqual(row["state_effective_cycle"], row["decision_cycle"])
                self.assertEqual(
                    row["state_publication_cycle"], row["state_effective_cycle"]
                )

    def test_exact_replay_verifier_rejects_state_route_and_dependency_mutations(self):
        bundle, baseline = _candidate_fixture()
        output = generate_locked_pll_output(bundle, baseline)
        self.assertEqual(
            verify_locked_pll_output(output, bundle, baseline),
            output["aggregate_sha256"],
        )
        mutations = []
        changed = deepcopy(output)
        changed["windows"][0]["events"][0]["route"] = ROUTE_CURRENT_CAV
        mutations.append(changed)
        changed = deepcopy(output)
        changed["windows"][0]["events"][0]["state_sha256"] = "0" * 64
        mutations.append(changed)
        changed = deepcopy(output)
        changed["candidate_executable_dependencies"]["files"][0]["sha256"] = "0" * 64
        mutations.append(changed)
        for changed in mutations:
            with self.subTest(), self.assertRaisesRegex(
                PLLOutputError, "exact native replay"
            ):
                verify_locked_pll_output(changed, bundle, baseline)

    def test_invalid_pose_has_no_publication_and_cannot_enter_dependencies(self):
        bundle, baseline = _candidate_fixture()
        window_id = bundle.neutral_registry[0].window_id
        poses = list(bundle.pose_streams[window_id])
        poses[3] = _pose(3, 50_000_000, 0, 0.3, valid=False)
        pose_streams = {window_id: tuple(poses)}
        baseline = evaluate_current_cav_registry(
            bundle.neutral_registry, bundle.event_streams, pose_streams
        )
        changed = _LabelTrapBundle(
            bundle.neutral_registry, bundle.event_streams, pose_streams
        )
        output = generate_locked_pll_output(changed, baseline)
        transition = output["windows"][0]["state_transitions"][-1]
        self.assertFalse(transition["accepted"])
        self.assertIsNone(transition["published_state"])
        self.assertIsNone(transition["published_state_sha256"])
        self.assertIsNone(transition["effective_cycle"])
        self.assertNotIn(3, transition["dependency_pose_ids"])
        last = output["windows"][0]["events"][-1]
        self.assertEqual(last["route"], ROUTE_SENSOR_FIXED)
        self.assertEqual(last["used_pose_ids"], [3])
        self.assertNotIn(3, last["state_dependency_pose_ids"])

    def test_pre_roll_and_neutral_binding_fail_closed(self):
        bundle, baseline = _candidate_fixture()
        short_bundle, short_baseline = _candidate_fixture(pre_roll_ns=49_000_000)
        with self.assertRaisesRegex(PLLOutputError, "locked 50 ms pre-roll"):
            generate_locked_pll_output(short_bundle, short_baseline)
        changed_events = dict(bundle.event_streams)
        key = bundle.neutral_registry[0].window_id
        changed_events[key] = changed_events[key][:-1]
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
