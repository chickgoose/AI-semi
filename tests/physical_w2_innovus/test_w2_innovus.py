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
MMMC = ROOT / "scripts/ppa/p6_multiclock_mmmc.tcl"
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
            "W2_SETUP_LIBERTY", "W2_HOLD_LIBERTY",
            "W2_SHARED_TYPICAL_QRC", "W2_STRICT_MULTICLOCK_SDC",
            "w2_setup_view", "w2_hold_view", "set_analysis_view",
        ):
            self.assertIn(token, text)
        self.assertIn("setup and hold Liberty must be distinct", text)
        self.assertIn("W2_SHARED_TYPICAL_QRC", text)
        self.assertEqual(text.count("create_rc_corner"), 2)
        self.assertEqual(text.count("-qrc_tech $shared_qrc"), 2)
        self.assertNotIn("-hold {w2_setup_view}", text)

    def test_strict_templates_use_only_canonical_link_and_source_ports(self):
        for relative, width in (("constraints/r1_multiclock.sdc", 2),
                                ("constraints/p6_multiclock.sdc", 5)):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text()
                for required in ("ref_clk_i", "sample_clk_i", "rst_n",
                                 "link_clk_o", "link_data_o"):
                    self.assertIn(required, text)
                for alias in ("burst_clk_o", "burst_data_o", "load_i",
                              "pending_i", "source_ready_o", "protocol_fault_o"):
                    self.assertNotIn(alias, text)
                self.assertIn(f"!= {width}", text)
                self.assertIn(
                    "set link_icg_e [w2_one link_icg_E "
                    "[get_pins -hierarchical *w2_ep_icg_0/E]]", text)
                self.assertIn(
                    "set_clock_gating_check -setup $gate_setup "
                    "-hold $gate_hold $link_icg_e", text)
                self.assertNotIn("-hold $gate_hold $sample_clock", text)

    def test_authoritative_environment_pins_exact_tool_and_technology(self):
        authority = json.loads((ROOT / "scripts/ppa/k2_physical_server_environment.json").read_text())
        self.assertEqual(authority["tool"]["version"], "23.14-s088_1")
        self.assertEqual(
            authority["tool"]["sha256"],
            "41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa",
        )
        self.assertEqual(authority["physical_policy"]["core_utilization"], "0.35")
        technology = authority["technology"]
        self.assertEqual(technology["setup_liberty"]["pvt"], [1.0, 0.9, 125.0])
        self.assertEqual(technology["hold_liberty"]["pvt"], [1.0, 1.1, 0.0])
        self.assertEqual(
            technology["shared_qrc"]["sha256"],
            "a089c567928e3c8653408ebc503cb4e8270732c5f23e6cb23498d51cd6c75bd5",
        )
        self.assertEqual(
            authority["mmmc_template"]["sha256"],
            hashlib.sha256(MMMC.read_bytes()).hexdigest(),
        )

    def test_pnr_closes_requested_physical_commands(self):
        text = PNR.read_text(encoding="utf-8")
        for token in (
            "floorPlan -r $aspect $util", "dbGet top.fPlan.rows.name",
            "dbGet top.fPlan.rows.site.name -u",
            "get_db base_cells -if {.name == BUFX2}",
            "get_db base_cells -if {.name == BUFX4}",
            "ecoChangeCell -inst", "setDontUse BUFX2 true",
            "foreach used_site $used_sites",
            "floorplan is missing required placement rows",
            "setAnalysisMode -analysisType onChipVariation -cppr both",
            "globalNetConnect $vdd -type pgpin",
            "globalNetConnect $vss -type pgpin",
            "addRing", "sroute", "checkPlace", "place_opt_design",
            "clock_opt_design", "routeDesign", "extractRC",
            "optDesign -postRoute", "optDesign -postRoute -hold",
            "set_interactive_constraint_modes [list w2_strict_functional]",
            "set boundary_clock_ports [get_ports {ref_clk_i sample_clk_i}]",
            "set expected_boundary_nonclock_inputs [get_ports {rst_n source_pending_i*}]",
            "expected_boundary_nonclock_inputs] != 17",
            "set_drive 0 $boundary_clock_ports",
            "set_driving_cell -lib_cell BUFX2 $boundary_nonclock_inputs",
            "set forwarded_link_source [get_pins -hierarchical *w2_ep_icg_0/ECK]",
            "create_generated_clock -name w2_forwarded_link_port_clk",
            "-source $forwarded_link_source -divide_by 1 $forwarded_link_port",
            "set forwarded_link_clock [get_clocks w2_forwarded_link_port_clk]",
            "expected exactly one forwarded generated clock on link_clk_o",
            "boundary_timing.machine",
            "set_propagated_clock [all_clocks]",
            "saveDesign -mmmc2 \"$output/database/${top}.postroute_checkpoint.enc\"",
            "redirect -tee -file \"$output/reports/activity_annotation.rpt\" {",
            "read_activity_file -format $activity_format -scope $activity_scope",
            "-check_type setup", "-check_type hold",
            "-check_type recovery", "-check_type removal",
            "-check_type clock_gating_setup", "-check_type clock_gating_hold",
            "-check_type pulse_width", "half_cycle_setup_timing.rpt",
            "half_cycle_hold_timing.rpt",
            "get_pins -hierarchical *w2_ep_icg_0/E",
            "expected exactly one preserved endpoint ICG enable pin",
            "-to $endpoint_icg_enable -max_paths 50",
            "setOptMode -fixHoldAllowSetupTnsDegrade $hold_setup_degrade",
            "for {set hold_iteration 1} {$hold_iteration <= 3}",
            "hold_metrics_improved $before $after",
            "post-route hold closure did not converge",
            "hold_closure.machine",
            "verifyConnectivity -type all", "verifyConnectivity -type special",
            "verify_drc", "verify_process_antenna", "saveNetlist",
            "write_sdf", "rcOut -spef",
            "saveDesign -mmmc2", "COMMANDS_COMPLETE",
        ):
            self.assertIn(token, text)
        self.assertNotIn("createRow -site", text)
        self.assertNotIn("concat $used_sites [list $site CoreSiteDouble]", text)
        self.assertNotIn("floorPlan -site", text)
        self.assertNotIn("FLOW_CLEAN", text.split("# FLOW_CLEAN", 1)[0])
        self.assertEqual(
            text.count("-to $endpoint_icg_enable -max_paths 50"), 2)
        self.assertIn(
            "w2_setup_view clock_gating_setup gating_setup $endpoint_icg_enable",
            text)
        self.assertIn(
            "w2_hold_view clock_gating_hold gating_hold $endpoint_icg_enable",
            text)
        self.assertLess(
            text.index("setOptMode -fixHoldAllowSetupTnsDegrade $hold_setup_degrade"),
            text.index("optDesign -postRoute -hold"))

    def test_forwarded_link_clock_is_created_before_it_is_checked(self):
        text = PNR.read_text(encoding="utf-8")
        create = "create_generated_clock -name w2_forwarded_link_port_clk"
        source = "set forwarded_link_source [get_pins -hierarchical *w2_ep_icg_0/ECK]"
        check = "set forwarded_link_clock [get_clocks w2_forwarded_link_port_clk]"
        self.assertEqual(text.count(create), 1)
        self.assertEqual(text.count(source), 1)
        self.assertLess(text.index(source), text.index(create))
        self.assertLess(text.index(create), text.index(check))
        self.assertNotIn("get_clocks -of_objects", text)
        self.assertIn(
            "expected exactly one *w2_ep_icg_0/ECK source and link_clk_o target",
            text)

    def test_pg_route_is_refreshed_after_each_hold_eco(self):
        text = PNR.read_text(encoding="utf-8")
        sroute = "sroute -nets [list $vdd $vss] -connect {blockPin padPin corePin}"
        trim = "editTrim -nets [list $vdd $vss]"
        pre_eco_drc = 'verify_drc -report "$output/reports/drc_pre_signal_eco.rpt"'
        signal_eco = "ecoRoute -fix_drc"
        self.assertEqual(text.count(sroute), 2)
        self.assertEqual(text.count(trim), 2)
        self.assertEqual(text.count(pre_eco_drc), 1)
        self.assertEqual(text.count(signal_eco), 1)
        self.assertLess(text.index("optDesign -postRoute -hold"), text.index(sroute))
        self.assertLess(text.index(sroute), text.index(trim))
        self.assertLess(text.index(trim), text.index(pre_eco_drc))
        self.assertLess(text.index(pre_eco_drc), text.index(signal_eco))
        self.assertLess(text.index(signal_eco), text.index("extractRC", text.index(trim)))
        closure_opt = text.rindex("optDesign -postRoute -hold")
        closure_sroute = text.rindex(sroute)
        closure_trim = text.rindex(trim)
        self.assertLess(closure_opt, closure_sroute)
        self.assertLess(closure_sroute, closure_trim)
        self.assertLess(closure_trim, text.index("extractRC", closure_trim))
        self.assertLess(closure_trim, text.index("verifyConnectivity -type all"))

    def test_propagated_clock_uses_the_shared_mmmc_constraint_mode(self):
        text = PNR.read_text(encoding="utf-8")
        interactive = "set_interactive_constraint_modes [list w2_strict_functional]"
        propagated = "set_propagated_clock [all_clocks]"
        self.assertEqual(text.count(interactive), 1)
        self.assertEqual(text.count(propagated), 1)
        self.assertLess(text.index(interactive), text.index(propagated))

    def test_timing_reports_select_compatible_setup_and_hold_modes(self):
        text = PNR.read_text(encoding="utf-8")
        setup_mode = "setAnalysisMode -checkType setup"
        hold_mode = "setAnalysisMode -checkType hold"
        self.assertEqual(text.count(setup_mode), 3)
        self.assertEqual(text.count(hold_mode), 2)
        report_anchor = text.index("# Innovus 23.14 defaults interactive timing queries")
        setup_start = text.index(setup_mode, report_anchor)
        hold_start = text.index(hold_mode, setup_start)
        setup_restore = text.rindex(setup_mode)
        self.assertLess(setup_start, text.index("-check_type recovery", setup_start))
        self.assertLess(text.index("-check_type recovery", setup_start), hold_start)
        self.assertLess(hold_start, text.index("-check_type removal", hold_start))
        self.assertLess(text.index("-check_type removal", hold_start), setup_restore)

    def test_activity_import_is_captured_before_power_reporting(self):
        text = PNR.read_text(encoding="utf-8")
        capture = 'redirect -tee -file "$output/reports/activity_annotation.rpt" {'
        activity = "read_activity_file -format $activity_format"
        power = 'report_power > "$output/reports/power.rpt"'
        self.assertEqual(text.count(capture), 1)
        self.assertEqual(text.count(activity), 1)
        self.assertLess(text.index(capture), text.index(activity))
        self.assertLess(text.index(activity), text.index(power))

    def test_runner_delegates_clean_marker_to_independent_verifier(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("--write-clean-marker", text)
        self.assertIn("--verify-descriptor", text)
        self.assertIn("AER_W2_EXECUTION_DESCRIPTOR_SHA256", text)
        for variable in (
                "AER_W2_TIMING_PROFILE", "AER_W2_TIMING_PROFILE_SHA256",
                "AER_W2_PERIOD_NS", "AER_HOLD_FIX_ALLOW_SETUP_TNS_DEGRADE"):
            self.assertIn(variable, text)
        self.assertIn("[[ ! -e \"$AER_PNR_OUTPUT_DIR\" ]]", text)
        self.assertNotIn("AER_W2_PLAN_VALIDATED", text)
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
        timing = self.module._timing_profile_contract("three_endpoint_5p0ns")
        descriptor = {
            "schema": "k2_w2_innovus_execution_descriptor_v2",
            "binding": {
                "top": "dut", "cohort": "tech_staged_complete_compositions",
                "timing_profile_id": timing["id"],
                "innovus_timing_profile_sha256": timing["profile_sha256"],
                "genus_timing_manifest_sha256":
                    timing["genus_timing_manifest_sha256"],
                "genus_timing_profile_sha256":
                    timing["genus_timing_profile_sha256"],
                "period_ns": timing["period_ns"],
                "hold_fix_allow_setup_tns_degrade": False,
            },
            "registry_sha256": hashlib.sha256(
                (ROOT / "scripts/ppa/k2_physical_innovus_cohorts.json").read_bytes()
            ).hexdigest(),
            "authority_sha256": hashlib.sha256(
                (ROOT / "scripts/ppa/k2_physical_server_environment.json").read_bytes()
            ).hexdigest(),
        }
        descriptor_payload = (json.dumps(descriptor, sort_keys=True) + "\n").encode()
        (self.root / "status/EXECUTION_DESCRIPTOR.json").write_bytes(descriptor_payload)
        (self.root / "status/EXECUTION_DESCRIPTOR.sha256").write_text(
            hashlib.sha256(descriptor_payload).hexdigest() + "\n")
        (self.root / "status/TECHNOLOGY_CONTRACT").write_text(
            "schema=k2_w2_innovus_technology_contract_v2\n"
            "top=dut\n"
            "cohort=tech_staged_complete_compositions\n"
            "design=fovea_a7\n"
            f"timing_profile={timing['id']}\n"
            f"timing_profile_sha256={timing['profile_sha256']}\n"
            f"period_ns={timing['period_ns']}\n"
            "hold_fix_allow_setup_tns_degrade=false\n"
            "innovus_path=/tools/cadence/DDI231/INNOVUS231/bin/innovus\n"
            "innovus_sha256=41670b96270692b6139dcae1c8d8721d7b01d41c0725eb22a1ef5ed2d4fbc3aa\n"
            "tech_lef_sha256=0310f32fe4fb5009053dcfe36ece6e8d7a1f8e8d6e58a0b6fdd2109c2c919f70\n"
            "cell_lef_sha256=7bb39c7adef5704aa10d886f9cc404b06d4f486219ffb4a6a8bbb31f965d52b2\n"
            "setup_library_role=slow_max_setup\n"
            "setup_library_basename=slow_vdd1v0_basicCells.lib\n"
            "setup_library_sha256=dec616b7b53aa5166eac9660ba83561a4057ee3b7e62f59f3d4bebad495ffe10\n"
            "hold_library_role=fast_min_hold\n"
            "hold_library_basename=fast_vdd1v0_basicCells.lib\n"
            "hold_library_sha256=e63762d156fd929cde2f58b0a5883020d6f16f0a41d3736577d0af6b94191560\n"
            "rc_model=shared_typical_gpdk045\n"
            "qrc_source_count=1\n"
            "setup_rc_corner=w2_shared_setup_rc\n"
            "hold_rc_corner=w2_shared_hold_rc\n"
            "qrc_basename=gpdk045.tch\n"
            "qrc_sha256=a089c567928e3c8653408ebc503cb4e8270732c5f23e6cb23498d51cd6c75bd5\n"
        )
        (self.root / "status/ACTIVITY_POWER_CONTRACT").write_text(
            "schema=k2_w2_activity_power_contract_v1\n"
            "mode=annotated_activity\n"
            "format=SAIF\n"
            "scope=tb/dut\n"
            f"activity_sha256={'a' * 64}\n"
            "window_start_ns=100\n"
            "window_end_ns=900\n"
        )
        (self.root / "tool.log").write_text(clean_log())
        for name in self.module.TIMING_REPORTS:
            check = self.module.EXPECTED_TIMING_CHECK[name].title()
            (self.root / "reports" / name).write_text(
                f"Path 1: MET {check} Check with Pin fixture/CK\n"
                "  Slack Time                    0.010\n"
            )
            (self.root / "reports" / name.replace(".rpt", ".machine")).write_text(
                "schema=k2_w2_timing_machine_summary_v1\n"
                f"check={self.module.EXPECTED_MACHINE_CHECK.get(name, self.module.EXPECTED_TIMING_CHECK[name])}\n"
                f"view={'w2_hold_view' if check.lower() in {'hold', 'removal'} else 'w2_setup_view'}\n"
                "path_count=1\n"
                "violation_count=0\n"
                "wns=0.010\n"
                "tns=0.0\n"
            )
        for name, key in self.module.ZERO_COUNT_REPORTS.items():
            suffix = ""
            if name.startswith("check_design_"):
                suffix = "check_design_errors=0\ncheck_design_violations=0\n"
            (self.root / "reports" / name).write_text(f"{key}=0\n{suffix}")
        (self.root / "reports/area.rpt").write_text("area report\n")
        (self.root / "reports/activity_annotation.rpt").write_text(
            "  Annotation coverage for this file\n"
            "   (Unique nets matched/Total nets)       : 320/400 = 80%\n"
            "'read_activity_file' finished successfully.\n"
        )
        (self.root / "reports/power.rpt").write_text("power report\n")
        (self.root / "reports/hold_closure.machine").write_text(
            "schema=k2_w2_hold_closure_v1\n"
            "status=CLOSED\n"
            "max_iterations=3\n"
            "observation_count=2\n"
            "observation_0=10,2,-0.100,-0.150\n"
            "observation_1=10,0,0.010,0.0\n"
        )
        (self.root / "reports/boundary_timing.machine").write_text(
            "schema=k2_w2_boundary_timing_v1\n"
            f"timing_profile={timing['id']}\n"
            f"timing_profile_sha256={timing['profile_sha256']}\n"
            f"period_ns={timing['period_ns']}\n"
            "clock_ports=ref_clk_i,sample_clk_i\n"
            "clock_drive=0\n"
            "nonclock_input_ports=rst_n,source_pending_i\n"
            "nonclock_driving_cell=BUFX2\n"
            "forwarded_link_clock=*w2_ep_icg_0/ECK,link_clk_o,divide_by_1\n"
            "link_clock_false_path=FORBIDDEN\n"
            "hold_fix_allow_setup_tns_degrade=false\n"
        )
        (self.root / "reports/route.rpt").write_text(
            "detailed_route_completed=1\n"
        )
        (self.root / "netlist/dut.postroute.v").write_text("module dut; endmodule\n")
        (self.root / "netlist/dut.postroute.sdf").write_text(
            '(DELAYFILE (DESIGN "dut"))\n')
        (self.root / "netlist/dut.postroute.spef").write_text(
            '*SPEF "IEEE 1481-1998"\n*DESIGN "dut"\n')
        (self.root / "database/dut.enc.dat").write_text("database fixture\n")

    def tearDown(self):
        self.temp.cleanup()

    def set_timing_profile(self, profile_id: str) -> dict[str, str]:
        timing = self.module._timing_profile_contract(profile_id)
        descriptor_path = self.root / "status/EXECUTION_DESCRIPTOR.json"
        descriptor = json.loads(descriptor_path.read_text())
        binding = descriptor["binding"]
        binding["timing_profile_id"] = timing["id"]
        binding["innovus_timing_profile_sha256"] = timing["profile_sha256"]
        binding["genus_timing_manifest_sha256"] = \
            timing["genus_timing_manifest_sha256"]
        binding["genus_timing_profile_sha256"] = \
            timing["genus_timing_profile_sha256"]
        binding["period_ns"] = timing["period_ns"]
        binding["hold_fix_allow_setup_tns_degrade"] = (
            timing["hold_fix_allow_setup_tns_degrade"] == "true")
        payload = (json.dumps(descriptor, sort_keys=True) + "\n").encode()
        descriptor_path.write_bytes(payload)
        (self.root / "status/EXECUTION_DESCRIPTOR.sha256").write_text(
            hashlib.sha256(payload).hexdigest() + "\n")
        for relative in ("status/TECHNOLOGY_CONTRACT",
                         "reports/boundary_timing.machine"):
            path = self.root / relative
            rows = dict(line.split("=", 1) for line in path.read_text().splitlines())
            rows["timing_profile"] = timing["id"]
            rows["timing_profile_sha256"] = timing["profile_sha256"]
            rows["period_ns"] = timing["period_ns"]
            rows["hold_fix_allow_setup_tns_degrade"] = timing[
                "hold_fix_allow_setup_tns_degrade"]
            path.write_text("".join(f"{key}={value}\n" for key, value in rows.items()))
        return timing

    def test_clean_fixture_passes(self):
        slacks = self.module.validate(self.root, "dut")
        self.assertEqual(set(slacks), {
            "setup", "hold", "recovery", "removal", "gating_setup",
            "gating_hold", "pulse_width", "half_cycle_setup", "half_cycle_hold",
        })

    def test_5p7_boundary_drive_forwarded_clock_and_hold_policy_pass(self):
        timing = self.set_timing_profile("three_endpoint_5p7ns")
        self.assertEqual(timing["period_ns"], "5.7")
        self.assertEqual(timing["hold_fix_allow_setup_tns_degrade"], "true")
        self.module.validate(self.root, "dut")

    def test_5p7_boundary_profile_mutations_fail_closed(self):
        for field, old, new in (
                ("clock_drive", "0", "1"),
                ("nonclock_driving_cell", "BUFX2", "BUFX4"),
                ("forwarded_link_clock",
                 "*w2_ep_icg_0/ECK,link_clk_o,divide_by_1",
                 "*w2_ep_icg_0/ECK,link_clk_o,divide_by_2"),
                ("link_clock_false_path", "FORBIDDEN", "ENABLED"),
                ("hold_fix_allow_setup_tns_degrade", "true", "false")):
            with self.subTest(field=field):
                self.set_timing_profile("three_endpoint_5p7ns")
                path = self.root / "reports/boundary_timing.machine"
                original = path.read_text()
                path.write_text(original.replace(f"{field}={old}",
                                                 f"{field}={new}"))
                with self.assertRaisesRegex(
                        self.module.QualificationError, "boundary timing"):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

    def test_descriptor_genus_timing_provenance_mutations_fail_closed(self):
        descriptor_path = self.root / "status/EXECUTION_DESCRIPTOR.json"
        sha_path = self.root / "status/EXECUTION_DESCRIPTOR.sha256"
        original = descriptor_path.read_bytes()
        for field in ("genus_timing_manifest_sha256",
                      "genus_timing_profile_sha256"):
            with self.subTest(field=field):
                document = json.loads(original)
                document["binding"][field] = "0" * 64
                payload = (json.dumps(document, sort_keys=True) + "\n").encode()
                descriptor_path.write_bytes(payload)
                sha_path.write_text(hashlib.sha256(payload).hexdigest() + "\n")
                with self.assertRaisesRegex(
                        self.module.QualificationError,
                        "execution descriptor timing"):
                    self.module.validate(self.root, "dut")
        descriptor_path.write_bytes(original)
        sha_path.write_text(hashlib.sha256(original).hexdigest() + "\n")

    def test_activity_annotation_exact_innovus_23_14_summary_parses(self):
        path = self.root / "reports/activity_annotation.rpt"
        self.assertEqual(
            self.module._activity_annotation(path),
            {"matched_nets": 320, "total_nets": 400, "coverage_percent": 80.0},
        )

    def test_hold_closure_receipt_fails_closed(self):
        path = self.root / "reports/hold_closure.machine"
        original = path.read_text()
        for mutation in (
                original.replace("status=CLOSED", "status=EXHAUSTED"),
                original.replace("observation_1=10,0,0.010,0.0",
                                 "observation_1=10,2,-0.100,-0.150"),
                original.replace("observation_1=10,0,0.010,0.0",
                                 "observation_1=10,1,-0.050,-0.050"),
                original.replace("observation_count=2", "observation_count=3"),
        ):
            with self.subTest(mutation=mutation):
                path.write_text(mutation)
                with self.assertRaises(self.module.QualificationError):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

    def test_activity_annotation_mutations_fail_closed(self):
        path = self.root / "reports/activity_annotation.rpt"
        original = path.read_text()
        mutations = {
            "missing": "'read_activity_file' finished successfully.\n",
            "missing_completion": original.replace(
                "'read_activity_file' finished successfully.\n", ""
            ),
            "malformed": original.replace("320/400 = 80%", "320 of 400 = 80%"),
            "duplicate": original + original,
            "duplicate_completion": original + (
                "'read_activity_file' finished successfully.\n"
            ),
            "duplicate_header": original.replace(
                "  Annotation coverage for this file\n",
                "  Annotation coverage for this file\n"
                "  Annotation coverage for this file\n",
            ),
            "malformed_duplicate_header": original.replace(
                "  Annotation coverage for this file\n",
                "  Annotation coverage for this file\n"
                "  Annotation coverage for this file (duplicate)\n",
            ),
            "zero": original.replace("320/400 = 80%", "0/400 = 0%"),
            "total_zero": original.replace("320/400 = 80%", "320/0 = 80%"),
            "matched_exceeds_total": original.replace(
                "320/400 = 80%", "401/400 = 100.25%"
            ),
            "percentage_mismatch": original.replace(
                "320/400 = 80%", "320/400 = 79%"
            ),
            "completion_before_summary": original.replace(
                "'read_activity_file' finished successfully.\n", ""
            ).replace(
                "  Annotation coverage for this file\n",
                "'read_activity_file' finished successfully.\n"
                "  Annotation coverage for this file\n",
            ),
            "report_local_error": original + "**ERROR: activity import failed\n",
            "noncanonical_count": original.replace(
                "320/400 = 80%", "0320/400 = 80%"
            ),
            "unbounded_integer": original.replace(
                "320/400 = 80%", f"{'9' * 500}/400 = 80%"
            ),
        }
        for name, payload in mutations.items():
            with self.subTest(name=name):
                path.write_text(payload)
                with self.assertRaisesRegex(
                    self.module.QualificationError, "activity annotation"
                ):
                    self.module.validate(self.root, "dut")
        path.write_text(original)

    def test_shared_typical_rc_contract_is_required_and_fail_closed(self):
        path = self.root / "status/TECHNOLOGY_CONTRACT"
        original = path.read_text()
        mutations = (
            original.replace("qrc_source_count=1", "qrc_source_count=2"),
            original.replace("shared_typical_gpdk045", "invented_hold_rc"),
            original.replace("fast_min_hold", "slow_max_hold"),
        )
        for payload in mutations:
            with self.subTest(payload=payload):
                path.write_text(payload)
                with self.assertRaisesRegex(
                    self.module.QualificationError, "technology contract"
                ):
                    self.module.validate(self.root, "dut")
        path.write_text(original)

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
                original = path.read_text()
                path.write_text(f"{key}=1\n")
                with self.assertRaisesRegex(self.module.QualificationError, "nonzero"):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

    def test_actual_23_14_place_and_connectivity_formats_fail_closed(self):
        place = self.root / "reports/check_place_post_place.rpt"
        place.write_text(
            "Begin checking placement ... (start mem=2940.8M, init mem=2940.8M)\n"
            "Overlapping with other instance:\t100\n"
            "*info: Placed = 296\n"
            "*info: Unplaced = 0\n"
            "Finished checkPlace (total: cpu=0:00:00.0, real=0:00:00.0)\n"
        )
        with self.assertRaisesRegex(
            self.module.QualificationError, r"placement_violations is nonzero \(100\)"
        ):
            self.module._require_zero(place, "placement_violations")
        place.write_text(
            "Begin checking placement ... (start mem=3178.3M, init mem=3178.3M)\n"
            "*info: Placed = 373            (Fixed = 16)\n"
            "*info: Unplaced = 0\n"
            "Finished checkPlace (total: cpu=0:00:00.1, real=0:00:00.0)\n"
        )
        self.module._require_zero(place, "placement_violations")

        header = (
            "###############################################################\n"
            "#  Generated by:      Cadence Innovus 23.14-s088_1\n"
            "###############################################################\n"
        )
        connectivity = self.root / "reports/connectivity.rpt"
        connectivity.write_text(
            header + "Begin Summary\n"
            "    24 Problem(s) (IMPVFC-96): Terminal(s) are not connected.\n"
            "    2 Problem(s) (IMPVFC-200): Special Wires are open.\n"
            "    4 Problem(s) (IMPVFC-92): Regular wires are open.\n"
            "    30 total info(s) created.\nEnd Summary\n"
        )
        with self.assertRaisesRegex(
            self.module.QualificationError, r"connectivity_violations is nonzero \(30\)"
        ):
            self.module._require_zero(connectivity, "connectivity_violations")
        connectivity.write_text(
            header
            + "Net VSS: dangling Wire at (2.000, 11.780) (2.000, 11.780) "
            "on layer: Metal1\n\n"
            "Begin Summary\n"
            "    1 Problem(s) (IMPVFC-94): The net has dangling wire(s).\n"
            "    1 total info(s) created.\nEnd Summary\n"
        )
        with self.assertRaisesRegex(
            self.module.QualificationError, r"connectivity_violations is nonzero \(1\)"
        ):
            self.module._require_zero(connectivity, "connectivity_violations")
        connectivity.write_text(
            header + "Begin Summary\n    Found no problems or warnings.\nEnd Summary\n"
        )
        self.module._require_zero(connectivity, "connectivity_violations")

        drc = self.root / "reports/drc.rpt"
        drc.write_text(
            header + "SHORT: ( Metal Short ) Special Wire of Net VDD & Pin "
            "of Cell FE_OFC18_source_accept_o_2  ( Metal1 )\n"
            "Bounds : ( 10.000, 30.530 ) ( 11.000, 30.650 )\n\n"
            "  Total Violations : 28 Viols.\n"
        )
        with self.assertRaisesRegex(
            self.module.QualificationError, r"drc_violations is nonzero \(28\)"
        ):
            self.module._require_zero(drc, "drc_violations")
        drc.write_text(
            header
            + "SHORT: ( Metal Short ) Regular Wire of Net FE_RN_15_0 & "
            "Special Wire of Net VDD  ( Metal1 )\n"
            "Bounds : ( 14.070, 10.010 ) ( 14.130, 10.130 )\n\n"
            "  Total Violations : 1 Viols.\n"
        )
        with self.assertRaisesRegex(
            self.module.QualificationError, r"drc_violations is nonzero \(1\)"
        ):
            self.module._require_zero(drc, "drc_violations")

    def test_actual_format_no_drive_and_no_load_are_rejected(self):
        path = self.root / "reports/check_timing.rpt"
        for warning in ("no_drive", "no_load"):
            with self.subTest(warning=warning):
                path.write_text(
                    "unconstrained_paths=0\n"
                    f"| {warning} | fixture warning | 1 |\n"
                )
                with self.assertRaisesRegex(
                    self.module.QualificationError, "no_drive/no_load"
                ):
                    self.module.validate(self.root, "dut")

    def test_tns_violation_and_vectorless_power_are_rejected(self):
        summary = self.root / "reports/setup_timing.machine"
        original = summary.read_text()
        for old, new in (("tns=0.0", "tns=-0.1"),
                         ("violation_count=0", "violation_count=1")):
            with self.subTest(new=new):
                summary.write_text(original.replace(old, new))
                with self.assertRaisesRegex(
                    self.module.QualificationError, "machine summary"
                ):
                    self.module.validate(self.root, "dut")
        summary.write_text(original)
        activity = self.root / "status/ACTIVITY_POWER_CONTRACT"
        activity.write_text(activity.read_text().replace(
            "mode=annotated_activity", "mode=vectorless"
        ))
        with self.assertRaisesRegex(
            self.module.QualificationError, "vectorless"
        ):
            self.module.validate(self.root, "dut")

    def test_interrupted_or_error_log_fails(self):
        for marker in ("**ERROR: bad", "ERROR: bad", "Error : bad",
                       "FATAL: bad", "INTERRUPT", "SEGMENTATION FAULT"):
            with self.subTest(marker=marker):
                (self.root / "tool.log").write_text(marker + "\n")
                with self.assertRaisesRegex(self.module.QualificationError, "log"):
                    self.module.validate(self.root, "dut")
        (self.root / "tool.log").write_text(clean_log())

    def test_innovus_error_limit_banner_is_not_an_error(self):
        log = clean_log().replace(
            "*** Message Summary:",
            "Error Limit = 1000; Warning Limit = 1000\n*** Message Summary:",
        )
        (self.root / "tool.log").write_text(log)
        self.module.validate(self.root, "dut")

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

    def test_missing_or_cross_top_sdf_spef_fails(self):
        for name in ("dut.postroute.sdf", "dut.postroute.spef"):
            with self.subTest(name=name):
                path = self.root / "netlist" / name
                original = path.read_text()
                path.write_text(original.replace("dut", "other_top"))
                with self.assertRaisesRegex(
                    self.module.QualificationError, "SDF|SPEF"
                ):
                    self.module.validate(self.root, "dut")
                path.write_text(original)

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
        with self.assertRaisesRegex(
            self.module.QualificationError, "no_drive/no_load"
        ):
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            )

        # The captured raw report is a negative calibration fixture.  Only an
        # explicit zero-count mutation demonstrates the remaining grammar.
        timing_text = paths["check_timing"].read_text()
        paths["check_timing"].write_text(
            self.module.TIMING_WARNING_ROW.sub(
                lambda match: f"| {match.group(1)} | calibrated zero | 0 |",
                timing_text,
            )
        )
        self.assertEqual(
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            ), {"setup": 0.042, "hold": 0.160}
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
        paths["check_timing"].write_text(
            self.module.TIMING_WARNING_ROW.sub(
                lambda match: f"| {match.group(1)} | calibrated zero | 0 |",
                paths["check_timing"].read_text(),
            )
        )
        with self.assertRaisesRegex(self.module.QualificationError, "setup WNS"):
            self.module.validate_minimum_signoff(
                paths["log"], paths["setup"], paths["hold"], paths["drc"],
                paths["antenna"], paths["check_timing"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
