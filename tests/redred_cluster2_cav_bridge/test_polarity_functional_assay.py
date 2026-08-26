from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import unittest
from unittest import mock

from benchmarks.redred_cluster2_cav_bridge import functional_assay as legacy
from benchmarks.redred_cluster2_cav_bridge import polarity_functional_assay as module
from benchmarks.redred_cluster2_cav_bridge.cav_adapter import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from benchmarks.redred_cluster2_cav_bridge.contract import (
    canonical_event_content_sha256,
    canonical_json_bytes,
)
from benchmarks.redred_cluster2_cav_bridge.functional_source import (
    FunctionalSourceBundle,
    NativeEventIdentity,
)
from benchmarks.redred_cluster2_cav_bridge.native_outcome_bundle import (
    NativeOutcome,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle


def _pose(pose_id, timestamp_ns):
    quaternion = (0.0, 0.0, 0.0, 1.0)
    digest = hashlib.sha256(canonical_json_bytes({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion),
    })).hexdigest()
    return NeutralPoseInput(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, 0),
        quaternion,
        digest,
        True,
        True,
    )


def _event(event_id, timestamp_ns, sensor_ray, polarity):
    digest = canonical_event_content_sha256(
        event_id, timestamp_ns, polarity, True, sensor_ray, 1, True
    )
    return NeutralEventInput(
        event_id,
        timestamp_ns,
        polarity,
        True,
        sensor_ray,
        1,
        digest,
        True,
    )


def _source():
    return FunctionalSourceBundle(
        registry=NeutralRegistryWindow("synthetic-polarity-assay", 0, 1_000, 3_000_000),
        events=(
            _event(1, 1_000, (1.0, 0.0, 0.0), 1),
            _event(0, 2_000, (0.0, 1.0, 0.0), 0),
            _event(2, 2_000_000, (0.0, 0.0, 1.0), 1),
        ),
        poses=(_pose(0, 500), _pose(1, 900)),
        native_identities=(
            NativeEventIdentity(1, 0, 10, 110, 85),
            NativeEventIdentity(0, 1, 20, 111, 85),
            NativeEventIdentity(2, 2, 30, 112, 85),
        ),
        required_pose_start_id=0,
        required_pose_end_id=1,
        required_pose_pre_roll_ns=1,
        causal_cav_eligible_count=1,
        fresh_zoh_fallback_count=1,
        stale_pose_count=1,
    )


def _outcomes():
    schema = module.TRANSPORT_OUTCOME_POLARITY_SCHEMA
    return (
        module.HardwarePolarityOutcomeV2(schema, 0, 1, 20, 22, 2, 0),
        module.HardwarePolarityOutcomeV2(schema, 1, 0, 10, 11, 1, 1),
        module.HardwarePolarityOutcomeV2(schema, 2, 2, 30, 33, 3, 1),
    )


@contextmanager
def _synthetic_authority():
    with mock.patch.multiple(
        legacy,
        EXPECTED_EVENT_COUNT=3,
        EXPECTED_POSE_COUNT=2,
        EXPECTED_CAUSAL_CAV_COUNT=1,
        EXPECTED_ZOH_COUNT=1,
        EXPECTED_BYPASS_COUNT=1,
    ):
        yield


class PolarityFunctionalAssayTests(unittest.TestCase):
    def test_wraps_unchanged_legacy_result_and_carries_hardware_polarity(self):
        source = _source()
        outcomes = _outcomes()
        legacy_outcomes = tuple(row.legacy_outcome() for row in outcomes)
        with _synthetic_authority():
            expected_legacy = legacy.run_functional_assay(source, legacy_outcomes)
            result = module.run_hardware_polarity_functional_assay(source, outcomes)

        self.assertEqual(result.legacy_result, expected_legacy)
        self.assertIs(result.geometry, result.legacy_result.geometry)
        self.assertEqual(
            result.legacy_result.views[2].sidecar_semantics,
            legacy.LATENCY_SIDECAR_ONLY,
        )
        self.assertTrue(all(
            not hasattr(row, "hardware_observed_polarity")
            for row in result.retire_sidecar
        ))
        self.assertEqual(
            tuple(
                (
                    row.event_id,
                    row.source_index,
                    row.native_occurrence_cycle,
                    row.hardware_observed_polarity,
                    row.semantics_label,
                )
                for row in result.hardware_polarity_sidecar
            ),
            (
                (1, 0, 10, 1, module.HARDWARE_CARRIED_POLARITY),
                (0, 1, 20, 0, module.HARDWARE_CARRIED_POLARITY),
                (2, 2, 30, 1, module.HARDWARE_CARRIED_POLARITY),
            ),
        )
        with _synthetic_authority():
            self.assertIs(
                module.validate_hardware_polarity_assay_result(
                    result, source, outcomes
                ),
                result,
            )

    def test_polarity_mismatch_fails_before_legacy_assay_or_geometry(self):
        changed = list(_outcomes())
        changed[0] = replace(changed[0], retire_polarity=1)
        with _synthetic_authority(), mock.patch.object(
            module.legacy, "run_functional_assay"
        ) as legacy_run:
            with self.assertRaisesRegex(
                module.PolarityFunctionalAssayError,
                "hardware-observed polarity differs from source polarity",
            ):
                module.run_hardware_polarity_functional_assay(
                    _source(), tuple(changed)
                )
        legacy_run.assert_not_called()

    def test_geometry_stays_occurrence_timestamp_driven_when_polarity_changes(self):
        source = _source()
        outcomes = _outcomes()
        changed_events = list(source.events)
        changed_events[1] = _event(0, 2_000, (0.0, 1.0, 0.0), 1)
        changed_source = replace(source, events=tuple(changed_events))
        changed_outcomes = list(outcomes)
        changed_outcomes[0] = replace(changed_outcomes[0], retire_polarity=1)
        with _synthetic_authority():
            baseline = module.run_hardware_polarity_functional_assay(
                source, outcomes
            )
            changed = module.run_hardware_polarity_functional_assay(
                changed_source, tuple(changed_outcomes)
            )
        self.assertEqual(
            baseline.statistics.geometry_sha256,
            changed.statistics.geometry_sha256,
        )
        self.assertEqual(
            tuple(row.event_timestamp_ns for row in baseline.geometry),
            tuple(row.event_timestamp_ns for row in changed.geometry),
        )
        self.assertNotEqual(
            baseline.hardware_polarity_sidecar,
            changed.hardware_polarity_sidecar,
        )

    def test_v2_row_adapter_is_exact_and_never_uses_source_polarity(self):
        row = {
            "schema": module.TRANSPORT_OUTCOME_POLARITY_SCHEMA,
            "event_id": 0,
            "source_index": 1,
            "occurrence_cycle": 20,
            "outcome": "DELIVERED",
            "retire_cycle": 22,
            "retire_native_lane": 0,
            "retire_row": 0,
            "retire_col": 1,
            "retire_polarity": 0,
        }
        adapted = module.HardwarePolarityOutcomeV2.from_transport_outcome_v2(row)
        self.assertEqual(adapted.retire_polarity, 0)
        self.assertEqual(adapted.legacy_outcome(), NativeOutcome(0, 1, 20, 22, 2))

        missing = dict(row)
        missing.pop("retire_polarity")
        with self.assertRaisesRegex(
            module.PolarityFunctionalAssayError, "input fields differ"
        ):
            module.HardwarePolarityOutcomeV2.from_transport_outcome_v2(missing)
        overrun = dict(row)
        overrun["outcome"] = "OVERRUN"
        with self.assertRaisesRegex(
            module.PolarityFunctionalAssayError, "only delivered"
        ):
            module.HardwarePolarityOutcomeV2.from_transport_outcome_v2(overrun)

    def test_legacy_outcomes_and_wrong_schema_cannot_enter_v2_path(self):
        source = _source()
        legacy_outcomes = tuple(row.legacy_outcome() for row in _outcomes())
        with _synthetic_authority(), self.assertRaisesRegex(
            module.PolarityFunctionalAssayError, "invalid adapter type"
        ):
            module.run_hardware_polarity_functional_assay(
                source, legacy_outcomes
            )
        with self.assertRaisesRegex(
            module.PolarityFunctionalAssayError, "transport_outcome/v2"
        ):
            module.HardwarePolarityOutcomeV2(
                "redred.cluster2_cav_bridge.transport_outcome/v1",
                0, 1, 20, 22, 2, 0,
            )


if __name__ == "__main__":
    unittest.main()
