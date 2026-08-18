#!/usr/bin/env python3
"""Fail-closed tests for the actual-RTL public projected extension."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]

import sys
sys.path.insert(0, str(PACKAGE))
import run_public_projected_extension as extension  # noqa: E402


class PublicProjectedExtensionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pins = extension.load_pins()
        configured = Path(os.environ.get(
            "REDRED_UZH_PROJECTION_DIR", "/tmp/redred-uzh-shapes-projection-f59c10e"
        ))
        cls._temporary: tempfile.TemporaryDirectory[str] | None = None
        if configured.is_dir():
            cls.projection_dir = configured
        else:
            bundle = PACKAGE / "public_projected_export.tar.gz"
            if not bundle.is_file():
                raise unittest.SkipTest("pinned projection directory/export bundle unavailable")
            cls._temporary = tempfile.TemporaryDirectory(prefix="a23-public-projection-")
            root = Path(cls._temporary.name)
            with tarfile.open(bundle, "r:gz") as archive:
                for member in archive.getmembers():
                    if member.isfile() and member.name.startswith("inputs/"):
                        payload = archive.extractfile(member)
                        assert payload is not None
                        destination = root / Path(member.name).name
                        destination.write_bytes(payload.read())
            cls.projection_dir = root

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._temporary is not None:
            cls._temporary.cleanup()

    def test_status_lineage_and_non_pooling_are_fixed(self) -> None:
        self.assertEqual(self.pins["status"], "PUBLIC_PROJECTED_EXTENSION")
        self.assertEqual(self.pins["release_status"], "HOLD")
        self.assertEqual(self.pins["selection_status"], "HOLD")
        self.assertFalse(self.pins["canonical_redred_traffic"])
        self.assertFalse(self.pins["official_redred_traffic"])
        self.assertFalse(self.pins["p6_evidence_used"])
        self.assertEqual(self.pins["identity_accounting"], {
            "unique_projected_window_events": 1100,
            "scenario_retimings": 3,
            "pooled_3300_unique_events": False,
        })

    def test_exact_pinned_package_and_identity_order(self) -> None:
        traces, receipt = extension.verify_projection_package(
            self.projection_dir, self.pins,
        )
        self.assertEqual(tuple(traces), extension.SCENARIOS)
        self.assertTrue(all(len(rows) == 1100 for rows in traces.values()))
        self.assertEqual(receipt["conservation"]["projected_events"], 1100)
        identities = [
            [(r["tb_only_event_id"], r["logical_source"], r["polarity"]) for r in traces[name]]
            for name in extension.SCENARIOS
        ]
        self.assertEqual(identities[0], identities[1])
        self.assertEqual(identities[0], identities[2])

    def test_wrong_projection_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a23-bad-receipt-") as temporary:
            root = Path(temporary) / "projection"
            shutil.copytree(self.projection_dir, root)
            path = root / "receipt.json"
            path.chmod(0o644)
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(extension.ExtensionError, "receipt hash"):
                extension.verify_projection_package(root, self.pins)

    def test_wrong_trace_hash_is_rejected(self) -> None:
        scenario = self.pins["scenarios"][0]
        with tempfile.TemporaryDirectory(prefix="a23-bad-hash-") as temporary:
            path = Path(temporary) / scenario["trace_file"]
            path.write_bytes((self.projection_dir / scenario["trace_file"]).read_bytes() + b" ")
            with self.assertRaisesRegex(extension.ExtensionError, "trace hash"):
                extension.parse_trace(path, scenario)

    def test_wrong_trace_count_is_rejected_even_with_matching_hash(self) -> None:
        scenario = copy.deepcopy(self.pins["scenarios"][0])
        with tempfile.TemporaryDirectory(prefix="a23-bad-count-") as temporary:
            path = Path(temporary) / scenario["trace_file"]
            lines = (self.projection_dir / scenario["trace_file"]).read_bytes().splitlines(keepends=True)
            path.write_bytes(b"".join(lines[:-1]))
            scenario["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(extension.ExtensionError, "row count"):
                extension.parse_trace(path, scenario)

    def test_wrong_trace_order_is_rejected_even_with_matching_hash(self) -> None:
        scenario = copy.deepcopy(self.pins["scenarios"][0])
        with tempfile.TemporaryDirectory(prefix="a23-bad-order-") as temporary:
            path = Path(temporary) / scenario["trace_file"]
            lines = (self.projection_dir / scenario["trace_file"]).read_bytes().splitlines(keepends=True)
            lines[0], lines[1] = lines[1], lines[0]
            path.write_bytes(b"".join(lines))
            scenario["trace_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(extension.ExtensionError, "event order"):
                extension.parse_trace(path, scenario)

    def test_published_result_and_export_remain_hold(self) -> None:
        result_path = PACKAGE / "public_projected_result.json"
        publication_path = PACKAGE / "public_projected_publication.json"
        bundle_path = PACKAGE / "public_projected_export.tar.gz"
        if not (result_path.is_file() and publication_path.is_file() and bundle_path.is_file()):
            self.skipTest("actual extension campaign not yet published")
        result = json.loads(result_path.read_text(encoding="ascii"))
        publication = json.loads(publication_path.read_text(encoding="ascii"))
        self.assertEqual(result["status"], "PUBLIC_PROJECTED_EXTENSION")
        self.assertEqual(result["release_status"], "HOLD")
        self.assertEqual(result["selection_status"], "HOLD")
        self.assertFalse(result["p6_evidence_used"])
        self.assertFalse(result["identity_accounting"]["pooled_3300_unique_events"])
        self.assertEqual(publication["result_sha256"], hashlib.sha256(result_path.read_bytes()).hexdigest())
        self.assertEqual(publication["export_bundle_sha256"], hashlib.sha256(bundle_path.read_bytes()).hexdigest())
        for scenario in extension.SCENARIOS:
            hashes = {
                result["owners"][owner]["scenarios"][scenario]["prepared_trace_sha256"]
                for owner in ("a2", "a3")
            }
            self.assertEqual(len(hashes), 1)


if __name__ == "__main__":
    unittest.main()
