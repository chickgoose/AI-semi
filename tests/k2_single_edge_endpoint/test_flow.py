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
                                   CONTRACT=self.contract_path)

    def rewrite_contract(self) -> None:
        self.contract_path.write_bytes(flow.canonical(self.contract))

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

    def test_mapped_sdc_exact_values_and_no_exceptions(self):
        good = """create_clock -period 6.5 [get_ports clk_i]
set_clock_uncertainty 0.25 [get_clocks se_primary_clk]
set_input_delay -min 0.10 -clock se_primary_clk [all_inputs]
set_input_delay -max 0.50 -clock se_primary_clk [all_inputs]
set_output_delay -min 0.10 -clock se_primary_clk [all_outputs]
set_output_delay -max 0.50 -clock se_primary_clk [all_outputs]
set_input_transition 0.05 [all_inputs]
set_load 0.01 [all_outputs]
"""
        flow.validate_sdc(good)
        for bad in (good.replace("6.5", "7.0", 1), good + "set_false_path -from [all_inputs]\n"):
            with self.assertRaisesRegex(flow.FlowError, "mapped SDC"):
                flow.validate_sdc(bad)

    def test_strong_zero_report_parser_rejects_contradictions(self):
        bad = {
            "drc": b"No DRC violations were found\nTotal Violations : 1 Viols.\n",
            "antenna": b"No Violations Found\nTotal number of violations: 3\n",
            "connectivity": b"Found no problems or warnings.\nFound 1 problems.\n",
        }
        for kind, payload in bad.items():
            with self.subTest(kind=kind), self.assertRaisesRegex(flow.FlowError, "contains violations"):
                flow.require_zero_native(payload, kind)

    def test_empty_netlist_comment_sdf_and_header_spef_reject(self):
        top = self.fixture.contract["candidates"]["a2"]["top"]
        with self.assertRaisesRegex(flow.FlowError, "empty or lacks"):
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
        ports = [item["name"] for d in ("inputs", "outputs")
                 for item in self.contract["boundary"]["normalized_ports"][d]]
        netlist = (f"module {top}({','.join(ports)});\n" +
                   "input clk_i; output protocol_error_o; DFFX1 u0 (.D(clk_i), .Q(protocol_error_o));\nendmodule\n").encode()
        sdc = b"create_clock -period 6.5 [get_ports clk_i]\nset_clock_uncertainty 0.25 [get_clocks se_primary_clk]\nset_input_delay -min 0.10 -clock se_primary_clk [all_inputs]\nset_input_delay -max 0.50 -clock se_primary_clk [all_inputs]\nset_output_delay -min 0.10 -clock se_primary_clk [all_outputs]\nset_output_delay -max 0.50 -clock se_primary_clk [all_outputs]\nset_input_transition 0.05 [all_inputs]\nset_load 0.01 [all_outputs]\n"
        common = f"Cadence report\nDesign: {top}\n".encode()
        table = {
            "genus_log": (f"Version: {self.contract['tools']['genus']['version']}\nInfo=1, Error=0, Fatal=0\nK2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\nNormal exit.\n").encode(),
            "innovus_log": (f"Version: v{self.contract['tools']['innovus']['version']}, test\nK2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n*** Message Summary: 0 warning(s), 0 error(s)\n--- Ending \"Innovus\" (test) ---\n").encode(),
            "setup_timing": common + b"Timing setup path slack: 0.12\n",
            "hold_timing": common + b"Timing hold path slack: 0.04\n",
            "setup_timing_machine": b"schema=k2_single_edge_timing_summary_v1\nview=se_setup_view\ncheck=setup\npath_count=8\nviolation_count=0\nwns=0.12\ntns=0.0\n",
            "hold_timing_machine": b"schema=k2_single_edge_timing_summary_v1\nview=se_hold_view\ncheck=hold\npath_count=7\nviolation_count=0\nwns=0.04\ntns=0.0\n",
            "drc": b"No DRC violations were found\nTotal Violations: 0\n",
            "antenna": b"No Violations Found\nTotal violations: 0\n",
            "connectivity": b"Found no problems or warnings.\nTotal problems: 0\n",
            "pg_connectivity": b"Found no problems or warnings.\nTotal problems: 0\n",
            "check_timing": ("TIMING CHECK SUMMARY\n" + "\n".join(f"{x} | count | 0" for x in ("no_clock", "no_input_delay", "no_output_delay", "unconstrained", "no_drive", "no_load")) + "\n").encode(),
            "mapped_sdc": sdc, "mapped_netlist": netlist, "postroute_netlist": netlist,
            "mapped_sdf": f'(DELAYFILE (SDFVERSION "4.0") (DESIGN "{top}") (CELL (CELLTYPE "DFFX1") (INSTANCE u0) (DELAY (ABSOLUTE (IOPATH D Q (0.1)(0.1))))))\n'.encode(),
            "postroute_sdf": f'(DELAYFILE (SDFVERSION "4.0") (DESIGN "{top}") (CELL (CELLTYPE "DFFX1") (INSTANCE u0) (DELAY (ABSOLUTE (IOPATH D Q (0.1)(0.1))))))\n'.encode(),
            "postroute_spef": f'*SPEF "IEEE 1481"\n*DESIGN "{top}"\n*D_NET n1 0.1\n*CONN\n*P clk_i I\n*CAP\n1 n1 0.1\n*RES\n1 n1 clk_i 1.0\n*END\n'.encode(),
            "postroute_area": common + b"Area report\nTotal cell area: 123.45\n",
            "genus_commands_complete": f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\n".encode(),
            "innovus_commands_complete": f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n".encode(),
            "innovus_database_manifest": (f"Design: {top}\ncheckpoint={top}.enc\nentry=saveDesign-mmmc2\nproducer_authentication=UNAUTHENTICATED_LOCAL_SELF_HASH\n").encode(),
        }
        if role in table:
            return table[role]
        words = {"genus_timing": "timing slack", "genus_area": "area cell",
                 "genus_check_design": "check design", "genus_timing_intent": "timing intent",
                 "genus_qor": "qor timing", "genus_power": "power total",
                 "genus_clocks": "clock se_primary_clk", "postroute_power": "power total",
                 "check_design_pre_place": "check design", "check_place": "check place",
                 "check_design_post_route": "check design", "route": "route wire"}
        return common + words[role].encode() + b"\n"

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
            receipt = flow.seal({"schema": "k2_single_edge_execution_receipt_v2",
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
        self.assertTrue(result["artifact_bundle_consistency_verified"])
        self.assertFalse(result["producer_authenticated"])
        self.assertFalse(result["candidate_physical_go"])
        self.assertNotIn("real_artifacts_verified", result)

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
        with self.assertRaisesRegex(flow.FlowError, "missing/duplicate"):
            flow.validate_check_timing("TIMING CHECK SUMMARY\nno_clock | count | 0\n")


if __name__ == "__main__":
    unittest.main()
