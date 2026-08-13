from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "physical/k2_w2_qualifier/qualify_ganghee_golden.py"
ARCHIVE = Path(os.environ.get(
    "K2_GANGHEE_GOLDEN_ARCHIVE", "/tmp/ganghee-pnr-golden-20260813.tar.gz"))
SPEC = importlib.util.spec_from_file_location("k2_w2_ganghee_golden", PARSER)
assert SPEC and SPEC.loader
GOLDEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GOLDEN)


class GangheeGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ARCHIVE.is_file():
            raise RuntimeError(
                f"authoritative fixture is required (set K2_GANGHEE_GOLDEN_ARCHIVE): {ARCHIVE}")
        cls.archive_data, _ = GOLDEN.stable_read(ARCHIVE)
        if GOLDEN.sha256(cls.archive_data) != GOLDEN.ARCHIVE_SHA256:
            raise RuntimeError("authoritative fixture SHA-256 mismatch")
        cls.members = GOLDEN.extract_members(cls.archive_data)

    def period(self, members: dict[str, bytes], key: str) -> dict:
        return GOLDEN.analyze_members(members)["periods"][key]

    @staticmethod
    def replace(members: dict[str, bytes], path: str, old: bytes, new: bytes) -> dict[str, bytes]:
        mutated = dict(members)
        original = mutated[path]
        if original.count(old) != 1:
            raise AssertionError(f"mutation anchor must occur exactly once: {path}: {old!r}")
        mutated[path] = original.replace(old, new, 1)
        return mutated

    def test_authoritative_archive_pin_inventory_and_all_period_results(self) -> None:
        receipt = GOLDEN.qualify_archive(ARCHIVE)
        self.assertEqual(receipt["archive"]["sha256"], GOLDEN.ARCHIVE_SHA256)
        self.assertEqual(receipt["archive"]["member_count"], 302)
        self.assertEqual(receipt["summary"], {"period_count": 14, "pass": 0, "fail": 14})
        self.assertEqual(receipt["status"], "AUTHORITATIVE_RAW_FIXTURE_FAIL")
        expected_keys = {
            f"{design}@{period}ns"
            for design, row in GOLDEN.EXPECTED_DESIGNS.items()
            for period in row["periods"]
        }
        self.assertEqual(set(receipt["periods"]), expected_keys)
        self.assertTrue(all(row["status"] == "FAIL" for row in receipt["periods"].values()))
        self.assertEqual(receipt["claim_boundary"]["physical_campaign_qualification"], "HOLD")
        self.assertEqual(GOLDEN.canonical(receipt),
                         GOLDEN.canonical(GOLDEN.qualify_archive(ARCHIVE)))

    def test_real_period_numeric_semantics_are_complete_and_stable(self) -> None:
        analysis = GOLDEN.analyze_members(self.members)
        expected_late = {
            "fovea_buffered@0.8ns": -0.349, "fovea_buffered@1.0ns": -0.301,
            "fovea_buffered@1.2ns": -0.128, "fovea_buffered@1.4ns": 0.023,
            "fovea_buffered@1.6ns": 0.022, "fovea_buffered@1.8ns": 0.042,
            "fovea_buffered@2.0ns": 0.005, "fovea_buffered@2.2ns": 0.058,
            "fovea_buffered@2.5ns": 0.107,
            "cluster2_buffered@0.8ns": -0.096, "cluster2_buffered@1.0ns": 0.012,
            "cluster2_buffered@1.3ns": 0.016, "cluster2_buffered@1.6ns": 0.253,
            "cluster2_buffered@2.0ns": 0.652,
        }
        for key, expected in expected_late.items():
            with self.subTest(period=key):
                row = analysis["periods"][key]
                observed = row["gates"]["timing"]["evidence"]["innovus_late"][
                    "wns_ns_from_worst_path"]
                self.assertEqual(observed, expected)
                self.assertIn("tns_not_reported", row["gates"]["timing"]["diagnostics"])
                self.assertIn("violation_count_not_reported", row["gates"]["timing"]["diagnostics"])
                no_drive = row["gates"]["constraint_coverage"]["evidence"]["no_drive"]
                self.assertEqual(no_drive, 19 if key.startswith("fovea") else 20)
                self.assertEqual(row["gates"]["connectivity"]["status"], "FAIL")
                self.assertIn("technology_inputs_unhashed",
                              row["gates"]["provenance"]["diagnostics"])
                self.assertGreater(row["gates"]["innovus_errors"]["evidence"][
                    "severity_error_lines"], 0)

    def test_archive_byte_mutation_is_rejected_before_interpretation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-pin-") as directory:
            path = Path(directory) / "mutated.tar.gz"
            path.write_bytes(self.archive_data + b"x")
            with self.assertRaisesRegex(GOLDEN.GoldenQualificationError, "SHA-256 mismatch"):
                GOLDEN.qualify_archive(path)

    def test_real_report_provenance_error_and_clean_exit_mutations_fail(self) -> None:
        base = "synth/pnr/resynth_fovea_buffered"
        timing = f"{base}/aer_fovea_buffered_1.6_setup_timing.rpt"
        mutated = self.replace(self.members, timing, b"Cadence Innovus 23.14-s088_1",
                               b"Cadence Innovus 99.99-corrupt")
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertIn("setup_timing:tool_version_mismatch",
                      row["gates"]["provenance"]["diagnostics"])

        genus_log = f"{base}/genus_1.6.log"
        mutated = self.replace(self.members, genus_log, b"Normal exit.",
                               b"**FATAL: injected\nAbnormal exit.")
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertIn("genus_log:severity_errors=1", row["gates"]["genus_errors"]["diagnostics"])
        self.assertIn("genus_clean:terminal_marker_missing",
                      row["gates"]["clean_exit"]["diagnostics"])

    def test_real_timing_and_constraint_mutations_fail_with_exact_diagnostics(self) -> None:
        base = "synth/pnr/resynth_fovea_buffered"
        setup = f"{base}/aer_fovea_buffered_1.6_setup_timing.rpt"
        mutated = self.replace(self.members, setup, b"Path 1: MET Setup", b"Path 1: VIOLATED Setup")
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertIn("innovus_setup:negative_or_violated_wns",
                      row["gates"]["timing"]["diagnostics"])

        check = f"{base}/aer_fovea_buffered_1.6_check_timing.rpt"
        anchor = b"| no_drive             | No drive assertion      |       19 |"
        replacement = (anchor + b"\n     | no_load              | No load assertion       |        1 |")
        mutated = self.replace(self.members, check, anchor, replacement)
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertEqual(row["gates"]["constraint_coverage"]["evidence"]["no_load"], 1)
        self.assertIn("innovus_check_timing:no_load=1",
                      row["gates"]["constraint_coverage"]["diagnostics"])

        mutated = self.replace(self.members, check, b"|       19 |", b"|        0 |")
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertIn("innovus_check_timing:no_drive_summary_detail_mismatch=0:19",
                      row["gates"]["constraint_coverage"]["diagnostics"])

    def test_real_drc_antenna_and_icg_mutations_fail(self) -> None:
        base = "synth/pnr/resynth_cluster2_buffered"
        drc = f"{base}/aer_cluster2_buffered_1.6_drc.rpt"
        mutated = self.replace(self.members, drc, b"No DRC violations were found",
                               b"DRC violations were found: 1")
        row = self.period(mutated, "cluster2_buffered@1.6ns")
        self.assertIn("innovus_drc:zero_summary_missing_or_contradicted",
                      row["gates"]["drc"]["diagnostics"])

        mutated = dict(self.members)
        mutated[drc] += b"\nDRC violation count: 1\nNo DRC violations were found\n"
        row = self.period(mutated, "cluster2_buffered@1.6ns")
        self.assertIn("innovus_drc:zero_summary_missing_or_contradicted",
                      row["gates"]["drc"]["diagnostics"])

        antenna = f"{base}/aer_cluster2_buffered_1.6_antenna.rpt"
        mutated = self.replace(self.members, antenna, b"No Violations Found", b"Violations Found: 1")
        row = self.period(mutated, "cluster2_buffered@1.6ns")
        self.assertIn("innovus_antenna:zero_summary_missing_or_contradicted",
                      row["gates"]["antenna"]["diagnostics"])

        netlist = f"{base}/aer_cluster2_buffered_1.6_netlist.v"
        old = (b"module RC_CG_MOD(enable, ck_in, ck_out, test);\n"
               b"  input enable, ck_in, test;\n"
               b"  output ck_out;\n"
               b"  wire enable, ck_in, test;\n"
               b"  wire ck_out;\n"
               b"  TLATNTSCAX2 RC_CGIC_INST")
        new = old.replace(b"TLATNTSCAX2", b"TLATNTSCAX3")
        mutated = self.replace(self.members, netlist, old, new)
        row = self.period(mutated, "cluster2_buffered@1.6ns")
        self.assertIn("scan_icg:icg_inventory_mismatch", row["gates"]["scan_icg"]["diagnostics"])

    def test_missing_real_report_is_fail_closed(self) -> None:
        mutated = dict(self.members)
        path = ("synth/pnr/resynth_cluster2_buffered/"
                "aer_cluster2_buffered_1.6_setup_timing.rpt")
        del mutated[path]
        row = self.period(mutated, "cluster2_buffered@1.6ns")
        self.assertIn(f"missing_artifact:{path}", row["gates"]["provenance"]["diagnostics"])
        self.assertIn("innovus_setup:missing", row["gates"]["timing"]["diagnostics"])

    def test_cli_preserves_fail_receipt_and_output_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-cli-") as directory:
            output = Path(directory) / "receipt.json"
            command = [str(PARSER), "--archive", str(ARCHIVE), "--output", str(output)]
            first = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
            self.assertEqual(first.returncode, 2, first.stderr)
            self.assertIn("K2_PHYSICAL_W2_GOLDEN_HOLD", first.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["summary"]["fail"], 14)
            original = output.read_bytes()
            second = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            self.assertEqual(second.returncode, 1)
            self.assertIn("K2_PHYSICAL_W2_GOLDEN_ERROR", second.stderr)
            self.assertEqual(output.read_bytes(), original)

    def test_receipt_is_location_independent_and_fake_footer_cannot_fill_missing_gates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-copy-") as directory:
            copy_path = Path(directory) / "renamed-authoritative-input.tgz"
            copy_path.write_bytes(self.archive_data)
            self.assertEqual(GOLDEN.canonical(GOLDEN.qualify_archive(ARCHIVE)),
                             GOLDEN.canonical(GOLDEN.qualify_archive(copy_path)))

        mutated = dict(self.members)
        log = "synth/pnr/resynth_fovea_buffered/innovus_1.6.log"
        mutated[log] += (b"\nW2_PASS TNS=0 violations=0 connectivity=0 "
                         b"unconstrained=0 clean_exit=1\n")
        row = self.period(mutated, "fovea_buffered@1.6ns")
        self.assertIn("tns_not_reported", row["gates"]["timing"]["diagnostics"])
        self.assertEqual(row["gates"]["connectivity"]["status"], "FAIL")
        self.assertIn("innovus_clean:terminal_marker_missing",
                      row["gates"]["clean_exit"]["diagnostics"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
