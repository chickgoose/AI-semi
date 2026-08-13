from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PNR = ROOT / "scripts/ppa/k2_physical_innovus_pnr.tcl"
MMMC = ROOT / "scripts/ppa/k2_physical_innovus_mmmc.tcl"
RUNNER = ROOT / "scripts/ppa/run_k2_physical_innovus.sh"
VERIFY = ROOT / "scripts/ppa/verify_k2_physical_innovus.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("w2_innovus_verify", VERIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaticFlowContractTests(unittest.TestCase):
    def test_mmmc_has_distinct_setup_and_hold_corners(self):
        text = MMMC.read_text(encoding="utf-8")
        for token in (
            "AER_SETUP_LIBRARY_FILE", "AER_HOLD_LIBRARY_FILE",
            "AER_SETUP_QRC_TECH", "AER_HOLD_QRC_TECH",
            "w2_view_setup", "w2_view_hold", "set_analysis_view",
        ):
            self.assertIn(token, text)
        self.assertIn("setup and hold Liberty files must be distinct", text)
        self.assertIn("setup and hold QRC files must be distinct", text)
        self.assertNotIn("-hold {w2_view_setup}", text)

    def test_pnr_closes_requested_physical_commands(self):
        text = PNR.read_text(encoding="utf-8")
        for token in (
            "floorPlan -site $site", "dbGet top.fPlan.rows.name",
            "dbGet top.fPlan.rows.site.name -u",
            "setAnalysisMode -analysisType onChipVariation -cppr both",
            "globalNetConnect $vdd -type pgpin",
            "globalNetConnect $vss -type pgpin", "applyGlobalNets",
            "addRing", "sroute", "checkPlace", "place_opt_design",
            "clock_opt_design", "routeDesign", "extractRC",
            "-check_type setup", "-check_type hold",
            "-check_type recovery", "-check_type removal",
            "verifyConnectivity -type all", "verifyConnectivity -type special",
            "verify_drc", "verify_process_antenna", "saveNetlist",
            "saveDesign", "COMMANDS_COMPLETE",
        ):
            self.assertIn(token, text)
        self.assertNotIn("FLOW_CLEAN", text.split("# FLOW_CLEAN", 1)[0])

    def test_runner_delegates_clean_marker_to_independent_verifier(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("--write-clean-marker", text)
        self.assertIn("[[ ! -e \"$AER_PNR_OUTPUT_DIR\" ]]", text)
        self.assertNotIn("FLOW_CLEAN", text)


class FixtureQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_verifier()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="w2-innovus-test-")
        self.root = Path(self.temp.name) / "run"
        for directory in ("status", "reports", "netlist", "database"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "status/COMMANDS_COMPLETE").write_bytes(
            self.module.COMMAND_SENTINEL
        )
        (self.root / "tool.log").write_text("Innovus batch complete\n")
        for name in self.module.TIMING_REPORTS:
            (self.root / "reports" / name).write_text("slack (MET) 0.010\n")
        for name, key in self.module.ZERO_COUNT_REPORTS.items():
            (self.root / "reports" / name).write_text(f"{key}=0\n")
        (self.root / "reports/area.rpt").write_text("area report\n")
        (self.root / "reports/power.rpt").write_text("power report\n")
        (self.root / "reports/route.rpt").write_text(
            "detailed_route_completed=1\n"
        )
        (self.root / "netlist/dut.postroute.v").write_text("module dut; endmodule\n")
        (self.root / "database/dut.enc.dat").write_text("database fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_clean_fixture_passes(self):
        slacks = self.module.validate(self.root, "dut")
        self.assertEqual(set(slacks), {"setup", "hold", "recovery", "removal"})

    def test_each_negative_timing_check_fails(self):
        for name in self.module.TIMING_REPORTS:
            with self.subTest(name=name):
                path = self.root / "reports" / name
                original = path.read_text()
                path.write_text("slack (VIOLATED) -0.001\n")
                with self.assertRaisesRegex(self.module.QualificationError, "negative"):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

    def test_missing_timing_path_fails(self):
        (self.root / "reports/recovery_timing.rpt").write_text("no paths\n")
        with self.assertRaisesRegex(self.module.QualificationError, "no recognized"):
            self.module.validate(self.root, "dut")

    def test_each_nonzero_physical_gate_fails(self):
        for name, key in self.module.ZERO_COUNT_REPORTS.items():
            with self.subTest(name=name):
                path = self.root / "reports" / name
                path.write_text(f"{key}=1\n")
                with self.assertRaisesRegex(self.module.QualificationError, "nonzero"):
                    self.module.validate(self.root, "dut")
                path.write_text(f"{key}=0\n")

    def test_interrupted_or_error_log_fails(self):
        for marker in ("**ERROR: bad", "FATAL: bad", "INTERRUPT", "SEGMENTATION FAULT"):
            with self.subTest(marker=marker):
                (self.root / "tool.log").write_text(marker + "\n")
                with self.assertRaisesRegex(self.module.QualificationError, "log"):
                    self.module.validate(self.root, "dut")
        (self.root / "tool.log").write_text("Innovus batch complete\n")

    def test_command_marker_is_not_a_clean_marker(self):
        self.module.validate(self.root, "dut")
        self.assertFalse((self.root / "status/FLOW_CLEAN").exists())

    def test_failure_marker_blocks_clean(self):
        (self.root / "status/COMMANDS_FAILED").write_text("route failed\n")
        with self.assertRaisesRegex(self.module.QualificationError, "failure sentinel"):
            self.module.validate(self.root, "dut")

    def test_missing_postroute_netlist_fails(self):
        (self.root / "netlist/dut.postroute.v").rename(
            self.root / "netlist/not-the-top.v"
        )
        with self.assertRaisesRegex(self.module.QualificationError, "missing artifact"):
            self.module.validate(self.root, "dut")

    def test_clean_marker_is_exclusive_and_not_overwritten(self):
        marker = self.root / "status/FLOW_CLEAN"
        self.module._write_exclusive(marker, self.module.CLEAN_SENTINEL)
        self.assertEqual(marker.read_bytes(), self.module.CLEAN_SENTINEL)
        with self.assertRaises(FileExistsError):
            self.module._write_exclusive(marker, b"replacement\n")
        self.assertEqual(marker.read_bytes(), self.module.CLEAN_SENTINEL)

    def test_symlink_report_fails(self):
        target = self.root / "outside.rpt"
        target.write_text("slack (MET) 0.010\n")
        path = self.root / "reports/setup_timing.rpt"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(self.module.QualificationError, "non-symlink"):
            self.module.validate(self.root, "dut")


if __name__ == "__main__":
    unittest.main(verbosity=2)
