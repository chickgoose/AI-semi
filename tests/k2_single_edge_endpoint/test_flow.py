from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[2]
FLOW_PATH = REPO / "physical/k2_single_edge_endpoint/flow.py"
SPEC = importlib.util.spec_from_file_location("k2_single_edge_flow", FLOW_PATH)
assert SPEC is not None and SPEC.loader is not None
flow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(flow)


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(flow.canonical(document))


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="k2-se-hardened-")
        self.root = Path(self.temp.name)
        self.here = self.root / "physical/k2_single_edge_endpoint"
        self.here.parent.mkdir(parents=True)
        shutil.copytree(REPO / "physical/k2_single_edge_endpoint", self.here)
        self.vectorless = self.root / "physical/k2_single_edge_vectorless"
        shutil.copytree(REPO / "physical/k2_single_edge_vectorless", self.vectorless)
        self.vectorless_contract_path = self.vectorless / "contract.json"
        self.vectorless_contract = json.loads(self.vectorless_contract_path.read_text())
        self.contract_path = self.here / "contract.json"
        self.contract = json.loads(self.contract_path.read_text())
        paths = {self.contract["source_policy"]["nested_generic_filelist"]["path"]}
        for row in self.contract["candidates"].values():
            paths.add(row["filelist"]["path"])
            paths.update(item["path"] for item in row["expanded_sources"])
        commit = self.contract["rtl_authority"]["source_commit"]
        for raw in paths:
            payload = subprocess.check_output(["git", "show", f"{commit}:{raw}"], cwd=REPO)
            target = self.root / raw
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)

    def patch(self):
        return mock.patch.multiple(flow, ROOT=self.root, HERE=self.here,
                                   CONTRACT=self.contract_path,
                                   VECTORLESS_CONTRACT=self.vectorless / "contract.json")

    def rewrite_contract(self) -> None:
        self.contract_path.write_bytes(flow.canonical(self.contract))

    def rewrite_vectorless_contract(self) -> None:
        self.vectorless_contract_path.write_bytes(flow.canonical(self.vectorless_contract))

    def close(self) -> None:
        self.temp.cleanup()


class ContractTests(unittest.TestCase):
    def test_repository_static_is_hold_and_eda_free(self):
        with mock.patch.object(flow.subprocess, "run", side_effect=AssertionError("EDA called")):
            receipt = flow.static_preflight(None)
        self.assertEqual(receipt["maximum_decision"],
                         "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE")
        self.assertFalse(receipt["candidate_physical_go_allowed"])
        self.assertNotIn("real_artifacts_verified", receipt)

    def test_hardened_six_source_closure_includes_error_latch(self):
        _, contract = flow.validate_contract()
        authority = contract["rtl_authority"]
        self.assertEqual(authority["source_commit"],
                         "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b")
        self.assertEqual(authority["integration_commit"],
                         "a0a4eb38632245db8ff5937ea5b6c6e3f3839246")
        for design in ("a2", "a3"):
            paths = [item["path"] for item in contract["candidates"][design]["expanded_sources"]]
            self.assertEqual(len(paths), 6)
            self.assertEqual(paths[1],
                             "rtl/technology/single_edge/w2_single_edge_error_latch.sv")
        self.assertEqual(contract["source_policy"]["nested_generic_filelist"]["entries"][0],
                         "rtl/technology/single_edge/w2_single_edge_error_latch.sv")

    def test_static_reports_integrated_hardened_worktree_bytes_present(self):
        self.assertEqual(flow.static_preflight(None)["candidate_sources_present"],
                         {"a2": True, "a3": True})

    def test_repository_local_compatibility_is_exact_and_eda_free(self):
        with mock.patch.object(flow.subprocess, "run", side_effect=AssertionError("EDA called")):
            receipt = flow.local_compatibility_preflight(None)
        self.assertEqual(receipt["status"],
                         "PASS_LOCAL_RTL_PHYSICAL_COMPATIBILITY")
        self.assertEqual(receipt["candidate_order"], ["a2", "a3"])
        self.assertFalse(receipt["live_tools_or_pdk_examined"])
        self.assertFalse(receipt["server_genus_smoke_executed"])
        self.assertFalse(receipt["candidate_physical_go_allowed"])
        self.assertTrue(receipt["vectorless_compatibility"]
                        ["exact_ordered_top_boundary"])
        self.assertEqual(receipt["vectorless_compatibility"]
                         ["exact_synthesis_defines"], ["SYNTHESIS"])
        for design in ("a2", "a3"):
            self.assertEqual(receipt["candidates"][design]["source_count"], 6)
            self.assertTrue(receipt["candidates"][design]["exact_ordered_top_boundary"])
            self.assertTrue(receipt["candidates"][design]["exact_module_inventory"])
            self.assertEqual(receipt["candidates"][design]["synthesis_defines"],
                             ["SYNTHESIS"])

    def test_endpoint_and_vectorless_complete_boundaries_are_identical(self):
        _, endpoint = flow.validate_contract()
        result = flow.validate_vectorless_compatibility(endpoint)
        self.assertTrue(result["exact_candidate_order"])
        self.assertTrue(result["exact_ordered_top_boundary"])
        self.assertEqual(result["exact_synthesis_defines"], ["SYNTHESIS"])

    def test_innovus_collects_safe_reports_before_timing_failure_exit(self):
        text = (REPO / "physical/k2_single_edge_endpoint/innovus_single_edge.tcl").read_text()
        setup_catch = text.index("set setup_failed [catch")
        hold_catch = text.index("set hold_failed [catch")
        area = text.index('report_area > "$output/reports/area.rpt"')
        drc = text.index('verify_drc -report "$output/reports/drc.rpt"')
        timing_failure = text.index('if {[llength $diagnostic_failures] != 0}')
        completion = text.index("K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE", timing_failure)
        self.assertLess(setup_catch, hold_catch)
        self.assertLess(hold_catch, area)
        self.assertLess(area, drc)
        self.assertLess(drc, timing_failure)
        self.assertLess(timing_failure, completion)
        self.assertIn('se_append_report_context "$output/reports/area.rpt" area postroute',
                      text)


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.patch = self.fixture.patch()
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.fixture.close()

    def test_plan_binds_six_current_worktree_sources(self):
        plan = flow.make_plan("a2", self.fixture.root / "attempt",
                              self.fixture.root / "attempt/plan.json")
        self.assertEqual(len(plan["sources"]), 6)
        self.assertIn("error_latch", plan["sources"][1]["path"])

    def test_synthesis_text_excludes_comments_and_inactive_conditionals(self):
        payload = b"""/* module commented_out; endmodule */
`ifdef UNUSED_MUTATION
module inactive_ifdef; endmodule
`else
module synthesis_active; endmodule
`endif
`ifndef SYNTHESIS
module simulation_only; endmodule
`endif
"""
        text = flow.synthesis_text(payload, "fixture")
        self.assertEqual(flow.MODULE.findall(text), ["synthesis_active"])
        for payload in (b"`ifdef SYNTHESIS\nmodule x; endmodule\n",
                        b"`else\nmodule x; endmodule\n",
                        b"`ifdef SYNTHESIS\n`else\n`else\n`endif\n"):
            with self.assertRaises(flow.FlowError):
                flow.synthesis_text(payload, "malformed fixture")

    def test_source_boundary_rejects_order_direction_width_missing_and_extra(self):
        _, contract = flow.validate_contract()
        top = contract["candidates"]["a2"]["top"]
        rows = [("input", row) for row in contract["boundary"]["normalized_ports"]["inputs"]]
        rows += [("output", row) for row in contract["boundary"]["normalized_ports"]["outputs"]]

        def declaration(direction, row):
            width = f" [{row['width'] - 1}:0]" if row["width"] > 1 else ""
            return f"  {direction} logic{width} {row['name']}"

        declarations = [declaration(direction, row) for direction, row in rows]

        def payload(items):
            return (f"module {top} (\n" + ",\n".join(items) +
                    "\n);\nendmodule\n").encode()

        flow.validate_top_boundary(payload(declarations), top, contract, "good RTL top")
        mutants = {
            "order": declarations[:-2] + [declarations[-1], declarations[-2]],
            "direction": [item.replace("output logic [15:0] source_accept_o",
                                        "input logic [15:0] source_accept_o")
                          for item in declarations],
            "width": [item.replace("output logic [1:0] accept_count_o",
                                    "output logic [2:0] accept_count_o")
                      for item in declarations],
            "missing": declarations[:-1],
            "extra": declarations + ["  output logic unexpected_o"],
            "ascending-range": [item.replace("input logic [15:0] source_pending_i",
                                               "input logic [0:15] source_pending_i")
                                for item in declarations],
            "symbolic-range": [item.replace("input logic [15:0] source_pending_i",
                                              "input logic [SOURCE_COUNT-1:0] source_pending_i")
                               for item in declarations],
            "multidimensional-range": [item.replace(
                "input logic [15:0] source_pending_i",
                "input logic [15:0][1:0] source_pending_i")
                for item in declarations],
            "unpacked-range": [item.replace("input logic [15:0] source_pending_i",
                                              "input logic source_pending_i [15:0]")
                               for item in declarations],
        }
        for name, items in mutants.items():
            with self.subTest(name=name), self.assertRaisesRegex(
                    flow.FlowError, "exact complete boundary|directions/widths|range"):
                flow.validate_top_boundary(payload(items), top, contract,
                                           f"{name} RTL top")

    def test_local_compatibility_rejects_vectorless_boundary_and_define_drift(self):
        mutations = ("output-order", "synthesis-define")
        for mutation in mutations:
            original = json.loads(json.dumps(self.fixture.vectorless_contract))
            if mutation == "output-order":
                outputs = self.fixture.vectorless_contract["candidates"] \
                    ["a2_single_edge"]["outputs"]
                outputs[-2], outputs[-1] = outputs[-1], outputs[-2]
            else:
                self.fixture.vectorless_contract["execution_policy"] \
                    ["synthesis_defines"] = []
            self.fixture.rewrite_vectorless_contract()
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    flow.FlowError, "vectorless compile boundary|synthesis defines"):
                flow.local_compatibility_preflight(None)
            self.fixture.vectorless_contract = original
            self.fixture.rewrite_vectorless_contract()

    def test_local_compatibility_rejects_vectorless_driver_without_synthesis_define(self):
        driver = self.fixture.root / self.fixture.vectorless_contract["templates"] \
            ["driver"]["path"]
        driver.write_text(driver.read_text().replace("-define SYNTHESIS ", "", 1))
        self.fixture.vectorless_contract["templates"]["driver"]["sha256"] = \
            flow.sha256(driver.read_bytes())
        self.fixture.rewrite_vectorless_contract()
        with self.assertRaisesRegex(flow.FlowError, "SYNTHESIS-defined RTL read"):
            flow.local_compatibility_preflight(None)

    def test_old_or_missing_latch_bytes_reject(self):
        latch = self.fixture.root / self.fixture.contract["candidates"]["a2"]["expanded_sources"][1]["path"]
        latch.write_text("module w2_single_edge_error_latch; endmodule\n")
        with self.assertRaisesRegex(flow.FlowError, "RTL authority"):
            flow.make_plan("a2", self.fixture.root / "attempt",
                           self.fixture.root / "attempt/plan.json")

    def test_symlinked_rtl_rejects_even_with_identical_bytes(self):
        item = self.fixture.contract["candidates"]["a2"]["expanded_sources"][1]
        path = self.fixture.root / item["path"]
        copy = self.fixture.root / "latch-copy.sv"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
        with self.assertRaisesRegex(flow.FlowError, "symlink"):
            flow.make_plan("a2", self.fixture.root / "attempt",
                           self.fixture.root / "attempt/plan.json")

    def test_each_constraint_value_is_immutable(self):
        for key in list(self.fixture.contract["constraints"]["values"]):
            original = self.fixture.contract["constraints"]["values"][key]
            self.fixture.contract["constraints"]["values"][key] = "9.9"
            self.fixture.rewrite_contract()
            with self.subTest(key=key), self.assertRaisesRegex(flow.FlowError, "classification"):
                flow.validate_contract()
            self.fixture.contract["constraints"]["values"][key] = original

    def test_sdc_exceptions_and_comment_only_substitution_reject(self):
        sdc = self.fixture.root / self.fixture.contract["constraints"]["sdc"]
        for command in ("set_false_path -from [all_inputs]", "set_multicycle_path 2",
                        "set_case_analysis 0 [get_ports link_enable_i]",
                        "set_disable_timing [get_cells *]"):
            original = sdc.read_bytes()
            sdc.write_bytes(original + (command + "\n").encode())
            with self.subTest(command=command), self.assertRaisesRegex(flow.FlowError, "SDC byte"):
                flow.validate_contract()
            sdc.write_bytes(original)
        sdc.write_text("# create_clock\n# set_input_delay\n# set_output_delay\n")
        with self.assertRaisesRegex(flow.FlowError, "SDC byte"):
            flow.validate_contract()

    def test_template_command_removal_rejects(self):
        row = self.fixture.contract["flow_templates"]["genus"]
        path = self.fixture.root / row["path"]
        path.write_text(path.read_text().replace("syn_map", "# syn_map", 1))
        with self.assertRaisesRegex(flow.FlowError, "template byte identity"):
            flow.validate_contract()

    def test_genus_synthesis_define_is_semantically_pinned(self):
        row = self.fixture.contract["flow_templates"]["genus"]
        path = self.fixture.root / row["path"]
        path.write_text(path.read_text().replace(" -define SYNTHESIS", "", 1))
        digest = flow.sha256(path.read_bytes())
        row["sha256"] = digest
        self.fixture.rewrite_contract()
        with mock.patch.dict(flow.TEMPLATE_IDENTITIES,
                             {"genus": (row["path"], digest)}):
            with self.assertRaisesRegex(flow.FlowError, "read_hdl synthesis define"):
                flow.validate_contract()

    def test_mapped_sdc_exact_values_and_no_exceptions(self):
        inputs = "rst_i link_enable_i source_pending_i"
        outputs = "source_accept_o accept_count_o accept_addr0_o accept_addr1_o link_valid_o link_addr0_o link_addr1_o retire_valid_o retire_addr0_o retire_addr1_o protocol_error_o drain_idle_o"
        good = f"""create_clock -name se_primary_clk -period 6.5 -waveform {{0.0 3.25}} [get_ports {{clk_i}}]
set_clock_uncertainty 0.25 [get_clocks {{se_primary_clk}}]
set_min_pulse_width -high 0.50 [get_clocks {{se_primary_clk}}]
set_min_pulse_width -low 0.50 [get_clocks {{se_primary_clk}}]
set_input_delay -clock se_primary_clk -min 0.10 [get_ports {{{inputs}}}]
set_input_delay -clock se_primary_clk -max 0.50 [get_ports {{{inputs}}}]
set_input_transition 0.05 [get_ports {{{inputs}}}]
set_output_delay -clock se_primary_clk -min 0.10 [get_ports {{{outputs}}}]
set_output_delay -clock se_primary_clk -max 0.50 [get_ports {{{outputs}}}]
set_load 0.01 [get_ports {{{outputs}}}]
"""
        flow.validate_sdc(good, self.fixture.contract)
        bad_sdcs = (
            good.replace("6.5", "7.0", 1),
            good.replace("set_min_pulse_width -high 0.50", "set_min_pulse_width -high 0.60", 1),
            good.replace("set_min_pulse_width -low 0.50", "", 1),
            good + "set_false_path -from [all_inputs]\n",
            good.rstrip() + "; set_false_path -from [all_inputs]\n",
            good + "create_clock -period 9.0 [get_ports {rst_i}]\n",
            good.replace(inputs, "rst_i source_pending_i", 1),
            good.replace("set_load 0.01", "set_load 0.01; set_load 0.02", 1),
        )
        for bad in bad_sdcs:
            with self.assertRaisesRegex(flow.FlowError, "mapped SDC"):
                flow.validate_sdc(bad, self.fixture.contract)

    def test_strong_zero_report_parser_rejects_contradictions(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        version = self.fixture.contract["tools"]["innovus"]["version"]
        contexts = {"drc": "postroute", "antenna": "postroute",
                    "connectivity": "signal_postroute"}
        commands = {"drc": "verify_drc -report drc.rpt",
                    "antenna": "verify_process_antenna -report antenna.rpt",
                    "connectivity": ("verifyConnectivity -type all -error 1000 "
                                     "-warning 1000 -report connectivity.rpt")}
        def header(kind):
            return (f"#  Generated by:      Cadence Innovus {version}\n"
                    f"#  Design:            {top}\n"
                    f"#  Command:           {commands[kind]}\n")
        markers = {kind: flow.report_context_line("Innovus", version, top, kind, context)
                   for kind, context in contexts.items()}
        bad = {
            "drc": (header("drc") + f"No DRC violations were found\n"
                    f"DRC VIOLATION #1\n{markers['drc']}\n").encode(),
            "antenna": (header("antenna") + f"No Violations Found\n"
                         f"3 violations\n{markers['antenna']}\n").encode(),
            "connectivity": (header("connectivity") + "Begin Summary\n"
                             "Found no problems or warnings.\nEnd Summary\n"
                             f"check incomplete\n{markers['connectivity']}\n").encode(),
        }
        for kind, payload in bad.items():
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    flow.FlowError, "contradictory|nonzero"):
                flow.require_zero_native(payload, kind, top, version, contexts[kind])
        for kind, phrase in (("drc", "No DRC violations were found"),
                             ("antenna", "No Violations Found"),
                             ("connectivity", "Found no problems or warnings.")):
            with self.subTest(comment_only=kind), self.assertRaisesRegex(
                    flow.FlowError, "comment-only"):
                flow.require_zero_native(f"# {phrase}\n".encode(), kind, top, version,
                                         contexts[kind])

    def test_timing_and_check_timing_require_active_exact_context(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        version = self.fixture.contract["tools"]["innovus"]["version"]
        comment = f"# Design: {top}; timing setup slack: 0.12\nnonempty unrelated\n"
        with self.assertRaisesRegex(flow.FlowError, "bound native"):
            flow.require_report(comment.encode(), "setup timing", top,
                                (r"timing", r"setup", r"slack"))
        classes = ("no_clock", "no_input_delay", "no_output_delay",
                   "unconstrained", "no_drive", "no_load")
        check = "# TIMING CHECK SUMMARY\n" + "\n".join(
            f"# {name} | count | 0" for name in classes)
        with self.assertRaisesRegex(flow.FlowError, "comment-only"):
            flow.validate_check_timing(check.encode(), top, version)

    def test_native_innovus_slack_check_timing_and_clean_report_forms(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        version = self.fixture.contract["tools"]["innovus"]["version"]
        header = lambda command: (f"#  Generated by:      Cadence Innovus {version}\n"
                                  f"#  Design:            {top}\n"
                                  f"#  Command:           {command}\n")
        timing = (header("report_timing -view se_setup_view -check_type setup -max_paths 50") +
                  "Path 1: MET (0.123 ns) Setup Check\n"
                  "= Slack Time 0.123\n" +
                  flow.report_context_line("Innovus", version, top,
                                           "setup_timing", "postroute") + "\n")
        flow.validate_innovus_timing(timing.encode(), top, version, "setup",
                                     {"path_count": 1, "wns": 0.12349, "tns": 0.0})
        with self.assertRaisesRegex(flow.FlowError, "sequential MET paths"):
            flow.validate_innovus_timing(timing.encode(), top, version, "setup",
                                         {"path_count": 2, "wns": 0.12349, "tns": 0.0})
        fifty_paths = "".join(
            f"Path {index}: MET (0.123 ns) Setup Check\n= Slack Time 0.123\n"
            for index in range(1, 51))
        capped = (header("report_timing -view se_setup_view -check_type setup "
                         "-max_paths 50") + fifty_paths +
                  flow.report_context_line("Innovus", version, top,
                                           "setup_timing", "postroute") + "\n")
        flow.validate_innovus_timing(capped.encode(), top, version, "setup",
                                     {"path_count": 51, "wns": 0.123, "tns": 0.0})
        check = (header("check_timing -verbose") +
                 "+---------------- TIMING CHECK SUMMARY ----------------+\n"
                 "| ideal_clock_waveform | Clock waveform is ideal | 1 |\n"
                 "TIMING CHECK IDEAL CLOCKS\n"
                 "| se_primary_clk | se_setup_view |\n" +
                 flow.report_context_line("Innovus", version, top,
                                          "check_timing", "postroute") + "\n")
        flow.validate_check_timing(check.encode(), top, version)
        clean = {
            "drc": ("verify_drc -report drc.rpt", "No DRC violations were found\n", "postroute"),
            "antenna": ("verify_process_antenna -report antenna.rpt", "No Violations Found\n", "postroute"),
            "connectivity": ("verifyConnectivity -type all -error 1000 -warning 1000 "
                             "-report connectivity.rpt",
                             "Begin Summary\nFound no problems or warnings.\nEnd Summary\n",
                             "signal_postroute"),
        }
        for kind, (command, body, context) in clean.items():
            payload = (header(command) + body + flow.report_context_line(
                "Innovus", version, top, kind, context) + "\n").encode()
            flow.require_zero_native(payload, kind, top, version, context)

    def test_foreign_violated_no_slack_and_failure_spellings_reject(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        version = self.fixture.contract["tools"]["innovus"]["version"]
        header = (f"#  Generated by:      Cadence Innovus {version}\n"
                  f"#  Design:            {top}\n"
                  "#  Command:           report_timing -view se_setup_view "
                  "-check_type setup -max_paths 50\n")
        context = flow.report_context_line(
            "Innovus", version, top, "setup_timing", "postroute")
        clean = (header + "Path 1: MET (0.123 ns) Setup Check\n"
                 "= Slack Time 0.123\n" + context + "\n")
        metrics = {"path_count": 1, "wns": 0.123, "tns": 0.0}
        flow.validate_innovus_timing(clean.encode(), top, version, "setup", metrics)
        timing_mutations = (
            clean + "Path 2: VIOLATED (foreign) Hold Check\nforeign no-slack text\n",
            clean + "foreign timing text: VIOLATED and no_slack\n",
            clean.replace("-max_paths 50", "-max_paths 50 caller_fabricated", 1),
        )
        for payload in timing_mutations:
            with self.subTest(payload=payload[-70:]), self.assertRaises(flow.FlowError):
                flow.validate_innovus_timing(
                    payload.encode(), top, version, "setup", metrics)

        genus_version = self.fixture.contract["tools"]["genus"]["version"]
        genus_log = (f"Version: {genus_version}\nInfo=1, Error=0, Fatal=0\n"
                     f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\nNormal exit.\n")
        innovus_log = (f"Version: v{version}, test\n"
                       f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n"
                       "*** Message Summary: 0 warning(s), 0 error(s)\n"
                       "--- Ending \"Innovus\" (test) ---\n")
        flow.validate_genus_log(genus_log, top, genus_version)
        flow.validate_innovus_log(innovus_log, top, version)
        for diagnostic in ("Error=10", "10 errors", "fatal-text diagnostics",
                           "ERROR", "FATAL", "(ERROR)", "(FATAL)",
                           "ERRORS", "FATALS", "ERROR(10)", "FATAL (10)",
                           "10 ERROR", "10 FATAL"):
            with self.subTest(tool="genus", diagnostic=diagnostic), \
                    self.assertRaisesRegex(flow.FlowError, "zero-error"):
                flow.validate_genus_log(
                    genus_log.replace("Normal exit.", diagnostic + "\nNormal exit."),
                    top, genus_version)
            with self.subTest(tool="innovus", diagnostic=diagnostic), \
                    self.assertRaisesRegex(flow.FlowError, "zero-error"):
                flow.validate_innovus_log(
                    innovus_log.replace('--- Ending', diagnostic + '\n--- Ending'),
                    top, version)
            with self.subTest(report=diagnostic), self.assertRaisesRegex(
                    flow.FlowError, "failure diagnostics"):
                flow.report_texts(("active native report\n" + diagnostic + "\n").encode(),
                                  "mutated report")

    def test_netlist_requires_exact_boundary_and_connectivity(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        rows = [("input", row) for row in
                self.fixture.contract["boundary"]["normalized_ports"]["inputs"]] + [
                ("output", row) for row in
                self.fixture.contract["boundary"]["normalized_ports"]["outputs"]]
        names = [row["name"] for _, row in rows]
        declarations = "\n".join(
            f"{direction} " + (f"[{row['width'] - 1}:0] " if row["width"] > 1 else "") +
            f"{row['name']};" for direction, row in rows)
        good = (f"module {top}({','.join(names)});\n{declarations}\n"
                "DFFX1 u0 (.D(clk_i), .Q(protocol_error_o));\nendmodule\n").encode()
        flow.validate_netlist(good, top, self.fixture.contract, "mapped_netlist")
        mutations = (
            good.replace(b");\n", b",uncontracted_debug_o);\n", 1),
            good.replace(b"input [15:0] source_pending_i;", b"input source_pending_i;"),
            good.replace(b"output protocol_error_o;", b"input protocol_error_o;"),
            good.replace(b"DFFX1 u0 (.D(clk_i), .Q(protocol_error_o));", b"wire unused;"),
        )
        for payload in mutations:
            with self.assertRaisesRegex(flow.FlowError,
                                        "exact complete|directions/widths|connectivity"):
                flow.validate_netlist(payload, top, self.fixture.contract, "mapped_netlist")

    def test_empty_netlist_comment_sdf_and_header_spef_reject(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        with self.assertRaisesRegex(flow.FlowError, "header|structural"):
            flow.validate_netlist(f"module {top}; endmodule\n".encode(), top,
                                  self.fixture.contract, "mapped_netlist")
        with self.assertRaisesRegex(flow.FlowError, "comment-only"):
            flow.validate_sdf(b"/* (DELAYFILE) */\n", top, "mapped SDF")
        with self.assertRaisesRegex(flow.FlowError, "RC structure"):
            flow.validate_spef(f'*SPEF "IEEE"\n*DESIGN "{top}"\n'.encode(), top)

    def test_stable_read_detects_lstat_to_open_replacement(self):
        target = self.fixture.root / "race.txt"
        replacement = self.fixture.root / "replacement.txt"
        target.write_text("same bytes\n")
        replacement.write_text("same bytes\n")
        original_open = flow.os.open
        fired = False
        def racing_open(path, flags, *args):
            nonlocal fired
            if not fired and Path(path) == target:
                fired = True
                target.unlink()
                replacement.rename(target)
            return original_open(path, flags, *args)
        with mock.patch.object(flow.os, "open", side_effect=racing_open):
            with self.assertRaisesRegex(flow.FlowError, "before open"):
                flow.stable_read(target)

    def test_hardlink_and_artifact_parent_symlink_reject(self):
        original = self.fixture.root / "original.bin"
        linked = self.fixture.root / "linked.bin"
        original.write_bytes(b"bound bytes\n")
        os.link(original, linked)
        with self.assertRaisesRegex(flow.FlowError, "hard link"):
            flow.stable_read(linked)
        attempt = self.fixture.root / "attempt-fs"
        outside = self.fixture.root / "outside"
        attempt.mkdir(); outside.mkdir()
        (outside / "x.rpt").write_text("report\n")
        (attempt / "reports").symlink_to(outside)
        with self.assertRaisesRegex(flow.FlowError, "not a real directory"):
            flow.safe_artifact(attempt, "reports/x.rpt")

    def test_live_environment_unknown_top_field_rejects_before_live_reads(self):
        contract_payload, contract = flow.validate_contract()
        fake = flow.seal({"schema": "k2_single_edge_live_environment_snapshot_v2",
            "contract_sha256": flow.sha256(contract_payload), "tools": {}, "technology": {},
            "environment_allowlist": {}, "environment_allowlist_sha256": flow.sha256(b"{}\n"),
            "live_bytes_reverified": True, "candidate_physical_go_allowed": False,
            "unreviewed": True})
        path = self.fixture.root / "bad-live-env.json"
        write_json(path, fake)
        with self.assertRaisesRegex(flow.FlowError, "fields differ"):
            flow.validate_live_environment(path, flow.sha256(contract_payload), contract)

    def test_nonzero_execution_receipt_classifies_outputs_unbound(self):
        attempt = self.fixture.root / "failed-attempt"
        plan_path = attempt / "plan.json"
        flow.make_plan("a2", attempt, plan_path)
        environment_path = attempt / "LIVE_ENVIRONMENT.json"
        environment = {"environment_allowlist": {
            key: "" for key in flow.ENV_ALLOWLIST_KEYS}}
        environment_payload = flow.canonical(environment)
        environment_path.write_bytes(environment_payload)

        def fail_tool(*args, **kwargs):
            reports = attempt / "genus/reports"
            reports.mkdir()
            (reports / "timing.rpt").write_text(
                "safely collected before nonzero timing exit\n")
            kwargs["stdout"].write(b"tool exited after diagnostic collection\n")
            return mock.Mock(returncode=1)

        with mock.patch.object(flow, "validate_live_environment", return_value=(
                environment_payload, environment)), \
                mock.patch.object(flow.subprocess, "run", side_effect=fail_tool):
            with self.assertRaisesRegex(flow.FlowError, "exited nonzero"):
                flow.execute_stage(
                    "a2", "genus", plan_path, environment_path,
                    "I_UNDERSTAND_THIS_LAUNCHES_REAL_GENUS")
        receipt = json.loads((attempt / "genus/EXECUTION_RECEIPT.json").read_text())
        self.assertEqual(receipt["schema"], "k2_single_edge_execution_receipt_v3")
        self.assertEqual(receipt["status"], "FAIL_NONZERO_EXIT")
        self.assertEqual(receipt["artifact_evidence_classification"],
                         "UNBOUND_NONZERO_EXIT_FILES_NOT_LEDGER_ELIGIBLE")
        self.assertEqual(receipt["artifacts"], [])
        self.assertFalse(receipt["candidate_physical_go_allowed"])
        self.assertTrue((attempt / "genus/reports/timing.rpt").is_file())
        self.assertNotIn("genus/reports/timing.rpt", json.dumps(receipt))

    def test_cohort_binds_same_environment_hash_but_remains_unready_hold(self):
        contract_payload, contract = flow.validate_contract()
        environment_sha = "a" * 64
        paths = {}
        for design in ("a2", "a3"):
            receipt = flow.seal({
                "schema": "k2_single_edge_physical_qualification_v3",
                "design": design,
                "decision": "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE",
                "contract_sha256": flow.sha256(contract_payload),
                "environment_receipt_sha256": environment_sha,
                "command_plan_sha256": ("b" if design == "a2" else "c") * 64,
                "artifact_ledger_sha256": ("d" if design == "a2" else "e") * 64,
                "diagnostic_metrics_only": {"setup": {}, "hold": {}, "area": 1.0},
                "diagnostic_artifact_checks_completed": True,
                "diagnostic_evidence_scope": "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY",
                "producer_authenticated": False,
                "constraint_evidence_class": contract["constraints"]["evidence_class"],
                "candidate_physical_go": False,
                "promotion_requires_new_reviewed_contract": True,
                "promotion_requires_out_of_band_producer_authentication": True,
            })
            paths[design] = self.fixture.root / f"{design}-qualification.json"
            write_json(paths[design], receipt)
        result = flow.bind_cohort(paths["a2"], paths["a3"],
                                  self.fixture.root / "cohort.json")
        self.assertEqual(result["decision"],
                         "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE")
        self.assertEqual(result["diagnostic_same_environment_snapshot_sha256"],
                         environment_sha)
        self.assertFalse(result["freshness_verified"])
        self.assertFalse(result["comparison_ready"])
        self.assertFalse(result["producer_authenticated"])
        self.assertFalse(result["candidate_physical_go"])

        a3 = json.loads(paths["a3"].read_text())
        a3.pop("document_sha256")
        a3["environment_receipt_sha256"] = "f" * 64
        write_json(paths["a3"], flow.seal(a3))
        with self.assertRaisesRegex(flow.FlowError, "same environment"):
            flow.bind_cohort(paths["a2"], paths["a3"],
                             self.fixture.root / "different-cohort.json")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.patch = self.fixture.patch()
        self.patch.start()
        self.root = self.fixture.root / "attempt"
        self.plan_path = self.root / "plan.json"
        self.plan = flow.make_plan("a2", self.root, self.plan_path)
        _, self.contract = flow.validate_contract()
        self.top = self.plan["top"]

    def tearDown(self):
        self.patch.stop()
        self.fixture.close()

    def _content(self, role: str) -> bytes:
        top = self.top
        port_rows = [("input", item) for item in
                     self.contract["boundary"]["normalized_ports"]["inputs"]] + [
                     ("output", item) for item in
                     self.contract["boundary"]["normalized_ports"]["outputs"]]
        ports = [item["name"] for _, item in port_rows]
        declarations = "\n".join(
            f"{direction} " + (f"[{item['width'] - 1}:0] " if item["width"] > 1 else "") +
            f"{item['name']};" for direction, item in port_rows)
        netlist = (f"module {top}({','.join(ports)});\n{declarations}\n" +
                   "DFFX1 u0 (.D(clk_i), .Q(protocol_error_o));\nendmodule\n").encode()
        inputs = "rst_i link_enable_i source_pending_i"
        outputs = "source_accept_o accept_count_o accept_addr0_o accept_addr1_o link_valid_o link_addr0_o link_addr1_o retire_valid_o retire_addr0_o retire_addr1_o protocol_error_o drain_idle_o"
        sdc = f"""create_clock -name se_primary_clk -period 6.5 -waveform {{0.0 3.25}} [get_ports {{clk_i}}]
set_clock_uncertainty 0.25 [get_clocks {{se_primary_clk}}]
set_min_pulse_width -high 0.50 [get_clocks {{se_primary_clk}}]
set_min_pulse_width -low 0.50 [get_clocks {{se_primary_clk}}]
set_input_delay -clock se_primary_clk -min 0.10 [get_ports {{{inputs}}}]
set_input_delay -clock se_primary_clk -max 0.50 [get_ports {{{inputs}}}]
set_input_transition 0.05 [get_ports {{{inputs}}}]
set_output_delay -clock se_primary_clk -min 0.10 [get_ports {{{outputs}}}]
set_output_delay -clock se_primary_clk -max 0.50 [get_ports {{{outputs}}}]
set_load 0.01 [get_ports {{{outputs}}}]
""".encode()
        version = self.contract["tools"]["innovus"]["version"]
        genus_version = self.contract["tools"]["genus"]["version"]
        genus_header = ("Generated by: Genus(TM) Synthesis Solution "
                        f"{genus_version}\nModule: {top}\n")
        def innovus_header(command: str) -> str:
            return (f"#  Generated by:      Cadence Innovus {version}\n"
                    f"#  Design:            {top}\n"
                    f"#  Command:           {command}\n")
        context = lambda kind, value: (flow.report_context_line(
            "Innovus", version, top, kind, value) + "\n").encode()
        table = {
            "genus_log": (f"Version: {self.contract['tools']['genus']['version']}\nInfo=1, Error=0, Fatal=0\nK2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\nNormal exit.\n").encode(),
            "innovus_log": (f"Version: v{self.contract['tools']['innovus']['version']}, test\nK2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n*** Message Summary: 0 warning(s), 0 error(s)\n--- Ending \"Innovus\" (test) ---\n").encode(),
            "setup_timing": (innovus_header(
                "report_timing -view se_setup_view -check_type setup -max_paths 50") +
                "Path 1: MET (0.120 ns) Setup Check\n= Slack Time 0.120\n").encode() +
                context("setup_timing", "postroute"),
            "hold_timing": (innovus_header(
                "report_timing -view se_hold_view -check_type hold -max_paths 50") +
                "Path 1: MET (0.040 ns) Hold Check\n= Slack Time 0.040\n").encode() +
                context("hold_timing", "postroute"),
            "setup_timing_machine": b"schema=k2_single_edge_timing_summary_v1\nview=se_setup_view\ncheck=setup\npath_count=1\nviolation_count=0\nwns=0.12\ntns=0.0\n",
            "hold_timing_machine": b"schema=k2_single_edge_timing_summary_v1\nview=se_hold_view\ncheck=hold\npath_count=1\nviolation_count=0\nwns=0.04\ntns=0.0\n",
            "drc": (innovus_header(
                f"verify_drc -report {self.root}/innovus/reports/drc.rpt") +
                    "No DRC violations were found\n").encode() +
                   context("drc", "postroute"),
            "antenna": (innovus_header(
                f"verify_process_antenna -report {self.root}/innovus/reports/antenna.rpt") +
                "No Violations Found\n").encode() +
                       context("antenna", "postroute"),
            "connectivity": (innovus_header(
                "verifyConnectivity -type all -error 1000 -warning 1000 "
                f"-report {self.root}/innovus/reports/connectivity.rpt") +
                "Begin Summary\nFound no problems or warnings.\nEnd Summary\n").encode() +
                            context("connectivity", "signal_postroute"),
            "pg_connectivity": (innovus_header(
                "verifyConnectivity -type special -error 1000 -warning 1000 "
                f"-report {self.root}/innovus/reports/pg_connectivity.rpt") +
                "Begin Summary\nFound no problems or warnings.\nEnd Summary\n").encode() +
                               context("pg_connectivity", "pg_postroute"),
            "check_timing": (innovus_header("check_timing -verbose") +
                "TIMING CHECK SUMMARY\n"
                "| ideal_clock_waveform | Clock waveform is ideal | 1 |\n"
                "TIMING CHECK IDEAL CLOCKS\n"
                "| se_primary_clk | se_setup_view |\n").encode() +
                context("check_timing", "postroute"),
            "mapped_sdc": sdc, "mapped_netlist": netlist, "postroute_netlist": netlist,
            "mapped_sdf": f'(DELAYFILE (SDFVERSION "4.0") (DESIGN "{top}") (CELL (CELLTYPE "DFFX1") (INSTANCE u0) (DELAY (ABSOLUTE (IOPATH D Q (0.1)(0.1))))))\n'.encode(),
            "postroute_sdf": f'(DELAYFILE (SDFVERSION "4.0") (DESIGN "{top}") (CELL (CELLTYPE "DFFX1") (INSTANCE u0) (DELAY (ABSOLUTE (IOPATH D Q (0.1)(0.1))))))\n'.encode(),
            "postroute_spef": f'*SPEF "IEEE 1481"\n*DESIGN "{top}"\n*D_NET n1 0.1\n*CONN\n*P clk_i I\n*CAP\n1 n1 0.1\n*RES\n1 n1 clk_i 1.0\n*END\n'.encode(),
            "postroute_area": (innovus_header("report_area") +
                               "Type Count Area\nTotal: 42 123.45\n").encode() +
                               context("area", "postroute"),
            "genus_commands_complete": f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\n".encode(),
            "innovus_commands_complete": f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n".encode(),
            "innovus_database_manifest": (f"Design: {top}\ncheckpoint={top}.enc\nentry=saveDesign-mmmc2\nproducer_authentication=UNAUTHENTICATED_LOCAL_SELF_HASH\n").encode(),
        }
        if role in table:
            return table[role]
        genus_reports = {
            "genus_timing": "Path 1: MET (120 ps) Setup Check\nSlack:= 120",
            "genus_area": ("Instance Module Cell-Count Cell-Area Net-Area Total-Area\n"
                           f"{top} NA 42 120.0 3.0 123.0"),
            "genus_check_design": "Check Design summary",
            "genus_timing_intent": "Check Timing Intent summary",
            "genus_qor": "Quality of Results Summary\nWNS (ps): 120",
            "genus_power": "Power summary\nTotal 0.001 W",
            "genus_clocks": "Clock se_primary_clk",
        }
        if role in genus_reports:
            return (genus_header + genus_reports[role] + "\n").encode()
        native = {
            "postroute_power": (innovus_header("report_power") +
                                "Power summary\nTotal 0.001 W\n"),
            "check_design_pre_place": ("Design check done.\n"
                                       "*** Message Summary: 3 warning(s), 0 error(s)\n"),
            "check_place": ("Begin checking placement ...\n"
                            "*info: Placed = 42\n*info: Unplaced = 0\n"
                            "Finished checkPlace (total: cpu=0:00:00.1)\n"),
            "check_design_post_route": ("Design check done.\n"
                                        "*** Message Summary: 2 warning(s), 0 error(s)\n"),
            "route": ("#Number of fails = 0\n#Total number of fails = 0\n"
                      "#Complete  on test fixture\n"
                      "Total net length = 6.089e+03 (3.362e+03 2.727e+03)\n"
                      "Total length: 6.273e+03um, number of vias: 2293\n"),
        }
        return native[role].encode()

    def make_fake_bundle(self) -> Path:
        env_path = self.root / "LIVE_ENVIRONMENT.json"
        fake_env = {"environment_allowlist": {key: "" for key in flow.ENV_ALLOWLIST_KEYS}}
        env_path.write_bytes(flow.canonical(fake_env))
        paths = flow.expected_artifact_paths(self.top)
        commands = {row["stage"]: row for row in self.plan["commands"]}
        manifests = {"genus": [], "innovus": []}
        for role, raw in paths.items():
            if role.endswith("execution_receipt"):
                continue
            payload = self._content(role)
            target = self.root / raw
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            stage = flow._stage_for_role(role)
            manifests[stage].append({"role": role, "path": raw,
                "sha256": flow.sha256(payload), "size_bytes": len(payload),
                "producer_command_sha256": commands[stage]["command_sha256"]})
        receipts = {}
        for stage in ("genus", "innovus"):
            mapped = []
            upstream = None
            if stage == "innovus":
                genus_bytes = (self.root / paths["genus_execution_receipt"]).read_bytes()
                upstream = flow.sha256(genus_bytes)
                for role in ("mapped_netlist", "mapped_sdc"):
                    raw = paths[role]; payload = (self.root / raw).read_bytes()
                    mapped.append({"role": role, "path": raw, "sha256": flow.sha256(payload),
                                   "size_bytes": len(payload)})
            log = (self.root / paths[f"{stage}_log"]).read_bytes()
            receipt = flow.seal({"schema": "k2_single_edge_execution_receipt_v3",
                "design": "a2", "top": self.top, "attempt_root": str(self.root),
                "stage": stage, "status": "PASS_NATIVE_EXIT_ZERO", "exit_code": 0,
                "contract_sha256": flow.sha256(flow.stable_read(self.fixture.contract_path)),
                "live_environment_snapshot_sha256": flow.sha256(env_path.read_bytes()),
                "command_plan_sha256": flow.sha256(self.plan_path.read_bytes()),
                "command_sha256": commands[stage]["command_sha256"],
                "planned_environment_sha256": commands[stage]["environment_sha256"],
                "runtime_environment_sha256": flow.sha256(flow.canonical({
                    **fake_env["environment_allowlist"], **commands[stage]["environment"],
                    "HOME": str(self.root / stage / "home")})),
                "tool_log_sha256": flow.sha256(log), "tool_log_size_bytes": len(log),
                "artifacts": manifests[stage],
                "artifact_manifest_sha256": flow.sha256(flow.canonical(manifests[stage])),
                "artifact_evidence_classification":
                    "BOUND_COMPLETE_EXIT_ZERO_STAGE_MANIFEST",
                "upstream_genus_receipt_sha256": upstream, "mapped_genus_inputs": mapped,
                "producer_authentication": "UNAUTHENTICATED_LOCAL_SELF_HASH",
                "candidate_physical_go_allowed": False})
            receipt_path = self.root / paths[f"{stage}_execution_receipt"]
            write_json(receipt_path, receipt)
            receipts[stage] = (receipt_path.read_bytes(), receipt)
        rows = []
        for role in self.contract["artifact_ledger"]["required_roles"]:
            if role.endswith("execution_receipt"):
                stage = role.split("_", 1)[0]; payload = receipts[stage][0]
                rows.append({"role": role, "path": paths[role], "sha256": flow.sha256(payload),
                             "size_bytes": len(payload),
                             "producer_command_sha256": commands[stage]["command_sha256"]})
            else:
                stage = flow._stage_for_role(role)
                rows.append(next(row for row in manifests[stage] if row["role"] == role))
        ledger = flow.seal({"schema": self.contract["artifact_ledger"]["schema"],
            "design": "a2", "top": self.top,
            "contract_sha256": flow.sha256(flow.stable_read(self.fixture.contract_path)),
            "attempt_root": str(self.root),
            "command_plan_sha256": flow.sha256(self.plan_path.read_bytes()),
            "live_environment_snapshot_sha256": flow.sha256(env_path.read_bytes()),
            "artifacts": rows, "candidate_physical_go_allowed": False})
        ledger_path = self.root / "ledger.json"
        write_json(ledger_path, ledger)
        return ledger_path

    def reseal_fake_bundle(self, ledger_path: Path) -> None:
        """Model an adversary who recomputes every caller-mintable self-hash."""
        paths = flow.expected_artifact_paths(self.top)
        receipts = {}
        for stage in ("genus", "innovus"):
            receipt_path = self.root / paths[f"{stage}_execution_receipt"]
            receipt = json.loads(receipt_path.read_text())
            receipt.pop("document_sha256")
            for row in receipt["artifacts"]:
                payload = (self.root / row["path"]).read_bytes()
                row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
            receipt["artifact_manifest_sha256"] = flow.sha256(
                flow.canonical(receipt["artifacts"]))
            log = (self.root / paths[f"{stage}_log"]).read_bytes()
            receipt["tool_log_sha256"] = flow.sha256(log)
            receipt["tool_log_size_bytes"] = len(log)
            if stage == "innovus":
                genus_payload = (self.root / paths["genus_execution_receipt"]).read_bytes()
                receipt["upstream_genus_receipt_sha256"] = flow.sha256(genus_payload)
                receipt["mapped_genus_inputs"] = []
                for role in ("mapped_netlist", "mapped_sdc"):
                    raw = paths[role]
                    payload = (self.root / raw).read_bytes()
                    receipt["mapped_genus_inputs"].append({
                        "role": role, "path": raw, "sha256": flow.sha256(payload),
                        "size_bytes": len(payload)})
            write_json(receipt_path, flow.seal(receipt))
            receipts[stage] = receipt_path.read_bytes()
        ledger = json.loads(ledger_path.read_text())
        ledger.pop("document_sha256")
        manifests = {}
        for stage in ("genus", "innovus"):
            receipt = json.loads(
                (self.root / paths[f"{stage}_execution_receipt"]).read_text())
            manifests.update({row["role"]: row for row in receipt["artifacts"]})
        for row in ledger["artifacts"]:
            role = row["role"]
            if role.endswith("execution_receipt"):
                payload = receipts[role.split("_", 1)[0]]
                row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
            else:
                row.clear()
                row.update(manifests[role])
        write_json(ledger_path, flow.seal(ledger))

    def reset_fake_bundle(self) -> Path:
        for stage in ("genus", "innovus"):
            path = self.root / stage
            if path.exists():
                shutil.rmtree(path)
        for name in ("ledger.json", "qualification.json"):
            path = self.root / name
            if path.exists():
                path.unlink()
        return self.make_fake_bundle()

    def test_missing_stage_receipts_is_explicit_unauthenticated_hold(self):
        output = self.root / "qualification.json"
        result = flow.qualify("a2", self.root, self.root / "LIVE_ENVIRONMENT.json",
                              self.plan_path, self.root / "ledger.json", output)
        self.assertEqual(result["decision"], "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE")
        self.assertFalse(result["producer_authenticated"])
        self.assertNotIn("real_artifacts_verified", result)

    def test_fully_resealed_fake_bundle_is_never_authenticated_or_go(self):
        ledger = self.make_fake_bundle()
        env_bytes = (self.root / "LIVE_ENVIRONMENT.json").read_bytes()
        with mock.patch.object(flow, "validate_live_environment",
                               return_value=(env_bytes, json.loads(env_bytes))):
            result = flow.qualify("a2", self.root, self.root / "LIVE_ENVIRONMENT.json",
                                  self.plan_path, ledger, self.root / "qualification.json")
        self.assertEqual(result["decision"], "HOLD_UNAUTHENTICATED_PRODUCER_EVIDENCE")
        self.assertTrue(result["diagnostic_artifact_checks_completed"])
        self.assertEqual(result["diagnostic_evidence_scope"],
                         "CALLER_SELF_SEALED_UNAUTHENTICATED_ONLY")
        self.assertIn("diagnostic_metrics_only", result)
        self.assertNotIn("verified_metrics", result)
        self.assertNotIn("artifact_bundle_consistency_verified", result)
        self.assertFalse(result["producer_authenticated"])
        self.assertFalse(result["candidate_physical_go"])
        self.assertNotIn("real_artifacts_verified", result)

    def test_end_to_end_resealed_parser_mutations_never_complete_diagnostics(self):
        paths = flow.expected_artifact_paths(self.top)
        inputs = "rst_i link_enable_i source_pending_i"

        def append(role: str, payload: bytes) -> None:
            with (self.root / paths[role]).open("ab") as handle:
                handle.write(payload)

        def replace(role: str, old: bytes, new: bytes) -> None:
            path = self.root / paths[role]
            path.write_bytes(path.read_bytes().replace(old, new, 1))

        mutations = {
            "extra_primary_clock": lambda: append(
                "mapped_sdc", b"create_clock -period 9.0 [get_ports {rst_i}]\n"),
            "semicolon_hidden_exception": lambda: append(
                "mapped_sdc", b"set_load 0.01 [get_ports {protocol_error_o}]; "
                              b"set_false_path -from [all_inputs]\n"),
            "wrong_input_collection": lambda: replace(
                "mapped_sdc", inputs.encode(), b"rst_i source_pending_i"),
            "fatal_drc": lambda: append("drc", b"FATAL: DRC engine aborted\n"),
            "foreign_drc_context": lambda: replace(
                "drc", f"top={self.top}".encode(), b"top=foreign_top"),
            "comment_only_antenna": lambda: (self.root / paths["antenna"]).write_bytes(
                b"# No Violations Found\n# Total violations: 0\n"),
            "contradictory_connectivity": lambda: append(
                "connectivity", b"Connectivity check incomplete; 1 problem\n"),
            "comment_supplied_timing": lambda: (self.root / paths["setup_timing"]).write_text(
                f"# Design: {self.top}; timing setup slack: 0.12\nactive filler\n"),
            "contradictory_timing": lambda: append(
                "setup_timing", b"1 violating path\n"),
            "foreign_violated_no_slack_timing": lambda: append(
                "setup_timing",
                b"Path 2: VIOLATED (foreign) Hold Check\nforeign no-slack text\n"),
            "foreign_genus_violated_no_slack_timing": lambda: append(
                "genus_timing",
                b"Path 2: VIOLATED (foreign) Hold Check\nforeign no-slack text\n"),
            "report_error_equals_ten": lambda: append(
                "postroute_power", b"Error=10\n"),
            "report_ten_errors": lambda: append("drc", b"10 errors\n"),
            "report_fatal_text": lambda: append(
                "antenna", b"fatal-text diagnostics\n"),
            "report_parenthesized_error": lambda: append(
                "postroute_area", b"(ERROR)\n"),
            "report_bare_fatal": lambda: append("route", b"FATAL\n"),
            "genus_log_error_equals_ten": lambda: append(
                "genus_log", b"Error=10\n"),
            "innovus_log_ten_errors": lambda: append(
                "innovus_log", b"10 errors\n"),
            "timing_command_suffix": lambda: replace(
                "setup_timing", b"-max_paths 50\n",
                b"-max_paths 50 caller_fabricated\n"),
            "drc_command_suffix": lambda: replace(
                "drc", f"verify_drc -report {self.root}/innovus/reports/drc.rpt\n".encode(),
                (f"verify_drc -report {self.root}/innovus/reports/drc.rpt "
                 "caller_fabricated\n").encode()),
            "drc_command_foreign_path": lambda: replace(
                "drc", f"{self.root}/innovus/reports/drc.rpt".encode(),
                b"/caller_fabricated/drc.rpt"),
            "check_timing_command_suffix": lambda: replace(
                "check_timing", b"check_timing -verbose\n",
                b"check_timing -verbose caller_fabricated\n"),
            "area_command_suffix": lambda: replace(
                "postroute_area", b"report_area\n",
                b"report_area caller_fabricated\n"),
            "power_command_suffix": lambda: replace(
                "postroute_power", b"report_power\n",
                b"report_power caller_fabricated\n"),
            "antenna_command_suffix": lambda: replace(
                "antenna",
                f"{self.root}/innovus/reports/antenna.rpt\n".encode(),
                (f"{self.root}/innovus/reports/antenna.rpt "
                 "caller_fabricated\n").encode()),
            "connectivity_command_suffix": lambda: replace(
                "connectivity",
                f"{self.root}/innovus/reports/connectivity.rpt\n".encode(),
                (f"{self.root}/innovus/reports/connectivity.rpt "
                 "caller_fabricated\n").encode()),
            "pg_connectivity_command_suffix": lambda: replace(
                "pg_connectivity",
                f"{self.root}/innovus/reports/pg_connectivity.rpt\n".encode(),
                (f"{self.root}/innovus/reports/pg_connectivity.rpt "
                 "caller_fabricated\n").encode()),
            "machine_display_count_mismatch": lambda: replace(
                "setup_timing_machine", b"path_count=1", b"path_count=2"),
            "foreign_genus_header": lambda: replace(
                "genus_area", f"Module: {self.top}".encode(), b"Module: foreign_top"),
            "missing_genus_slack": lambda: replace(
                "genus_timing", b"Slack:= 120", b"Arrival:= 120"),
            "comment_only_check_timing": lambda: (self.root / paths["check_timing"]).write_text(
                "# TIMING CHECK SUMMARY\n# no_clock | count | 0\n"),
            "headerless_check_timing": lambda: (self.root / paths["check_timing"]).write_bytes(
                self._content("check_timing").split(b"TIMING CHECK SUMMARY", 1)[1]),
            "extra_check_timing_class": lambda: append(
                "check_timing", b"| mystery_warning | unknown warning | 1 |\n"),
            "nonzero_check_place": lambda: append(
                "check_place", b"Overlapping with other instance:\t1\n"),
            "check_design_error": lambda: replace(
                "check_design_post_route", b"0 error(s)", b"1 error(s)"),
            "route_failure": lambda: replace(
                "route", b"#Number of fails = 0", b"#Number of fails = 1"),
            "zero_postroute_area": lambda: replace(
                "postroute_area", b"Total: 42 123.45", b"Total: 42 0.0"),
            "extra_netlist_port": lambda: replace(
                "mapped_netlist", b");\n", b",uncontracted_debug_o);\n"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                ledger = self.reset_fake_bundle()
                mutate()
                self.reseal_fake_bundle(ledger)
                environment = self.root / "LIVE_ENVIRONMENT.json"
                environment_payload = environment.read_bytes()
                with mock.patch.object(flow, "validate_live_environment", return_value=(
                        environment_payload, json.loads(environment_payload))):
                    with self.assertRaises(flow.FlowError):
                        flow.qualify("a2", self.root, environment, self.plan_path, ledger,
                                     self.root / "qualification.json")
                self.assertFalse((self.root / "qualification.json").exists())

    def test_ledger_builder_derives_exact_rows_from_both_receipt_manifests(self):
        old_ledger = self.make_fake_bundle()
        old_ledger.unlink()
        env_path = self.root / "LIVE_ENVIRONMENT.json"
        env_bytes = env_path.read_bytes()
        with mock.patch.object(flow, "validate_live_environment",
                               return_value=(env_bytes, json.loads(env_bytes))):
            ledger = flow.build_ledger("a2", self.root, self.plan_path, old_ledger)
        self.assertEqual(set(row["role"] for row in ledger["artifacts"]),
                         set(self.contract["artifact_ledger"]["required_roles"]))
        self.assertEqual(ledger["attempt_root"], str(self.root))

    def test_exact_attempt_root_and_stage_command_binding(self):
        ledger_path = self.make_fake_bundle()
        with self.assertRaisesRegex(flow.FlowError, "attempt root"):
            flow.validate_artifacts(self.fixture.root / "other", ledger_path, "a2",
                                    self.contract, self.plan)
        ledger = json.loads(ledger_path.read_text())
        row = next(row for row in ledger["artifacts"] if row["role"] == "genus_timing")
        row["producer_command_sha256"] = self.plan["commands"][1]["command_sha256"]
        ledger.pop("document_sha256")
        ledger_path.write_bytes(flow.canonical(flow.seal(ledger)))
        with self.assertRaisesRegex(flow.FlowError, "invalid provenance"):
            flow.validate_artifacts(self.root, ledger_path, "a2", self.contract, self.plan)

    def test_receipt_design_replay_and_post_receipt_mutation_reject(self):
        ledger_path = self.make_fake_bundle()
        receipt_path = self.root / "genus/EXECUTION_RECEIPT.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["design"] = "a3"
        receipt.pop("document_sha256")
        receipt_path.write_bytes(flow.canonical(flow.seal(receipt)))
        with self.assertRaisesRegex(flow.FlowError, "byte identity mismatch|binding mismatch"):
            flow.validate_artifacts(self.root, ledger_path, "a2", self.contract, self.plan)

    def test_resealed_stage_receipt_identity_exit_command_and_env_mutations_reject(self):
        mutations = {
            "attempt_root": str(self.fixture.root / "replayed-attempt"),
            "top": "wrong_top", "stage": "innovus", "exit_code": 1,
            "command_sha256": "f" * 64, "runtime_environment_sha256": "e" * 64,
            "artifact_evidence_classification":
                "UNBOUND_NONZERO_EXIT_FILES_NOT_LEDGER_ELIGIBLE",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                if (self.root / "genus").exists(): shutil.rmtree(self.root / "genus")
                if (self.root / "innovus").exists(): shutil.rmtree(self.root / "innovus")
                ledger_path = self.root / "ledger.json"
                if ledger_path.exists(): ledger_path.unlink()
                ledger_path = self.make_fake_bundle()
                receipt_path = self.root / "genus/EXECUTION_RECEIPT.json"
                receipt = json.loads(receipt_path.read_text())
                receipt[field] = value
                receipt.pop("document_sha256")
                receipt_path.write_bytes(flow.canonical(flow.seal(receipt)))
                ledger = json.loads(ledger_path.read_text())
                row = next(row for row in ledger["artifacts"]
                           if row["role"] == "genus_execution_receipt")
                payload = receipt_path.read_bytes()
                row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
                ledger.pop("document_sha256")
                ledger_path.write_bytes(flow.canonical(flow.seal(ledger)))
                with self.assertRaisesRegex(flow.FlowError, "binding mismatch"):
                    flow.validate_artifacts(self.root, ledger_path, "a2",
                                            self.contract, self.plan)
        # Restore the bundle then mutate a bound artifact without resealing its receipt.
        shutil.rmtree(self.root / "genus")
        shutil.rmtree(self.root / "innovus")
        ledger_path.unlink()
        ledger_path = self.make_fake_bundle()
        with (self.root / "genus/reports/timing.rpt").open("ab") as handle:
            handle.write(b"changed\n")
        with self.assertRaisesRegex(flow.FlowError, "byte identity mismatch|bytes changed"):
            flow.validate_artifacts(self.root, ledger_path, "a2", self.contract, self.plan)

    def test_innovus_ten_errors_and_partial_timing_classes_reject(self):
        log = (f"Version: v{self.contract['tools']['innovus']['version']}\n"
               f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={self.top}\n"
               "*** Message Summary: 0 warning(s), 10 error(s)\n"
               "--- Ending \"Innovus\" (test) ---\n")
        with self.assertRaisesRegex(flow.FlowError, "zero-error"):
            flow.validate_innovus_log(log, self.top,
                                      self.contract["tools"]["innovus"]["version"])
        with self.assertRaisesRegex(flow.FlowError, "missing-constraint|warning inventory"):
            context = flow.report_context_line(
                "Innovus", self.contract["tools"]["innovus"]["version"], self.top,
                "check_timing", "postroute")
            header = (f"#  Generated by:      Cadence Innovus "
                      f"{self.contract['tools']['innovus']['version']}\n"
                      f"#  Design:            {self.top}\n"
                      "#  Command:           check_timing -verbose\n")
            flow.validate_check_timing(
                (header + f"TIMING CHECK SUMMARY\n"
                 f"| no_clock | No clock | 1 |\n{context}\n").encode(),
                self.top, self.contract["tools"]["innovus"]["version"])


if __name__ == "__main__":
    unittest.main()
