from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import screen108
from benchmarks.redred_mc_wtb_so3_axis_audit import (
    stage3_new108_adapter as adapter_module,
)
from benchmarks.redred_mc_wtb_so3_axis_audit import (
    new108_adapter as legacy_adapter_module,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    evaluate_current_cav_registry,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import SourcePins, ValidatedSources
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import timestamp_to_cycle


STAGE3_PREROLL_NS = 50_000_000
ORIGINAL_SELECTOR_WARMUP_NS = 1_000_000
QUERY_NS = 1_000_000
ZERO_SHA256 = "0" * 64
CALIBRATION = b"100 100 120 90 0 0 0 0 0\n"
PINNED_DATA_ENV = "MCWTB_PINNED_SHAPES_ROTATION_DIR"


def _stage3_api(name):
    value = getattr(adapter_module, name, None)
    if not callable(value):
        raise AssertionError("missing expected Stage3 adapter API: %s" % name)
    return value


def _event_line(timestamp_ns, event_id):
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    # One fixed ray/polarity keeps a same-frame reference available for every
    # query while event_id remains its physical source-line identity.
    return ("%d.%09d 100 90 0\n" % (seconds, nanos)).encode("ascii")


def _pose_line(timestamp_ns, index):
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    angle = index * 0.002
    z = math.sin(angle / 2.0)
    w = math.cos(angle / 2.0)
    return ("%d.%09d 0 0 0 0 0 %.17g %.17g\n" % (
        seconds, nanos, z, w,
    )).encode("ascii")


class SyntheticPinnedSource:
    """Small immutable source with two overlapping 50 ms Stage3 windows."""

    event_times_ns = (
        9_000_000,
        10_000_000,
        30_000_000,
        31_000_000,
        59_000_000,
        60_000_000,
        60_500_000,
        79_000_000,
        80_000_000,
        80_500_000,
        81_000_000,
    )
    pose_times_ns = tuple(range(0, 91_000_000, 5_000_000))

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.events = b"".join(
            _event_line(timestamp, event_id)
            for event_id, timestamp in enumerate(self.event_times_ns)
        )
        self.poses = b"".join(
            _pose_line(timestamp, index)
            for index, timestamp in enumerate(self.pose_times_ns)
        )
        (self.root / "events.txt").write_bytes(self.events)
        (self.root / "groundtruth.txt").write_bytes(self.poses)
        (self.root / "calib.txt").write_bytes(CALIBRATION)
        pins = SourcePins(
            hashlib.sha256(self.events).hexdigest(),
            hashlib.sha256(self.poses).hexdigest(),
            hashlib.sha256(CALIBRATION).hexdigest(),
            len(self.events),
            len(self.event_times_ns),
        )
        self.validated = ValidatedSources(
            self.root / "events.txt",
            self.root / "groundtruth.txt",
            self.root / "calib.txt",
            CALIBRATION,
            pins.calibration_sha256,
            pins,
        )
        self.registry = self._registry()

    def close(self):
        self._temporary.cleanup()

    def _raw_digest(self, event_ids):
        return hashlib.sha256(b"".join(
            _event_line(self.event_times_ns[event_id], event_id)
            for event_id in event_ids
        )).hexdigest()

    def _row(self, query_start_ns, warmup_ids, query_ids, pose_indices):
        selected_ids = tuple(warmup_ids) + tuple(query_ids)
        return {
            "candidate_id": "shapes_rotation/query_start_ns=%d" % query_start_ns,
            "query_start_ns": query_start_ns,
            "warmup_start_ns": query_start_ns - ORIGINAL_SELECTOR_WARMUP_NS,
            "query_end_ns_exclusive": query_start_ns + QUERY_NS,
            "axis": "X",
            "sign": "POSITIVE",
            "motion_bin": "MID",
            "rotation_vector_rad": [0.01, 0.0, 0.0],
            "purity": 1.0,
            "motion_proxy": 0.7,
            "rank_sha256": hashlib.sha256(
                ("rank-%d" % query_start_ns).encode("ascii")
            ).hexdigest(),
            "axis_pose_support_indices": list(pose_indices[-2:]),
            "oracle_pose_support_indices": list(pose_indices[-3:]),
            "dataset_pose_support_indices": list(pose_indices),
            "pose_support_indices": list(pose_indices),
            "warmup_event_ids": list(warmup_ids),
            "query_event_ids": list(query_ids),
            "warmup_event_ids_sha256": canonical_sha256(list(warmup_ids)),
            "query_event_ids_sha256": canonical_sha256(list(query_ids)),
            "selected_raw_event_lines_sha256": self._raw_digest(selected_ids),
        }

    def _registry(self):
        # The selector authority remains the original 1 ms cohort.  Stage3 is
        # allowed to expand only its score-free warmup projection.
        rows = [
            self._row(60_000_000, (4,), (5, 6), tuple(range(9, 14))),
            self._row(80_000_000, (7,), (8, 9), tuple(range(13, 18))),
        ]
        body = {
            "schema": "redred.mc_wtb_so3_axis_audit.cohort_registry/v1",
            "contract": {"warmup_ns": ORIGINAL_SELECTOR_WARMUP_NS},
            "bindings": {
                "selector_py_sha256": "1" * 64,
                "source_lock_sha256": "a" * 64,
                "source_member_sha256": {
                    "events": self.validated.pins.events_sha256,
                    "poses": self.validated.pins.groundtruth_sha256,
                    "calibration": self.validated.pins.calibration_sha256,
                },
            },
            "window_count": len(rows),
            "windows": rows,
        }
        return dict(body, registry_sha256=canonical_sha256(body))

    def patched_authorities(self):
        return mock.patch.multiple(
            adapter_module,
            select_full_source=mock.DEFAULT,
            validate_sources=mock.DEFAULT,
            EXPECTED_WINDOW_COUNT=len(self.registry["windows"]),
        )


class Stage3New108AdapterTests(unittest.TestCase):
    def setUp(self):
        self.source = SyntheticPinnedSource()

    def tearDown(self):
        self.source.close()

    def _build(self):
        build = _stage3_api("build_locked_stage3_new108_adapter")
        with self.source.patched_authorities() as patched:
            patched["select_full_source"].side_effect = lambda _root: deepcopy(
                self.source.registry
            )
            patched["validate_sources"].return_value = self.source.validated
            bundle = build(self.source.root)
        return bundle

    def _verify(self, bundle):
        verify = _stage3_api("verify_stage3_new108_adapter")
        with self.source.patched_authorities() as patched:
            patched["select_full_source"].side_effect = lambda _root: deepcopy(
                self.source.registry
            )
            patched["validate_sources"].return_value = self.source.validated
            return verify(bundle, self.source.root)

    def test_exact_50ms_bounds_preserve_original_query_identity(self):
        bundle = self._build()
        source_rows = {
            row["candidate_id"]: row for row in self.source.registry["windows"]
        }
        self.assertEqual(len(bundle.neutral_registry), 2)
        for window in bundle.neutral_registry:
            with self.subTest(window=window.window_id):
                source = source_rows[window.window_id]
                self.assertEqual(
                    window.query_start_ns_inclusive
                    - window.warmup_start_ns_inclusive,
                    STAGE3_PREROLL_NS,
                )
                self.assertEqual(
                    window.query_start_ns_inclusive, source["query_start_ns"]
                )
                self.assertEqual(
                    window.query_end_ns_exclusive,
                    source["query_end_ns_exclusive"],
                )
                observed_query = [
                    event.event_id for event in bundle.event_streams[window.window_id]
                    if event.is_query
                ]
                self.assertEqual(observed_query, source["query_event_ids"])
        self.assertRegex(self._verify(bundle), r"^[0-9a-f]{64}$")

    def test_warmup_expands_from_source_and_overlap_does_not_duplicate_query(self):
        bundle = self._build()
        first, second = bundle.neutral_registry
        first_events = bundle.event_streams[first.window_id]
        second_events = bundle.event_streams[second.window_id]
        self.assertEqual(
            [event.event_id for event in first_events if not event.is_query],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            [event.event_id for event in second_events if not event.is_query],
            [2, 3, 4, 5, 6, 7],
        )
        self.assertEqual(
            {event.event_id for event in first_events if not event.is_query}
            & {event.event_id for event in second_events if not event.is_query},
            {2, 3, 4},
        )
        # Query IDs 5 and 6 remain query exactly once, while their reuse as
        # score-free warmup in the later overlapping window is permitted.
        query_occurrences = {}
        for window in bundle.neutral_registry:
            for event in bundle.event_streams[window.window_id]:
                if event.is_query:
                    query_occurrences[event.event_id] = (
                        query_occurrences.get(event.event_id, 0) + 1
                    )
        self.assertEqual(query_occurrences, {5: 1, 6: 1, 8: 1, 9: 1})
        self.assertFalse(next(
            event for event in second_events if event.event_id == 5
        ).is_query)

    def test_expanded_pose_stream_is_strictly_pre_edge_and_source_bound(self):
        bundle = self._build()
        first = bundle.neutral_registry[0]
        poses = bundle.pose_streams[first.window_id]
        events = bundle.event_streams[first.window_id]
        poses_by_id = {pose.pose_id: pose for pose in poses}
        self.assertTrue(any(
            pose.timestamp_ns < self.source.registry["windows"][0]["warmup_start_ns"]
            for pose in poses
        ))
        for event in events:
            pose = poses_by_id[event.causal_pose_source_index]
            occurrence = timestamp_to_cycle(
                event.timestamp_ns, first.warmup_start_ns_inclusive
            )
            with self.subTest(event=event.event_id):
                self.assertLess(pose.commit_cycle, occurrence)
                self.assertLessEqual(pose.timestamp_ns, event.timestamp_ns)
        # Event 1 and pose 2 both occur on the 10 ms reset edge.  The event
        # must cite the preceding 5 ms pose, never the same-edge packet.
        reset_edge_event = next(event for event in events if event.event_id == 1)
        self.assertEqual(reset_edge_event.causal_pose_source_index, 1)
        self.assertNotEqual(reset_edge_event.causal_pose_source_index, 2)

    def test_verifier_reconstructs_and_rejects_event_pose_and_seal_mutations(self):
        bundle = self._build()
        window_id = bundle.neutral_registry[0].window_id

        event_streams = dict(bundle.event_streams)
        events = list(event_streams[window_id])
        original = events[0]
        events[0] = replace(
            original,
            polarity=1 - original.polarity,
            event_content_sha256=canonical_event_content_sha256(
                original.event_id,
                original.timestamp_ns,
                1 - original.polarity,
                original.is_query,
                original.sensor_ray,
                original.causal_pose_source_index,
                original.transform_guard_valid,
            ),
        )
        event_streams[window_id] = tuple(events)
        with self.assertRaises(adapter_module.Stage3New108AdapterError):
            self._verify(replace(bundle, event_streams=event_streams))

        pose_streams = dict(bundle.pose_streams)
        poses = list(pose_streams[window_id])
        pose = poses[0]
        changed_quaternion = (
            0.0, 0.0, math.sin(0.123 / 2.0), math.cos(0.123 / 2.0)
        )
        poses[0] = replace(
            pose,
            quaternion_xyzw=changed_quaternion,
            pose_sha256=canonical_pose_value_sha256(
                pose.pose_id, pose.timestamp_ns, changed_quaternion
            ),
        )
        pose_streams[window_id] = tuple(poses)
        with self.assertRaises(adapter_module.Stage3New108AdapterError):
            self._verify(replace(bundle, pose_streams=pose_streams))

        seal = dict(bundle.provenance_seal)
        seal["aggregate_sha256"] = "f" * 64
        with self.assertRaises(adapter_module.Stage3New108AdapterError):
            self._verify(replace(bundle, provenance_seal=seal))

    def test_resealed_same_edge_pose_and_query_phase_mutations_fail_closed(self):
        bundle = self._build()
        window_id = bundle.neutral_registry[0].window_id
        source_events = list(bundle.event_streams[window_id])

        event = next(row for row in source_events if row.event_id == 1)
        same_edge_pose_id = 2
        mutated = replace(
            event,
            causal_pose_source_index=same_edge_pose_id,
            event_content_sha256=canonical_event_content_sha256(
                event.event_id,
                event.timestamp_ns,
                event.polarity,
                event.is_query,
                event.sensor_ray,
                same_edge_pose_id,
                event.transform_guard_valid,
            ),
        )
        changed = dict(bundle.event_streams)
        changed[window_id] = tuple(
            mutated if row.event_id == event.event_id else row
            for row in source_events
        )
        with self.assertRaises(adapter_module.Stage3New108AdapterError):
            self._verify(replace(bundle, event_streams=changed))

        query = next(row for row in source_events if row.event_id == 5)
        phase_mutant = replace(
            query,
            is_query=False,
            event_content_sha256=canonical_event_content_sha256(
                query.event_id,
                query.timestamp_ns,
                query.polarity,
                False,
                query.sensor_ray,
                query.causal_pose_source_index,
                query.transform_guard_valid,
            ),
        )
        changed = dict(bundle.event_streams)
        changed[window_id] = tuple(
            phase_mutant if row.event_id == query.event_id else row
            for row in source_events
        )
        with self.assertRaises(adapter_module.Stage3New108AdapterError):
            self._verify(replace(bundle, event_streams=changed))

    def test_verifier_rejects_post_build_event_and_pose_source_mutation(self):
        bundle = self._build()
        changed = self.source.events.replace(b" 100 90 ", b" 101 90 ", 1)
        self.assertEqual(len(changed), len(self.source.events))
        (self.source.root / "events.txt").write_bytes(changed)
        try:
            with self.assertRaises(adapter_module.Stage3New108AdapterError):
                self._verify(bundle)
        finally:
            (self.source.root / "events.txt").write_bytes(self.source.events)

        changed_pose = self.source.poses.replace(
            b" 0 0 0 0 0 0 1\n", b" 0 0 0 0 0 1 0\n", 1
        )
        self.assertEqual(len(changed_pose), len(self.source.poses))
        (self.source.root / "groundtruth.txt").write_bytes(changed_pose)
        try:
            with self.assertRaises(adapter_module.Stage3New108AdapterError):
                self._verify(bundle)
        finally:
            (self.source.root / "groundtruth.txt").write_bytes(self.source.poses)

    def test_screen_boundary_rejects_a_real_one_ms_adapter_bundle(self):
        # This is the missing cross-component smoke: no screen bundle mock and
        # no authority-constant mismatch can mask the actual pre-roll check.
        legacy = legacy_adapter_module._project(
            deepcopy(self.source.registry), self.source.validated
        )
        baseline = evaluate_current_cav_registry(
            legacy.neutral_registry, legacy.event_streams, legacy.pose_streams
        )
        self.assertEqual(
            legacy.neutral_registry[0].query_start_ns_inclusive
            - legacy.neutral_registry[0].warmup_start_ns_inclusive,
            ORIGINAL_SELECTOR_WARMUP_NS,
        )
        with self.assertRaisesRegex(
            screen108.Screen108Error, "locked 50 ms pre-roll"
        ):
            screen108._evaluate_verified(
                legacy,
                baseline,
                {},
                ZERO_SHA256,
                ZERO_SHA256,
                {},
                {},
                Path(__file__).resolve().parents[2],
            )


@unittest.skipUnless(
    os.environ.get(PINNED_DATA_ENV),
    "set %s to run the optional 509 MB pinned-source test" % PINNED_DATA_ENV,
)
class PinnedStage3New108AdapterTests(unittest.TestCase):
    def test_pinned_108_window_projection_is_screen_compatible(self):
        build = _stage3_api("build_locked_stage3_new108_adapter")
        verify = _stage3_api("verify_stage3_new108_adapter")
        dataset = Path(os.environ[PINNED_DATA_ENV])
        bundle = build(dataset)
        self.assertEqual(len(bundle.neutral_registry), 108)
        source_rows = {
            row["candidate_id"]: row
            for row in bundle.selector_registry["windows"]
        }
        query_seen = {}
        all_event_windows = {}
        for window in bundle.neutral_registry:
            self.assertEqual(
                window.query_start_ns_inclusive
                - window.warmup_start_ns_inclusive,
                STAGE3_PREROLL_NS,
            )
            observed = [
                event.event_id for event in bundle.event_streams[window.window_id]
                if event.is_query
            ]
            self.assertEqual(observed, source_rows[window.window_id]["query_event_ids"])
            self.assertTrue(any(
                not event.is_query
                and event.timestamp_ns
                < source_rows[window.window_id]["warmup_start_ns"]
                for event in bundle.event_streams[window.window_id]
            ))
            for event in bundle.event_streams[window.window_id]:
                all_event_windows.setdefault(event.event_id, set()).add(
                    window.window_id
                )
            for event_id in observed:
                query_seen[event_id] = query_seen.get(event_id, 0) + 1
        self.assertTrue(query_seen)
        self.assertTrue(all(count == 1 for count in query_seen.values()))
        self.assertTrue(any(
            len(window_ids) > 1 for window_ids in all_event_windows.values()
        ))
        self.assertEqual(
            verify(bundle, dataset), bundle.provenance_seal["aggregate_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
