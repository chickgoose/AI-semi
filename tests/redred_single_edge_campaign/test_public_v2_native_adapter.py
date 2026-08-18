#!/usr/bin/env python3
"""Focused adversarial tests for the public-projected-v2 native adapter."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks/redred_single_edge_campaign"
PROGRAM = PACKAGE / "public_v2_native_adapter.py"
SCHEMA = PACKAGE / "public_v2_native_adapter.schema.json"


def load_adapter():
    specification = importlib.util.spec_from_file_location(
        "redred_public_v2_native_adapter_test", PROGRAM,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load public-v2 native adapter")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


adapter = load_adapter()


def load_aggregate_gate():
    local = PACKAGE / "aggregate_gate.py"
    designated = Path("/tmp/redred-goal3-a6/benchmarks/redred_single_edge_campaign/aggregate_gate.py")
    path = local if local.is_file() else designated
    specification = importlib.util.spec_from_file_location(
        "redred_public_v2_aggregate_gate_compatibility", path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load aggregate campaign gate")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


class PublicV2NativeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = adapter.validate_tuple()

    def test_exact_native_tuple_passes_without_relabel_or_repack(self) -> None:
        report = self.report
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["adapter_mode"], "READ_ONLY_NATIVE_SCHEMA_NO_RELABEL_NO_REPACK")
        self.assertEqual(report["source_class"], "PUBLIC_PROJECTED_EXTENSION")
        self.assertFalse(report["canonical_redred_traffic"])
        self.assertFalse(report["official_redred_traffic"])
        self.assertFalse(report["p6_evidence_used"])
        self.assertEqual(report["release_status"], "HOLD")
        self.assertEqual(report["selection_status"], "HOLD")
        self.assertFalse(report["claim_boundary"]["archive_extracted_or_repacked"])
        self.assertFalse(report["claim_boundary"]["producer_schema_relabelled"])
        self.assertEqual(report["claim_boundary"]["synthetic_public_pooling"], "FORBIDDEN")

    def test_normalized_report_conforms_to_strict_schema(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="ascii"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(self.report, schema)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(self.report), adapter.REPORT_KEYS)

    def test_exact_closed_inventory_and_native_schema_names_are_preserved(self) -> None:
        report = self.report
        self.assertEqual(report["closed_inventory"], {
            "schema": "a23_public_projected_v2_closed_inventory_v2",
            "entry_count_excluding_manifest": 80,
            "archive_member_count_including_manifest": 81,
            "extra_entries_allowed": False,
            "ordered": True,
        })
        self.assertEqual(report["native_schemas"], {
            "publication": "a23_public_projected_v2_publication_v2",
            "archive_manifest": "a23_public_projected_v2_export_manifest_v2",
            "closed_inventory": "a23_public_projected_v2_closed_inventory_v2",
            "result": "a23_public_projected_v2_result_v2",
            "ordinal": "a23_accept_retire_sequence_ordinals_v2",
        })

    def test_exact_ordinals_and_same_cycle_order_are_consumed(self) -> None:
        ordinal = self.report["ordinal_validation"]
        self.assertTrue(ordinal["accept_and_retire_exact_contiguous"])
        self.assertTrue(ordinal["same_cycle_order_reconstructable"])
        self.assertEqual(ordinal["accepted_counts"], {
            "a2": {"1x": 1019, "64x": 1019, "256x": 906},
            "a3": {"1x": 1019, "64x": 1013, "256x": 817},
        })
        upstream = adapter.load_upstream()
        archive = upstream.stable_read(adapter.ARCHIVE, "test archive")
        members = adapter.archive_members(archive)
        result = upstream.load_json(adapter.RESULT, "test result")
        name = "run/sequences/a2/1x.jsonl"
        lines = members[name].splitlines()
        first = json.loads(lines[0])
        first["accept_sequence_ordinal"] = 1
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":")).encode("ascii")
        members[name] = b"\n".join(lines) + b"\n"
        with self.assertRaisesRegex(
            adapter.PublicV2NativeAdapterError, "not exact and contiguous",
        ):
            adapter.validate_ordinals(upstream, members, result)

    def test_publication_and_archive_raw_mutations_fail_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory(prefix="public-v2-native-mutant-") as temporary:
            root = Path(temporary)
            publication = root / "publication.json"
            archive = root / "archive.tar.gz"
            original_publication = adapter.PUBLICATION.read_bytes()
            publication.write_bytes(original_publication + b" ")
            archive.write_bytes(adapter.ARCHIVE.read_bytes())
            with self.assertRaisesRegex(
                adapter.PublicV2NativeAdapterError, "exact published publication bytes differ",
            ):
                adapter.validate_tuple(publication, archive)
            publication.write_bytes(original_publication)
            archive.write_bytes(adapter.ARCHIVE.read_bytes()[:-1])
            with self.assertRaisesRegex(
                adapter.PublicV2NativeAdapterError, "exact published archive bytes differ",
            ):
                adapter.validate_tuple(publication, archive)

    def test_symlinked_input_and_modified_upstream_validator_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="public-v2-native-links-") as temporary:
            root = Path(temporary)
            publication = root / "publication.json"
            publication.symlink_to(adapter.PUBLICATION)
            with self.assertRaisesRegex(Exception, "symlink"):
                adapter.validate_tuple(publication, adapter.ARCHIVE)
            producer = root / "run.py"
            producer.write_bytes(adapter.PRODUCER.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                adapter.PublicV2NativeAdapterError, "validator bytes differ",
            ):
                adapter.load_upstream(producer)

    def test_report_relabel_unknown_field_and_release_promotion_fail(self) -> None:
        for mutation in (
            {"canonical_redred_traffic": True},
            {"official_redred_traffic": True},
            {"p6_evidence_used": True},
            {"release_status": "GO"},
            {"selection_status": "GO"},
            {"source_class": "TEAM_DEFINED_SYNTHETIC"},
        ):
            report = copy.deepcopy(self.report)
            report.update(mutation)
            with self.assertRaisesRegex(
                adapter.PublicV2NativeAdapterError, "expands or relabels",
            ):
                adapter.validate_report(report)
        report = copy.deepcopy(self.report)
        report["translated_bundle"] = "forbidden"
        with self.assertRaisesRegex(adapter.PublicV2NativeAdapterError, "keys differ"):
            adapter.validate_report(report)

    def test_primary_reproduction_semantics_and_git_chain_are_exact(self) -> None:
        semantic = self.report["semantic_validation"]
        self.assertTrue(semantic["matched"])
        self.assertEqual(semantic["primary_sha256"], adapter.SEMANTIC_SHA256)
        self.assertEqual(semantic["reproduction_sha256"], adapter.SEMANTIC_SHA256)
        provenance = self.report["git_provenance"]
        self.assertEqual(provenance["payload_commit"], adapter.PAYLOAD_COMMIT)
        self.assertEqual(
            provenance["payload_commit_meaning"],
            "COMMIT_CONTAINING_RESULT_AND_EXPORT_PAYLOADS",
        )
        self.assertFalse(provenance["self_referential_commit_claim"])
        self.assertNotEqual(
            self.report["raw_artifacts"]["result"]["sha256"],
            self.report["raw_artifacts"]["reproduction"]["sha256"],
        )

    def test_validation_does_not_modify_any_producer_artifact(self) -> None:
        paths = (adapter.PUBLICATION, adapter.ARCHIVE, adapter.RESULT, adapter.REPRODUCTION)
        before = [(path.stat().st_size, adapter.sha256(path.read_bytes())) for path in paths]
        adapter.validate_tuple()
        after = [(path.stat().st_size, adapter.sha256(path.read_bytes())) for path in paths]
        self.assertEqual(after, before)

    def test_cli_emits_only_strict_pass_report(self) -> None:
        process = subprocess.run(
            [sys.executable, str(PROGRAM), "evaluate"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["status"], "PASS")
        self.assertNotIn("synthetic_v2", process.stdout)
        self.assertNotIn("redred_single_edge_synthetic_publication_v2", process.stdout)

    def test_normalized_public_view_is_accepted_by_aggregate_gate(self) -> None:
        gate = load_aggregate_gate()
        view = adapter.normalized_view(self.report)
        self.assertEqual(view["schema"], gate.VIEW_SCHEMA)
        self.assertEqual(gate.validate_view(view, "public_v2"), view)
        self.assertEqual(view["campaign_units"]["independent_sample_count"], 1)
        self.assertFalse(view["campaign_units"]["retimings_are_independent_samples"])
        self.assertFalse(view["classification"]["canonical_redred_traffic"])
        self.assertFalse(view["classification"]["official_contest_traffic"])

        process = subprocess.run(
            [sys.executable, str(PROGRAM), "evaluate", "--normalized-view-only"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        emitted = json.loads(process.stdout)
        self.assertEqual(gate.validate_view(emitted, "public_v2"), emitted)


if __name__ == "__main__":
    unittest.main()
