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
    "K2_GANGHEE_RAW_GOLDEN_ARCHIVE", "/tmp/ganghee-pnr-raw-golden-20260813.tar.gz"))
SPEC = importlib.util.spec_from_file_location("k2_w2_ganghee_raw_golden", PARSER)
assert SPEC and SPEC.loader
GOLDEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GOLDEN)


class GangheeRawGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ARCHIVE.is_file():
            raise RuntimeError(
                f"authoritative raw fixture is required "
                f"(set K2_GANGHEE_RAW_GOLDEN_ARCHIVE): {ARCHIVE}")
        cls.archive_data, _ = GOLDEN.stable_read(ARCHIVE)
        if GOLDEN.sha256(cls.archive_data) != GOLDEN.RAW_ARCHIVE_SHA256:
            raise RuntimeError("authoritative raw fixture SHA-256 mismatch")
        cls.members = GOLDEN.extract_members(
            cls.archive_data, GOLDEN.RAW_EXPECTED_MEMBER_COUNT)

    @staticmethod
    def replace(members: dict[str, bytes], path: str, old: bytes, new: bytes) -> dict[str, bytes]:
        mutated = dict(members)
        original = mutated[path]
        if original.count(old) != 1:
            raise AssertionError(f"mutation anchor must occur exactly once: {path}: {old!r}")
        mutated[path] = original.replace(old, new, 1)
        return mutated

    def analyze(self, members: dict[str, bytes]) -> dict:
        return GOLDEN.analyze_members(
            members, GOLDEN.RAW_EXPECTED_DESIGNS, include_sweeps=True)

    def test_raw_archive_pin_inventory_and_all_period_results(self) -> None:
        receipt = GOLDEN.qualify_raw_archive(ARCHIVE)
        self.assertEqual(receipt["archive"]["sha256"], GOLDEN.RAW_ARCHIVE_SHA256)
        self.assertEqual(receipt["archive"]["member_count"], 215)
        self.assertEqual(receipt["summary"], {"period_count": 10, "pass": 0, "fail": 10})
        self.assertEqual(receipt["status"], "AUTHORITATIVE_RAW_FIXTURE_FAIL")
        self.assertEqual(receipt["frequency_bracket"]["status"], "NON_MONOTONIC_HOLD")
        self.assertEqual(receipt["frequency_bracket"]["qualified_brackets"], {})
        self.assertEqual(receipt["frequency_bracket"]["selected_periods"], {})
        self.assertTrue(all(row["status"] == "FAIL" for row in receipt["periods"].values()))
        self.assertTrue(all(row["gates"]["constraint_coverage"]["evidence"]["no_drive"] == 18
                            for row in receipt["periods"].values()))
        self.assertTrue(all(row["gates"]["innovus_errors"]["evidence"][
                                "severity_error_lines"] == 2
                            for row in receipt["periods"].values()))
        self.assertTrue(all(row["gates"]["drc"]["status"] == "PASS" and
                            row["gates"]["antenna"]["status"] == "PASS"
                            for row in receipt["periods"].values()))
        self.assertEqual(receipt["claim_boundary"]["frequency_bracket_and_period_selection"],
                         "NON_MONOTONIC_HOLD")
        self.assertEqual(GOLDEN.canonical(receipt),
                         GOLDEN.canonical(GOLDEN.qualify_raw_archive(ARCHIVE)))

    def test_fovea_non_monotonic_sequence_is_exact_and_unselectable(self) -> None:
        sweep = self.analyze(self.members)["frequency_sweeps"]["fovea_raw"]
        self.assertEqual(
            [(point["period_ns"], point["innovus_late_wns_ns"], point["timing_met"])
             for point in sweep["points"]],
            [(1.2, 0.000, True), (1.3, -0.024, False), (1.4, 0.036, True),
             (1.6, -0.003, False), (2.0, -0.007, False)])
        self.assertEqual(sweep["status"], "NON_MONOTONIC_HOLD")
        self.assertEqual(sweep["pass_to_fail_reversions"], [
            {"from_period_ns": 1.2, "to_period_ns": 1.3},
            {"from_period_ns": 1.4, "to_period_ns": 1.6},
        ])
        self.assertEqual(len(sweep["slack_inversions"]), 3)
        self.assertIsNone(sweep["qualified_bracket"])
        self.assertIsNone(sweep["selected_period"])
        self.assertTrue(sweep["cherry_pick_forbidden"])

    def test_cluster2_observed_transition_is_not_promoted_to_bracket(self) -> None:
        sweep = self.analyze(self.members)["frequency_sweeps"]["cluster2_raw"]
        self.assertEqual(
            [point["innovus_late_wns_ns"] for point in sweep["points"]],
            [-0.178, -0.088, -0.029, 0.042, 0.080])
        self.assertEqual(sweep["status"], "MONOTONIC_OBSERVED_HOLD")
        self.assertEqual(sweep["observed_transition_not_a_qualified_bracket"], {
            "last_observed_fail_period_ns": 0.9,
            "first_observed_pass_period_ns": 1.0,
        })
        self.assertFalse(sweep["all_periods_qualified"])
        self.assertIsNone(sweep["qualified_bracket"])
        self.assertIsNone(sweep["selected_period"])

    def test_making_fovea_slacks_monotonic_still_cannot_select_a_period(self) -> None:
        members = self.members
        base = "synth/pnr/resynth_fovea_raw"
        changes = {
            "1.3": (b"Path 1: VIOLATED", b"Path 1: MET", b"Slack Time                   -0.024",
                    b"Slack Time                    0.024"),
            "1.6": (b"Path 1: VIOLATED", b"Path 1: MET", b"Slack Time                   -0.003",
                    b"Slack Time                    0.040"),
            "2.0": (b"Path 1: VIOLATED", b"Path 1: MET", b"Slack Time                   -0.007",
                    b"Slack Time                    0.050"),
        }
        for period, (old_status, new_status, old_slack, new_slack) in changes.items():
            path = f"{base}/aer_tx16_trad_rowcol_fovea_{period}_setup_timing.rpt"
            members = self.replace(members, path, old_status, new_status)
            members = self.replace(members, path, old_slack, new_slack)
        analysis = self.analyze(members)
        sweep = analysis["frequency_sweeps"]["fovea_raw"]
        self.assertEqual(sweep["status"], "MONOTONIC_OBSERVED_HOLD")
        self.assertFalse(sweep["all_periods_qualified"])
        self.assertIsNone(sweep["qualified_bracket"])
        self.assertIsNone(sweep["selected_period"])
        self.assertEqual(analysis["frequency_bracket"]["selected_periods"], {})

    def test_missing_period_and_fake_cherry_pick_footer_fail_closed(self) -> None:
        missing = dict(self.members)
        path = ("synth/pnr/resynth_fovea_raw/"
                "aer_tx16_trad_rowcol_fovea_1.3_setup_timing.rpt")
        del missing[path]
        sweep = self.analyze(missing)["frequency_sweeps"]["fovea_raw"]
        self.assertEqual(sweep["status"], "MISSING_DATA_HOLD")
        self.assertEqual(sweep["missing_periods"], ["1.3"])
        self.assertIsNone(sweep["selected_period"])
        aggregate = self.analyze(missing)["frequency_bracket"]
        self.assertEqual(aggregate["status"], "MISSING_DATA_HOLD")
        self.assertEqual(aggregate["missing_data_designs"], ["fovea_raw"])

        fabricated = dict(self.members)
        log = "synth/pnr/resynth_fovea_raw/innovus_1.4.log"
        fabricated[log] += b"\nRAW_GOLDEN_PASS SELECT_PERIOD=1.4 BRACKET=1.3:1.4\n"
        analysis = self.analyze(fabricated)
        self.assertEqual(analysis["frequency_bracket"]["status"], "NON_MONOTONIC_HOLD")
        self.assertEqual(analysis["frequency_bracket"]["selected_periods"], {})
        self.assertIn("innovus_clean:terminal_marker_missing",
                      analysis["periods"]["fovea_raw@1.4ns"]["gates"]["clean_exit"]["diagnostics"])

    def test_raw_archive_byte_mutation_and_wrong_profile_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-pin-") as directory:
            path = Path(directory) / "mutated.tar.gz"
            path.write_bytes(self.archive_data + b"x")
            with self.assertRaisesRegex(GOLDEN.GoldenQualificationError, "SHA-256 mismatch"):
                GOLDEN.qualify_raw_archive(path)
            with self.assertRaisesRegex(GOLDEN.GoldenQualificationError, "SHA-256 mismatch"):
                GOLDEN.qualify_archive(ARCHIVE)

    def test_raw_cli_writes_hold_receipt_with_no_selection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-cli-") as directory:
            output = Path(directory) / "receipt.json"
            command = [str(PARSER), "--profile", "raw", "--archive", str(ARCHIVE),
                       "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("K2_PHYSICAL_W2_GOLDEN_HOLD periods=10 failed=10", result.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["frequency_bracket"]["status"], "NON_MONOTONIC_HOLD")
            self.assertEqual(receipt["frequency_bracket"]["selected_periods"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
