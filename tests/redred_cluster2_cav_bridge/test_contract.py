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
    PROJECTION_SCHEMA,
    SOURCE_EVENT_SCHEMA,
    TIMESTAMP_TO_OCCURRENCE_RULE,
    TRANSPORT_OUTCOME_SCHEMA,
    BridgeValidationError,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    load_bridge_bundle,
    load_canonical_json,
    validate_manifest,
    validate_source_event,
    validate_transport_outcome,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_event(event_id, ordinal, timestamp_ns, source_index=3):
    return {
        "schema": SOURCE_EVENT_SCHEMA,
        "event_id": event_id,
        "ordinal": ordinal,
        "timestamp_ns": timestamp_ns,
        "source_index": source_index,
        "polarity": ordinal % 2,
        "window_id": "synthetic-window",
        "is_query": True,
        "sensor_ray": [0.25, -0.5, 1],
        "causal_pose_source_index": 7,
        "event_content_sha256": ("%x" % ((ordinal % 15) + 1)) * 64,
    }


def delivered(event_id, source_index, occurrence_cycle, retire_cycle, lane):
    return {
        "schema": TRANSPORT_OUTCOME_SCHEMA,
        "event_id": event_id,
        "source_index": source_index,
        "occurrence_cycle": occurrence_cycle,
        "outcome": "DELIVERED",
        "retire_cycle": retire_cycle,
        "retire_lane": lane,
    }


def overrun(event_id, source_index, occurrence_cycle):
    return {
        "schema": TRANSPORT_OUTCOME_SCHEMA,
        "event_id": event_id,
        "source_index": source_index,
        "occurrence_cycle": occurrence_cycle,
        "outcome": "OVERRUN",
        "retire_cycle": None,
        "retire_lane": None,
    }


def fixture_rows():
    sources = (
        source_event(11, 0, 1000, 3),
        source_event(12, 1, 1001, 4),
        source_event(13, 2, 1002, 3),
    )
    outcomes = (
        delivered(13, 3, 2, 4, 1),
        delivered(11, 3, 0, 2, 0),
        overrun(12, 4, 1),
    )
    return sources, outcomes


def manifest_for(source_bytes, outcome_bytes, mapping_bytes, rtl_bytes, count):
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
            "sha256": digest(mapping_bytes),
        },
        "rtl_authorities": [{
            "role": "ganghee_cluster2_top",
            "path": "authority/cluster2_top.v",
            "sha256": digest(rtl_bytes),
        }],
        "clock_period_ps": 1000,
        "cycle_zero_timestamp_ns": 1000,
        "timestamp_to_occurrence_cycle_rule": TIMESTAMP_TO_OCCURRENCE_RULE,
        "projection": {
            "schema": PROJECTION_SCHEMA,
            "views": ["RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"],
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
        self.mapping_bytes = b"synthetic exact-ID and clock mapping\n"
        self.rtl_bytes = b"module synthetic_cluster2; endmodule\n"
        self.manifest = manifest_for(
            self.source_bytes,
            self.outcome_bytes,
            self.mapping_bytes,
            self.rtl_bytes,
            len(self.sources),
        )

    def write(self):
        authority = self.root / "authority"
        authority.mkdir()
        (authority / "mapping.txt").write_bytes(self.mapping_bytes)
        (authority / "cluster2_top.v").write_bytes(self.rtl_bytes)
        (self.root / "source_events.jsonl").write_bytes(self.source_bytes)
        (self.root / "transport_outcomes.jsonl").write_bytes(self.outcome_bytes)
        manifest_bytes = canonical_json_bytes(self.manifest)
        manifest_path = self.root / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        return manifest_path, digest(manifest_bytes)


class MinimumContractTests(unittest.TestCase):
    def test_positive_two_stream_join_and_four_views(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary))
            manifest_path, manifest_sha = fixture.write()
            bundle = load_bridge_bundle(manifest_path, manifest_sha)
            views = bundle.project()

        self.assertEqual(tuple(views), (
            "RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"
        ))
        self.assertEqual([row["event_id"] for row in views["RAW4X4_ALL"]], [11, 12, 13])
        self.assertEqual([row["event_id"] for row in views["RAW4X4_MATCHED"]], [11, 13])
        raw_keys = [
            (row["event_id"], row["source_index"])
            for row in views["RAW4X4_MATCHED"]
        ]
        aer_keys = [
            (row["event_id"], row["source_index"])
            for row in views["AER_OCC"]
        ]
        self.assertEqual(raw_keys, aer_keys)
        self.assertEqual([row["retire_timestamp_ns"] for row in views["AER_RET"]], [1002, 1004])

    def test_negative_transport_outcome_cannot_carry_source_sidecar(self):
        mutation = delivered(11, 3, 0, 2, 0)
        for field, value in (
            ("timestamp_ns", 1000),
            ("polarity", 1),
            ("sensor_ray", [0, 0, 1]),
            ("causal_pose_source_index", 7),
        ):
            row = dict(mutation)
            row[field] = value
            with self.subTest(field=field), self.assertRaises(BridgeValidationError):
                validate_transport_outcome(row)


class SchemaAndAuthorityTests(unittest.TestCase):
    def test_source_and_outcome_exact_shapes_are_disjoint(self):
        source = source_event(1, 0, 1000)
        outcome = delivered(1, 3, 0, 1, 0)
        self.assertIs(validate_source_event(source), source)
        self.assertIs(validate_transport_outcome(outcome), outcome)
        with self.assertRaises(BridgeValidationError):
            validate_source_event(dict(source, occurrence_cycle=0))
        with self.assertRaises(BridgeValidationError):
            validate_transport_outcome(dict(outcome, window_id="forbidden"))

    def test_outcome_discriminator_requires_retire_pair_or_two_nulls(self):
        mutations = (
            dict(delivered(1, 3, 0, 1, 0), retire_cycle=None),
            dict(delivered(1, 3, 0, 1, 0), retire_lane=None),
            dict(overrun(1, 3, 0), retire_cycle=1),
            dict(overrun(1, 3, 0), retire_lane=0),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(BridgeValidationError):
                validate_transport_outcome(mutation)

    def test_manifest_binds_two_streams_authorities_clock_rule_and_views(self):
        sources, outcomes = fixture_rows()
        source_bytes = canonical_jsonl_bytes(sources)
        outcome_bytes = canonical_jsonl_bytes(outcomes)
        manifest = manifest_for(source_bytes, outcome_bytes, b"map", b"rtl", 3)
        self.assertIs(validate_manifest(manifest), manifest)
        mutations = []
        no_top = deepcopy(manifest)
        no_top["rtl_authorities"][0]["role"] = "not_the_top"
        mutations.append(no_top)
        wrong_rule = deepcopy(manifest)
        wrong_rule["timestamp_to_occurrence_cycle_rule"] = "floor"
        mutations.append(wrong_rule)
        wrong_views = deepcopy(manifest)
        wrong_views["projection"]["views"][0] = "transport"
        mutations.append(wrong_views)
        partial_sha = deepcopy(manifest)
        partial_sha["mapping_authority"]["sha256"] = "a" * 12
        mutations.append(partial_sha)
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(BridgeValidationError):
                validate_manifest(mutation)

    def test_loader_requires_full_manifest_sha_and_checks_mapping_and_rtl_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BundleFixture(root)
            manifest_path, manifest_sha = fixture.write()
            for authority in (None, "", manifest_sha[:16], "0" * 64):
                with self.subTest(authority=authority), self.assertRaises(BridgeValidationError):
                    load_bridge_bundle(manifest_path, authority)
            (root / "authority" / "mapping.txt").write_bytes(b"changed\n")
            with self.assertRaises(BridgeValidationError):
                load_bridge_bundle(manifest_path, manifest_sha)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = BundleFixture(root)
            manifest_path, manifest_sha = fixture.write()
            (root / "authority" / "cluster2_top.v").write_bytes(b"changed\n")
            with self.assertRaises(BridgeValidationError):
                load_bridge_bundle(manifest_path, manifest_sha)

    def test_json_schemas_match_runtime_versions_and_do_not_mix_sidecar(self):
        package = Path(__file__).resolve().parents[2] / "benchmarks" / "redred_cluster2_cav_bridge"
        source_schema = json.loads((package / "source_event.schema.json").read_text())
        outcome_schema = json.loads((package / "transport_outcome.schema.json").read_text())
        manifest_schema = json.loads((package / "manifest.schema.json").read_text())
        self.assertEqual(source_schema["properties"]["schema"]["const"], SOURCE_EVENT_SCHEMA)
        self.assertEqual(outcome_schema["properties"]["schema"]["const"], TRANSPORT_OUTCOME_SCHEMA)
        self.assertEqual(manifest_schema["properties"]["schema"]["const"], MANIFEST_SCHEMA)
        for forbidden in ("timestamp_ns", "polarity", "sensor_ray", "causal_pose_source_index"):
            self.assertNotIn(forbidden, outcome_schema["properties"])


class JoinInvariantTests(unittest.TestCase):
    def _assert_bundle_rejected(self, sources, outcomes, mutate_manifest=None):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            if mutate_manifest is not None:
                mutate_manifest(fixture.manifest)
            manifest_path, manifest_sha = fixture.write()
            with self.assertRaises(BridgeValidationError):
                load_bridge_bundle(manifest_path, manifest_sha)

    def test_exact_id_partition_rejects_missing_extra_and_duplicate(self):
        sources, outcomes = fixture_rows()
        self._assert_bundle_rejected(sources, outcomes[:-1])
        duplicate = (outcomes[0], outcomes[0], outcomes[2])
        self._assert_bundle_rejected(sources, duplicate)
        extra = (outcomes[0], outcomes[1], delivered(99, 4, 1, 3, 0))
        self._assert_bundle_rejected(sources, extra)

    def test_join_rejects_coordinate_mismatch_and_wrong_ceil_occurrence(self):
        sources, outcomes = fixture_rows()
        coordinate = list(outcomes)
        coordinate[1] = dict(coordinate[1], source_index=9)
        self._assert_bundle_rejected(sources, coordinate)
        occurrence = list(outcomes)
        occurrence[1] = dict(occurrence[1], occurrence_cycle=1)
        self._assert_bundle_rejected(sources, occurrence)

    def test_occurrence_must_not_exceed_retire_and_retire_slots_are_unique(self):
        sources, outcomes = fixture_rows()
        early = list(outcomes)
        early[0] = dict(early[0], retire_cycle=1)
        self._assert_bundle_rejected(sources, early)
        same_slot = list(outcomes)
        same_slot[0] = dict(same_slot[0], retire_cycle=2, retire_lane=0)
        self._assert_bundle_rejected(sources, same_slot)

    def test_per_source_fifo_retire_order_is_strict(self):
        sources, outcomes = fixture_rows()
        reordered = list(outcomes)
        reordered[0] = dict(reordered[0], retire_cycle=2, retire_lane=1)
        reordered[1] = dict(reordered[1], retire_cycle=3, retire_lane=0)
        self._assert_bundle_rejected(sources, reordered)

    def test_fractional_cav_retire_nanoseconds_fail_closed(self):
        sources = (source_event(1, 0, 1000),)
        outcomes = (delivered(1, 3, 0, 1, 0),)
        self._assert_bundle_rejected(
            sources,
            outcomes,
            lambda manifest: manifest.update(clock_period_ps=1500),
        )

    def test_ceil_mapping_accepts_nonintegral_source_delta_cycle(self):
        sources = (source_event(1, 0, 1001),)
        outcomes = (delivered(1, 3, 1, 2, 0),)
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BundleFixture(Path(temporary), sources, outcomes)
            fixture.manifest["clock_period_ps"] = 1500
            manifest_path, manifest_sha = fixture.write()
            bundle = load_bridge_bundle(manifest_path, manifest_sha)
            self.assertEqual(bundle.project()["AER_RET"][0]["retire_timestamp_ns"], 1003)

    def test_source_bitmap_slot_cannot_represent_multiplicity(self):
        sources = (
            source_event(1, 0, 1000, 3),
            source_event(2, 1, 1000, 3),
        )
        outcomes = (
            overrun(1, 3, 0),
            overrun(2, 3, 0),
        )
        self._assert_bundle_rejected(sources, outcomes)


class IsolationTests(unittest.TestCase):
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
