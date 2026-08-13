#!/usr/bin/env python3
"""Non-EDA contract, mutation, and exclusivity tests for the core cohort."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "physical/k2_core_physical_cohort/core_cohort.py"
SPEC = importlib.util.spec_from_file_location("core_cohort", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
cohort = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cohort)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def deterministic_tar(members: list[tuple[str, bytes]]) -> bytes:
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", filename="", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as bundle:
            for name, payload in members:
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o444
                info.mtime = 0
                bundle.addfile(info, io.BytesIO(payload))
    return compressed.getvalue()


class CoreCohortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="k2-core-cohort-")
        self.temp = Path(self.temporary.name)
        self.original_contract_path = cohort.CONTRACT_PATH
        base = json.loads((ROOT / "physical/k2_core_physical_cohort/contract.json").read_text())
        fovea = b"module aer_tx16_trad_rowcol_fovea(input clk,input rst,input [15:0] req,output valid,output [3:0] addr); endmodule\n"
        cluster = b"module aer_tx16_trad_rowcol_fovea_cluster2(input clk,input rst,input [15:0] req,output valid0,output [1:0] row0,output [3:0] col_mask0,output valid1,output [1:0] row1,output [3:0] col_mask1); endmodule\n"
        self.members = [
            ("rtl/ganghee_cluster2/fovea_fixture.v", fovea),
            ("rtl/ganghee_cluster2/cluster_fixture.v", cluster),
        ]
        archive = deterministic_tar(self.members)
        self.archive = self.temp / "raw.tar.gz"
        self.archive.write_bytes(archive)
        base["source_archive"] = {
            "default_path": str(self.archive),
            "sha256": digest(archive),
            "policy": "EXTRACT_ONLY_LISTED_REGULAR_MEMBERS_WITH_EXACT_SHA256",
        }
        base["candidates"]["fovea"]["sources"] = [
            {"member": self.members[0][0], "sha256": digest(fovea)}]
        base["candidates"]["cluster2"]["sources"] = [
            {"member": self.members[1][0], "sha256": digest(cluster)}]
        self.contract = self.temp / "contract.json"
        self.contract.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n")
        cohort.CONTRACT_PATH = self.contract

    def tearDown(self) -> None:
        cohort.CONTRACT_PATH = self.original_contract_path
        self.temporary.cleanup()

    def test_repository_contract_and_templates_are_exact(self) -> None:
        cohort.CONTRACT_PATH = self.original_contract_path
        _, contract = cohort.validate_contract()
        self.assertEqual(contract["candidate_order"], ["fovea", "cluster2"])
        self.assertEqual(contract["common_conditions"]["clock"]["period_ns"], "5.0")
        self.assertEqual(contract["common_conditions"]["physical"]["core_utilization"], "0.35")
        self.assertEqual(contract["technology"]["setup_liberty"]["pvt"], [1.0, 0.9, 125.0])
        self.assertEqual(contract["technology"]["hold_liberty"]["pvt"], [1.0, 1.1, 0.0])

    def test_plan_is_exact_two_rows_and_no_overwrite(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan)
        document = json.loads(plan.read_text())
        self.assertEqual([row["candidate"] for row in document["rows"]],
                         ["fovea", "cluster2"])
        self.assertEqual(document["state"], "PLANNED_NOT_EXECUTED")
        self.assertEqual(document["common_conditions"]["clock"]["period_ns"], "5.0")
        with self.assertRaises(FileExistsError):
            cohort.create_plan(self.archive, plan)

    def test_archive_byte_mutation_fails_closed(self) -> None:
        payload = bytearray(self.archive.read_bytes())
        payload[-1] ^= 1
        self.archive.write_bytes(payload)
        with self.assertRaisesRegex(cohort.CohortError, "archive SHA-256 mismatch"):
            cohort.create_plan(self.archive, self.temp / "plan.json")

    def test_duplicate_archive_member_fails_closed(self) -> None:
        duplicate = deterministic_tar([self.members[0], self.members[0], self.members[1]])
        self.archive.write_bytes(duplicate)
        contract = json.loads(self.contract.read_text())
        contract["source_archive"]["sha256"] = digest(duplicate)
        self.contract.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        with self.assertRaisesRegex(cohort.CohortError, "missing/duplicate/non-file"):
            cohort.create_plan(self.archive, self.temp / "plan.json")

    def test_plan_mutation_breaks_self_hash(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan)
        document = json.loads(plan.read_text())
        document["common_conditions"]["clock"]["period_ns"] = "4.9"
        plan.chmod(0o644)
        plan.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        with self.assertRaisesRegex(cohort.CohortError, "self-hash mismatch"):
            cohort.validate_plan(plan)

    def test_prepare_snapshots_identical_sdc_and_detects_mutation(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan)
        fake_receipt = self.temp / "server-go.json"
        fake_document = {"environment_binding_sha256": "a" * 64}
        fake_payload = json.dumps(fake_document, sort_keys=True).encode()
        fake_receipt.write_bytes(fake_payload)

        def accept(path: Path, contract: dict) -> tuple[bytes, dict]:
            self.assertEqual(path, fake_receipt)
            self.assertEqual(contract["candidate_order"], ["fovea", "cluster2"])
            return fake_payload, fake_document

        prepared = self.temp / "prepared"
        receipt = cohort.prepare_run(plan, fake_receipt, prepared, verifier=accept)
        self.assertTrue(receipt.is_file())
        fovea_sdc = (prepared / "fovea/input/common_5ns.sdc").read_bytes()
        cluster_sdc = (prepared / "cluster2/input/common_5ns.sdc").read_bytes()
        self.assertEqual(fovea_sdc, cluster_sdc)
        self.assertIn(b"-period 5.0", fovea_sdc)
        self.assertIn(b"set_load 0.01", fovea_sdc)
        cohort.validate_prepared(prepared)
        fovea_source = next((prepared / "fovea/input/sources").iterdir())
        fovea_source.chmod(0o644)
        fovea_source.write_bytes(fovea_source.read_bytes() + b"// mutation\n")
        with self.assertRaisesRegex(cohort.CohortError, "prepared source mutated"):
            cohort.validate_prepared(prepared)
        with self.assertRaisesRegex(cohort.CohortError, "already exists"):
            cohort.prepare_run(plan, fake_receipt, prepared, verifier=accept)

    def test_bad_execution_authorization_never_invokes_subprocess(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan)
        fake_receipt = self.temp / "server-go.json"
        fake_document = {"environment_binding_sha256": "b" * 64}
        fake_payload = json.dumps(fake_document, sort_keys=True).encode()
        fake_receipt.write_bytes(fake_payload)
        prepared = self.temp / "prepared"
        cohort.prepare_run(
            plan, fake_receipt, prepared,
            verifier=lambda path, contract: (fake_payload, fake_document))
        with mock.patch.object(cohort.subprocess, "run") as launched:
            with self.assertRaisesRegex(cohort.CohortError, "explicit execution authorization"):
                cohort.execute_stage(prepared, "fovea", "genus", "NO")
            launched.assert_not_called()

    def test_tcl_contract_has_reports_and_no_activity_dependency(self) -> None:
        genus = (ROOT / "physical/k2_core_physical_cohort/genus_core.tcl").read_text()
        innovus = (ROOT / "physical/k2_core_physical_cohort/innovus_core.tcl").read_text()
        for token in ("syn_generic", "syn_map", "syn_opt", "report_area", "write_hdl"):
            self.assertIn(token, genus)
        for token in ("floorPlan -r", "place_opt_design", "clock_opt_design",
                      "ecoChangeCell", "setDontUse BUFX2", "floorplan.machine",
                      "routeDesign", "reportCongestion", "verify_drc", "saveNetlist"):
            self.assertIn(token, innovus)
        self.assertNotIn("read_activity_file", innovus)
        self.assertNotIn("set_interactive_constraint_modes", innovus)

    def test_server_python_38_does_not_require_zip_strict(self) -> None:
        producer = (ROOT / "physical/k2_core_physical_cohort/core_cohort.py").read_text()
        self.assertNotIn("zip(descriptor[\"sources\"], expected_sources, strict=True)",
                         producer)
        self.assertIn("len(descriptor.get(\"sources\", [])) != len(expected_sources)",
                      producer)

    def test_native_stages_use_isolated_work_and_tmp_and_post_pg_hold(self) -> None:
        producer = (ROOT / "physical/k2_core_physical_cohort/core_cohort.py").read_text()
        innovus = (ROOT / "physical/k2_core_physical_cohort/innovus_core.tcl").read_text()
        self.assertIn('env["TMPDIR"] = str(temporary)', producer)
        self.assertIn("subprocess.run(command, cwd=work", producer)
        self.assertIn("setOptMode -fixHoldAllowSetupTnsDegrade true", innovus)
        self.assertIn("for {set hold_iteration 0} {$hold_iteration < 2}", innovus)
        self.assertGreaterEqual(innovus.count("sroute -nets"), 2)
        self.assertGreaterEqual(innovus.count("extractRC"), 2)


if __name__ == "__main__":
    unittest.main()
