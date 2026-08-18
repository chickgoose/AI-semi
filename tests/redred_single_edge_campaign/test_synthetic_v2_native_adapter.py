#!/usr/bin/env python3
"""Fail-closed tests for the producer-native synthetic-v2 campaign view."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_single_edge_campaign"
PROGRAM = PACKAGE / "synthetic_v2_native_adapter.py"
SCHEMA = PACKAGE / "synthetic_v2_native_view.schema.json"
PUBLICATION = ROOT / "tests/a23_single_edge_synthetic_v2/synthetic_v2_publication.json"
RESULT = ROOT / "tests/a23_single_edge_synthetic_v2/synthetic_v2_result.json"
ARCHIVE = ROOT / "tests/a23_single_edge_synthetic_v2/synthetic_v2_export.tar.gz"
EXPECTED_NORMALIZED_SHA256 = "86c48c897fdc74fed7869caed7506906ca74e23e9ab400c0f15c157c25aea917"


def load_adapter():
    specification = importlib.util.spec_from_file_location(
        "redred_synthetic_v2_native_adapter", PROGRAM,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load synthetic native adapter")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


adapter = load_adapter()


def load_aggregate_gate():
    program = PACKAGE / "aggregate_gate.py"
    if not program.is_file():
        program = Path(
            "/tmp/redred-goal3-a6/benchmarks/redred_single_edge_campaign/aggregate_gate.py"
        )
    if not program.is_file():
        return None
    specification = importlib.util.spec_from_file_location(
        "redred_campaign_aggregate_compatibility", program,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load aggregate gate")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


aggregate_gate = load_aggregate_gate()


def raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strict_json(data: bytes):
    return adapter.load_json_bytes(data, "test native JSON")


class SyntheticV2NativeAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.before = {
            path: (path.stat().st_size, path.stat().st_mtime_ns, raw_sha(path))
            for path in (PUBLICATION, RESULT, ARCHIVE)
        }
        cls.view = adapter.evaluate(ROOT)
        cls.after = {
            path: (path.stat().st_size, path.stat().st_mtime_ns, raw_sha(path))
            for path in (PUBLICATION, RESULT, ARCHIVE)
        }
        snapshots = {
            relative: (ROOT / relative).read_bytes()
            for relative in adapter.EXPECTED_ARTIFACTS
        }
        verifier, helper, cls.publication_inventory = adapter.verify_git_provenance(
            ROOT, snapshots,
        )
        cls.native = adapter.load_pinned_native(verifier, helper)
        cls.manifest, cls.payload = cls.native.read_archive(ARCHIVE)
        cls.publication = strict_json(PUBLICATION.read_bytes())
        cls.result = strict_json(RESULT.read_bytes())

    def test_exact_published_tuple_is_verified_before_view(self) -> None:
        self.assertEqual(self.view["schema"], adapter.VIEW_SCHEMA)
        self.assertEqual(self.view["status"], "PASS_NATIVE_VERIFIED")
        self.assertEqual(
            self.view["native_verification"]["verifier_execution"],
            "PINNED_GIT_BYTES",
        )
        self.assertEqual(
            self.view["native_verification"]["verifier_commit"],
            adapter.PACKAGE_COMMIT,
        )
        self.assertEqual(
            self.view["native_verification"]["result"],
            {
                "status": adapter.NATIVE_STATUS,
                "archive_sha256": adapter.EXPECTED_ARTIFACTS[
                    adapter.ARCHIVE_PATH
                ]["sha256"],
                "archive_size_bytes": 12279031,
                "inventory_entry_count": 1520,
                "semantic_digest_sha256":
                    "1623326f40fa5872b3b365c8e9c2b2850e93c6f4ad0c72e9812c16613559b01a",
            },
        )
        self.assertEqual(self.before, self.after)

    def test_native_documents_schemas_paths_and_bytes_are_not_repacked(self) -> None:
        documents = self.view["native_documents"]
        self.assertEqual(documents["publication"], self.publication)
        self.assertEqual(documents["v2_result"], self.result)
        self.assertEqual(documents["export_manifest"], self.manifest)
        self.assertEqual(
            adapter.pretty(documents["primary_result"]), self.payload["primary/result.json"],
        )
        self.assertEqual(
            adapter.pretty(documents["reproduction_result"]),
            self.payload["reproduction/result.json"],
        )
        artifacts = self.view["native_artifacts"]
        self.assertEqual(artifacts["publication"]["path"], adapter.PUBLICATION_PATH)
        self.assertEqual(artifacts["result"]["path"], adapter.RESULT_PATH)
        self.assertEqual(artifacts["archive"]["path"], adapter.ARCHIVE_PATH)
        self.assertEqual(artifacts["archive"]["manifest_member"], adapter.MANIFEST_MEMBER)
        self.assertEqual(artifacts["archive"]["result_member"], adapter.RESULT_MEMBER)
        self.assertEqual(artifacts["archive"]["manifest_schema"], adapter.NATIVE_MANIFEST_SCHEMA)
        self.assertFalse(self.view["claim_boundary"]["archive_repacked"])
        self.assertFalse(self.view["claim_boundary"]["traffic_relabeled"])

    def test_ordered_normalization_preserves_metrics_and_ordinal_sidecars(self) -> None:
        normalized = self.view["normalized"]
        self.assertEqual(normalized["owner_order"], ["a2", "a3"])
        self.assertEqual(len(normalized["traffic_run_order"]), 50)
        expected_totals = {
            "a2": {
                "accepted": 104046, "count2_commits": 26953,
                "fixed_window_cycles": 115968, "fixed_window_retired": 103940,
                "generated": 106416, "retired": 104046, "source_overrun": 2370,
            },
            "a3": {
                "accepted": 93645, "count2_commits": 22284,
                "fixed_window_cycles": 115968, "fixed_window_retired": 93548,
                "generated": 106416, "retired": 93645, "source_overrun": 12771,
            },
        }
        archive_rows = {row["path"]: row for row in self.manifest["inventory"]}
        for owner in normalized["owners"]:
            owner_id = owner["owner"]
            self.assertEqual(owner["primary_aggregate"]["totals"], expected_totals[owner_id])
            self.assertEqual(owner["primary_aggregate"], owner["reproduction_aggregate"])
            self.assertEqual(
                [run["trace"] for run in owner["runs"]],
                normalized["traffic_run_order"],
            )
            for run in owner["runs"]:
                primary = run["primary"]
                reproduction = run["reproduction"]
                self.assertEqual(primary["metrics"], reproduction["metrics"])
                self.assertEqual(
                    primary["ordinal_evidence"]["accepted_ordinal_count"],
                    primary["metrics"]["accepted"],
                )
                self.assertEqual(
                    primary["ordinal_evidence"]["retired_ordinal_count"],
                    primary["metrics"]["retired"],
                )
                for campaign in (primary, reproduction):
                    ordinal = campaign["ordinal_csv"]
                    relative = ordinal["native_relative_path"]
                    self.assertEqual(ordinal["sha256"], archive_rows[relative]["sha256"])
                    self.assertEqual(
                        hashlib.sha256(self.payload[relative]).hexdigest(), ordinal["sha256"],
                    )
                    header = self.payload[relative].splitlines()[0]
                    self.assertEqual(
                        header,
                        b"owner,trace,tb_event_id,logical_source,occurrence_cycle,"
                        b"accept_cycle,accept_ordinal,retire_cycle,retire_ordinal,event_state",
                    )
        self.assertEqual(self.view["normalized_sha256"], EXPECTED_NORMALIZED_SHA256)
        adapter.validate_view(self.view)

    def test_git_provenance_is_non_circular_reachable_and_byte_exact(self) -> None:
        provenance = self.view["normalized"]["provenance"]
        self.assertEqual(provenance["publication_commit"], adapter.PUBLICATION_COMMIT)
        self.assertEqual(provenance["publication_tree"], adapter.PUBLICATION_TREE)
        self.assertEqual(provenance["git_object_format"], "sha1")
        self.assertEqual(provenance["git_replace_objects"], "DISABLED")
        self.assertEqual(provenance["git_alternate_object_stores"], "FORBIDDEN")
        self.assertEqual(
            provenance["package"]["package_commit"], adapter.PACKAGE_COMMIT,
        )
        self.assertEqual(
            provenance["package"]["source_commit"], adapter.SOURCE_COMMIT,
        )
        self.assertEqual(
            provenance["package"]["integration_commit"], adapter.INTEGRATION_COMMIT,
        )
        self.assertEqual(len(provenance["source_inventory"]), 11)
        self.assertEqual(len(provenance["integration_inventory"]), 11)
        self.assertGreater(len(provenance["package_input_inventory"]), 20)
        for row in (
            provenance["publication_inventory"]
            + provenance["source_inventory"]
            + provenance["integration_inventory"]
            + provenance["package_input_inventory"]
        ):
            self.assertRegex(row["git_blob_oid"], r"^[0-9a-f]{40}$")
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertIs(type(row["size_bytes"]), int)
            self.assertGreater(row["size_bytes"], 0)

    def test_claims_remain_native_scoped_and_all_release_gates_hold(self) -> None:
        identity = self.view["normalized"]["native_identity"]
        self.assertEqual(identity["publication_schema"], adapter.NATIVE_PUBLICATION_SCHEMA)
        self.assertEqual(identity["result_schema"], adapter.NATIVE_RESULT_SCHEMA)
        self.assertEqual(identity["evidence_class"], adapter.NATIVE_EVIDENCE_CLASS)
        self.assertEqual(
            self.view["native_documents"]["publication"]["canonical_campaign_status"],
            "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
        )
        boundary = self.view["claim_boundary"]
        for field in (
            "official_contest_claimed", "physical_claimed", "power_claimed",
            "selection_claimed", "release_claimed", "archive_repacked",
            "traffic_relabeled",
        ):
            self.assertIs(boundary[field], False)
        self.assertEqual(
            self.view["normalized"]["qualification"],
            {
                "hardened_synthetic_single_edge_RTL": "PASS",
                "canonical_campaign": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
                "physical": "HOLD", "power": "HOLD", "CDC_RDC": "HOLD",
            },
        )

    def test_strict_aggregate_view_is_compatible_but_policy_held(self) -> None:
        if aggregate_gate is None:
            self.skipTest("aggregate gate is merged separately")
        common = adapter.campaign_normalized_view(self.view)
        self.assertEqual(
            aggregate_gate.validate_view(common, "synthetic_v2"), common,
        )
        self.assertEqual(
            common["schema"], "redred_single_edge_campaign_normalized_view_v1",
        )
        self.assertEqual(common["shared_gates"]["native_tuple_integrity"], "PASS")
        self.assertEqual(common["shared_gates"]["canonical_campaign_policy"], "HOLD")
        self.assertEqual(common["candidates"]["A2"]["gate_status"], "HOLD")
        self.assertEqual(common["candidates"]["A3"]["gate_status"], "HOLD")
        self.assertEqual(
            common["verification"]["source_result_sha256"],
            adapter.EXPECTED_ARTIFACTS[adapter.RESULT_PATH]["sha256"],
        )
        self.assertEqual(
            common["verification"]["source_publication_sha256"],
            adapter.EXPECTED_ARTIFACTS[adapter.PUBLICATION_PATH]["sha256"],
        )
        self.assertEqual(
            common["claims"],
            {"official": False, "physical": False, "power": False, "release": False},
        )

    def test_duplicate_keys_symlinks_hardlinks_and_raw_substitution_fail_closed(self) -> None:
        with self.assertRaisesRegex(adapter.SyntheticNativeAdapterError, "duplicate JSON key"):
            adapter.load_json_bytes(b'{"schema":"one","schema":"two"}', "attack")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"bytes")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(adapter.SyntheticNativeAdapterError, "non-symlink"):
                adapter.stable_file(link, "symlink attack")
            hard = root / "hard"
            os.link(target, hard)
            with self.assertRaisesRegex(adapter.SyntheticNativeAdapterError, "hardlinked"):
                adapter.stable_file(target, "hardlink attack")
        pristine = {
            relative: (ROOT / relative).read_bytes()
            for relative in adapter.EXPECTED_ARTIFACTS
        }
        attacks = {
            adapter.PUBLICATION_PATH: pristine[adapter.PUBLICATION_PATH] + b" ",
            adapter.RESULT_PATH: pristine[adapter.RESULT_PATH].replace(
                b'"combined_actual_full50_executions": 200',
                b'"combined_actual_full50_executions": 201', 1,
            ),
            adapter.ARCHIVE_PATH: pristine[adapter.ARCHIVE_PATH] + b"trailing-data",
        }
        gzip_header = bytearray(pristine[adapter.ARCHIVE_PATH])
        gzip_header[4] ^= 1
        attacks["gzip-header"] = bytes(gzip_header)
        for target, data in attacks.items():
            snapshots = copy.deepcopy(pristine)
            relative = adapter.ARCHIVE_PATH if target == "gzip-header" else target
            snapshots[relative] = data
            with self.subTest(target=target), self.assertRaisesRegex(
                adapter.SyntheticNativeAdapterError, "published native bytes differ",
            ):
                adapter.verify_git_provenance(ROOT, snapshots)

    def test_cross_owner_or_ordinal_mapping_mutation_is_rejected(self) -> None:
        mutated = copy.deepcopy(self.result)
        rows = mutated["sequence_evidence"]["primary_full50_runs"]
        rows[0], rows[1] = rows[1], rows[0]
        native_report = self.view["native_verification"]["result"]
        with self.assertRaisesRegex(
            adapter.SyntheticNativeAdapterError, "owner/trace order differs",
        ):
            adapter.build_normalized(
                ROOT, self.publication, mutated, self.manifest, self.payload,
                native_report, self.publication_inventory,
            )
        tampered_view = copy.deepcopy(self.view)
        tampered_view["normalized"]["owner_order"].reverse()
        with self.assertRaisesRegex(adapter.SyntheticNativeAdapterError, "digest differs"):
            adapter.validate_view(tampered_view)

    def test_native_verifier_failure_cannot_emit_a_normalized_view(self) -> None:
        broken = types.SimpleNamespace(
            validate_reopened=mock.Mock(side_effect=RuntimeError("native rejected")),
        )
        with mock.patch.object(adapter, "load_pinned_native", return_value=broken):
            with self.assertRaisesRegex(
                adapter.SyntheticNativeAdapterError, "native verification failed",
            ):
                adapter.evaluate(ROOT)
        self.assertEqual(broken.validate_reopened.call_count, 1)

    def test_schema_pin_and_cli_output_are_deterministic_and_exclusive(self) -> None:
        self.assertEqual(raw_sha(SCHEMA), adapter.VIEW_SCHEMA_SHA256)
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], adapter.VIEW_SCHEMA)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "view.json"
            completed = subprocess.run(
                [sys.executable, str(PROGRAM), "evaluate", "--campaign-view",
                 "--output", str(output)],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            emitted = strict_json(output.read_bytes())
            if aggregate_gate is not None:
                self.assertEqual(
                    aggregate_gate.validate_view(emitted, "synthetic_v2"), emitted,
                )
            else:
                self.assertEqual(emitted["schema"], adapter.CAMPAIGN_VIEW_SCHEMA)
            with self.assertRaisesRegex(
                adapter.SyntheticNativeAdapterError, "output already exists",
            ):
                adapter.write_new(output, b"replacement")


if __name__ == "__main__":
    unittest.main()
