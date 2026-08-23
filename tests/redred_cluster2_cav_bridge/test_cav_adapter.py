from __future__ import annotations

import ast
from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.redred_cluster2_cav_bridge.cav_adapter import (
    OBSERVATIONAL_SIDECAR_ASSISTED,
    SENSOR_FIXED_FRAME,
    TRANSPORT_LATENCY_SEPARATE,
    WORLD_FRAME,
    CAVAdapterError,
    project_bridge_bundle_to_cav,
)
from benchmarks.redred_cluster2_cav_bridge.contract import (
    BridgeBundle,
    load_bridge_bundle,
)
from benchmarks.redred_mc_wtb_pose_recovery import RecoveryMode
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle
from tests.redred_cluster2_cav_bridge.test_contract import BundleFixture


def pose_input(
    pose_id, timestamp_ns, value_valid=True, arithmetic_valid=True,
    quaternion=None,
):
    if quaternion is None:
        quaternion = (0.0, 0.0, 0.0, 1.0)
    digest = canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion)
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        digest,
        value_valid,
        arithmetic_valid,
    )


class AdapterFixture:
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory()
        fixture = BundleFixture(Path(self.temporary.name))
        manifest_path, manifest_sha256 = fixture.write()
        self.bundle = load_bridge_bundle(manifest_path, manifest_sha256)
        self.registry = (
            NeutralRegistryWindow("synthetic-window", 0, 999, 2000),
        )
        self.poses = {
            "synthetic-window": (
                pose_input(
                    6, 500,
                    quaternion=(0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)),
                ),
                pose_input(
                    7, 900,
                    quaternion=(0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)),
                ),
            ),
        }
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.temporary.cleanup()


class CommonNeutralProjectionTests(unittest.TestCase):
    def test_three_views_share_occurrence_time_source_order_and_one_cav_path(self):
        with AdapterFixture() as fixture:
            result = project_bridge_bundle_to_cav(
                fixture.bundle, fixture.registry, fixture.poses
            )

        raw = result.view("RAW4X4_MATCHED")
        occurrence = result.view("AER_OCC")
        retired = result.view("AER_RET")
        self.assertIs(raw.events, occurrence.events)
        self.assertIs(raw.events, retired.events)
        self.assertIs(raw.rays, occurrence.rays)
        self.assertIs(raw.rays, retired.rays)
        self.assertEqual(
            [row.neutral_input.event_id for row in raw.events], [11, 13, 14]
        )
        self.assertEqual([row.source_ordinal for row in raw.events], [0, 2, 3])
        self.assertEqual(
            [row.neutral_input.timestamp_ns for row in raw.events],
            [1000, 1002, 1003],
        )
        self.assertTrue(all(
            type(row.neutral_input) is NeutralEventInput for row in raw.events
        ))
        self.assertTrue(all(
            row.input_coordinate_frame == SENSOR_FIXED_FRAME
            for row in result.views
        ))
        self.assertTrue(all(
            ray.coordinate_frame == WORLD_FRAME
            and ray.recovery_mode is RecoveryMode.CAV
            for ray in raw.rays
        ))
        for ray in raw.rays:
            self.assertAlmostEqual(ray.ray_xyz[0], 0.0, places=12)
            self.assertAlmostEqual(ray.ray_xyz[1], 0.6, places=12)
            self.assertAlmostEqual(ray.ray_xyz[2], 0.8, places=12)

    def test_retire_order_and_latency_are_observational_sidecar_only(self):
        with AdapterFixture() as fixture:
            result = project_bridge_bundle_to_cav(
                fixture.bundle, fixture.registry, fixture.poses
            )

        raw = result.view("RAW4X4_MATCHED")
        occurrence = result.view("AER_OCC")
        retired = result.view("AER_RET")
        self.assertEqual(raw.transport_sidecar, ())
        self.assertEqual(occurrence.transport_sidecar, ())
        self.assertIsNone(raw.measurement_class)
        self.assertIsNone(occurrence.latency_semantics)
        self.assertEqual(
            retired.measurement_class, OBSERVATIONAL_SIDECAR_ASSISTED
        )
        self.assertEqual(retired.latency_semantics, TRANSPORT_LATENCY_SEPARATE)
        self.assertEqual(
            [row.event_id for row in retired.transport_sidecar], [13, 11, 14]
        )
        self.assertEqual(
            [row.source_ordinal for row in retired.transport_sidecar], [2, 0, 3]
        )
        self.assertEqual(
            [row.retire_ordinal for row in retired.transport_sidecar], [0, 1, 2]
        )
        self.assertEqual(
            [row.latency_cycles for row in retired.transport_sidecar], [1, 3, 1]
        )
        self.assertEqual(
            [row.latency_ns for row in retired.transport_sidecar], [2, 6, 3]
        )
        self.assertEqual(
            [row.occurrence_timestamp_ns for row in retired.transport_sidecar],
            [1002, 1000, 1003],
        )
        # Retire timestamps never replace the shared geometry timestamps.
        self.assertEqual(
            [row.neutral_input.timestamp_ns for row in retired.events],
            [1000, 1002, 1003],
        )
        self.assertNotEqual(
            [row.derived_retire_timestamp_ns for row in retired.transport_sidecar],
            [row.neutral_input.timestamp_ns for row in retired.events],
        )

    def test_raw_bypass_is_never_labeled_as_world_coordinates(self):
        with AdapterFixture() as fixture:
            invalid_poses = {
                "synthetic-window": (
                    fixture.poses["synthetic-window"][0],
                    pose_input(7, 900, value_valid=False),
                ),
            }
            result = project_bridge_bundle_to_cav(
                fixture.bundle, fixture.registry, invalid_poses
            )

        raw = result.view("RAW4X4_MATCHED")
        self.assertTrue(all(
            ray.recovery_mode is RecoveryMode.BYPASS
            and ray.coordinate_frame == SENSOR_FIXED_FRAME
            for ray in raw.rays
        ))
        self.assertEqual(
            [ray.ray_xyz for ray in raw.rays],
            [row.neutral_input.sensor_ray for row in raw.events],
        )


class FailClosedAdapterTests(unittest.TestCase):
    def _assert_projection_rejected(self, mutate):
        with AdapterFixture() as fixture:
            projection = deepcopy(fixture.bundle.project())
            mutate(projection)
            with patch.object(BridgeBundle, "project", return_value=projection):
                with self.assertRaises(CAVAdapterError):
                    project_bridge_bundle_to_cav(
                        fixture.bundle, fixture.registry, fixture.poses
                    )

    def test_population_order_and_identity_mismatches_fail_closed(self):
        mutations = (
            lambda rows: rows["AER_RET"].pop(),
            lambda rows: rows["AER_OCC"].reverse(),
            lambda rows: rows["AER_RET"][0].__setitem__(
                "occurrence_timestamp_ns", 1001
            ),
            lambda rows: rows["AER_RET"][0].__setitem__(
                "causal_pose_source_index", 6
            ),
            lambda rows: rows["AER_RET"][0].__setitem__(
                "projection_semantics", "AER_PAYLOAD"
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self._assert_projection_rejected(mutate)

    def test_pose_identity_and_registry_semantics_fail_closed(self):
        with AdapterFixture() as fixture:
            wrong_latest_pose = {
                "synthetic-window": (pose_input(6, 500),),
            }
            with self.assertRaises(CAVAdapterError):
                project_bridge_bundle_to_cav(
                    fixture.bundle, fixture.registry, wrong_latest_pose
                )

            wrong_bounds = (
                NeutralRegistryWindow("synthetic-window", 0, 1001, 2000),
            )
            with self.assertRaises(CAVAdapterError):
                project_bridge_bundle_to_cav(
                    fixture.bundle, wrong_bounds, fixture.poses
                )

    def test_adapter_has_no_scorer_selector_or_evaluator_call(self):
        module_path = (
            Path(__file__).parents[2]
            / "benchmarks/redred_cluster2_cav_bridge/cav_adapter.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name):
                    called.add(function.id)
                elif isinstance(function, ast.Attribute):
                    called.add(function.attr)
        forbidden = {
            "evaluate_current_cav_registry",
            "evaluate_current_cav_registry_bounded",
            "CausalReferenceBank",
            "load_stage3_label_authority",
            "select_candidate",
            "score_candidate",
        }
        self.assertFalse(forbidden & imported)
        self.assertFalse(forbidden & called)


if __name__ == "__main__":
    unittest.main()
