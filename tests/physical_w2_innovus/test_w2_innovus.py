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
BUFFERED_GOLDEN_PIN = ROOT / "tests/physical_w2_innovus/ganghee_golden_pin.json"
BUFFERED_GOLDEN_ARCHIVE = Path(os.environ.get(
    "W2_GANGHEE_GOLDEN_ARCHIVE",
    "/tmp/ganghee-pnr-golden-20260813.tar.gz",
))
RAW_GOLDEN_PIN = ROOT / "tests/physical_w2_innovus/ganghee_raw_golden_pin.json"
RAW_GOLDEN_ARCHIVE = Path(os.environ.get(
    "W2_GANGHEE_RAW_GOLDEN_ARCHIVE",
    "/tmp/ganghee-pnr-raw-golden-20260813.tar.gz",
))


def clean_log() -> str:
    return (
        "Cadence Innovus(TM) Implementation System.\n"
        "Version:\tv23.14-s088_1, built fixture\n"
        "*** Message Summary: 0 warning(s), 0 error(s)\n"
        "--- Ending \"Innovus\" (fixture) ---\n"
    )


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
        (self.root / "tool.log").write_text(clean_log())
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
        (self.root / "tool.log").write_text(clean_log())

    def test_log_requires_pinned_version_summary_and_normal_end(self):
        log = self.root / "tool.log"
        mutations = (
            clean_log().replace("v23.14-s088_1", "v23.13-s001"),
            clean_log().replace(
                "*** Message Summary: 0 warning(s), 0 error(s)\n", ""
            ),
            clean_log().replace('--- Ending "Innovus" (fixture) ---\n', ""),
            clean_log().replace("0 error(s)", "1 error(s)"),
        )
        for value in mutations:
            with self.subTest(value=value):
                log.write_text(value)
                with self.assertRaises(self.module.QualificationError):
                    self.module.validate(self.root, "dut")
        log.write_text(clean_log())

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
        cls.pin = json.loads(BUFFERED_GOLDEN_PIN.read_text(encoding="utf-8"))
        if not BUFFERED_GOLDEN_ARCHIVE.is_file():
            raise RuntimeError(
                f"authoritative golden archive missing: {BUFFERED_GOLDEN_ARCHIVE}"
            )
        actual = hashlib.sha256(BUFFERED_GOLDEN_ARCHIVE.read_bytes()).hexdigest()
        expected = cls.pin["archive"]["sha256"]
        if actual != expected:
            raise RuntimeError(
                f"authoritative golden archive SHA mismatch: {actual} != {expected}"
            )
        cls.archive = tarfile.open(BUFFERED_GOLDEN_ARCHIVE, mode="r:gz")
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
        self.assertEqual(self.pin["golden_kind"], "buffered")
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
        path = self.materialize_member("innovus_2.0.log")
        with self.assertRaisesRegex(self.module.QualificationError, "error"):
            self.module._require_clean_log(path)

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


class AuthoritativeGangheeRawGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_verifier()
        cls.pin = json.loads(RAW_GOLDEN_PIN.read_text(encoding="utf-8"))
        if not RAW_GOLDEN_ARCHIVE.is_file():
            raise RuntimeError(
                f"authoritative raw golden archive missing: {RAW_GOLDEN_ARCHIVE}"
            )
        actual = hashlib.sha256(RAW_GOLDEN_ARCHIVE.read_bytes()).hexdigest()
        expected = cls.pin["archive"]["sha256"]
        if actual != expected:
            raise RuntimeError(
                f"authoritative raw golden archive SHA mismatch: {actual} != {expected}"
            )
        cls.archive = tarfile.open(RAW_GOLDEN_ARCHIVE, mode="r:gz")
        cls.names = set(cls.archive.getnames())

    @classmethod
    def tearDownClass(cls):
        cls.archive.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="w2-raw-golden-")
        self.temp_path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def member_bytes(self, path: str) -> bytes:
        handle = self.archive.extractfile(path)
        self.assertIsNotNone(handle, path)
        return handle.read()

    def member_text(self, path: str) -> str:
        return self.member_bytes(path).decode("utf-8")

    def materialize(self, path: str, label: str | None = None) -> Path:
        target = self.temp_path / (label or Path(path).name)
        target.write_bytes(self.member_bytes(path))
        return target

    @staticmethod
    def run_paths(spec: dict, period: str) -> dict[str, str]:
        directory = spec["directory"]
        top = spec["top"]
        stem = f"{directory}/{top}_{period}"
        return {
            "run": f"{directory}/run_{period}.tcl",
            "mmmc": f"{directory}/mmmc_{period}.tcl",
            "cmd": f"{directory}/innovus_{period}.cmd",
            "log": f"{directory}/innovus_{period}.log",
            "sdc": f"{stem}.sdc",
            "setup": f"{stem}_setup_timing.rpt",
            "hold": f"{stem}_hold_timing.rpt",
            "drc": f"{stem}_drc.rpt",
            "antenna": f"{stem}_antenna.rpt",
            "check_timing": f"{stem}_check_timing.rpt",
        }

    def materialize_signoff(self, spec: dict, period: str) -> dict[str, Path]:
        paths = self.run_paths(spec, period)
        return {
            key: self.materialize(paths[key], key + ".rpt")
            for key in ("log", "setup", "hold", "drc", "antenna", "check_timing")
        }

    def test_raw_and_buffered_archives_are_distinct_sha_bound_sources(self):
        buffered = json.loads(BUFFERED_GOLDEN_PIN.read_text(encoding="utf-8"))
        self.assertEqual(self.pin["golden_kind"], "raw")
        self.assertEqual(buffered["golden_kind"], "buffered")
        self.assertEqual(
            self.pin["archive"]["sha256"],
            "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3",
        )
        self.assertNotEqual(
            self.pin["archive"]["sha256"], buffered["archive"]["sha256"]
        )
        for path, expected in self.pin["representative_members"].items():
            actual = hashlib.sha256(self.member_bytes(path)).hexdigest()
            self.assertEqual(actual, expected, path)

    def test_every_raw_period_uses_real_scripts_and_report_grammar(self):
        common_commands = (
            "floorPlan -r 1.0 0.5 10 10 10 10",
            "globalNetConnect VDD -type pgpin -pin VDD",
            "globalNetConnect VSS -type pgpin -pin VSS",
            "addRing -nets {VDD VSS}",
            "sroute -nets {VDD VSS} -connect {blockPin padPin corePin}",
            "place_opt_design", "clock_opt_design", "routeDesign", "extractRC",
        )
        for owner, spec in self.pin["sweeps"].items():
            for index, period in enumerate(spec["periods_ns"]):
                with self.subTest(owner=owner, period=period):
                    paths = self.run_paths(spec, period)
                    for path in paths.values():
                        self.assertIn(path, self.names)

                    run = self.member_text(paths["run"])
                    command = self.member_text(paths["cmd"])
                    mmmc = self.member_text(paths["mmmc"])
                    sdc = self.member_text(paths["sdc"])
                    log = self.member_text(paths["log"])
                    for token in common_commands:
                        self.assertIn(token, run)
                        self.assertIn(token, command)
                    for token in (
                        "report_timing -late", "report_timing -early",
                        "check_timing -verbose", "verify_drc",
                        "verify_process_antenna", "write_db",
                    ):
                        self.assertIn(token, run)
                    self.assertIn(self.pin["tool_version"], command)
                    self.assertIn(
                        "set_analysis_view -setup {view_slow} -hold {view_slow}",
                        mmmc,
                    )
                    self.assertIn(
                        f"create_clock -name clk -period {period} [get_ports clk]",
                        sdc,
                    )

                    setup = self.materialize(paths["setup"], "setup.rpt")
                    hold = self.materialize(paths["hold"], "hold.rpt")
                    drc = self.materialize(paths["drc"], "drc.rpt")
                    antenna = self.materialize(paths["antenna"], "antenna.rpt")
                    timing = self.materialize(paths["check_timing"], "timing.rpt")
                    setup_value = self.module._timing_observation(setup, "setup")[1]
                    hold_value = self.module._timing_observation(hold, "hold")[1]
                    self.assertAlmostEqual(setup_value, spec["setup_wns_ns"][index])
                    self.assertAlmostEqual(hold_value, spec["hold_wns_ns"][index])
                    self.module._require_zero(drc, "drc_violations")
                    self.module._require_zero(antenna, "antenna_violations")
                    self.module._require_zero(timing, "unconstrained_paths")
                    self.assertIn("IMPCCOPT-2215", log)
                    self.assertIn("IMPIMEX-7043", log)
                    log_path = self.materialize(paths["log"], "innovus.log")
                    with self.assertRaisesRegex(
                        self.module.QualificationError, "error"
                    ):
                        self.module._require_clean_log(log_path)

    def test_signoff_gate_requires_clean_log_and_all_five_reports(self):
        spec = self.pin["sweeps"]["cluster2"]
        paths = self.materialize_signoff(spec, "1.0")
        with self.assertRaisesRegex(self.module.QualificationError, "error"):
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            )

        paths["log"].write_text(clean_log())
        self.assertEqual(
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            ),
            {"setup": 0.042, "hold": 0.160},
        )

        garbage_by_key = {
            "log": "file exists\n",
            "setup": "file exists\n",
            "hold": "file exists\n",
            "drc": "No DRC violations were found\n",
            "antenna": "No Violations Found\n",
            "check_timing": "TIMING CHECK SUMMARY\n",
        }
        for key, garbage in garbage_by_key.items():
            with self.subTest(nonempty_garbage=key):
                fresh = self.materialize_signoff(spec, "1.0")
                fresh["log"].write_text(clean_log())
                fresh[key].write_text(garbage)
                with self.assertRaises(self.module.QualificationError):
                    self.module.validate_minimum_signoff(
                        fresh["log"], fresh["setup"], fresh["hold"], fresh["drc"],
                        fresh["antenna"], fresh["check_timing"],
                    )

    def test_clean_log_cannot_rescue_negative_raw_setup(self):
        spec = self.pin["sweeps"]["cluster2"]
        paths = self.materialize_signoff(spec, "0.7")
        paths["log"].write_text(clean_log())
        with self.assertRaisesRegex(self.module.QualificationError, "setup WNS"):
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
