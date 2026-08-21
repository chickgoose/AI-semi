from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    canonical_event_content_sha256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterError,
    _project,
    verify_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_assay.source import SourcePins, ValidatedSources
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


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


class New108AdapterTests(unittest.TestCase):
    def test_projects_bounds_only_and_strictly_pre_edge_pose(self):
        source = SyntheticSource((1_100_000, 2_000_000, 2_100_000))
        try:
            bundle = _project(_registry((1_100_000, 2_000_000, 2_100_000)), source.validated)
            self.assertEqual(len(bundle.neutral_registry), 1)
            self.assertEqual(
                set(bundle.neutral_registry[0].to_mapping()),
                {"window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive", "query_end_ns_exclusive"},
            )
            self.assertEqual(bundle.selector_labels[bundle.neutral_registry[0].window_id]["axis"], "X")
            # Pose 2 has the same timestamp/cycle as the warmup event, so it is
            # deliberately invisible at that event's pre-edge snapshot.
            self.assertEqual(bundle.event_streams[bundle.neutral_registry[0].window_id][0].causal_pose_source_index, 1)
            self.assertRegex(verify_new108_adapter(bundle), r"^[0-9a-f]{64}$")
        finally:
            source.close()

    def test_event_content_mutation_fails_closed(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            bundle = _project(_registry((1_100_000, 2_000_000)), source.validated)
            window_id = bundle.neutral_registry[0].window_id
            event = bundle.event_streams[window_id][0]
            object.__setattr__(event, "polarity", 1)
            with self.assertRaisesRegex(Exception, "event content digest"):
                verify_new108_adapter(bundle)
        finally:
            source.close()

    def test_provenance_mutation_fails_closed(self):
        source = SyntheticSource((1_100_000, 2_000_000))
        try:
            bundle = _project(_registry((1_100_000, 2_000_000)), source.validated)
            changed = dict(bundle.provenance_seal)
            changed["selected_event_count"] = 999
            tampered = replace(bundle, provenance_seal=changed)
            with self.assertRaisesRegex(New108AdapterError, "aggregate provenance"):
                verify_new108_adapter(tampered)
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
