from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import current_cav_trace
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    BASELINE_SCHEMA,
    CurrentCAVTraceError,
    NEUTRAL_INPUT_SCHEMA,
    build_current_cav_trace,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    load_current_cav_trace,
    verify_current_cav_trace,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    PosePacket,
    PoseSource,
    pose_timestamp_to_cycle,
    run_cycle_model,
    timestamp_to_cycle,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE = "benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace"


@dataclass(frozen=True)
class Registry:
    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int


@dataclass(frozen=True)
class NeutralEvent:
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: tuple
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool


@dataclass(frozen=True)
class NeutralPose:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: tuple
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool


@dataclass(frozen=True)
class InjectedProfile:
    profile_id: str
    raw_ingress_lanes: int = 8
    ingress_staging_entries: int = 8
    event_service_lanes: int = 2
    scope: str = "MODEL_ONLY_LOGICAL_REPLAY"

    def to_mapping(self):
        return {
            "schema": "test.ingress_profile/v1",
            "profile_id": self.profile_id,
            "raw_ingress_lanes": self.raw_ingress_lanes,
            "ingress_staging_entries": self.ingress_staging_entries,
            "event_service_lanes": self.event_service_lanes,
            "scope": self.scope,
        }


def pose(
    pose_id: int,
    timestamp_ns: int,
    *,
    value_valid: bool = True,
    arithmetic_valid: bool = True,
) -> NeutralPose:
    quaternion = (0.0, 0.0, 0.0, 1.0)
    return NeutralPose(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
        value_valid,
        arithmetic_valid,
    )


def event(
    event_id: int,
    timestamp_ns: int,
    causal_pose_id: int,
    *,
    transform_guard_valid: bool = True,
) -> NeutralEvent:
    ray = (1.0, 0.0, 0.0)
    is_query = timestamp_ns >= 2_000_000
    polarity = event_id % 2
    digest = canonical_event_content_sha256(
        event_id,
        timestamp_ns,
        polarity,
        is_query,
        ray,
        causal_pose_id,
        transform_guard_valid,
    )
    return NeutralEvent(
        event_id,
        timestamp_ns,
        polarity,
        is_query,
        ray,
        causal_pose_id,
        digest,
        transform_guard_valid,
    )


def fixture():
    registry = (Registry("w0", 0, 2_000_000, 9_000_000),)
    poses = (
        pose(0, 0),
        pose(1, 1_000_000),
        pose(2, 4_000_000, value_valid=False),
        pose(3, 6_000_000),
    )
    events = (
        # The pose committed on this exact edge is not yet visible.
        event(10, 1_000_000, 0),
        event(11, 1_500_000, 1),
        event(12, 1_600_000, 1, transform_guard_valid=False),
        event(13, 4_500_000, 2),
        event(14, 8_000_001, 3),
    )
    return registry, {"w0": events}, {"w0": poses}


def direct_cycle_result(registry, events, poses):
    window = registry[0]
    return run_cycle_model(
        window_id=window.window_id,
        window_start_ns=window.warmup_start_ns_inclusive,
        arm=Arm.CAUSAL_CAV,
        events=tuple(Event(
            row.event_id,
            row.timestamp_ns,
            row.transform_guard_valid,
            row.causal_pose_source_index,
        ) for row in events["w0"]),
        poses=tuple(PosePacket(
            row.pose_id,
            row.timestamp_ns,
            row.commit_cycle,
            PoseSource.DATASET,
            row.pose_sha256,
            row.value_valid,
            row.arithmetic_valid,
        ) for row in poses["w0"]),
    )


def reseal_trace_mapping(value):
    for window in value["windows"]:
        simulation = window["simulation"]
        for decision in simulation["records"]:
            decision["decision_sha256"] = canonical_sha256({
                key: item for key, item in decision.items()
                if key != "decision_sha256"
            })
        simulation["decision_records_sha256"] = canonical_sha256(
            simulation["records"]
        )
        window["window_sha256"] = canonical_sha256({
            key: item for key, item in window.items() if key != "window_sha256"
        })
    neutral = {
        "schema": NEUTRAL_INPUT_SCHEMA,
        "registry": [window["registry"] for window in value["windows"]],
        "windows": [
            {
                "window_id": window["registry"]["window_id"],
                "events": window["input_events"],
                "poses": window["input_poses"],
            }
            for window in value["windows"]
        ],
    }
    value["neutral_input_sha256"] = canonical_sha256(neutral)
    value["baseline_decisions_sha256"] = canonical_sha256({
        "schema": BASELINE_SCHEMA,
        "windows": [
            {
                "window_id": window["registry"]["window_id"],
                "decisions": window["simulation"]["records"],
            }
            for window in value["windows"]
        ],
    })
    value["profile_sha256"] = canonical_sha256(value["profile"])
    value["aggregate_sha256"] = canonical_sha256({
        key: item for key, item in value.items() if key != "aggregate_sha256"
    })
    return value


class CurrentCAVTraceTests(unittest.TestCase):
    def test_clean_import_does_not_load_scoring_or_selector_modules(self):
        script = (
            "import json,sys; import " + MODULE + "; "
            "bad=[m for m in sys.modules if ("
            "'causal_reference' in m or m.endswith('.evaluator') or "
            "m.endswith('.selector') or 'motion_qualification' in m or "
            "m.endswith('.screen108'))]; print(json.dumps(sorted(bad)))"
        )
        completed = subprocess.run(
            [sys.executable, "-S", "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [])

    def test_exact_routes_pose_provenance_and_edges(self):
        registry, events, poses = fixture()
        trace = build_current_cav_trace(registry, events, poses)
        window = trace.windows[0]
        records = window.simulation.records

        self.assertEqual(window.input_events[0].event_id, 10)
        self.assertEqual(window.input_poses[-1].pose_id, 3)
        self.assertEqual(
            [record.disposition_reason for record in records],
            [
                "fresh_zoh_fallback",
                "causal_cav",
                "fresh_zoh_fallback",
                "invalid_pose",
                "stale_pose",
            ],
        )
        self.assertEqual(
            [record.used_pose_ids for record in records],
            [(0,), (0, 1), (1,), (2,), (3,)],
        )
        self.assertEqual(records[0].occurrence_pose_ids, (0,))
        self.assertNotIn(1, records[0].occurrence_pose_ids)
        self.assertEqual(records[3].occurrence_pose_ids, (1, 2))
        self.assertEqual(records[4].occurrence_pose_ids, (2, 3))
        self.assertEqual(
            [record.occurrence_cycle for record in records],
            [timestamp_to_cycle(row.timestamp_ns, 0) for row in events["w0"]],
        )
        self.assertEqual(
            verify_current_cav_trace(trace, registry, events, poses),
            trace.aggregate_sha256,
        )

        serialized = json.dumps(trace.to_mapping(), sort_keys=True)
        for forbidden in (
            "loss", "score", "reference_event", "motion_bin", "selector_label"
        ):
            self.assertNotIn(forbidden, serialized)

    def test_loader_round_trip_is_byte_exact_and_never_runs_cycle_model(self):
        registry, events, poses = fixture()
        trace = build_current_cav_trace(registry, events, poses)
        serialized = json.loads(json.dumps(trace.to_mapping(), sort_keys=True))
        with mock.patch.object(
            current_cav_trace,
            "run_cycle_model",
            side_effect=AssertionError("loader called cycle model"),
        ) as runner:
            loaded = load_current_cav_trace(serialized)
        runner.assert_not_called()
        self.assertEqual(loaded, trace)
        self.assertEqual(
            canonical_json_bytes(loaded.to_mapping()),
            canonical_json_bytes(serialized),
        )

    def test_loader_rejects_fully_resealed_semantic_mutations(self):
        registry, events, poses = fixture()
        original = build_current_cav_trace(registry, events, poses).to_mapping()

        edge = deepcopy(original)
        edge["windows"][0]["simulation"]["records"][0][
            "occurrence_cycle"
        ] += 1

        same_edge_pose = deepcopy(original)
        pose_row = same_edge_pose["windows"][0]["input_poses"][1]
        decision = same_edge_pose["windows"][0]["simulation"]["records"][0]
        decision["occurrence_pose_ids"].append(pose_row["pose_id"])
        decision["occurrence_pose_timestamps_ns"].append(
            pose_row["timestamp_ns"]
        )
        decision["occurrence_pose_commit_cycles"].append(
            pose_row["commit_cycle"]
        )
        decision["occurrence_pose_sha256"].append(pose_row["pose_sha256"])

        route = deepcopy(original)
        route["windows"][0]["simulation"]["records"][1][
            "disposition_reason"
        ] = "fresh_zoh_fallback"

        cases = (
            (edge, "occurrence_cycle"),
            (same_edge_pose, "occurrence_pose_ids"),
            (route, "disposition_reason"),
        )
        for mutated, message in cases:
            with self.subTest(message=message):
                reseal_trace_mapping(mutated)
                with self.assertRaisesRegex(CurrentCAVTraceError, message):
                    load_current_cav_trace(mutated)

    def test_loader_rechecks_content_profile_and_mapping_digests(self):
        registry, events, poses = fixture()
        original = build_current_cav_trace(registry, events, poses).to_mapping()

        event_digest = deepcopy(original)
        event_digest["windows"][0]["input_events"][0][
            "event_content_sha256"
        ] = "0" * 64
        reseal_trace_mapping(event_digest)
        with self.assertRaisesRegex(CurrentCAVTraceError, "event content digest"):
            load_current_cav_trace(event_digest)

        profile = deepcopy(original)
        decoded = json.loads(profile["profile"]["profile_mapping_json"])
        decoded["profile_id"] = "substituted-profile"
        profile["profile"]["profile_mapping_json"] = (
            canonical_json_bytes(decoded).decode("ascii")
        )
        profile["profile"]["profile_mapping_sha256"] = canonical_sha256(decoded)
        reseal_trace_mapping(profile)
        with self.assertRaisesRegex(CurrentCAVTraceError, "profile identity"):
            load_current_cav_trace(profile)

        extra = deepcopy(original)
        extra["windows"][0]["simulation"]["records"][0]["score"] = 0.0
        reseal_trace_mapping(extra)
        with self.assertRaisesRegex(CurrentCAVTraceError, "field schema"):
            load_current_cav_trace(extra)

    def test_semantic_projection_equals_direct_cycle_model(self):
        registry, events, poses = fixture()
        trace = build_current_cav_trace(registry, events, poses)
        direct = direct_cycle_result(registry, events, poses)
        fields = (
            "window_id",
            "event_id",
            "event_timestamp_ns",
            "occurrence_cycle",
            "occurrence_pose_ids",
            "occurrence_pose_timestamps_ns",
            "occurrence_pose_commit_cycles",
            "occurrence_pose_sha256",
            "used_pose_ids",
            "used_pose_timestamps_ns",
            "used_pose_commit_cycles",
            "used_pose_sha256",
            "disposition",
            "disposition_reason",
        )
        for projected, source in zip(trace.windows[0].simulation.records, direct.records):
            self.assertEqual(
                tuple(getattr(projected, field) for field in fields),
                tuple(getattr(source, field) for field in fields),
            )

    def test_injected_profile_and_runner_preserve_baseline_semantics(self):
        registry, events, poses = fixture()
        default = build_current_cav_trace(registry, events, poses)
        calls = []

        def logical_ingress_runner(**kwargs):
            profile = kwargs.pop("ingress_profile")
            calls.append(profile.profile_id)
            return run_cycle_model(**kwargs)

        profile = InjectedProfile("test.logical_ingress/v1")
        logical = build_current_cav_trace(
            registry,
            events,
            poses,
            cycle_runner=logical_ingress_runner,
            cycle_profile=profile,
        )
        self.assertEqual(calls, [profile.profile_id])
        self.assertEqual(logical.neutral_input_sha256, default.neutral_input_sha256)
        self.assertEqual(
            logical.baseline_decisions_sha256,
            default.baseline_decisions_sha256,
        )
        self.assertEqual(logical.windows, default.windows)
        self.assertNotEqual(logical.profile_sha256, default.profile_sha256)
        self.assertNotEqual(logical.aggregate_sha256, default.aggregate_sha256)
        self.assertEqual(
            verify_current_cav_trace(
                logical,
                registry,
                events,
                poses,
                cycle_runner=logical_ingress_runner,
                cycle_profile=profile,
            ),
            logical.aggregate_sha256,
        )

    def test_runner_edge_and_pose_mutations_fail_closed(self):
        registry, events, poses = fixture()
        direct = direct_cycle_result(registry, events, poses)

        def changed_edge(**kwargs):
            del kwargs
            bad = replace(
                direct.records[0],
                occurrence_cycle=direct.records[0].occurrence_cycle + 1,
            )
            return SimpleNamespace(
                records=(bad,) + direct.records[1:],
                synthetic_test_mode=False,
                all_event_pose_indices_verified=True,
            )

        with self.assertRaisesRegex(CurrentCAVTraceError, "occurrence_cycle"):
            build_current_cav_trace(
                registry, events, poses, cycle_runner=changed_edge
            )

        def changed_pose(**kwargs):
            del kwargs
            bad = replace(direct.records[1], used_pose_ids=(1,))
            return SimpleNamespace(
                records=(direct.records[0], bad) + direct.records[2:],
                synthetic_test_mode=False,
                all_event_pose_indices_verified=True,
            )

        with self.assertRaisesRegex(CurrentCAVTraceError, "used_pose_ids"):
            build_current_cav_trace(
                registry, events, poses, cycle_runner=changed_pose
            )

    def test_trace_and_source_mutations_fail_integrity_replay(self):
        registry, events, poses = fixture()
        trace = build_current_cav_trace(registry, events, poses)
        original_window = trace.windows[0]
        original_simulation = original_window.simulation
        changed_decision = replace(
            original_simulation.records[1],
            disposition_reason="fresh_zoh_fallback",
        )
        changed_simulation = replace(
            original_simulation,
            records=(original_simulation.records[0], changed_decision)
            + original_simulation.records[2:],
        )
        changed_window = replace(original_window, simulation=changed_simulation)
        changed_trace = replace(trace, windows=(changed_window,))
        with self.assertRaisesRegex(CurrentCAVTraceError, "differs from replay"):
            verify_current_cav_trace(changed_trace, registry, events, poses)

        changed_source_event = replace(
            events["w0"][4],
            polarity=1 - events["w0"][4].polarity,
        )
        changed_source_event = replace(
            changed_source_event,
            event_content_sha256=canonical_event_content_sha256(
                changed_source_event.event_id,
                changed_source_event.timestamp_ns,
                changed_source_event.polarity,
                changed_source_event.is_query,
                changed_source_event.sensor_ray,
                changed_source_event.causal_pose_source_index,
                changed_source_event.transform_guard_valid,
            ),
        )
        changed_events = dict(events, w0=events["w0"][:4] + (changed_source_event,))
        with self.assertRaisesRegex(CurrentCAVTraceError, "differs from replay"):
            verify_current_cav_trace(trace, registry, changed_events, poses)

    def test_extra_selector_or_label_fields_are_not_representable(self):
        registry, events, poses = fixture()
        leaked = dict(vars(registry[0]), motion_bin="HIGH")
        with self.assertRaisesRegex(CurrentCAVTraceError, "field schema"):
            build_current_cav_trace((leaked,), events, poses)


if __name__ == "__main__":
    unittest.main()
