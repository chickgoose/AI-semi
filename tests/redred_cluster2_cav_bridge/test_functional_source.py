from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge import functional_source as module
from benchmarks.redred_cluster2_cav_bridge.cav_adapter import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from benchmarks.redred_cluster2_cav_bridge.source_crosswalk import (
    SourceCrosswalkError,
    SourceCrosswalkEvent,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import (
    OFFICIAL_SOURCE_PINS,
    SourceInputError,
    ValidatedSources,
)


CALIBRATION = b"100 100 120 90 0 0 0 0 0\n"


def pose_line(timestamp_ns, pose_id):
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return ("%d.%09d 0 0 0 0 0 0 1\n" % (seconds, nanos)).encode("ascii")


class FunctionalFixture:
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mask = self.root / "trace.cyclemask"
        self.events_path = self.root / "events.txt"
        self.pose_path = self.root / "groundtruth.txt"
        self.calibration_path = self.root / "calib.txt"
        self.events_path.write_bytes(b"fixture-events\n")
        self.mask.write_bytes(b"fixture-mask\n")
        self.calibration_path.write_bytes(CALIBRATION)
        self.pose_times = (999_000_000, 1_000_000_000, 1_000_000_001)
        self.pose_path.write_bytes(b"".join(
            pose_line(timestamp, pose_id)
            for pose_id, timestamp in enumerate(self.pose_times)
        ))
        self.crosswalk = (
            SourceCrosswalkEvent(0, 1_000_000_001, 111, 85, 1, 1, 1000),
            SourceCrosswalkEvent(1, 1_001_500_000, 110, 85, 0, 0, 1001),
            SourceCrosswalkEvent(2, 1_001_000_001, 113, 88, 0, 15, 1001),
        )
        pins = OFFICIAL_SOURCE_PINS
        self.sources = ValidatedSources(
            self.events_path,
            self.pose_path,
            self.calibration_path,
            CALIBRATION,
            pins.calibration_sha256,
            pins,
        )
        self.patches = (
            mock.patch.object(module, "validate_sources", return_value=self.sources),
            mock.patch.object(
                module,
                "derive_official_uzh_source_crosswalk_files",
                return_value=self.crosswalk,
            ),
            mock.patch.object(module, "EXPECTED_EVENT_COUNT", len(self.crosswalk)),
            mock.patch.object(module, "EXPECTED_POSE_COUNT", len(self.pose_times)),
            mock.patch.object(
                module,
                "_stream_sha256",
                return_value=OFFICIAL_SOURCE_PINS.groundtruth_sha256,
            ),
        )
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def build(self):
        return module.build_official_uzh_functional_source(self.root, self.mask)


class FunctionalSourceFixtureTests(unittest.TestCase):
    def test_returns_adapter_local_types_in_cav_timestamp_order(self):
        with FunctionalFixture() as fixture:
            bundle = fixture.build()
        self.assertIs(type(bundle.registry), NeutralRegistryWindow)
        self.assertTrue(all(type(event) is NeutralEventInput for event in bundle.events))
        self.assertTrue(all(type(pose) is NeutralPoseInput for pose in bundle.poses))
        self.assertEqual([event.event_id for event in bundle.events], [0, 2, 1])
        self.assertEqual(
            [(row.event_id, row.native_occurrence_cycle) for row in bundle.native_identities],
            [(0, 1000), (2, 1001), (1, 1001)],
        )
        self.assertTrue(all(event.is_query for event in bundle.events))
        self.assertEqual(bundle.registry.warmup_start_ns_inclusive, 1_000_000_000)
        self.assertEqual(bundle.registry.query_start_ns_inclusive, 1_000_000_001)
        self.assertEqual(bundle.registry.query_end_ns_exclusive, 1_002_000_000)
        self.assertEqual(bundle.registry_rows, (bundle.registry,))
        self.assertIs(bundle.event_streams[bundle.registry.window_id], bundle.events)
        self.assertIs(bundle.pose_streams[bundle.registry.window_id], bundle.poses)

    def test_same_edge_pose_is_excluded_and_pre_roll_is_explicit(self):
        with FunctionalFixture() as fixture:
            bundle = fixture.build()
        first = bundle.events[0]
        self.assertEqual(first.causal_pose_source_index, 1)
        self.assertNotEqual(first.causal_pose_source_index, 2)
        self.assertLess(
            bundle.poses[first.causal_pose_source_index].commit_cycle,
            1,
        )
        self.assertEqual(bundle.required_pose_start_id, 0)
        self.assertEqual(bundle.required_pose_pre_roll_ns, 1_000_000)
        self.assertEqual(bundle.required_pose_end_id, 2)

    def test_sensor_ray_and_content_digest_are_source_bound(self):
        with FunctionalFixture() as fixture:
            bundle = fixture.build()
        rays = [event.sensor_ray for event in bundle.events]
        self.assertEqual(len(set(rays)), 3)
        for event in bundle.events:
            self.assertAlmostEqual(sum(value * value for value in event.sensor_ray), 1.0)
            self.assertTrue(event.transform_guard_valid)
            self.assertRegex(event.event_content_sha256, r"^[0-9a-f]{64}$")

    def test_missing_hash_collision_ordering_and_causality_fail_closed(self):
        with FunctionalFixture() as fixture:
            with mock.patch.object(
                module, "validate_sources", side_effect=SourceInputError("hash mismatch")
            ):
                with self.assertRaisesRegex(module.FunctionalSourceError, "validation failed"):
                    fixture.build()
            with mock.patch.object(
                module,
                "derive_official_uzh_source_crosswalk_files",
                side_effect=SourceCrosswalkError("collide"),
            ):
                with self.assertRaisesRegex(module.FunctionalSourceError, "crosswalk failed"):
                    fixture.build()

            duplicate = fixture.crosswalk[:-1] + (
                SourceCrosswalkEvent(1, 1_001_000_001, 113, 88, 0, 15, 1001),
            )
            with mock.patch.object(
                module, "derive_official_uzh_source_crosswalk_files", return_value=duplicate
            ):
                with self.assertRaisesRegex(module.FunctionalSourceError, "event IDs"):
                    fixture.build()

            fixture.pose_times = (1_000_000_001, 1_000_000_002, 1_000_000_003)
            fixture.pose_path.write_bytes(b"".join(
                pose_line(timestamp, pose_id)
                for pose_id, timestamp in enumerate(fixture.pose_times)
            ))
            with self.assertRaisesRegex(module.FunctionalSourceError, "no strictly pre-edge"):
                fixture.build()

    def test_module_has_no_score_or_evaluator_import(self):
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 8))
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("score" in name or "evaluator" in name for name in imported))
        clean = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import sys; "
                    "import benchmarks.redred_cluster2_cav_bridge.functional_source; "
                    "bad=[name for name in sys.modules "
                    "if 'score' in name or 'evaluator' in name]; "
                    "assert not bad, bad"
                ),
            ],
            cwd=path.parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)


@unittest.skipUnless(
    os.environ.get("REDRED_RUN_CLUSTER2_FUNCTIONAL_SOURCE_OFFICIAL") == "1",
    "set REDRED_RUN_CLUSTER2_FUNCTIONAL_SOURCE_OFFICIAL=1 for the 509 MB smoke",
)
class OfficialFunctionalSourceSmoke(unittest.TestCase):
    def test_official_8503_population_and_8420_cav_horizon(self):
        dataset = os.environ.get("REDRED_UZH_SHAPES_ROTATION_ROOT")
        cyclemask = os.environ.get("REDRED_CLUSTER2_CYCLEMASK_PATH")
        if not dataset or not cyclemask:
            self.fail("official dataset and cyclemask environment paths are required")
        bundle = module.build_official_uzh_functional_source(
            Path(dataset), Path(cyclemask)
        )
        self.assertEqual(len(bundle.events), 8_503)
        self.assertEqual(len(bundle.native_identities), 8_503)
        self.assertEqual(len(bundle.poses), 11_883)
        self.assertEqual(bundle.causal_cav_eligible_count, 8_420)
        self.assertEqual(bundle.fresh_zoh_fallback_count, 0)
        self.assertEqual(bundle.stale_pose_count, 83)
        self.assertEqual(bundle.required_pose_start_id, 814)
        self.assertEqual(bundle.required_pose_end_id, 11_805)
        self.assertEqual(bundle.required_pose_pre_roll_ns, 9_069_538)
        self.assertEqual(bundle.registry.warmup_start_ns_inclusive, 4_101_000_000)
        self.assertEqual(bundle.registry.query_start_ns_inclusive, 4_101_324_001)
        self.assertEqual(bundle.registry.query_end_ns_exclusive, 59_425_000_000)


if __name__ == "__main__":
    unittest.main()
