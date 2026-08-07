#!/usr/bin/env python3
"""Candidate-owned tests for A4 immutable physical preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from physical_handoff_preflight import verify_package, verify_stage, verify_stage_record


class PhysicalHandoffPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = Path(__file__).resolve().parents[2]
        cls.manifest = json.loads((cls.project /
            "rtl/candidates/a4_quadtree_fabric/handoff/a4_physical_handoff.json"
        ).read_text(encoding="utf-8"))

    def test_package_hashes_and_source_identity(self) -> None:
        verify_package(self.project, self.manifest, require_clean=False)

    def test_xcelium_record_binds_identity_and_assertions(self) -> None:
        profile = self.manifest["profiles"]["n16"]
        record = {
            "schema": self.manifest["head_stage_record_schema"]["schema"],
            "stage": "xcelium",
            "profile": "n16",
            "candidate_key": profile["candidate_key"],
            "source_commit": self.manifest["source_identity"]["commit_sha"],
            "top": profile["synthesis_top"],
            "filelist_sha256": profile["synthesis_filelist_sha256"],
            "status": "PASS",
            "assertions_passed": profile["expected_assertions"],
        }
        with tempfile.TemporaryDirectory(prefix="a4-preflight-") as directory:
            path = Path(directory) / "xcelium.json"
            path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_stage_record(str(path), digest, "xcelium", "n16", profile,
                                self.manifest)
            record["assertions_passed"] = record["assertions_passed"][:-1]
            path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(SystemExit):
                verify_stage_record(str(path), digest, "xcelium", "n16", profile,
                                    self.manifest)

    def test_n64_is_explicitly_not_functionally_qualified(self) -> None:
        profile = self.manifest["profiles"]["n64"]
        self.assertEqual("CONDITIONAL_SHORTLIST", profile["local_shortlist_decision"])
        self.assertIn("BLOCKED_PENDING", profile["physical_authorization"])
        self.assertEqual("NOT_QUALIFIED", profile["capability_profile"]["status"])

    def test_genus_preflight_requires_explicit_frozen_inputs(self) -> None:
        profile = self.manifest["profiles"]["n16"]
        record = {
            "schema": self.manifest["head_stage_record_schema"]["schema"],
            "stage": "xcelium", "profile": "n16",
            "candidate_key": profile["candidate_key"],
            "source_commit": self.manifest["source_identity"]["commit_sha"],
            "top": profile["synthesis_top"],
            "filelist_sha256": profile["synthesis_filelist_sha256"],
            "status": "PASS", "assertions_passed": profile["expected_assertions"],
        }
        with tempfile.TemporaryDirectory(prefix="a4-genus-preflight-") as directory:
            root = Path(directory)
            xcelium = root / "xcelium.json"
            library = root / "slow_vdd1v0_basicCells.lib"
            config = root / "head-config.json"
            xcelium.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
            library.write_text("candidate preflight fixture\n", encoding="utf-8")
            config.write_text("{}\n", encoding="utf-8")
            digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            args = argparse.Namespace(
                stage="genus", profile="n16", top=profile["synthesis_top"],
                filelist=profile["synthesis_filelist"], num_sources=16, addr_width=16,
                override_local_decision=True, xcelium_pass_record=str(xcelium),
                xcelium_pass_record_sha256=digest(xcelium), defines="SYNTHESIS",
                sdc="constraints/aer_common.sdc", clock_port="clk", reset_port="rst_n",
                input_delay_ns=0.25, output_delay_ns=0.25,
                clock_uncertainty_ns=0.1, output_load_pf=0.01, period_ns=5.0,
                corner="fixture_pvt_rc", library_file=str(library),
                library_sha256=digest(library), tool_config=str(config),
                tool_config_sha256=digest(config), synthesis_mode="genus_screening",
            )
            self.assertIs(profile, verify_stage(args, self.project, self.manifest))


if __name__ == "__main__":
    unittest.main()
