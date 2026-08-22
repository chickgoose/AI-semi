from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_so3_axis_audit import new108_adapter as legacy
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    CurrentCAVEvaluationError,
    load_neutral_registry,
)
from benchmarks.redred_mc_wtb_so3_axis_audit import stage3_new108_adapter as stage3
from benchmarks.redred_mc_wtb_so3_axis_audit.stage3_new108_adapter import (
    STAGE3_PREROLL_NS,
    Stage3New108AdapterError,
    _project,
    _verify_against_pinned_source,
    verify_stage3_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import SourcePins, ValidatedSources
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
)


CALIBRATION = b"100 100 120 90 0 0 0 0 0\n"
POSE_TIMES = (
    1_000_000, 8_000_000, 9_000_000, 10_000_000, 30_000_000,
    59_000_000, 60_000_000, 79_000_000, 80_000_000, 81_000_000,
)


def _pose_bytes():
    rows = []
    for timestamp_ns in POSE_TIMES:
        seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
        rows.append(("%d.%09d 0 0 0 0 0 0 1\n" % (seconds, nanos)).encode("ascii"))
    return b"".join(rows)


POSES = _pose_bytes()


def _event_line(timestamp_ns, event_id):
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return ("%d.%09d %d 90 0\n" % (seconds, nanos, 100 + event_id)).encode("ascii")


class SyntheticSource:
    def __init__(self, event_times):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.event_times = tuple(event_times)
        self.events = b"".join(
            _event_line(value, index) for index, value in enumerate(self.event_times)
        )
        (self.root / "events.txt").write_bytes(self.events)
        (self.root / "groundtruth.txt").write_bytes(POSES)
        (self.root / "calib.txt").write_bytes(CALIBRATION)
        pins = SourcePins(
            hashlib.sha256(self.events).hexdigest(),
            hashlib.sha256(POSES).hexdigest(),
            hashlib.sha256(CALIBRATION).hexdigest(),
            len(self.events), len(self.event_times),
        )
        self.validated = ValidatedSources(
            self.root / "events.txt", self.root / "groundtruth.txt",
            self.root / "calib.txt", CALIBRATION, pins.calibration_sha256, pins,
        )

    def close(self):
        self._temporary.cleanup()


def _window(event_times, query_ns, name):
    legacy_start = query_ns - 1_000_000
    end = query_ns + 1_000_000
    warmup = [
        index for index, value in enumerate(event_times)
        if legacy_start <= value < query_ns
    ]
    query = [
        index for index, value in enumerate(event_times)
        if query_ns <= value < end
    ]
    selected = warmup + query
    raw = b"".join(_event_line(event_times[index], index) for index in selected)
    return {
        "candidate_id": name,
        "query_start_ns": query_ns,
        "warmup_start_ns": legacy_start,
        "query_end_ns_exclusive": end,
        "axis": "X", "sign": "POSITIVE", "motion_bin": "MID",
        "rotation_vector_rad": [0.1, 0.0, 0.0],
        "purity": 1.0, "motion_proxy": 1.0,
        "rank_sha256": hashlib.sha256(name.encode("ascii")).hexdigest(),
        "dataset_pose_support_indices": [0, 1, 2],
        "warmup_event_ids": warmup,
        "query_event_ids": query,
        "selected_raw_event_lines_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _registry(source, queries=(60_000_000,)):
    rows = [
        _window(source.event_times, query, "synthetic-%d" % index)
        for index, query in enumerate(queries)
    ]
    body = {
        "schema": "synthetic-selector-registry/v1",
        "bindings": {
            "source_lock_sha256": "a" * 64,
            "selector_py_sha256": "b" * 64,
            "source_member_sha256": {
                "events": source.validated.pins.events_sha256,
                "poses": source.validated.pins.groundtruth_sha256,
                "calibration": source.validated.pins.calibration_sha256,
            },
        },
        "window_count": len(rows),
        "windows": rows,
    }
    body["registry_sha256"] = canonical_sha256(body)
    return body


def _reseal(mapping):
    changed = deepcopy(mapping)
    changed.pop("aggregate_sha256", None)
    changed["aggregate_sha256"] = canonical_sha256(changed)
    return changed


class Stage3New108AdapterTests(unittest.TestCase):
    def test_fifty_ms_boundaries_pose_closure_and_strict_edge(self):
        times = (
            9_999_999, 10_000_000, 11_000_000, 59_100_000,
            60_000_000, 60_999_999, 61_000_000,
        )
        source = SyntheticSource(times)
        try:
            registry = _registry(source)
            bundle = _project(registry, source.validated)
            window = bundle.neutral_registry[0]
            self.assertEqual(window.warmup_start_ns_inclusive, 10_000_000)
            self.assertEqual(
                window.query_start_ns_inclusive - window.warmup_start_ns_inclusive,
                STAGE3_PREROLL_NS,
            )
            events = bundle.event_streams[window.window_id]
            self.assertEqual([event.event_id for event in events], [1, 2, 3, 4, 5])
            self.assertEqual([event.event_id for event in events if event.is_query], [4, 5])
            # At reset, the pose committed on the same edge is unavailable.
            self.assertEqual(events[0].causal_pose_source_index, 2)
            poses = bundle.pose_streams[window.window_id]
            self.assertEqual([pose.pose_id for pose in poses], [1, 2, 3, 4, 5, 6])
            self.assertEqual([pose.commit_cycle < 0 for pose in poses[:3]], [True, True, False])
            self.assertEqual(bundle.selector_registry, registry)
            self.assertEqual(bundle.provenance_seal["pre_roll_ns"], 50_000_000)
            self.assertEqual(
                bundle.provenance_seal["cycle_model_ingress_profile"],
                STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.to_mapping(),
            )
            self.assertEqual(
                bundle.provenance_seal["cycle_model_ingress_profile_sha256"],
                STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.canonical_sha256(),
            )
            self.assertLessEqual(
                bundle.provenance_seal["maximum_occurrence_batch_size"], 8
            )
            self.assertLessEqual(
                bundle.provenance_seal["peak_ingress_staging_occupancy"], 8
            )
            self.assertEqual(
                _verify_against_pinned_source(bundle, registry, source.validated),
                bundle.provenance_seal["aggregate_sha256"],
            )
        finally:
            source.close()

    def test_overlapping_warmups_replay_shared_events_but_queries_are_exact_once(self):
        times = (31_000_000, 40_000_000, 59_100_000, 60_000_000,
                 60_500_000, 79_100_000, 80_000_000)
        source = SyntheticSource(times)
        try:
            registry = _registry(source, (60_000_000, 80_000_000))
            bundle = _project(registry, source.validated)
            first, second = bundle.neutral_registry
            self.assertLess(second.warmup_start_ns_inclusive, first.query_end_ns_exclusive)
            first_events = bundle.event_streams[first.window_id]
            second_events = bundle.event_streams[second.window_id]
            shared = set(event.event_id for event in first_events) & set(
                event.event_id for event in second_events
            )
            self.assertEqual(shared, {0, 1, 2, 3, 4})
            first_query = [event.event_id for event in first_events if event.is_query]
            second_query = [event.event_id for event in second_events if event.is_query]
            self.assertEqual(first_query, registry["windows"][0]["query_event_ids"])
            self.assertEqual(second_query, registry["windows"][1]["query_event_ids"])
            self.assertFalse(next(event for event in second_events if event.event_id == 3).is_query)
            self.assertEqual(len(set(first_query + second_query)), len(first_query + second_query))
        finally:
            source.close()

    def test_query_authority_and_order_fail_closed(self):
        times = (11_000_000, 59_100_000, 60_000_000, 60_500_000)
        source = SyntheticSource(times)
        try:
            registry = _registry(source)
            changed = deepcopy(registry)
            changed["windows"][0]["query_event_ids"] = [3, 2]
            unsigned = dict(changed)
            unsigned.pop("registry_sha256")
            changed["registry_sha256"] = canonical_sha256(unsigned)
            with self.assertRaisesRegex(
                Stage3New108AdapterError, "query event IDs differ"
            ):
                _project(changed, source.validated)
        finally:
            source.close()

    def test_reconstruction_rejects_coordinated_stream_and_seal_mutations(self):
        times = (11_000_000, 59_100_000, 60_000_000)
        source = SyntheticSource(times)
        try:
            registry = _registry(source)
            bundle = _project(registry, source.validated)
            window_id = bundle.neutral_registry[0].window_id
            dropped_events = dict(bundle.event_streams)
            dropped_events[window_id] = dropped_events[window_id][1:]
            tampered = replace(
                bundle, event_streams=dropped_events,
                provenance_seal=_reseal(bundle.provenance_seal),
            )
            with self.assertRaisesRegex(Stage3New108AdapterError, "event inputs differ"):
                _verify_against_pinned_source(tampered, registry, source.validated)

            dropped_poses = dict(bundle.pose_streams)
            dropped_poses[window_id] = dropped_poses[window_id][1:]
            tampered = replace(
                bundle, pose_streams=dropped_poses,
                provenance_seal=_reseal(bundle.provenance_seal),
            )
            with self.assertRaisesRegex(Stage3New108AdapterError, "pose inputs differ"):
                _verify_against_pinned_source(tampered, registry, source.validated)
        finally:
            source.close()

    def test_exact_seal_and_dependency_manifest(self):
        source = SyntheticSource((11_000_000, 59_100_000, 60_000_000))
        try:
            registry = _registry(source)
            bundle = _project(registry, source.validated)
            manifest = bundle.provenance_seal["projection_dependency_manifest"]
            self.assertEqual([row["path"] for row in manifest], sorted(stage3._DEPENDENCY_PATHS))
            for row in manifest:
                self.assertEqual(
                    row["sha256"],
                    hashlib.sha256((stage3._REPO_ROOT / row["path"]).read_bytes()).hexdigest(),
                )
            changed = deepcopy(bundle.provenance_seal)
            changed["windows"][0]["axis"] = "X"
            changed = _reseal(changed)
            with self.assertRaisesRegex(Stage3New108AdapterError, "field schema"):
                _verify_against_pinned_source(
                    replace(bundle, provenance_seal=changed), registry, source.validated
                )
        finally:
            source.close()

    def test_stage12_authority_is_digest_pinned(self):
        source = SyntheticSource((11_000_000, 59_100_000, 60_000_000))
        try:
            with mock.patch.object(stage3, "_PLAN_SHA256", "0" * 64), self.assertRaisesRegex(
                Stage3New108AdapterError, "frozen authority digest"
            ):
                _project(_registry(source), source.validated)
        finally:
            source.close()

    def test_public_verifier_rebuilds_authority(self):
        source = SyntheticSource((11_000_000, 59_100_000, 60_000_000))
        try:
            registry = _registry(source)
            bundle = _project(registry, source.validated)
            with mock.patch.object(stage3, "select_full_source", return_value=registry), \
                    mock.patch.object(stage3, "validate_sources", return_value=source.validated), \
                    mock.patch.object(stage3, "EXPECTED_WINDOW_COUNT", 1):
                self.assertEqual(
                    verify_stage3_new108_adapter(bundle, source.root),
                    bundle.provenance_seal["aggregate_sha256"],
                )
        finally:
            source.close()

    def test_legacy_adapter_and_evaluator_contracts_remain_unchanged(self):
        self.assertEqual(legacy._SEAL_SCHEMA, "redred.mc_wtb_so3_axis_audit.new108_adapter_seal/v2")
        row = _window((59_100_000, 60_000_000), 60_000_000, "legacy")
        self.assertEqual(legacy._neutral_rows((row,))[0].warmup_start_ns_inclusive, 59_000_000)
        overlapping = (
            {"window_id": "a", "warmup_start_ns_inclusive": 0,
             "query_start_ns_inclusive": 10, "query_end_ns_exclusive": 20},
            {"window_id": "b", "warmup_start_ns_inclusive": 19,
             "query_start_ns_inclusive": 30, "query_end_ns_exclusive": 40},
        )
        with self.assertRaisesRegex(CurrentCAVEvaluationError, "overlap"):
            load_neutral_registry(overlapping)


if __name__ == "__main__":
    unittest.main()
