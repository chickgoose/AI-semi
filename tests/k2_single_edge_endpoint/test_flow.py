from __future__ import annotations

import importlib.util
import json
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


class Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="k2-se-test-")
        self.root = Path(self.temp.name)
        source = REPO / "physical/k2_single_edge_endpoint"
        target = self.root / "physical/k2_single_edge_endpoint"
        target.parent.mkdir(parents=True)
        shutil.copytree(source, target)
        self.here = target
        self.contract_path = target / "contract.json"
        self.contract = json.loads(self.contract_path.read_text())
        paths = {self.contract["source_policy"]["nested_generic_filelist"]["path"]}
        for row in self.contract["candidates"].values():
            paths.add(row["filelist"]["path"])
            paths.update(item["path"] for item in row["expanded_sources"])
        commit = self.contract["rtl_authority"]["commit"]
        for raw in paths:
            payload = subprocess.check_output(
                ["git", "show", f"{commit}:{raw}"], cwd=REPO)
            path = self.root / raw
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    def patch(self):
        return mock.patch.multiple(
            flow, ROOT=self.root, HERE=self.here, CONTRACT=self.contract_path)

    def close(self) -> None:
        self.temp.cleanup()


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(flow.canonical(document))


class StaticContractTests(unittest.TestCase):
    def test_repository_static_preflight_passes_without_live_servers(self):
        receipt = flow.static_preflight(None)
        self.assertEqual(receipt["status"], "PASS_STATIC_PACKAGE")
        self.assertFalse(receipt["real_tools_or_pdk_examined"])
        self.assertFalse(receipt["candidate_physical_go_allowed"])
        self.assertEqual(receipt["candidate_sources_present"], {"a2": False, "a3": False})
        flow.verify_seal(receipt, "static receipt")

    def test_exact_tops_and_filelists_are_pinned(self):
        _, contract = flow.validate_contract()
        self.assertEqual(contract["candidates"]["a2"]["top"],
                         "a2_batched_iwrr_single_edge_top")
        self.assertEqual(contract["candidates"]["a3"]["top"],
                         "a3_exact_scalar_prefix_k2_single_edge_top")
        for design in ("a2", "a3"):
            row = contract["candidates"][design]
            payload = subprocess.check_output(
                ["git", "show", f"{contract['rtl_authority']['commit']}:{row['filelist']['path']}"],
                cwd=REPO)
            self.assertEqual(flow.sha256(payload), row["filelist"]["sha256"])
            lines = payload.decode().splitlines()
            self.assertEqual(lines, row["filelist"]["entries"])
            self.assertEqual(len(lines), 3)
            self.assertEqual(len(row["expanded_sources"]), 5)

    def test_sdc_has_one_primary_clock_and_all_placeholder_classes(self):
        _, contract = flow.validate_contract()
        text = (REPO / contract["constraints"]["sdc"]).read_text()
        self.assertEqual(text.count("create_clock "), 1)
        self.assertNotIn("create_generated_clock", text)
        for command in ("set_input_delay", "set_output_delay", "set_input_transition", "set_load"):
            self.assertIn(command, text)
        self.assertFalse(contract["constraints"]["candidate_go_eligible"])
        self.assertFalse(contract["qualification"]["candidate_physical_go_possible"])

    def test_artifact_ledger_covers_physical_closure_classes(self):
        _, contract = flow.validate_contract()
        roles = set(contract["artifact_ledger"]["required_roles"])
        self.assertTrue({"setup_timing", "hold_timing", "postroute_area", "drc",
                         "antenna", "connectivity", "pg_connectivity",
                         "check_timing"}.issubset(roles))

    def test_static_receipt_is_exclusive_create(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "receipt.json"
            flow.static_preflight(output)
            with self.assertRaises(FileExistsError):
                flow.static_preflight(output)


class MutatedContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.patch = self.fixture.patch()
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.fixture.close()

    def rewrite_contract(self) -> None:
        self.fixture.contract_path.write_bytes(flow.canonical(self.fixture.contract))

    def test_forbidden_multiedge_filelist_is_rejected(self):
        filelist = self.fixture.root / self.fixture.contract["candidates"]["a2"]["filelist"]["path"]
        filelist.write_text(filelist.read_text() + "rtl/technology/p6/stolen.sv\n")
        with self.assertRaisesRegex(flow.FlowError, "byte hash mismatch"):
            flow.validate_contract()

    def test_placeholder_authority_cannot_be_flipped_in_place(self):
        self.fixture.contract["constraints"]["candidate_go_eligible"] = True
        self.rewrite_contract()
        with self.assertRaisesRegex(flow.FlowError, "classification weakened"):
            flow.validate_contract()

    def test_maximum_decision_cannot_be_promoted_in_place(self):
        self.fixture.contract["qualification"]["candidate_physical_go_possible"] = True
        self.rewrite_contract()
        with self.assertRaisesRegex(flow.FlowError, "fails closed"):
            flow.validate_contract()

    def test_second_clock_is_rejected(self):
        sdc = self.fixture.root / self.fixture.contract["constraints"]["sdc"]
        sdc.write_text(sdc.read_text() + "create_clock -name stolen [get_ports rst_ni]\n")
        with self.assertRaisesRegex(flow.FlowError, "one-primary-posedge"):
            flow.validate_contract()

    def test_plan_hashes_sources_commands_and_package(self):
        output = self.fixture.root / "attempt/plan.json"
        plan = flow.make_plan("a2", self.fixture.root / "attempt", output)
        self.assertEqual([row["stage"] for row in plan["commands"]], ["genus", "innovus"])
        self.assertEqual(len(plan["sources"]), 5)
        for command in plan["commands"]:
            unsigned = dict(command)
            recorded = unsigned.pop("command_sha256")
            self.assertEqual(recorded, flow.sha256(flow.canonical(unsigned)))
            self.assertEqual(command["environment_sha256"],
                             flow.sha256(flow.canonical(command["environment"])))
            self.assertIn("TMPDIR", command["environment"])
        self.assertFalse(plan["candidate_physical_go_allowed"])

    def test_a3_plan_binds_exact_committed_complete_top(self):
        output = self.fixture.root / "attempt-a3/plan.json"
        plan = flow.make_plan("a3", self.fixture.root / "attempt-a3", output)
        self.assertEqual(plan["top"], "a3_exact_scalar_prefix_k2_single_edge_top")
        self.assertEqual(len(plan["sources"]), 5)
        self.assertEqual(plan["sources"][-1]["sha256"],
                         "3bac4faa2e249e0326a8eba4e3b010437e16c33b5dff236bd621acdb27c5bb07")

    def test_real_execution_requires_literal_authorization_before_any_tool_check(self):
        plan_path = self.fixture.root / "attempt/plan.json"
        flow.make_plan("a2", self.fixture.root / "attempt", plan_path)
        with self.assertRaisesRegex(flow.FlowError, "authorization mismatch"):
            flow.execute_stage("a2", "genus", plan_path,
                               self.fixture.root / "missing-environment.json", "NO")

    def test_plan_rejects_changed_authority_top_bytes(self):
        top = self.fixture.root / self.fixture.contract["candidates"]["a2"]["expanded_sources"][-1]["path"]
        top.write_text("module a2_batched_iwrr_single_edge_top(input logic clk_i); endmodule\n")
        with self.assertRaisesRegex(flow.FlowError, "RTL authority commit"):
            flow.make_plan("a2", self.fixture.root / "attempt",
                           self.fixture.root / "attempt/plan.json")

    def test_plan_rejects_source_contamination_even_when_filelist_is_unchanged(self):
        link = self.fixture.root / self.fixture.contract["candidates"]["a2"]["expanded_sources"][1]["path"]
        link.write_text("// borrowed p6 behavior\nmodule changed_link; endmodule\n")
        with self.assertRaisesRegex(flow.FlowError, "RTL authority commit"):
            flow.make_plan("a2", self.fixture.root / "attempt",
                           self.fixture.root / "attempt/plan.json")

    def test_forbidden_token_matching_does_not_confuse_address_names(self):
        self.assertFalse(flow.contains_forbidden("retire_addr0_o", ["p6", "ddr"]))
        self.assertTrue(flow.contains_forbidden("borrowed_p6_endpoint", ["p6", "ddr"]))
        self.assertTrue(flow.contains_forbidden("a DDR endpoint", ["p6", "ddr"]))


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.patch = self.fixture.patch()
        self.patch.start()
        self.attempt = self.fixture.root / "attempt"
        self.plan_path = self.attempt / "plan.json"
        self.plan = flow.make_plan("a2", self.attempt, self.plan_path)
        _, self.contract = flow.validate_contract()

    def tearDown(self):
        self.patch.stop()
        self.fixture.close()

    def make_artifacts(self) -> tuple[Path, dict]:
        top = self.contract["candidates"]["a2"]["top"]
        contents = {role: f"artifact {role}\n".encode()
                    for role in self.contract["artifact_ledger"]["required_roles"]}
        contents.update({
            "genus_log": (f"Version: {self.contract['tools']['genus']['version']}\n"
                          "Info=10, Warn=1, Error=0, Fatal=0\n"
                          f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\n"
                          "Normal exit.\n").encode(),
            "innovus_log": (f"Version: v{self.contract['tools']['innovus']['version']}, test\n"
                            f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n"
                            "*** Message Summary: 1 warning(s), 0 error(s)\n"
                            "--- Ending \"Innovus\" (test) ---\n").encode(),
            "setup_timing_machine": ("schema=k2_single_edge_timing_summary_v1\n"
                                     "view=se_setup_view\ncheck=setup\npath_count=8\n"
                                     "violation_count=0\nwns=0.12\ntns=0.0\n").encode(),
            "hold_timing_machine": ("schema=k2_single_edge_timing_summary_v1\n"
                                    "view=se_hold_view\ncheck=hold\npath_count=7\n"
                                    "violation_count=0\nwns=0.04\ntns=0.0\n").encode(),
            "drc": b"No DRC violations were found\n",
            "antenna": b"No Violations Found\n",
            "connectivity": b"Found no problems or warnings.\n",
            "pg_connectivity": b"Found no problems or warnings.\n",
            "check_timing": b"TIMING CHECK SUMMARY\nno_clock | count | 0\nno_load | count | 0\n",
            "mapped_sdc": (b"create_clock -name se_primary_clk -period 6.5 [get_ports clk_i]\n"
                           b"set_input_delay -clock se_primary_clk 0.5 [all_inputs]\n"
                           b"set_input_transition 0.05 [all_inputs]\n"
                           b"set_output_delay -clock se_primary_clk 0.5 [all_outputs]\n"
                           b"set_load 0.01 [all_outputs]\n"),
            "mapped_netlist": f"module {top}; endmodule\n".encode(),
            "postroute_netlist": f"module {top}; endmodule\n".encode(),
            "postroute_area": b"Total cell area: 123.45\n",
            "genus_commands_complete":
                f"K2_SINGLE_EDGE_GENUS_COMMANDS_COMPLETE top={top}\n".encode(),
            "innovus_commands_complete":
                f"K2_SINGLE_EDGE_INNOVUS_COMMANDS_COMPLETE top={top}\n".encode(),
        })
        command_hash = {
            "genus": self.plan["commands"][0]["command_sha256"],
            "innovus": self.plan["commands"][1]["command_sha256"],
        }
        genus_roles = {"genus_log", "genus_timing", "genus_area", "genus_check_design",
                       "genus_timing_intent", "mapped_netlist", "mapped_sdc", "mapped_sdf",
                       "genus_commands_complete"}
        rows = []
        exact_paths = flow.expected_artifact_paths(top)
        for role, payload in contents.items():
            relative = exact_paths[role]
            path = self.attempt / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            rows.append({"role": role, "path": relative, "sha256": flow.sha256(payload),
                         "size_bytes": len(payload),
                         "producer_command_sha256": command_hash[
                             "genus" if role in genus_roles else "innovus"]})
        ledger = flow.seal({
            "schema": self.contract["artifact_ledger"]["schema"],
            "design": "a2", "top": top,
            "contract_sha256": flow.sha256(flow.stable_read(self.fixture.contract_path)),
            "command_plan_sha256": flow.sha256(flow.stable_read(self.plan_path)),
            "artifacts": rows, "candidate_physical_go_allowed": False,
        })
        ledger_path = self.attempt / "ledger.json"
        write_json(ledger_path, ledger)
        return ledger_path, ledger

    def reseal_ledger(self, ledger_path: Path, ledger: dict) -> None:
        ledger.pop("document_sha256", None)
        write_json(ledger_path, flow.seal(ledger))

    def test_missing_real_bundle_yields_hold_never_go(self):
        output = self.attempt / "qualification.json"
        receipt = flow.qualify("a2", self.attempt, self.attempt / "missing-env.json",
                               self.plan_path, self.attempt / "missing-ledger.json", output)
        self.assertEqual(receipt["decision"], "HOLD_MISSING_REAL_ARTIFACTS")
        self.assertFalse(receipt["candidate_physical_go"])
        self.assertFalse(receipt["real_artifacts_verified"])

    def test_complete_artifact_ledger_can_be_independently_audited(self):
        ledger_path, _ = self.make_artifacts()
        _, _, metrics = flow.validate_artifacts(
            self.attempt, ledger_path, "a2", self.contract, self.plan)
        self.assertEqual(metrics["setup"]["path_count"], 8)
        self.assertEqual(metrics["area"], 123.45)

    def test_ledger_builder_uses_exact_paths_and_command_hashes(self):
        ledger_path, _ = self.make_artifacts()
        ledger_path.unlink()
        ledger = flow.build_ledger("a2", self.attempt, self.plan_path, ledger_path)
        self.assertEqual({row["path"] for row in ledger["artifacts"]},
                         set(flow.expected_artifact_paths(ledger["top"]).values()))
        self.assertFalse(ledger["candidate_physical_go_allowed"])

    def test_rehashed_nonzero_drc_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        row = next(item for item in ledger["artifacts"] if item["role"] == "drc")
        payload = b"DRC violations: 1\n"
        (self.attempt / row["path"]).write_bytes(payload)
        row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
        self.reseal_ledger(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "zero evidence"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_zero_timing_paths_are_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        row = next(item for item in ledger["artifacts"]
                   if item["role"] == "setup_timing_machine")
        path = self.attempt / row["path"]
        payload = path.read_bytes().replace(b"path_count=8", b"path_count=0")
        path.write_bytes(payload)
        row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
        self.reseal_ledger(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "timing is not closed"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_extra_ledger_role_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        ledger["artifacts"].append(dict(ledger["artifacts"][0], role="unreviewed_claim"))
        self.reseal_ledger(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "roles are not exact"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_artifact_path_traversal_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        ledger["artifacts"][0]["path"] = "../escape.log"
        self.reseal_ledger(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "path is not exact"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_symlink_artifact_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        row = ledger["artifacts"][0]
        original = self.attempt / row["path"]
        target = self.attempt / "target.log"
        target.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(target)
        with self.assertRaisesRegex(flow.FlowError, "symlink is forbidden"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_rehashed_forbidden_netlist_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        row = next(item for item in ledger["artifacts"] if item["role"] == "mapped_netlist")
        path = self.attempt / row["path"]
        payload = path.read_bytes() + b"// p6 borrowed marker\n"
        path.write_bytes(payload)
        row["sha256"], row["size_bytes"] = flow.sha256(payload), len(payload)
        self.reseal_ledger(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "forbidden multi-edge"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_tampered_ledger_self_hash_is_rejected(self):
        ledger_path, ledger = self.make_artifacts()
        ledger["design"] = "a3"
        write_json(ledger_path, ledger)
        with self.assertRaisesRegex(flow.FlowError, "self-hash mismatch"):
            flow.validate_artifacts(self.attempt, ledger_path, "a2", self.contract, self.plan)

    def test_forged_real_environment_cannot_bypass_live_byte_check(self):
        contract_payload, contract = flow.validate_contract()
        fake = flow.seal({
            "schema": "k2_single_edge_real_environment_v1",
            "contract_sha256": flow.sha256(contract_payload),
            "tools": {name: {**row, "version_output_sha256": "0" * 64}
                      for name, row in contract["tools"].items()},
            "technology": {role: {"path": str(self.attempt / f"fake-{role}"),
                                   "sha256": row["sha256"], "size_bytes": 1}
                           for role, row in contract["technology"]["files"].items()},
            "environment_allowlist": {},
            "environment_allowlist_sha256": flow.sha256(flow.canonical({})),
            "real_server_bytes_verified": True,
            "candidate_physical_go_allowed": False,
        })
        path = self.attempt / "forged-env.json"
        write_json(path, fake)
        with self.assertRaises((flow.FlowError, FileNotFoundError)):
            flow.validate_real_environment(path, flow.sha256(contract_payload), contract)


if __name__ == "__main__":
    unittest.main()
