from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit import new108_adapter as adapter_module
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterError,
    _preflight,
    _project,
    _projection_seal,
    _verify_against_pinned_source,
    verify_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import SourcePins, ValidatedSources
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


CALIBRATION = b"100 100 120 90 0 0 0 0 0\n"
POSES = (
    b"0.000000000 0 0 0 0 0 0 1\n"
    b"0.000500000 0 0 0 0 0 0.01 0.9999499987499375\n"
    b"0.001100000 0 0 0 0 0 0.02 0.999799979995999\n"
)


def _event_line(timestamp_ns, event_id):
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return ("%d.%09d %d 90 0\n" % (seconds, nanos, 100 + event_id)).encode("ascii")


class SyntheticSource:
    def __init__(self, event_times):
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        self.root = root
        self.events = b"".join(_event_line(value, index) for index, value in enumerate(event_times))
        (root / "events.txt").write_bytes(self.events)
        (root / "groundtruth.txt").write_bytes(POSES)
        (root / "calib.txt").write_bytes(CALIBRATION)
        pins = SourcePins(
            hashlib.sha256(self.events).hexdigest(),
            hashlib.sha256(POSES).hexdigest(),
            hashlib.sha256(CALIBRATION).hexdigest(),
            len(self.events),
            len(event_times),
        )
        self.validated = ValidatedSources(
            root / "events.txt", root / "groundtruth.txt", root / "calib.txt",
            CALIBRATION, pins.calibration_sha256, pins,
        )

    def close(self):
        self._temporary.cleanup()


def _registry(event_times):
    warmup_ids = [index for index, value in enumerate(event_times) if value < 2_000_000]
    query_ids = [index for index, value in enumerate(event_times) if value >= 2_000_000]
    raw_hash = hashlib.sha256(
        b"".join(_event_line(value, index) for index, value in enumerate(event_times))
    ).hexdigest()
    row = {
        "candidate_id": "synthetic-X-POSITIVE-MID",
        "query_start_ns": 2_000_000,
        "warmup_start_ns": 1_000_000,
        "query_end_ns_exclusive": 3_000_000,
        "axis": "X",
        "sign": "POSITIVE",
        "motion_bin": "MID",
        "rotation_vector_rad": [0.1, 0.0, 0.0],
        "purity": 1.0,
        "motion_proxy": 1.0,
        "rank_sha256": "1" * 64,
        "dataset_pose_support_indices": [0, 1, 2],
        "warmup_event_ids": warmup_ids,
        "query_event_ids": query_ids,
        "selected_raw_event_lines_sha256": raw_hash,
    }
    body = {
        "schema": "synthetic-selector-registry/v1",
        "bindings": {
            "selector_py_sha256": "2" * 64,
            "source_member_sha256": {
                "events": hashlib.sha256(b"".join(
                    _event_line(value, index) for index, value in enumerate(event_times)
                )).hexdigest(),
                "poses": hashlib.sha256(POSES).hexdigest(),
                "calibration": hashlib.sha256(CALIBRATION).hexdigest(),
            },
        },
        "window_count": 1,
        "windows": [row],
    }
    body["registry_sha256"] = canonical_sha256(body)
    return body


def _verify(bundle, registry, source):
    return _verify_against_pinned_source(bundle, registry, source.validated)


def _raw_hashes(registry):
    return {
        row["candidate_id"]: row["selected_raw_event_lines_sha256"]
        for row in registry["windows"]
    }


def _forged_seal(bundle, registry, source, events=None, poses=None):
    event_streams = bundle.event_streams if events is None else events
    pose_streams = bundle.pose_streams if poses is None else poses
    preflight = _preflight(bundle.neutral_registry, event_streams, pose_streams)
    return _projection_seal(
        registry,
        source.validated,
        bundle.neutral_registry,
        event_streams,
        pose_streams,
        bundle.selector_labels,
        _raw_hashes(registry),
        preflight,
    )


def _reseal_top(mapping):
    changed = dict(mapping)
    changed.pop("aggregate_sha256", None)
    changed["aggregate_sha256"] = canonical_sha256(changed)
    return changed


class New108AdapterTests(unittest.TestCase):
    def test_projects_bounds_only_and_strictly_pre_edge_pose(self):
        source = SyntheticSource((1_100_000, 2_000_000, 2_100_000))
        try:
            registry = _registry((1_100_000, 2_000_000, 2_100_000))
            bundle = _project(registry, source.validated)
            self.assertEqual(len(bundle.neutral_registry), 1)
            self.assertEqual(
                set(bundle.neutral_registry[0].to_mapping()),
                {"window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive", "query_end_ns_exclusive"},
            )
            self.assertEqual(bundle.selector_labels[bundle.neutral_registry[0].window_id]["axis"], "X")
            # Pose 2 has the same timestamp/cycle as the warmup event, so it is
            # deliberately invisible at that event's pre-edge snapshot.
            self.assertEqual(bundle.event_streams[bundle.neutral_registry[0].window_id][0].causal_pose_source_index, 1)
            self.assertRegex(_verify(bundle, registry, source), r"^[0-9a-f]{64}$")
        finally:
            source.close()

    def test_event_content_mutation_fails_closed(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            window_id = bundle.neutral_registry[0].window_id
            event = bundle.event_streams[window_id][0]
            object.__setattr__(event, "polarity", 1)
            with self.assertRaisesRegex(New108AdapterError, "projected event inputs"):
                _verify(bundle, registry, source)
        finally:
            source.close()

    def test_provenance_mutation_fails_closed(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            changed = dict(bundle.provenance_seal)
            changed["selected_event_count"] = 999
            tampered = replace(bundle, provenance_seal=changed)
            with self.assertRaisesRegex(New108AdapterError, "aggregate provenance"):
                _verify(tampered, registry, source)
        finally:
            source.close()

    def test_public_verifier_regenerates_selector_authority(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            with mock.patch.object(
                adapter_module, "select_full_source", return_value=registry
            ) as select, mock.patch.object(
                adapter_module, "validate_sources", return_value=source.validated
            ) as validate, mock.patch.object(
                adapter_module, "EXPECTED_WINDOW_COUNT", 1
            ):
                self.assertEqual(
                    verify_new108_adapter(bundle, source.root),
                    bundle.provenance_seal["aggregate_sha256"],
                )
            select.assert_called_once_with(source.root)
            validate.assert_called_once_with(source.root)
        finally:
            source.close()

    def test_stale_or_resealed_selector_registry_fails_authority(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            stale = deepcopy(registry)
            stale["windows"][0]["warmup_event_ids"] = [998]
            stale["windows"][0]["query_event_ids"] = [999]
            stale["windows"][0]["dataset_pose_support_indices"] = [999]
            stale_unsigned = dict(stale)
            stale_unsigned.pop("registry_sha256")
            stale["registry_sha256"] = canonical_sha256(stale_unsigned)
            tampered = replace(bundle, selector_registry=stale)
            with self.assertRaisesRegex(New108AdapterError, "authenticated authority"):
                _verify(tampered, registry, source)
        finally:
            source.close()

    def test_consumed_pose_calibration_and_event_bytes_are_pinned(self):
        pose_source = SyntheticSource((1_100_000, 2_000_000))
        try:
            pose_source.validated.groundtruth_path.write_bytes(POSES.replace(
                b"0.01 0.9999499987499375", b"0.02 0.999799979995999"
            ))
            with self.assertRaisesRegex(New108AdapterError, "groundtruth source hash"):
                _project(_registry((1_100_000, 2_000_000)), pose_source.validated)
        finally:
            pose_source.close()

        calibration_source = SyntheticSource((1_100_000, 2_000_000))
        try:
            changed = replace(
                calibration_source.validated,
                calibration_bytes=b"101 100 120 90 0 0 0 0 0\n",
            )
            with self.assertRaisesRegex(New108AdapterError, "calibration source hash"):
                _project(_registry((1_100_000, 2_000_000)), changed)
        finally:
            calibration_source.close()

        event_source = SyntheticSource((1_100_000, 2_000_000))
        try:
            changed_events = event_source.events.replace(b" 100 90 ", b" 101 90 ", 1)
            self.assertEqual(len(changed_events), len(event_source.events))
            event_source.validated.events_path.write_bytes(changed_events)
            with self.assertRaisesRegex(New108AdapterError, "events source hash"):
                _project(_registry((1_100_000, 2_000_000)), event_source.validated)
        finally:
            event_source.close()

    def test_coordinated_reseal_cannot_replace_projected_event_or_pose(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            window_id = bundle.neutral_registry[0].window_id
            event = bundle.event_streams[window_id][0]
            altered_ray = (event.sensor_ray[1], event.sensor_ray[0], event.sensor_ray[2])
            changed_event = NeutralEventInput(
                event.event_id,
                event.timestamp_ns,
                event.polarity,
                event.is_query,
                altered_ray,
                event.causal_pose_source_index,
                canonical_event_content_sha256(
                    event.event_id,
                    event.timestamp_ns,
                    event.polarity,
                    event.is_query,
                    altered_ray,
                    event.causal_pose_source_index,
                ),
            )
            event_streams = dict(bundle.event_streams)
            event_streams[window_id] = (
                changed_event,
            ) + event_streams[window_id][1:]
            event_tampered = replace(
                bundle,
                event_streams=event_streams,
                provenance_seal=_forged_seal(
                    bundle, registry, source, events=event_streams
                ),
            )
            with self.assertRaisesRegex(New108AdapterError, "projected event inputs"):
                _verify(event_tampered, registry, source)

            pose = bundle.pose_streams[window_id][1]
            quaternion = (0.0, 0.0, 0.0, 1.0)
            changed_pose = NeutralPoseInput(
                pose.pose_id,
                pose.timestamp_ns,
                pose.commit_cycle,
                quaternion,
                canonical_pose_value_sha256(
                    pose.pose_id, pose.timestamp_ns, quaternion
                ),
            )
            pose_streams = dict(bundle.pose_streams)
            pose_streams[window_id] = (
                pose_streams[window_id][0],
                changed_pose,
            ) + pose_streams[window_id][2:]
            pose_tampered = replace(
                bundle,
                pose_streams=pose_streams,
                provenance_seal=_forged_seal(
                    bundle, registry, source, poses=pose_streams
                ),
            )
            with self.assertRaisesRegex(New108AdapterError, "projected pose inputs"):
                _verify(pose_tampered, registry, source)
        finally:
            source.close()

    def test_query_drop_and_extra_pose_fail_closed(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            window_id = bundle.neutral_registry[0].window_id
            no_query = dict(bundle.event_streams)
            no_query[window_id] = tuple(
                event for event in no_query[window_id] if not event.is_query
            )
            with self.assertRaisesRegex(New108AdapterError, "no query events"):
                _preflight(bundle.neutral_registry, no_query, bundle.pose_streams)

            dropped = dict(bundle.event_streams)
            dropped[window_id] = dropped[window_id][1:]
            dropped_bundle = replace(
                bundle,
                event_streams=dropped,
                provenance_seal=_forged_seal(
                    bundle, registry, source, events=dropped
                ),
            )
            with self.assertRaisesRegex(New108AdapterError, "projected event inputs"):
                _verify(dropped_bundle, registry, source)

            extra_pose = NeutralPoseInput(
                3,
                2_900_000,
                pose_timestamp_to_cycle(2_900_000, 1_000_000),
                (0.0, 0.0, 0.0, 1.0),
                canonical_pose_value_sha256(3, 2_900_000, (0.0, 0.0, 0.0, 1.0)),
            )
            pose_streams = dict(bundle.pose_streams)
            pose_streams[window_id] = pose_streams[window_id] + (extra_pose,)
            tampered = replace(
                bundle,
                pose_streams=pose_streams,
                provenance_seal=_forged_seal(
                    bundle, registry, source, poses=pose_streams
                ),
            )
            with self.assertRaisesRegex(New108AdapterError, "projected pose inputs"):
                _verify(tampered, registry, source)
        finally:
            source.close()

    def test_exact_seal_schemas_reject_resealed_extensions(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            registry = _registry((1_100_000, 2_000_000))
            bundle = _project(registry, source.validated)
            mutations = []
            top = deepcopy(bundle.provenance_seal)
            top["score"] = 0.125
            mutations.append(_reseal_top(top))
            member = deepcopy(bundle.provenance_seal)
            member["source_member_sha256"]["score"] = "0" * 64
            mutations.append(_reseal_top(member))
            window = deepcopy(bundle.provenance_seal)
            window["windows"][0]["axis"] = "X"
            mutations.append(_reseal_top(window))
            for changed in mutations:
                with self.subTest(keys=sorted(changed)), self.assertRaisesRegex(
                    New108AdapterError, "field schema|source member schema"
                ):
                    _verify(
                        replace(bundle, provenance_seal=changed), registry, source
                    )
        finally:
            source.close()

    def test_seven_same_cycle_events_fail_closed(self):
        event_times = (1_100_000,) * 7 + (2_000_000,)
        source = SyntheticSource(event_times)
        try:
            with self.assertRaisesRegex(New108AdapterError, "more than six"):
                _project(_registry(event_times), source.validated)
        finally:
            source.close()


if __name__ == "__main__":
    unittest.main()
