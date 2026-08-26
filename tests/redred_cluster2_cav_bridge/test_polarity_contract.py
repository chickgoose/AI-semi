from __future__ import annotations

import json
from pathlib import Path
import unittest

from benchmarks.redred_cluster2_cav_bridge.contract import (
    TRANSPORT_OUTCOME_SCHEMA,
    BridgeValidationError,
    validate_transport_outcome as validate_v1_transport_outcome,
)
from benchmarks.redred_cluster2_cav_bridge.polarity_contract import (
    TRANSPORT_OUTCOME_POLARITY_SCHEMA,
    validate_polarity_transport_outcome,
    validate_transport_outcome_stream,
    validate_versioned_transport_outcome,
)


def delivered(retire_polarity=0, **changes):
    row = {
        "schema": TRANSPORT_OUTCOME_POLARITY_SCHEMA,
        "event_id": 1,
        "source_index": 4,
        "occurrence_cycle": 0,
        "outcome": "DELIVERED",
        "retire_cycle": 1,
        "retire_native_lane": 0,
        "retire_row": 1,
        "retire_col": 0,
        "retire_polarity": retire_polarity,
    }
    row.update(changes)
    return row


def overrun(**changes):
    row = {
        "schema": TRANSPORT_OUTCOME_POLARITY_SCHEMA,
        "event_id": 1,
        "source_index": 4,
        "occurrence_cycle": 0,
        "outcome": "OVERRUN",
        "retire_cycle": None,
        "retire_native_lane": None,
        "retire_row": None,
        "retire_col": None,
        "retire_polarity": None,
    }
    row.update(changes)
    return row


def legacy_delivered():
    row = delivered()
    del row["retire_polarity"]
    row["schema"] = TRANSPORT_OUTCOME_SCHEMA
    return row


class PolarityTransportContractTests(unittest.TestCase):
    def test_delivered_binds_native_coordinate_and_hardware_polarity(self):
        for polarity in (0, 1):
            row = delivered(polarity)
            with self.subTest(polarity=polarity):
                self.assertIs(validate_polarity_transport_outcome(row), row)
        with self.assertRaises(BridgeValidationError):
            validate_polarity_transport_outcome(delivered(source_index=5))

    def test_delivered_rejects_missing_extra_or_invalid_polarity(self):
        for polarity in (None, -1, 2, True, False, "0"):
            with self.subTest(polarity=polarity), self.assertRaises(
                BridgeValidationError
            ):
                validate_polarity_transport_outcome(delivered(polarity))
        missing = delivered()
        del missing["retire_polarity"]
        extra = delivered(observational_polarity=0)
        for row in (missing, extra):
            with self.assertRaises(BridgeValidationError):
                validate_polarity_transport_outcome(row)

    def test_overrun_requires_every_retire_field_null(self):
        row = overrun()
        self.assertIs(validate_polarity_transport_outcome(row), row)
        for field, value in (
            ("retire_cycle", 1),
            ("retire_native_lane", 0),
            ("retire_row", 1),
            ("retire_col", 0),
            ("retire_polarity", 0),
            ("retire_polarity", 1),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(
                BridgeValidationError
            ):
                validate_polarity_transport_outcome(overrun(**{field: value}))

    def test_hardware_polarity_is_not_inferred_from_observational_sidecar(self):
        source_event = {"event_id": 1, "source_index": 4, "polarity": 0}
        transport = delivered(retire_polarity=1)
        self.assertIs(validate_polarity_transport_outcome(transport), transport)
        self.assertEqual(source_event["polarity"], 0)
        self.assertEqual(transport["retire_polarity"], 1)
        self.assertNotIn("retire_polarity", source_event)
        self.assertNotIn("polarity", transport)

    def test_version_dispatch_preserves_sealed_v1_behavior(self):
        legacy = legacy_delivered()
        self.assertIs(validate_v1_transport_outcome(legacy), legacy)
        self.assertIs(validate_versioned_transport_outcome(legacy), legacy)
        current = delivered(1)
        self.assertIs(validate_versioned_transport_outcome(current), current)
        with self.assertRaises(BridgeValidationError):
            validate_v1_transport_outcome(current)
        with self.assertRaises(BridgeValidationError):
            validate_polarity_transport_outcome(legacy)

    def test_stream_rejects_empty_mixed_or_unknown_versions(self):
        legacy = legacy_delivered()
        current = delivered(1, event_id=2)
        self.assertEqual(validate_transport_outcome_stream((legacy,)), (legacy,))
        self.assertEqual(validate_transport_outcome_stream((current,)), (current,))
        for rows in ((), (legacy, current)):
            with self.assertRaises(BridgeValidationError):
                validate_transport_outcome_stream(rows)
        with self.assertRaises(BridgeValidationError):
            validate_versioned_transport_outcome(
                delivered(schema="redred.cluster2_cav_bridge.transport_outcome/v3")
            )

    def test_v2_json_schema_matches_runtime_contract(self):
        package = (
            Path(__file__).resolve().parents[2]
            / "benchmarks" / "redred_cluster2_cav_bridge"
        )
        legacy_schema = json.loads(
            (package / "transport_outcome.schema.json").read_text()
        )
        schema = json.loads(
            (package / "transport_outcome_v2.schema.json").read_text()
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            TRANSPORT_OUTCOME_POLARITY_SCHEMA,
        )
        self.assertIn("retire_polarity", schema["required"])
        delivered_polarity = schema["allOf"][0]["then"]["properties"][
            "retire_polarity"
        ]
        self.assertEqual(delivered_polarity, {"enum": [0, 1], "type": "integer"})
        self.assertEqual(
            schema["allOf"][1]["then"]["properties"]["retire_polarity"],
            {"type": "null"},
        )
        self.assertNotIn("retire_polarity", legacy_schema["properties"])


if __name__ == "__main__":
    unittest.main()
