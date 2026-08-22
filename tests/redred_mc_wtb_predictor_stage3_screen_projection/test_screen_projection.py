from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import math
from types import SimpleNamespace
import unittest

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    dspb_output,
    pll_output,
    rg3_output,
    screen108,
)
from benchmarks.redred_mc_wtb_predictor_stage3.screen_projection import (
    ScreenProjectionError,
    project_native_output,
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
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


ADAPTER_SHA256 = "7" * 64


def _rotation_z(angle):
    return (0.0, 0.0, math.sin(0.5 * angle), math.cos(0.5 * angle))


def _ray(angle):
    return (math.cos(angle), math.sin(angle), 0.0)


def _pose(pose_id, timestamp_ns, start_ns, angle):
    quaternion = _rotation_z(angle)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
    )


def _event(event_id, timestamp_ns, query_ns, angle, pose_id):
    sensor_ray = _ray(angle)
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


def _fixture():
    start = 0
    query = 50_000_000
    window_id = "projection-motion"
    registry = (
        NeutralRegistryWindow(window_id, start, query, query + 500_000),
    )
    angles = tuple(
        math.radians(value) for value in (0, 1, 3, 6, 10, 15, 21, 28, 36, 45)
    )
    poses = {
        window_id: tuple(
            _pose(index, start + index * 5_000_000, start, angle)
            for index, angle in enumerate(angles)
        )
    }
    events = {
        window_id: (
            _event(0, start + 45_000_000, query, 0.00, 8),
            _event(1, start + 45_000_000, query, 0.01, 8),
            _event(2, start + 49_200_000, query, 0.02, 9),
            _event(3, start + 49_800_000, query, 0.03, 9),
            _event(4, query, query, 0.04, 9),
            _event(5, query, query, 0.05, 9),
        )
    }
    baseline = evaluate_current_cav_registry(registry, events, poses)
    bundle = SimpleNamespace(
        neutral_registry=registry,
        event_streams=events,
        pose_streams=poses,
        provenance_seal={"aggregate_sha256": ADAPTER_SHA256},
    )
    return bundle, baseline


def _fallback_fixture():
    start = 0
    query = 50_000_000
    window_id = "projection-fallback"
    registry = (
        NeutralRegistryWindow(window_id, start, query, query + 500_000),
    )
    poses = {window_id: (
        _pose(0, query - 1_000_000, start, 0.0),
        _pose(1, query - 500_000, start, 0.1),
    )}
    events = {window_id: (
        _event(0, query - 200_000, query, 0.00, 1),
        _event(1, query - 100_000, query, 0.01, 1),
        _event(2, query, query, 0.02, 1),
    )}
    baseline = evaluate_current_cav_registry(registry, events, poses)
    bundle = SimpleNamespace(
        neutral_registry=registry,
        event_streams=events,
        pose_streams=poses,
        provenance_seal={"aggregate_sha256": ADAPTER_SHA256},
    )
    return bundle, baseline


def _native_outputs(bundle, baseline):
    return {
        "RG3": rg3_output.generate_locked_rg3_output(
            bundle.neutral_registry,
            bundle.event_streams,
            bundle.pose_streams,
            ADAPTER_SHA256,
        ),
        "DSPB": dspb_output.generate_dspb_candidate_output(
            bundle.neutral_registry,
            bundle.event_streams,
            bundle.pose_streams,
            ADAPTER_SHA256,
        ),
        "PLL": pll_output.generate_locked_pll_output(bundle, baseline),
    }


class ScreenProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle, cls.baseline = _fixture()
        cls.native = _native_outputs(cls.bundle, cls.baseline)

    def test_all_native_candidates_project_and_pass_independent_screen_validator(self):
        for name, native in self.native.items():
            with self.subTest(candidate=name):
                projection = project_native_output(native)
                executable_sha = hashlib.sha256(
                    projection.executable_artifact_bytes
                ).hexdigest()
                config_sha = hashlib.sha256(projection.config_bytes).hexdigest()
                self.assertEqual(
                    projection.screen_output["candidate_executable_sha256"],
                    executable_sha,
                )
                self.assertEqual(
                    projection.screen_output["candidate_config_sha256"],
                    config_sha,
                )
                candidate_id, checked = screen108._validate_candidate_output(
                    projection.screen_output,
                    self.bundle,
                    self.baseline,
                    executable_sha,
                    config_sha,
                )
                self.assertEqual(candidate_id, native["candidate_id"])
                self.assertEqual(
                    sum(len(events) for events in checked.values()),
                    len(self.bundle.event_streams["projection-motion"]),
                )
                for window in projection.screen_output["windows"]:
                    for event in window["events"]:
                        self.assertEqual(event["route"], event["route"].lower())
                        if not event["candidate_used"]:
                            self.assertEqual(event["model_id"], "CURRENT_CAV")
                            self.assertIsNone(event["world_ray"])

    def test_artifact_and_config_bytes_are_exact_native_authorities(self):
        rg3 = project_native_output(self.native["RG3"])
        self.assertEqual(
            rg3.executable_artifact_bytes,
            rg3_output.RG3_EXECUTABLE_MANIFEST_BYTES,
        )
        self.assertEqual(rg3.config_bytes, rg3_output.RG3_CONFIG_BYTES)

        dspb = project_native_output(self.native["DSPB"])
        dspb_manifest = dict(dspb_output.locked_dspb_executable_manifest())
        del dspb_manifest["manifest_sha256"]
        self.assertEqual(
            dspb.executable_artifact_bytes,
            canonical_json_bytes(dspb_manifest),
        )
        self.assertEqual(dspb.config_bytes, dspb_output.locked_dspb_config_bytes())

        pll = project_native_output(self.native["PLL"])
        self.assertEqual(
            pll.executable_artifact_bytes,
            canonical_json_bytes(pll_output.executable_dependency_manifest()),
        )
        self.assertEqual(pll.config_bytes, pll_output.locked_config_bytes())

    def test_fallback_geometry_is_dropped_and_screen_model_semantics_are_exact(self):
        bundle, baseline = _fallback_fixture()
        outputs = _native_outputs(bundle, baseline)
        self.assertTrue(any(
            event["world_ray"] is not None and not event["candidate_used"]
            for event in outputs["DSPB"]["windows"][0]["events"]
        ))
        for name, native in outputs.items():
            with self.subTest(candidate=name):
                projection = project_native_output(native)
                executable_sha = hashlib.sha256(
                    projection.executable_artifact_bytes
                ).hexdigest()
                config_sha = hashlib.sha256(projection.config_bytes).hexdigest()
                screen108._validate_candidate_output(
                    projection.screen_output,
                    bundle,
                    baseline,
                    executable_sha,
                    config_sha,
                )
                for event in projection.screen_output["windows"][0]["events"]:
                    self.assertFalse(event["candidate_used"])
                    self.assertEqual(event["model_id"], "CURRENT_CAV")
                    self.assertIsNone(event["world_ray"])

    def test_projection_receipt_binds_source_projection_and_per_window_events(self):
        for name, native in self.native.items():
            with self.subTest(candidate=name):
                projection = project_native_output(native)
                receipt = dict(projection.projection_receipt)
                supplied = receipt.pop("projection_receipt_sha256")
                self.assertEqual(supplied, canonical_sha256(receipt))
                self.assertEqual(
                    receipt["native_aggregate_sha256"], native["aggregate_sha256"]
                )
                self.assertEqual(
                    receipt["projected_aggregate_sha256"],
                    projection.screen_output["aggregate_sha256"],
                )
                for window in projection.projection_receipt["windows"]:
                    body = dict(window)
                    window_sha = body.pop("window_projection_sha256")
                    self.assertEqual(window_sha, canonical_sha256(body))
                    self.assertEqual(
                        window["event_bindings_sha256"],
                        canonical_sha256(window["event_bindings"]),
                    )

    def test_result_attributes_are_frozen(self):
        projection = project_native_output(self.native["RG3"])
        with self.assertRaises(FrozenInstanceError):
            projection.config_bytes = b"changed"

    def test_mutated_ray_route_pose_or_seal_fails_closed(self):
        mutations = ("ray", "route", "pose", "seal")
        for candidate, original in self.native.items():
            for mutation in mutations:
                with self.subTest(candidate=candidate, mutation=mutation):
                    changed = deepcopy(original)
                    events = changed["windows"][0]["events"]
                    event = next(
                        row for row in events
                        if row["candidate_used"] and row["world_ray"] is not None
                    )
                    if mutation == "ray":
                        event["world_ray"][0] += 0.01
                    elif mutation == "route":
                        event["route"] = "CURRENT_CAV"
                    elif mutation == "pose":
                        event["used_pose_ids"].append(999)
                    else:
                        event["decision_sha256"] = "0" * 64
                    with self.assertRaises(ScreenProjectionError):
                        project_native_output(changed)


if __name__ == "__main__":
    unittest.main()
