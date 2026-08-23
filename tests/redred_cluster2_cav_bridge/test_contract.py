from __future__ import annotations

from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.redred_cluster2_cav_bridge import (
    MANIFEST_SCHEMA,
    OBSERVATIONAL_JOIN_LABEL,
    PROJECTION_SCHEMA,
    SOURCE_EVENT_SCHEMA,
    TIMESTAMP_TO_OCCURRENCE_RULE,
    TRANSPORT_OUTCOME_SCHEMA,
    BridgeValidationError,
    canonical_event_content_sha256,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    load_bridge_bundle,
    load_canonical_json,
    validate_manifest,
    validate_source_event,
    validate_transport_outcome,
)
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256 as current_cav_event_content_sha256,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_event(
    event_id, ordinal, timestamp_ns, source_index=4, ray=None, guard=True
):
    sensor_ray = [0.6, 0.0, 0.8] if ray is None else list(ray)
    row = {
        "schema": SOURCE_EVENT_SCHEMA,
        "event_id": event_id,
        "ordinal": ordinal,
        "timestamp_ns": timestamp_ns,
        "source_index": source_index,
        "polarity": ordinal % 2,
        "window_id": "synthetic-window",
        "is_query": True,
        "sensor_ray": sensor_ray,
        "causal_pose_source_index": 7,
        "transform_guard_valid": guard,
    }
    row["event_content_sha256"] = canonical_event_content_sha256(
        row["event_id"],
        row["timestamp_ns"],
        row["polarity"],
        row["is_query"],
        row["sensor_ray"],
        row["causal_pose_source_index"],
        row["transform_guard_valid"],
    )
    return row


def delivered(
    event_id, source_index, occurrence_cycle, retire_cycle,
    native_lane, retire_col,
):
    return {
        "schema": TRANSPORT_OUTCOME_SCHEMA,
        "event_id": event_id,
        "source_index": source_index,
        "occurrence_cycle": occurrence_cycle,
        "outcome": "DELIVERED",
        "retire_cycle": retire_cycle,
        "retire_native_lane": native_lane,
        "retire_col": retire_col,
    }


def overrun(event_id, source_index, occurrence_cycle):
    return {
        "schema": TRANSPORT_OUTCOME_SCHEMA,
        "event_id": event_id,
        "source_index": source_index,
        "occurrence_cycle": occurrence_cycle,
        "outcome": "OVERRUN",
        "retire_cycle": None,
        "retire_native_lane": None,
        "retire_col": None,
    }


def fixture_rows():
    sources = (
        source_event(11, 0, 1000, 4),
        source_event(12, 1, 1001, 0),
        source_event(13, 2, 1002, 5),
        source_event(14, 3, 1003, 12),
    )
    outcomes = (
        delivered(14, 12, 3, 3, 1, 0),
        delivered(13, 5, 2, 2, 0, 1),
        delivered(11, 4, 0, 2, 0, 0),
        overrun(12, 0, 1),
    )
    return sources, outcomes


def manifest_for(source_bytes, outcome_bytes, authority_bytes, count):
    return {
        "schema": MANIFEST_SCHEMA,
        "bridge_id": "synthetic-bridge",
        "source_events": {
            "format": "canonical-jsonl/v1",
            "path": "source_events.jsonl",
            "sha256": digest(source_bytes),
            "event_count": count,
        },
        "transport_outcomes": {
            "format": "canonical-jsonl/v1",
            "path": "transport_outcomes.jsonl",
            "sha256": digest(outcome_bytes),
            "event_count": count,
        },
        "mapping_authority": {
            "path": "authority/mapping.txt",
            "sha256": digest(authority_bytes["mapping"]),
        },
        "source_registry_authority": {
            "path": "authority/source_registry.bin",
            "sha256": digest(authority_bytes["source_registry"]),
        },
        "pose_stream_authority": {
            "path": "authority/pose_stream.bin",
            "sha256": digest(authority_bytes["pose_stream"]),
        },
        "native_transport_receipt_authority": {
            "path": "authority/native_transport_receipt.bin",
            "sha256": digest(authority_bytes["native_transport_receipt"]),
        },
        "rtl_authorities": [{
            "role": "ganghee_cluster2_top",
            "path": "authority/cluster2_top.v",
            "sha256": digest(authority_bytes["rtl"]),
        }],
        "aer_clock_period_ps": 1000,
        "aer_cycle_zero_timestamp_ns": 1000,
        "timestamp_to_occurrence_cycle_rule": TIMESTAMP_TO_OCCURRENCE_RULE,
        "projection": {
            "schema": PROJECTION_SCHEMA,
            "views": ["RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"],
            "aer_projection_semantics": OBSERVATIONAL_JOIN_LABEL,
        },
    }


class BundleFixture:
    def __init__(self, root, sources=None, outcomes=None):
        self.root = root
        default_sources, default_outcomes = fixture_rows()
        self.sources = tuple(default_sources if sources is None else sources)
        self.outcomes = tuple(default_outcomes if outcomes is None else outcomes)
        self.source_bytes = canonical_jsonl_bytes(self.sources)
        self.outcome_bytes = canonical_jsonl_bytes(self.outcomes)
        self.authority_bytes = {
            "mapping": b"synthetic exact-ID and AER clock mapping\n",
            "source_registry": b"opaque synthetic source registry\n",
            "pose_stream": b"opaque synthetic pose stream\n",
            "native_transport_receipt": b"opaque synthetic native simulator receipt\n",
            "rtl": b"module synthetic_cluster2; endmodule\n",
        }
        self.manifest = manifest_for(
            self.source_bytes,
            self.outcome_bytes,
            self.authority_bytes,
            len(self.sources),
        )

    def write(self):
        authority = self.root / "authority"
        authority.mkdir()
        paths = {
            "mapping": "mapping.txt",
            "source_registry": "source_registry.bin",
            "pose_stream": "pose_stream.bin",
            "native_transport_receipt": "native_transport_receipt.bin",
            "rtl": "cluster2_top.v",
        }
        for name, basename in paths.items():
            (authority / basename).write_bytes(self.authority_bytes[name])
        (self.root / "source_events.jsonl").write_bytes(self.source_bytes)
        (self.root / "transport_outcomes.jsonl").write_bytes(self.outcome_bytes)
        manifest_bytes = canonical_json_bytes(self.manifest)
        manifest_path = self.root / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        return manifest_path, digest(manifest_bytes)


class CurrentCAVCompatibilityTests(unittest.TestCase):
    def test_digest_matches_exact_current_cav_neutral_preimage(self):
        row = source_event(91, 0, 123456, 4, ray=[0.0, 0.0, 1.0], guard=False)
        expected = current_cav_event_content_sha256(
            row["event_id"],
            row["timestamp_ns"],
            row["polarity"],
            row["is_query"],
            row["sensor_ray"],
            row["causal_pose_source_index"],
            row["transform_guard_valid"],
        )
        self.assertEqual(row["event_content_sha256"], expected)
        self.assertIs(validate_source_event(row), row)

    def test_digest_ray_and_guard_mutations_fail_closed(self):
        row = source_event(1, 0, 1000)
        bad_digest = dict(row, event_content_sha256="0" * 64)
        changed_guard = dict(row, transform_guard_valid=False)
        missing_guard = dict(row)
        del missing_guard["transform_guard_valid"]
        wrong_guard = dict(row, transform_guard_valid=1)
        nonunit = source_event(1, 0, 1000, ray=[0.5, 0.0, 0.5])
        for mutation in (
            bad_digest, changed_guard, missing_guard, wrong_guard, nonunit,
        ):
            with self.subTest(mutation=mutation), self.assertRaises(BridgeValidationError):
                validate_source_event(mutation)


class ProjectionTests(unittest.TestCase):
    def test_positive_join_is_deterministic_and_explicitly_observational(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            manifest_path, manifest_sha = fixture.write()
            bundle = load_bridge_bundle(manifest_path, manifest_sha)
            first = bundle.project()
            second = bundle.project()
        self.assertEqual(first, second)
        self.assertEqual(tuple(first), (
            "RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"
        ))
        self.assertEqual(
            [row["event_id"] for row in first["RAW4X4_MATCHED"]],
            [11, 13, 14],
        )
        for view in ("AER_OCC", "AER_RET"):
            self.assertTrue(first[view])
            self.assertTrue(all(
                row["projection_semantics"] == OBSERVATIONAL_JOIN_LABEL
                for row in first[view]
            ))
        self.assertEqual(first["AER_OCC"][0]["timestamp_ns"], 1000)
        self.assertEqual(first["AER_OCC"][0]["transform_guard_valid"], True)
        self.assertEqual(first["AER_RET"][0]["occurrence_timestamp_ns"], 1000)
        self.assertEqual(first["AER_RET"][0]["derived_retire_timestamp_ns"], 1002)
        self.assertNotIn("retire_timestamp_ns", first["AER_RET"][0])

    def test_manifest_and_projection_cannot_claim_native_aer_payload(self):
        sources, outcomes = fixture_rows()
        authority_bytes = {
            "mapping": b"m", "source_registry": b"s", "pose_stream": b"p",
            "native_transport_receipt": b"n", "rtl": b"r",
        }
        manifest = manifest_for(
            canonical_jsonl_bytes(sources), canonical_jsonl_bytes(outcomes),
            authority_bytes, len(sources),
        )
        manifest["projection"]["aer_projection_semantics"] = "AER_PAYLOAD"
        with self.assertRaises(BridgeValidationError):
            validate_manifest(manifest)


class NativeBitmapTests(unittest.TestCase):
    def test_delivered_coordinates_follow_native_lane_row_and_column(self):
        valid = (
            delivered(1, 4, 0, 1, 0, 0),
            delivered(2, 11, 0, 1, 0, 3),
            delivered(3, 0, 0, 1, 1, 0),
            delivered(4, 15, 0, 1, 1, 3),
        )
        for row in valid:
            self.assertIs(validate_transport_outcome(row), row)

        invalid = (
            delivered(1, 4, 0, 1, 1, 0),
            delivered(1, 0, 0, 1, 0, 0),
            delivered(1, 4, 0, 1, 0, 1),
            delivered(1, 4, 0, 1, 2, 0),
        )
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(BridgeValidationError):
                validate_transport_outcome(row)

    def test_overrun_requires_all_native_retire_fields_null(self):
        base = overrun(1, 4, 0)
        for field, value in (
            ("retire_cycle", 1),
            ("retire_native_lane", 0),
            ("retire_col", 0),
        ):
            with self.subTest(field=field), self.assertRaises(BridgeValidationError):
                validate_transport_outcome(dict(base, **{field: value}))

    def test_same_native_lane_cycle_must_share_row(self):
        sources = (
            source_event(1, 0, 1000, 4),
            source_event(2, 1, 1001, 8),
        )
        outcomes = (
            delivered(1, 4, 0, 2, 0, 0),
            delivered(2, 8, 1, 2, 0, 0),
        )
        self._assert_bundle_rejected(sources, outcomes)

    def test_two_native_bitmaps_expand_to_eight_events_in_one_cycle(self):
        source_indices = tuple(range(4, 8)) + tuple(range(0, 4))
        sources = tuple(
            source_event(index + 1, index, 1000, source_index)
            for index, source_index in enumerate(source_indices)
        )
        outcomes = tuple(
            delivered(
                index + 1, source_index, 0, 1,
                0 if source_index // 4 == 1 else 1,
                source_index % 4,
            )
            for index, source_index in enumerate(source_indices)
        )
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            manifest_path, manifest_sha = fixture.write()
            views = load_bridge_bundle(manifest_path, manifest_sha).project()
        self.assertEqual(len(views["AER_RET"]), 8)
        self.assertEqual({row["retire_native_lane"] for row in views["AER_RET"]}, {0, 1})

    def _assert_bundle_rejected(self, sources, outcomes):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            manifest_path, manifest_sha = fixture.write()
            with self.assertRaises(BridgeValidationError):
                load_bridge_bundle(manifest_path, manifest_sha)


class ManifestAuthorityTests(unittest.TestCase):
    def test_manifest_requires_unique_relative_full_sha_opaque_authorities(self):
        sources, outcomes = fixture_rows()
        source_bytes = canonical_jsonl_bytes(sources)
        outcome_bytes = canonical_jsonl_bytes(outcomes)
        authority_bytes = {
            "mapping": b"m", "source_registry": b"s", "pose_stream": b"p",
            "native_transport_receipt": b"n", "rtl": b"r",
        }
        manifest = manifest_for(source_bytes, outcome_bytes, authority_bytes, 4)
        self.assertIs(validate_manifest(manifest), manifest)
        for field in (
            "source_registry_authority", "pose_stream_authority",
            "native_transport_receipt_authority",
        ):
            missing = deepcopy(manifest)
            del missing[field]
            with self.subTest(field=field), self.assertRaises(BridgeValidationError):
                validate_manifest(missing)
        aliased = deepcopy(manifest)
        aliased["pose_stream_authority"]["path"] = aliased[
            "source_registry_authority"
        ]["path"]
        traversal = deepcopy(manifest)
        traversal["source_registry_authority"]["path"] = "../registry"
        partial = deepcopy(manifest)
        partial["native_transport_receipt_authority"]["sha256"] = "a" * 12
        for mutation in (aliased, traversal, partial):
            with self.subTest(mutation=mutation), self.assertRaises(BridgeValidationError):
                validate_manifest(mutation)

    def test_loader_rejects_each_tampered_opaque_authority(self):
        names = (
            "mapping.txt", "source_registry.bin", "pose_stream.bin",
            "native_transport_receipt.bin", "cluster2_top.v",
        )
        for basename in names:
            with self.subTest(basename=basename), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixture = BundleFixture(root)
                manifest_path, manifest_sha = fixture.write()
                (root / "authority" / basename).write_bytes(b"tampered\n")
                with self.assertRaises(BridgeValidationError):
                    load_bridge_bundle(manifest_path, manifest_sha)


class RemainingInvariantTests(unittest.TestCase):
    def _assert_bundle_rejected(self, sources, outcomes, mutate_manifest=None):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            if mutate_manifest is not None:
                mutate_manifest(fixture.manifest)
            manifest_path, manifest_sha = fixture.write()
            with self.assertRaises(BridgeValidationError):
                load_bridge_bundle(manifest_path, manifest_sha)

    def test_exact_id_coordinate_and_ceil_join(self):
        sources, outcomes = fixture_rows()
        wrong_id = list(outcomes)
        wrong_id[0] = dict(wrong_id[0], event_id=99)
        self._assert_bundle_rejected(sources, wrong_id)
        wrong_coordinate = list(outcomes)
        wrong_coordinate[0] = dict(wrong_coordinate[0], source_index=13, retire_col=1)
        self._assert_bundle_rejected(sources, wrong_coordinate)
        wrong_occurrence = list(outcomes)
        wrong_occurrence[0] = dict(wrong_occurrence[0], occurrence_cycle=4)
        self._assert_bundle_rejected(sources, wrong_occurrence)

    def test_occurrence_fifo_and_integer_retire_time(self):
        sources = (
            source_event(1, 0, 1000, 4),
            source_event(2, 1, 1001, 4),
        )
        reordered = (
            delivered(1, 4, 0, 3, 0, 0),
            delivered(2, 4, 1, 2, 0, 0),
        )
        self._assert_bundle_rejected(sources, reordered)
        early = (delivered(1, 4, 0, 0, 0, 0), delivered(2, 4, 1, 0, 0, 0))
        self._assert_bundle_rejected(sources, early)
        one_source = (source_event(1, 0, 1000, 4),)
        fractional = (delivered(1, 4, 0, 1, 0, 0),)
        self._assert_bundle_rejected(
            one_source, fractional,
            lambda manifest: manifest.update(aer_clock_period_ps=1500),
        )

    def test_ceil_mapping_with_aer_named_clock_fields(self):
        sources = (source_event(1, 0, 1001, 4),)
        outcomes = (delivered(1, 4, 1, 2, 0, 0),)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            fixture.manifest["aer_clock_period_ps"] = 1500
            manifest_path, manifest_sha = fixture.write()
            row = load_bridge_bundle(manifest_path, manifest_sha).project()["AER_RET"][0]
        self.assertEqual(row["occurrence_timestamp_ns"], 1001)
        self.assertEqual(row["derived_retire_timestamp_ns"], 1003)


class SchemaAndIsolationTests(unittest.TestCase):
    def test_schemas_match_runtime_and_native_coordinate_names(self):
        package = Path(__file__).resolve().parents[2] / "benchmarks" / "redred_cluster2_cav_bridge"
        source_schema = json.loads((package / "source_event.schema.json").read_text())
        outcome_schema = json.loads((package / "transport_outcome.schema.json").read_text())
        manifest_schema = json.loads((package / "manifest.schema.json").read_text())
        self.assertEqual(source_schema["properties"]["schema"]["const"], SOURCE_EVENT_SCHEMA)
        self.assertIn("transform_guard_valid", source_schema["required"])
        self.assertEqual(outcome_schema["properties"]["schema"]["const"], TRANSPORT_OUTCOME_SCHEMA)
        self.assertIn("retire_native_lane", outcome_schema["required"])
        self.assertIn("retire_col", outcome_schema["required"])
        self.assertNotIn("retire_lane", outcome_schema["properties"])
        self.assertEqual(manifest_schema["properties"]["schema"]["const"], MANIFEST_SCHEMA)

    def test_contract_has_no_scorer_or_evaluator_import(self):
        contract_path = (
            Path(__file__).resolve().parents[2]
            / "benchmarks" / "redred_cluster2_cav_bridge" / "contract.py"
        )
        tree = ast.parse(contract_path.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("score" in name or "evaluator" in name for name in imported))

    def test_canonical_loader_rejects_noncanonical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            payload = b'{"a": 1}\n'
            path.write_bytes(payload)
            with self.assertRaises(BridgeValidationError):
                load_canonical_json(path, digest(payload))


if __name__ == "__main__":
    unittest.main()
