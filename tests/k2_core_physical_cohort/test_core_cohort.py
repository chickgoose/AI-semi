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
FIXTURES = ROOT / "tests/k2_core_physical_cohort/fixtures"
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

    def prepare_fixture(self, profile_id: str = "5ns") -> tuple[Path, Path]:
        plan = self.temp / f"plan-{profile_id}.json"
        cohort.create_plan(self.archive, plan, profile_id)
        fake_receipt = self.temp / f"server-go-{profile_id}.json"
        fake_document = {"environment_binding_sha256": "a" * 64}
        fake_payload = json.dumps(fake_document, sort_keys=True).encode()
        fake_receipt.write_bytes(fake_payload)
        prepared = self.temp / f"prepared-{profile_id}"
        cohort.prepare_run(
            plan, fake_receipt, prepared,
            verifier=lambda path, contract: (fake_payload, fake_document))
        return plan, prepared

    def test_repository_contract_and_templates_are_exact(self) -> None:
        cohort.CONTRACT_PATH = self.original_contract_path
        _, contract = cohort.validate_contract()
        self.assertEqual(contract["candidate_order"], ["fovea", "cluster2"])
        self.assertEqual(contract["common_conditions"]["clock"]["period_ns"], "5.0")
        self.assertEqual(contract["common_conditions"]["physical"]["core_utilization"], "0.35")
        self.assertEqual(contract["technology"]["setup_liberty"]["pvt"], [1.0, 0.9, 125.0])
        self.assertEqual(contract["technology"]["hold_liberty"]["pvt"], [1.0, 1.1, 0.0])
        self.assertEqual(contract["timing_cohort"], {
            "default_profile_id": "5ns",
            "profiles": {
                "5ns": {"period_ns": "5.0", "waveform_ns": ["0.0", "2.5"]},
                "4ns": {"period_ns": "4.0", "waveform_ns": ["0.0", "2.0"]},
                "3p5ns": {"period_ns": "3.5", "waveform_ns": ["0.0", "1.75"]},
                "2p2ns": {"period_ns": "2.2", "waveform_ns": ["0.0", "1.1"]},
                "1p8ns": {"period_ns": "1.8", "waveform_ns": ["0.0", "0.9"]},
                "1p5ns": {"period_ns": "1.5", "waveform_ns": ["0.0", "0.75"]},
            },
        })

    def test_plan_is_exact_two_rows_and_no_overwrite(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan)
        document = json.loads(plan.read_text())
        self.assertEqual([row["candidate"] for row in document["rows"]],
                         ["fovea", "cluster2"])
        self.assertEqual(document["state"], "PLANNED_NOT_EXECUTED")
        self.assertEqual(document["common_conditions"]["clock"]["period_ns"], "5.0")
        self.assertEqual(document["timing_profile"], {
            "id": "5ns", "period_ns": "5.0", "waveform_ns": ["0.0", "2.5"]})
        self.assertEqual(document["clock_period_ns"], "5.0")
        with self.assertRaises(FileExistsError):
            cohort.create_plan(self.archive, plan)

    def test_every_allowlisted_profile_selects_exact_sdc_for_both_rows(self) -> None:
        expected = {
            "5ns": ("5.0", "2.5"), "4ns": ("4.0", "2.0"),
            "3p5ns": ("3.5", "1.75"), "2p2ns": ("2.2", "1.1"),
            "1p8ns": ("1.8", "0.9"), "1p5ns": ("1.5", "0.75"),
        }
        for profile_id, (period, falling_edge) in expected.items():
            with self.subTest(profile_id=profile_id):
                plan, prepared = self.prepare_fixture(profile_id)
                plan_document = json.loads(plan.read_text())
                self.assertEqual(plan_document["timing_profile"]["id"], profile_id)
                self.assertEqual(plan_document["timing_profile"]["period_ns"], period)
                self.assertEqual(plan_document["clock_period_ns"], period)
                self.assertEqual(
                    plan_document["common_conditions"]["clock"]["waveform_ns"],
                    ["0.0", falling_edge])
                fovea_sdc = (prepared / f"fovea/input/common_{profile_id}.sdc").read_bytes()
                cluster_sdc = (prepared / f"cluster2/input/common_{profile_id}.sdc").read_bytes()
                self.assertEqual(fovea_sdc, cluster_sdc)
                self.assertIn(f"-period {period}".encode(), fovea_sdc)
                self.assertIn(f"-waveform {{0.0 {falling_edge}}}".encode(), fovea_sdc)
                receipt, retained_plan, _ = cohort.validate_prepared(prepared)
                self.assertEqual(receipt["timing_profile"], plan_document["timing_profile"])
                self.assertEqual(receipt["clock_period_ns"], period)
                self.assertEqual(retained_plan["timing_profile"], plan_document["timing_profile"])

    def test_unknown_timing_profile_is_rejected_without_plan(self) -> None:
        output = self.temp / "unknown-plan.json"
        with self.assertRaisesRegex(cohort.CohortError, "unknown timing profile"):
            cohort.create_plan(self.archive, output, "3p0ns")
        self.assertFalse(output.exists())

    def test_resealed_plan_cannot_substitute_period_for_profile(self) -> None:
        plan = self.temp / "plan.json"
        cohort.create_plan(self.archive, plan, "3p5ns")
        document = json.loads(plan.read_text())
        document.pop("document_sha256")
        document["timing_profile"]["period_ns"] = "3.6"
        document["common_conditions"]["clock"]["period_ns"] = "3.6"
        document["common_condition_sha256"] = cohort.condition_sha(
            document["common_conditions"])
        document = cohort.seal(document)
        plan.chmod(0o644)
        plan.write_bytes(cohort.canonical(document))
        with self.assertRaisesRegex(cohort.CohortError, "differs from current immutable contract"):
            cohort.validate_plan(plan)

    def test_resealed_preparation_and_descriptor_profile_mutations_fail(self) -> None:
        _, prepared = self.prepare_fixture("4ns")
        receipt_path = prepared / "PREPARATION_RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("document_sha256")
        receipt["clock_period_ns"] = "3.5"
        receipt_path.chmod(0o644)
        receipt_path.write_bytes(cohort.canonical(cohort.seal(receipt)))
        with self.assertRaisesRegex(cohort.CohortError, "plan/condition binding mismatch"):
            cohort.validate_prepared(prepared)

        _, prepared = self.prepare_fixture("3p5ns")
        receipt_path = prepared / "PREPARATION_RECEIPT.json"
        descriptor_path = prepared / "fovea/input/execution_descriptor.json"
        descriptor = json.loads(descriptor_path.read_text())
        descriptor.pop("document_sha256")
        descriptor["timing_profile"]["period_ns"] = "4.0"
        descriptor_path.chmod(0o644)
        descriptor_payload = cohort.canonical(cohort.seal(descriptor))
        descriptor_path.write_bytes(descriptor_payload)
        receipt = json.loads(receipt_path.read_text())
        receipt.pop("document_sha256")
        receipt["descriptors"]["fovea"]["sha256"] = digest(descriptor_payload)
        receipt_path.chmod(0o644)
        receipt_path.write_bytes(cohort.canonical(cohort.seal(receipt)))
        with self.assertRaisesRegex(cohort.CohortError, "descriptor binding mismatch"):
            cohort.validate_prepared(prepared)

    def test_execution_receipt_profile_substitution_fails_before_artifacts(self) -> None:
        _, prepared = self.prepare_fixture("2p2ns")
        prep = json.loads((prepared / "PREPARATION_RECEIPT.json").read_text())
        _, plan, contract = cohort.validate_prepared(prepared)
        output = prepared / "fovea/genus"
        output.mkdir()
        execution = cohort.seal({
            "schema": "k2_core_physical_execution_receipt_v1",
            "state": "NATIVE_TOOL_COMPLETED_AND_VERIFIED",
            "stage": "genus", "candidate": "fovea",
            "top": contract["candidates"]["fovea"]["top"],
            "prepared_receipt_sha256": digest(
                (prepared / "PREPARATION_RECEIPT.json").read_bytes()),
            "descriptor_sha256": prep["descriptors"]["fovea"]["sha256"],
            "timing_profile": {
                "id": "2p2ns", "period_ns": "1.8", "waveform_ns": ["0.0", "0.9"]},
            "clock_period_ns": "1.8",
            "common_condition_sha256": cohort.condition_sha(plan["common_conditions"]),
            "tool": contract["tools"]["genus"],
            "source_archive_sha256": contract["source_archive"]["sha256"],
            "power_disclosure": "VECTORLESS_DISCLOSED_SCREENING_ONLY_NOT_SIGNOFF",
            "metrics": {}, "artifacts": {},
        })
        receipt_path = output / "EXECUTION_RECEIPT.json"
        receipt_path.write_bytes(cohort.canonical(execution))
        with self.assertRaisesRegex(cohort.CohortError, "execution receipt binding mismatch"):
            cohort.validate_execution_receipt(
                receipt_path, prepared, "fovea", "genus")

    def test_final_seal_records_profile_and_rejects_mixed_profile_receipt(self) -> None:
        _, prepared = self.prepare_fixture("1p8ns")
        _, plan, _ = cohort.validate_prepared(prepared)
        for candidate in ("fovea", "cluster2"):
            for stage in ("genus", "innovus"):
                output = prepared / candidate / stage
                output.mkdir()
                (output / "EXECUTION_RECEIPT.json").write_text("placeholder\n")

        clean_execution = {
            "state": "NATIVE_TOOL_COMPLETED_AND_VERIFIED",
            "timing_profile": plan["timing_profile"],
            "clock_period_ns": "1.8",
        }
        final_path = self.temp / "final.json"
        with mock.patch.object(
                cohort, "validate_execution_receipt", return_value=clean_execution):
            cohort.seal_cohort(prepared, final_path)
        final = json.loads(final_path.read_text())
        self.assertEqual(final["timing_profile"], plan["timing_profile"])
        self.assertEqual(final["clock_period_ns"], "1.8")

        mixed = dict(clean_execution)
        mixed["clock_period_ns"] = "1.5"
        with mock.patch.object(
                cohort, "validate_execution_receipt", return_value=mixed):
            with self.assertRaisesRegex(cohort.CohortError, "profile binding mismatch"):
                cohort.seal_cohort(prepared, self.temp / "mixed-final.json")

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
        self.assertIn("set_interactive_constraint_modes [list core_functional]", innovus)

    def test_common_boundary_drive_is_exact_and_applied_after_mmmc_init(self) -> None:
        innovus = (ROOT / "physical/k2_core_physical_cohort/innovus_core.tcl").read_text()
        init = innovus.index("init_design")
        mode = innovus.index("set_interactive_constraint_modes [list core_functional]")
        drive_clock = innovus.index("set_drive 0 $boundary_clock_ports")
        drive_inputs = innovus.index(
            "set_driving_cell -lib_cell BUFX2 $boundary_nonclock_inputs")
        placement = innovus.index("floorPlan -r")
        self.assertLess(init, mode)
        self.assertLess(mode, drive_clock)
        self.assertLess(drive_clock, drive_inputs)
        self.assertLess(drive_inputs, placement)
        self.assertIn("set boundary_clock_ports [get_ports clk]", innovus)
        self.assertIn("set expected_boundary_nonclock_inputs [get_ports {rst req*}]", innovus)
        self.assertIn("expected_boundary_nonclock_inputs] != 17", innovus)

    def test_final_signal_drc_repair_is_single_bounded_and_fail_closed(self) -> None:
        innovus = (ROOT / "physical/k2_core_physical_cohort/innovus_core.tcl").read_text()
        trim = innovus.rindex("editTrim -nets")
        pre_drc = innovus.index(
            'verify_drc -report "$output/reports/drc_pre_signal_eco.rpt"')
        eco = innovus.index("ecoRoute -fix_drc")
        extraction = innovus.index("extractRC", eco)
        final_connectivity = innovus.index("verifyConnectivity -type all")
        final_drc = innovus.index('verify_drc -report "$output/reports/drc.rpt"')
        self.assertEqual(innovus.count("ecoRoute -fix_drc"), 1)
        self.assertLess(trim, pre_drc)
        self.assertLess(pre_drc, eco)
        self.assertLess(eco, extraction)
        self.assertLess(extraction, final_connectivity)
        self.assertLess(final_connectivity, final_drc)
        self.assertIn("reportCongestion -overflow", innovus)
        self.assertNotIn('reportCongestion >', innovus)

    def test_actual_failed_native_reports_remain_rejected(self) -> None:
        with self.assertRaisesRegex(cohort.CohortError, "no_drive"):
            cohort.require_clean_check_timing(
                FIXTURES / "cluster2_check_timing_no_drive.rpt")
        with self.assertRaisesRegex(cohort.CohortError, "native zero-count"):
            cohort.require_zero_report(
                FIXTURES / "cluster2_drc_m1_short.rpt", "drc")
        failed_log = (FIXTURES / "innovus_impsp_9110.log").read_text()
        self.assertIn("IMPSP-9110", failed_log)
        with self.assertRaisesRegex(cohort.CohortError, "zero-error evidence"):
            cohort.clean_innovus(failed_log, "23.14-s088_1")

    def test_clean_native_check_timing_may_omit_detail_section(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "check_timing.rpt"
            report.write_text(
                "#  Generated by:      Cadence Innovus 23.14-s088_1\n"
                "#  Design:            clean_core\n"
                "#  Command:           check_timing -verbose\n"
                "TIMING CHECK SUMMARY\n"
                "| ideal_clock_waveform | Clock waveform is ideal | 1 |\n"
                "TIMING CHECK IDEAL CLOCKS\n"
                "| core_clk | core_setup_view |\n",
                encoding="utf-8")
            cohort.require_clean_check_timing(
                report, "clean_core", "23.14-s088_1")
            for mutation in (
                    "TIMING CHECK IDEAL CLOCKS\n| core_clk | core_setup_view |\n",
                    "| mystery_warning | unknown | 9 |\n",
                    "no_drive endpoints: 17\n"):
                report.write_text(
                    "#  Generated by:      Cadence Innovus 23.14-s088_1\n"
                    "#  Design:            clean_core\n"
                    "#  Command:           check_timing -verbose\n"
                    "TIMING CHECK SUMMARY\n" + mutation,
                    encoding="utf-8")
                with self.assertRaises(cohort.CohortError):
                    cohort.require_clean_check_timing(
                        report, "clean_core", "23.14-s088_1")

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
