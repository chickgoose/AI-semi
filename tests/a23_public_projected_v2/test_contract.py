#!/usr/bin/env python3
"""Adversarial, fail-closed contracts for public projected v2."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(PACKAGE))
import run as v2  # noqa: E402


class PublicProjectedV2ContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pins = v2.load_pins(permit_unbound=True)

    def test_v2_inventory_is_exact_closed_and_versioned(self) -> None:
        names = v2.expected_export_names()
        self.assertEqual(len(names), 80)
        self.assertEqual(len(set(names)), 80)
        self.assertEqual(tuple(sorted(names)), names)
        self.assertIn("run/instrumentation/a23_public_projected_v2_tb.sv", names)
        for owner in v2.OWNERS:
            for scenario in v2.SCENARIOS:
                self.assertIn(f"run/sequences/{owner}/{scenario}.jsonl", names)

    def test_pins_classification_and_nested_p6_fail_closed(self) -> None:
        for field, replacement in (
            ("canonical_redred_traffic", True),
            ("official_redred_traffic", True),
            ("p6_evidence_used", True),
        ):
            mutant = copy.deepcopy(self.pins)
            mutant[field] = replacement
            with self.assertRaises(v2.PublicV2Error, msg=field):
                v2.validate_pins(mutant, permit_unbound=True)
        mutant = copy.deepcopy(self.pins)
        mutant["commit_provenance"]["p6_evidence_used"] = True
        with self.assertRaisesRegex(v2.PublicV2Error, "P6|p6"):
            v2.validate_pins(mutant, permit_unbound=True)

    def test_symlink_hardlink_and_open_race_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a23-public-v2-source-") as temporary:
            root = Path(temporary)
            original = root / "original"
            original.write_bytes(b"fixed")
            symlink = root / "symlink"
            symlink.symlink_to(original)
            with self.assertRaisesRegex(v2.PublicV2Error, "symlink"):
                v2.stable_read(symlink, "symlink mutant")
            hardlink = root / "hardlink"
            os.link(original, hardlink)
            with self.assertRaisesRegex(v2.PublicV2Error, "hard link"):
                v2.stable_read(original, "hardlink mutant")
            hardlink.unlink()
            replacement = root / "replacement"
            replacement.write_bytes(b"changed")
            def race() -> None:
                replacement.replace(original)
            with self.assertRaisesRegex(v2.PublicV2Error, "changed during read"):
                v2.stable_read(original, "race mutant", after_open=race)

    def test_projection_extra_name_is_rejected(self) -> None:
        configured = Path(os.environ.get("REDRED_UZH_PROJECTION_DIR", str(v2.DEFAULT_PROJECTION)))
        if not configured.is_dir():
            self.skipTest("projection package unavailable")
        with tempfile.TemporaryDirectory(prefix="a23-public-v2-inventory-") as temporary:
            root = Path(temporary) / "projection"
            shutil.copytree(configured, root)
            (root / "unexpected").write_bytes(b"x")
            with self.assertRaisesRegex(v2.PublicV2Error, "seven-name inventory"):
                v2.exact_projection_payloads(root)

    def test_projection_traces_retain_same_1100_identities(self) -> None:
        configured = Path(os.environ.get("REDRED_UZH_PROJECTION_DIR", str(v2.DEFAULT_PROJECTION)))
        if not configured.is_dir():
            self.skipTest("projection package unavailable")
        _, traces, receipt = v2.verify_projection_package(configured, self.pins)
        self.assertEqual(receipt["conservation"]["projected_events"], 1100)
        identities = [
            [(row["tb_only_event_id"], row["logical_source"], row["polarity"]) for row in traces[name]]
            for name in v2.SCENARIOS
        ]
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(identities[0], identities[2])

    def _published(self) -> tuple[dict, dict, dict, bytes] | None:
        paths = (
            PACKAGE / "public_projected_v2_result.json",
            PACKAGE / "public_projected_v2_publication.json",
            PACKAGE / "public_projected_v2_reproduction_result.json",
            PACKAGE / "public_projected_v2_export.tar.gz",
        )
        if not all(path.is_file() for path in paths):
            return None
        return (
            json.loads(paths[0].read_text(encoding="ascii")),
            json.loads(paths[1].read_text(encoding="ascii")),
            json.loads(paths[2].read_text(encoding="ascii")),
            paths[3].read_bytes(),
        )

    def test_published_layers_reject_canonical_official_and_p6_flips(self) -> None:
        published = self._published()
        if published is None:
            self.skipTest("v2 evidence not published yet")
        result, publication, _, archive = published
        manifest = v2.validate_archive_bytes(archive)
        for layer, document, validator in (
            ("result", result, v2.validate_result),
            ("manifest", manifest, v2.validate_manifest),
            ("publication", publication, v2.validate_publication),
        ):
            for field in ("canonical_redred_traffic", "official_redred_traffic", "p6_evidence_used"):
                mutant = copy.deepcopy(document)
                mutant[field] = True
                with self.assertRaises(v2.PublicV2Error, msg=f"{layer}/{field}"):
                    validator(mutant)

    def test_all_nested_p6_bindings_are_false_and_mutation_rejected(self) -> None:
        published = self._published()
        if published is None:
            self.skipTest("v2 evidence not published yet")
        result, publication, _, archive = published
        manifest = v2.validate_archive_bytes(archive)
        self.assertFalse(self.pins["commit_provenance"]["p6_evidence_used"])
        self.assertFalse(result["projection"]["p6_evidence_used"])
        self.assertFalse(result["provenance"]["p6_evidence_used"])
        self.assertFalse(manifest["commit_provenance"]["p6_evidence_used"])
        self.assertFalse(publication["commit_provenance"]["p6_evidence_used"])
        for document, path, validator in (
            (result, ("projection", "p6_evidence_used"), v2.validate_result),
            (result, ("provenance", "p6_evidence_used"), v2.validate_result),
            (manifest, ("commit_provenance", "p6_evidence_used"), v2.validate_manifest),
            (publication, ("commit_provenance", "p6_evidence_used"), v2.validate_publication),
        ):
            mutant = copy.deepcopy(document)
            mutant[path[0]][path[1]] = True
            with self.assertRaises(v2.PublicV2Error):
                validator(mutant)

    def test_semantic_reproduction_and_exact_ordinals(self) -> None:
        published = self._published()
        if published is None:
            self.skipTest("v2 evidence not published yet")
        result, publication, reproduction, archive = published
        v2.validate_result(result)
        v2.validate_result(reproduction)
        self.assertEqual(v2.semantic_sha256(result), v2.semantic_sha256(reproduction))
        self.assertEqual(publication["semantic_sha256"], v2.semantic_sha256(result))
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for owner in v2.OWNERS:
                for scenario in v2.SCENARIOS:
                    member = bundle.extractfile(f"run/sequences/{owner}/{scenario}.jsonl")
                    self.assertIsNotNone(member)
                    rows = [json.loads(line) for line in member.read().splitlines()]
                    accepted = result["owners"][owner]["scenarios"][scenario]["accepted"]
                    self.assertEqual(len(rows), accepted)
                    self.assertEqual([row["accept_sequence_ordinal"] for row in rows], list(range(accepted)))
                    self.assertEqual([row["retire_sequence_ordinal"] for row in rows], list(range(accepted)))

    def test_archive_mutations_fail_closed(self) -> None:
        published = self._published()
        if published is None:
            self.skipTest("v2 evidence not published yet")
        _, _, _, archive = published
        with self.assertRaises(v2.PublicV2Error):
            v2.validate_archive_bytes(archive[:-32])
        manifest = v2.validate_archive_bytes(archive)
        mutant = copy.deepcopy(manifest)
        mutant["inventory"]["ordered_names"].append("extra")
        with self.assertRaises(v2.PublicV2Error):
            v2.validate_manifest(mutant)

    def test_publication_binds_payload_commit_blobs(self) -> None:
        published = self._published()
        if published is None:
            self.skipTest("v2 evidence not published yet")
        result, publication, reproduction, archive = published
        v2.validate_publication(publication)
        commit = publication["commit_provenance"]["publication_commit"]
        result_rel = "tests/a23_public_projected_v2/public_projected_v2_result.json"
        export_rel = "tests/a23_public_projected_v2/public_projected_v2_export.tar.gz"
        self.assertEqual(hashlib.sha256(v2.git_bytes(commit, result_rel)).hexdigest(), publication["result_sha256"])
        self.assertEqual(hashlib.sha256(v2.git_bytes(commit, export_rel)).hexdigest(), publication["export_bundle_sha256"])


if __name__ == "__main__":
    unittest.main()
