from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from benchmarks.redred_cluster2_cav_bridge.cav_adapter import (
    OBSERVATIONAL_SIDECAR_ASSISTED,
    SENSOR_FIXED_FRAME,
    TRANSPORT_TIME_SEMANTICS,
    WORLD_FRAME,
    CAVAdapterError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    project_bridge_bundle_to_cav,
)
from benchmarks.redred_cluster2_cav_bridge.contract import (
    BridgeBundle,
    canonical_event_content_sha256,
    load_bridge_bundle,
)
from benchmarks.redred_mc_wtb_pose_recovery import RecoveryMode
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle
from tests.redred_cluster2_cav_bridge.test_contract import (
    BundleFixture,
    delivered,
    source_event,
)


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


def rehash_projected_event(row):
    timestamp = row.get("timestamp_ns", row.get("occurrence_timestamp_ns"))
    row["event_content_sha256"] = canonical_event_content_sha256(
        row["event_id"],
        timestamp,
        row["polarity"],
        row["is_query"],
        row["sensor_ray"],
        row["causal_pose_source_index"],
        row["transform_guard_valid"],
    )


def set_projected_identity_field(rows, event_id, field, value):
    for view_name in ("RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"):
        for row in rows[view_name]:
            if row["event_id"] == event_id:
                row[field] = value
                if field in {
                    "event_id", "timestamp_ns", "occurrence_timestamp_ns",
                    "polarity", "is_query", "sensor_ray",
                    "causal_pose_source_index", "transform_guard_valid",
                }:
                    rehash_projected_event(row)


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
        self.assertEqual(retired.latency_semantics, TRANSPORT_TIME_SEMANTICS)
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
            [row.latency_ns for row in retired.transport_sidecar], [2, 6, 2]
        )
        self.assertEqual(
            [row.event_timestamp_ns for row in retired.transport_sidecar],
            [1002, 1000, 1003],
        )
        self.assertEqual(
            [row.latency_injected_timestamp_ns for row in retired.transport_sidecar],
            [1004, 1006, 1005],
        )
        self.assertTrue(all(
            row.semantics_label == TRANSPORT_TIME_SEMANTICS
            for row in retired.transport_sidecar
        ))
        self.assertTrue(all(
            not hasattr(row, "occurrence_timestamp_ns")
            and not hasattr(row, "derived_retire_timestamp_ns")
            for row in retired.transport_sidecar
        ))
        # Retire timestamps never replace the shared geometry timestamps.
        self.assertEqual(
            [row.neutral_input.timestamp_ns for row in retired.events],
            [1000, 1002, 1003],
        )
        self.assertEqual(
            retired.transport_sidecar[-1].latency_injected_timestamp_ns,
            1005,
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

    def test_non_grid_timestamp_keeps_physical_and_injected_time_distinct(self):
        sources = (source_event(1, 0, 1001, 4),)
        outcomes = (delivered(1, 4, 1, 2, 0, 0),)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            manifest_path, manifest_sha256 = fixture.write()
            bundle = load_bridge_bundle(manifest_path, manifest_sha256)
            projected_retire = bundle.project()["AER_RET"][0]
            result = project_bridge_bundle_to_cav(
                bundle,
                (NeutralRegistryWindow("synthetic-window", 0, 999, 2000),),
                {"synthetic-window": (pose_input(7, 900),)},
            )

        sidecar = result.view("AER_RET").transport_sidecar[0]
        self.assertEqual(projected_retire["occurrence_timestamp_ns"], 1001)
        self.assertEqual(projected_retire["physical_retire_timestamp_ns"], 1004)
        self.assertEqual(projected_retire["latency_injected_timestamp_ns"], 1003)
        self.assertEqual(projected_retire["latency_cycles"], 1)
        self.assertEqual(projected_retire["latency_ns"], 2)
        self.assertEqual(sidecar.event_timestamp_ns, 1001)
        self.assertEqual(sidecar.latency_injected_timestamp_ns, 1003)
        self.assertEqual(sidecar.latency_cycles, 1)
        self.assertEqual(sidecar.latency_ns, 2)
        self.assertEqual(sidecar.semantics_label, TRANSPORT_TIME_SEMANTICS)
        self.assertFalse(hasattr(sidecar, "physical_retire_timestamp_ns"))


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

    def test_same_cycle_pose_is_strictly_past_and_cannot_be_claimed(self):
        with AdapterFixture() as fixture:
            projection = deepcopy(fixture.bundle.project())
            set_projected_identity_field(
                projection, 13, "causal_pose_source_index", 8
            )
            set_projected_identity_field(
                projection, 14, "causal_pose_source_index", 8
            )
            poses = {
                "synthetic-window": fixture.poses["synthetic-window"] + (
                    pose_input(8, 1000),
                ),
            }
            with patch.object(
                BridgeBundle, "project", return_value=projection
            ):
                result = project_bridge_bundle_to_cav(
                    fixture.bundle, fixture.registry, poses
                )
            self.assertEqual(
                [row.used_pose_ids for row in result.view("AER_OCC").rays],
                [(6, 7), (7, 8), (7, 8)],
            )

            set_projected_identity_field(
                projection, 11, "causal_pose_source_index", 8
            )
            with patch.object(
                BridgeBundle, "project", return_value=projection
            ):
                with self.assertRaises(CAVAdapterError):
                    project_bridge_bundle_to_cav(
                        fixture.bundle, fixture.registry, poses
                    )

    def test_nine_projection_seam_mutations_fail_closed(self):
        def event_row(rows, view_name, event_id):
            return next(
                row for row in rows[view_name]
                if row["event_id"] == event_id
            )

        mutations = (
            lambda rows: rows["RAW4X4_ALL"][0].__setitem__(
                "schema", "not-source-event/v1"
            ),
            lambda rows: rows["RAW4X4_ALL"][0].__setitem__(
                "source_index", 999
            ),
            lambda rows: set_projected_identity_field(
                rows, 11, "source_index", 16
            ),
            lambda rows: (
                event_row(rows, "AER_OCC", 13).__setitem__(
                    "occurrence_cycle", 0
                ),
                event_row(rows, "AER_RET", 13).__setitem__(
                    "occurrence_cycle", 0
                ),
            ),
            lambda rows: rows["AER_RET"][0].__setitem__(
                "retire_native_lane", 2
            ),
            lambda rows: rows["AER_RET"][0].__setitem__("retire_row", 4),
            lambda rows: rows["AER_RET"][0].__setitem__("retire_col", 4),
            lambda rows: rows["AER_RET"][0].__setitem__("retire_row", 0),
            lambda rows: rows["AER_RET"][0].__setitem__(
                "derived_retire_timestamp_ns", 1005
            ),
        )
        self.assertEqual(len(mutations), 9)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_projection_rejected(mutation)

    def test_v2_physical_and_dual_time_mutations_fail_closed(self):
        def last_retire(rows):
            return rows["AER_RET"][-1]

        def replace_physical_with_old_field(rows):
            row = last_retire(rows)
            physical_timestamp = row.pop("physical_retire_timestamp_ns")
            row["derived_retire_timestamp_ns"] = physical_timestamp

        mutations = (
            lambda rows: last_retire(rows).__setitem__(
                "physical_retire_timestamp_ns", 1005
            ),
            lambda rows: last_retire(rows).__setitem__(
                "latency_injected_timestamp_ns", 1006
            ),
            lambda rows: last_retire(rows).__setitem__("latency_cycles", 2),
            lambda rows: last_retire(rows).__setitem__("latency_ns", 3),
            lambda rows: last_retire(rows).__setitem__(
                "transport_time_semantics", "PHYSICAL_REPLAY"
            ),
            replace_physical_with_old_field,
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self._assert_projection_rejected(mutation)

    def test_adapter_is_python38_and_clean_subprocess_loads_no_evaluator(self):
        module_path = (
            Path(__file__).parents[2]
            / "benchmarks/redred_cluster2_cav_bridge/cav_adapter.py"
        )
        ast.parse(
            module_path.read_text(encoding="utf-8"),
            filename=str(module_path),
            feature_version=(3, 8),
        )
        repository = Path(__file__).resolve().parents[2]
        source = """
import json
import sys
import benchmarks.redred_cluster2_cav_bridge.cav_adapter
terms = (\"evaluator\", \"reference\", \"score\", \"selector\")
loaded = sorted(
    name for name in sys.modules
    if name.startswith(\"benchmarks.\")
    and any(term in name.lower() for term in terms)
)
print(json.dumps(loaded))
raise SystemExit(1 if loaded else 0)
"""
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=str(repository),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])


if __name__ == "__main__":
    unittest.main()
