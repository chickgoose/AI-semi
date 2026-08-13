from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PNR = ROOT / "scripts/ppa/k2_physical_innovus_pnr.tcl"
MMMC = ROOT / "scripts/ppa/k2_physical_innovus_mmmc.tcl"
RUNNER = ROOT / "scripts/ppa/run_k2_physical_innovus.sh"
VERIFY = ROOT / "scripts/ppa/verify_k2_physical_innovus.py"
GOLDEN_PIN = ROOT / "tests/physical_w2_innovus/ganghee_golden_pin.json"
GOLDEN_ARCHIVE = Path(os.environ.get(
    "W2_GANGHEE_GOLDEN_ARCHIVE",
    "/tmp/ganghee-pnr-golden-20260813.tar.gz",
))


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
            "floorPlan -r $aspect $util", "dbGet top.fPlan.rows.name",
            "dbGet top.fPlan.rows.site.name -u",
            "setAnalysisMode -analysisType onChipVariation -cppr both",
            "globalNetConnect $vdd -type pgpin",
            "globalNetConnect $vss -type pgpin",
            "addRing", "sroute", "checkPlace", "place_opt_design",
            "clock_opt_design", "routeDesign", "extractRC",
            "-check_type setup", "-check_type hold",
            "-check_type recovery", "-check_type removal",
            "verifyConnectivity -type all", "verifyConnectivity -type special",
            "verify_drc", "verify_process_antenna", "saveNetlist",
            "saveDesign -mmmc2", "COMMANDS_COMPLETE",
        ):
            self.assertIn(token, text)
        self.assertNotIn("floorPlan -site", text)
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
            check = self.module.EXPECTED_TIMING_CHECK[name].title()
            (self.root / "reports" / name).write_text(
                f"Path 1: MET {check} Check with Pin fixture/CK\n"
                "  Slack Time                    0.010\n"
            )
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
                check = self.module.EXPECTED_TIMING_CHECK[name].title()
                path.write_text(
                    f"Path 1: VIOLATED {check} Check with Pin fixture/CK\n"
                    "  Slack Time                   -0.001\n"
                )
                with self.assertRaisesRegex(self.module.QualificationError, "negative"):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

    def test_missing_timing_path_fails(self):
        (self.root / "reports/recovery_timing.rpt").write_text("no paths\n")
        with self.assertRaisesRegex(
            self.module.QualificationError, "exactly one check class"
        ):
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
        target.write_text(
            "Path 1: MET Setup Check with Pin fixture/CK\n"
            "  Slack Time                    0.010\n"
        )
        path = self.root / "reports/setup_timing.rpt"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(self.module.QualificationError, "non-symlink"):
            self.module.validate(self.root, "dut")


class AuthoritativeGangheeGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_verifier()
        cls.pin = json.loads(GOLDEN_PIN.read_text(encoding="utf-8"))
        if not GOLDEN_ARCHIVE.is_file():
            raise RuntimeError(f"authoritative golden archive missing: {GOLDEN_ARCHIVE}")
        actual = hashlib.sha256(GOLDEN_ARCHIVE.read_bytes()).hexdigest()
        expected = cls.pin["archive"]["sha256"]
        if actual != expected:
            raise RuntimeError(
                f"authoritative golden archive SHA mismatch: {actual} != {expected}"
            )
        cls.archive = tarfile.open(GOLDEN_ARCHIVE, mode="r:gz")
        cls.members = {}
        for path, digest in cls.pin["members"].items():
            handle = cls.archive.extractfile(path)
            if handle is None:
                raise RuntimeError(f"golden archive member missing: {path}")
            data = handle.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != digest:
                raise RuntimeError(
                    f"golden member SHA mismatch: {path}: {actual} != {digest}"
                )
            cls.members[path] = data

    @classmethod
    def tearDownClass(cls):
        cls.archive.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="w2-golden-format-")

    def tearDown(self):
        self.temp.cleanup()

    def member_text(self, suffix: str) -> str:
        rows = [data for path, data in self.members.items() if path.endswith(suffix)]
        self.assertEqual(len(rows), 1, suffix)
        return rows[0].decode("utf-8")

    def materialize_member(self, suffix: str) -> Path:
        data = next(data for path, data in self.members.items() if path.endswith(suffix))
        path = Path(self.temp.name) / Path(suffix).name
        path.write_bytes(data)
        return path

    def test_archive_and_consumed_members_are_sha_bound(self):
        self.assertEqual(
            self.pin["archive"]["sha256"],
            "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f",
        )
        self.assertEqual(len(self.members), len(self.pin["members"]))

    def test_real_setup_and_hold_formats_parse_exactly(self):
        setup = self.materialize_member("aer_fovea_buffered_2.0_setup_timing.rpt")
        hold = self.materialize_member("aer_fovea_buffered_2.0_hold_timing.rpt")
        self.assertEqual(self.module._timing_observation(setup), ("setup", 0.005))
        self.assertEqual(self.module._timing_observation(hold), ("hold", 0.073))

    def test_real_violated_setup_and_removal_formats_are_caught(self):
        setup = self.materialize_member("aer_cluster2_buffered_0.8_setup_timing.rpt")
        removal = self.materialize_member("aer_fovea_buffered_0.8_hold_timing.rpt")
        self.assertEqual(self.module._timing_observation(setup), ("setup", -0.096))
        self.assertEqual(self.module._timing_observation(removal), ("removal", -0.044))
        with self.assertRaisesRegex(self.module.QualificationError, "expected hold"):
            self.module._timing_observation(removal, "hold")

    def test_real_drc_antenna_and_check_timing_formats_parse(self):
        drc = self.materialize_member("aer_fovea_buffered_2.0_drc.rpt")
        antenna = self.materialize_member("aer_fovea_buffered_2.0_antenna.rpt")
        timing = self.materialize_member("aer_fovea_buffered_2.0_check_timing.rpt")
        self.module._require_zero(drc, "drc_violations")
        self.module._require_zero(antenna, "antenna_violations")
        self.module._require_zero(timing, "unconstrained_paths")

    def test_real_command_and_tool_version_anchor_flow(self):
        command = self.member_text("innovus_2.0.cmd")
        self.assertIn("Innovus v23.14-s088_1", command)
        for token in (
            "floorPlan -r 1.0 0.5 10 10 10 10",
            "globalNetConnect VDD -type pgpin -pin VDD",
            "globalNetConnect VSS -type pgpin -pin VSS",
            "addRing -nets {VDD VSS}",
            "sroute -nets {VDD VSS} -connect {blockPin padPin corePin}",
            "place_opt_design", "clock_opt_design", "routeDesign", "extractRC",
        ):
            self.assertIn(token, command)

    def test_real_golden_log_is_not_a_clean_hardened_run(self):
        log = self.member_text("innovus_2.0.log")
        self.assertIn("IMPCCOPT-2215", log)
        self.assertIn("IMPIMEX-7043", log)
        self.assertRegex(log, self.module.BAD_LOG)

    def test_real_mmmc_and_sdc_are_diagnostic_single_corner(self):
        mmmc = self.member_text("mmmc_2.0.tcl")
        sdc = self.member_text("aer_fovea_buffered_2.0.sdc")
        self.assertIn("set_analysis_view -setup {view_slow} -hold {view_slow}", mmmc)
        self.assertIn("create_clock -name clk -period 2.0 [get_ports clk]", sdc)

    def test_real_golden_lacks_hardened_completion_artifacts(self):
        names = set(self.archive.getnames())
        self.assertFalse(any(name.endswith("recovery_timing.rpt") for name in names))
        self.assertFalse(any(name.endswith("connectivity.rpt") for name in names))
        self.assertFalse(any(name.endswith("postroute.v") for name in names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
