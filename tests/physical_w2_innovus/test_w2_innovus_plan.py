from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/ppa/run_k2_physical_innovus_plan.py"
SHELL = ROOT / "scripts/ppa/run_k2_physical_innovus.sh"
REGISTRY = ROOT / "scripts/ppa/k2_physical_innovus_cohorts.json"
AUTHORITY = ROOT / "scripts/ppa/k2_physical_server_environment.json"
GENUS_PROVIDER_REPO = Path(os.environ.get(
    "W2_GENUS_PROVIDER_REPO", "/tmp/k2-phys-w2-genus"))
GENUS_PROVIDER_COMMIT = "8610bd0bf70eb9f9e2bcc35efe3f398afb78b9d6"


def load_runner():
    spec = importlib.util.spec_from_file_location("w2_innovus_plan", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InnovusPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_runner()
        cls.registry = json.loads(REGISTRY.read_text())
        cls.authority = json.loads(AUTHORITY.read_text())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="w2-plan-v2-")
        self.root = Path(self.temporary.name)
        self.ready_registry = copy.deepcopy(self.registry)
        self.ready_registry["integration_state"] = "ready"
        self.ready_registry["technology_stage_authorities"] = {
            "r1": {"repository_commit": "2" * 40,
                   "path": "rtl/technology/r1/r1_tech_manifest.json",
                   "sha256": "3" * 64},
            "p6": {"repository_commit": "4" * 40,
                   "path": "rtl/technology/p6/p6_tech_manifest.json",
                   "sha256": "5" * 64},
        }
        self.original_load_contracts = self.module.load_contracts
        self.original_verify_committed_blob = self.module.verify_committed_blob
        self.module.load_contracts = lambda: (
            copy.deepcopy(self.ready_registry), copy.deepcopy(self.authority))
        self.module.verify_committed_blob = lambda *_args: None

    def tearDown(self):
        self.module.load_contracts = self.original_load_contracts
        self.module.verify_committed_blob = self.original_verify_committed_blob
        self.temporary.cleanup()

    def bound(self, path: Path) -> dict:
        return {"path": str(path), "sha256": digest(path)}

    def write_manifest(self) -> tuple[Path, dict]:
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        common_ports = cohort["common_ports"]
        document = {
            "schema": "k2_w2_tech_staged_compositions_v1",
            "status": "READY_FOR_GENUS_AND_INNOVUS",
            "repository_commit": "1" * 40,
            "goal_order": cohort["exact_design_set"],
            "common_ports": common_ports,
            "common_inputs": [row for row in common_ports
                              if row["direction"] == "input"],
            "common_outputs": [row for row in common_ports
                               if row["direction"] == "output"],
            "constraint_templates": self.authority["constraint_templates"],
            "technology_authorities": self.ready_registry[
                "technology_stage_authorities"],
            "designs": {
                design: {
                    "top": cohort["designs"][design]["top"],
                    "required_ports": common_ports,
                    "link_pins": cohort["designs"][design]["link_pins"],
                    "strict_sdc": copy.deepcopy(
                        self.authority["constraint_templates"][
                            cohort["designs"][design]["constraint_template"]]),
                    "endpoint_root": copy.deepcopy(
                        cohort["designs"][design]["endpoint_root"]),
                    "endpoint_leaf_contract": copy.deepcopy(
                        cohort["designs"][design]["endpoint_leaf_contract"]),
                }
                for design in cohort["exact_design_set"]
            },
        }
        path = self.root / "staged-manifest.json"
        path.write_text(json.dumps(document))
        self.ready_registry["committed_techmap_manifest"] = {
            "repository_commit": document["repository_commit"],
            "path": "rtl/technology/physical_staging/physical_staging_manifest.json",
            "sha256": digest(path),
        }
        return path, document

    def write_environment(self) -> Path:
        tech = self.authority["technology"]
        tool = self.authority["tool"]
        document = {
            "schema": "k2_w2_server_env_result_v1",
            "qualification_status": "PROVEN_ENVIRONMENT",
            "campaign_launch_allowed": True,
            "gates": {
                "tool_executables": {"evidence": {"innovus": {
                    "path": tool["path"], "sha256": tool["sha256"],
                    "parsed_version": tool["version"],
                }}},
                "technology_files": {"evidence": {
                    "setup_liberty": {"sha256": tech["setup_liberty"]["sha256"]},
                    "hold_liberty": {"sha256": tech["hold_liberty"]["sha256"]},
                    "tech_lef": {"sha256": tech["tech_lef"]["sha256"]},
                    "macro_lef": {"sha256": tech["macro_lef"]["sha256"]},
                    "setup_qrc": {"sha256": tech["shared_qrc"]["sha256"]},
                    "hold_qrc": {"sha256": tech["shared_qrc"]["sha256"]},
                }},
                "library_semantics": {"evidence": {
                    "setup": {"pvt": tech["setup_liberty"]["pvt"]},
                    "hold": {"pvt": tech["hold_liberty"]["pvt"]},
                }},
                "dffnsrx1_contract": {"evidence": self.authority["rx_cell"]},
            },
        }
        path = self.root / "PROVEN_ENVIRONMENT.json"
        path.write_text(json.dumps(document))
        return path

    def write_run(self, design: str, staged: dict, staged_bound: dict,
                  environment_bound: dict, period: str = "5.0") -> dict:
        contract = self.registry["cohorts"]["tech_staged_complete_compositions"]["designs"][design]
        top = contract["top"]
        declarations = []
        seen = set()
        common = self.registry["cohorts"]["tech_staged_complete_compositions"]["common_ports"]
        for port in common + contract["link_pins"]:
            if port["name"] in seen:
                continue
            seen.add(port["name"])
            width = "" if port["width"] == 1 else f"[{port['width'] - 1}:0] "
            declarations.append(f"  {port['direction']} {width}{port['name']};")
        netlist = self.root / f"{design}.v"
        endpoint_contract = staged["designs"][design]["endpoint_leaf_contract"]
        inventory = endpoint_contract["leaf_counts"]
        prefixes = endpoint_contract["preserved_name_prefixes"]
        cells = []
        endpoint_records = []
        for index in range(inventory["TLATNTSCAX2"]):
            name = f"{prefixes['TLATNTSCAX2']}{index}"
            pins = {"E": "1'b1", "SE": "1'b0", "CK": "sample_clk_i"}
            cells.append(f"TLATNTSCAX2 {name} (.E(1'b1), .SE(1'b0), .CK(sample_clk_i), .ECK());")
            endpoint_records.append({"name": name, "cell": "TLATNTSCAX2", "pins": pins})
        for index in range(inventory["MX2X1"]):
            name = f"{prefixes['MX2X1']}{index}"
            pins = {"A": "1'b0", "B": "1'b1", "S0": "1'b0"}
            cells.append(f"MX2X1 {name} (.A(1'b0), .B(1'b1), .S0(1'b0), .Y());")
            endpoint_records.append({"name": name, "cell": "MX2X1", "pins": pins})
        for index in range(inventory["DFFRHQX1"]):
            name = f"{prefixes['DFFRHQX1']}{index}"
            pins = {"CK": "link_clk_o", "D": "1'b0", "RN": "rst_n"}
            cells.append(f"DFFRHQX1 {name} (.CK(link_clk_o), .D(1'b0), .RN(rst_n), .Q());")
            endpoint_records.append({"name": name, "cell": "DFFRHQX1", "pins": pins})
        for index in range(inventory["DFFNSRX1"]):
            bit = index % contract["link_pins"][1]["width"]
            name = f"{prefixes['DFFNSRX1']}{index}"
            pins = {"CKN": "link_clk_o", "D": f"link_data_o[{bit}]",
                    "SN": "1'b1", "RN": "rst_n"}
            cells.append(
                f"DFFNSRX1 {name} (.CKN(link_clk_o), .D(link_data_o[{bit}]), "
                ".SN(1'b1), .RN(rst_n), .Q());")
            endpoint_records.append({"name": name, "cell": "DFFNSRX1", "pins": pins})
        # Legitimate flattened scheduler/observer logic is accounted globally,
        # never charged as endpoint leaf inventory.
        cells += [
            "TLATNTSCAX2 scheduler_icg (.E(1'b1), .SE(1'b0), .CK(ref_clk_i), .ECK());",
            "MX2X1 scheduler_mux0 (.A(1'b0), .B(1'b1), .S0(1'b0), .Y());",
            "MX2X1 scheduler_mux1 (.A(1'b0), .B(1'b1), .S0(1'b0), .Y());",
            "DFFRHQX1 scheduler_state0 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
            "DFFRHQX1 scheduler_state1 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
            "DFFRHQX1 scheduler_state2 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
        ]
        netlist.write_text(f"module {top};\n" + "\n".join(declarations + cells) +
                           "\nendmodule\n")
        sdc = self.root / f"{design}.sdc"
        lines = [f"current_design {top}"]
        for clock in contract["clocks"]["input"]:
            lines.append(f"create_clock -period {period} [get_ports {clock}]")
        lines += [
            "create_generated_clock -source [get_ports sample_clk_i] [get_ports link_clk_o]",
            "set_clock_gating_check -setup 0.1 -hold 0.1 [get_pins gate/E]",
            "set_input_transition 0.1 [all_inputs]",
            "set_output_delay -min 0.1 -clock link [get_ports link_data_o*]",
            "set_output_delay -max 0.2 -clock link [get_ports link_data_o*]",
            "set_output_delay -min 0.1 -clock link -clock_fall [get_ports link_data_o*]",
            "set_output_delay -max 0.2 -clock link -clock_fall [get_ports link_data_o*]",
            "set_load 0.01 [all_outputs]",
        ]
        sdc.write_text("\n".join(lines) + "\n")
        template = self.authority["constraint_templates"][contract["constraint_template"]]
        endpoint_map = self.root / f"{design}-endpoint-map.json"
        endpoint_map.write_text(json.dumps({
            "schema": "k2_w2_endpoint_connectivity_map_v1",
            "design": design, "top": top,
            "preserved_name_prefixes": prefixes,
            "leaf_counts": inventory,
            "no_other_negedge_state_proven": True,
            "instances": endpoint_records,
        }))
        handoff = self.root / f"{design}-innovus-handoff.json"
        tech = self.authority["technology"]
        handoff.write_text(json.dumps({
            "schema": "k2_w2_innovus_strict_sdc_handoff_v1",
            "design": design, "top": top,
            "mapped_netlist_sha256": digest(netlist),
            "mapped_sdc_sha256": digest(sdc),
            "strict_input_sdc_sha256": template["sha256"],
            "setup_liberty_sha256": tech["setup_liberty"]["sha256"],
            "hold_liberty_sha256": tech["hold_liberty"]["sha256"],
            "cell_lef_sha256": tech["macro_lef"]["sha256"],
            "shared_setup_hold_qrc_sha256": tech["shared_qrc"]["sha256"],
            "shared_qrc_limitation": "ONE_TYPICAL_GPDK045_TCH_FOR_SETUP_AND_HOLD",
            "innovus_consumption_status": "PENDING_REQUIRES_EXACT_HASH_RECEIPT",
        }))
        functional = self.root / f"{design}-mapped-functional.json"
        scenarios = {
            "fovea_a7": ["held_pending", "conservation", "reset", "drain"],
            "a2_p6": ["ordered_pairs", "back_to_back", "reset"],
            "a3_p6": ["ordered_pairs", "back_to_back", "reset"],
        }[design]
        functional.write_text(json.dumps({
            "schema": "k2_w2_mapped_functional_gate_v1", "status": "PASS",
            "design": design, "top": top,
            "mapped_netlist_sha256": digest(netlist),
            "method": "xcelium_vendor_models",
            "scenarios": scenarios,
            "checks": {"accepted": "EXACT", "retired": "EXACT",
                       "global_order": "EXACT", "conservation": "EXACT",
                       "protocol_error": "ZERO", "reset_and_drain": "PASS"},
            "log_sha256": "b" * 64,
            "model_sha256": {"gsclib045_functional.v": "c" * 64},
            "sdf_status": "UNAVAILABLE_EXPLICIT",
            "sdf_sha256": None,
        }))
        receipt = self.root / f"{design}-receipt.json"
        receipt.write_text(json.dumps({
            "schema": "k2_w2_genus_exact_three_endpoint_receipt_v3",
            "status": "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD",
            "design": design, "top": top,
            "boundary_cohort": "tech_staged_complete_compositions",
            "staged_manifest": {**staged_bound, "repository_commit": "1" * 40},
            "mapped_inventory": {
                "mapped_netlist_sha256": digest(netlist),
                "mapped_cell_types": {
                    **inventory,
                    "TLATNTSCAX2": inventory["TLATNTSCAX2"] + 1,
                    "MX2X1": inventory["MX2X1"] + 2,
                    "DFFRHQX1": inventory["DFFRHQX1"] + 3,
                }, "scan_cell_types": [],
                "mapped_cell_count": sum(inventory.values()) + 6,
                "required_rx_contract": {"cell": "DFFNSRX1",
                                         "exact_instances": inventory["DFFNSRX1"]},
            },
            "mapped_sdc_sha256": digest(sdc),
            "innovus_handoff_sha256": digest(handoff),
            "endpoint_leaf_inventory": {
                "connectivity_map_sha256": digest(endpoint_map),
                "preserved_name_prefixes": prefixes,
                "leaf_counts": inventory,
                "no_other_negedge_state_proven": True,
            },
            "checks": {
                "dffnsrx1_rx_mapping":
                    "PASS_EXACT_COUNT_PINS_AND_NONZERO_RECOVERY_REMOVAL",
                "power_activity_gate": "HOLD_VECTORLESS_IS_NOT_ACTIVITY_QUALIFIED",
            },
            "mapped_functional_gate_sha256": digest(functional),
            "claim_boundary":
                "GENUS_MAPPED_TIMING_SCREENING_ONLY_POWER_AND_PHYSICAL_PPA_HOLD",
        }))
        activity = self.root / f"{design}.saif"
        activity.write_text(f"(SAIFILE (DESIGN {top}))\n")
        return {
            "design": design, "top": top,
            "clocks": copy.deepcopy(contract["clocks"]),
            "link_pins": copy.deepcopy(contract["link_pins"]),
            "period_ns": period,
            "mapped_netlist": self.bound(netlist), "mapped_sdc": self.bound(sdc),
            "producer": {"kind": "k2_w2_genus_exact_three_endpoint_receipt_v3",
                         "receipt": self.bound(receipt),
                         "innovus_handoff": self.bound(handoff),
                         "endpoint_connectivity_map": self.bound(endpoint_map),
                         "mapped_functional_gate": self.bound(functional)},
            "activity": {"file": self.bound(activity), "format": "SAIF",
                         "scope": top, "window_start_ns": "100", "window_end_ns": "900"},
            "output_dir": str(self.root / f"out-{design}"),
        }

    def plan(self) -> tuple[Path, dict]:
        staged_path, staged = self.write_manifest()
        environment_path = self.write_environment()
        staged_bound = self.bound(staged_path)
        environment_bound = self.bound(environment_path)
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        document = {
            "schema": "k2_w2_innovus_plan_v2",
            "cohort": "tech_staged_complete_compositions",
            "purpose": "final_physical_comparison", "ranking_eligible": True,
            "staged_manifest": staged_bound,
            "server_environment": environment_bound,
            "runs": [self.write_run(d, staged, staged_bound, environment_bound)
                     for d in cohort["exact_design_set"]],
        }
        path = self.root / "plan.json"
        path.write_text(json.dumps(document))
        return path, document

    def rewrite_bound(self, document: dict, path: Path, key: str) -> None:
        document[key] = self.bound(path)

    def test_registry_is_only_three_tech_staged_normalized_tops(self):
        self.assertEqual(set(self.registry["cohorts"]), {"tech_staged_complete_compositions"})
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        self.assertEqual([cohort["designs"][d]["top"] for d in cohort["exact_design_set"]], [
            "w2_fovea_r1_physical_staging_top",
            "w2_a2_p6_physical_staging_top",
            "w2_a3_p6_physical_staging_top",
        ])
        for old in ("k2_w2_fovea_a7_top", "k2_w2_a2_p6_top", "k2_w2_a3_p6_top"):
            self.assertIn(old, self.registry["forbidden_final_tops"])
        common_names = {row["name"] for row in cohort["common_ports"]}
        self.assertEqual(common_names, {
            "ref_clk_i", "sample_clk_i", "rst_n", "source_pending_i",
            "source_accept_o", "retire_valid_o", "retire_addr0_o",
            "retire_addr1_o", "drain_idle_o", "protocol_error_o",
        })
        self.assertFalse(common_names & {
            "load_i", "pending_i", "source_ready_o", "protocol_fault_o"})
        self.assertEqual(self.registry["staged_manifest_contract"], {
            "schema": "k2_w2_tech_staged_compositions_v1",
            "status": "READY_FOR_GENUS_AND_INNOVUS",
            "goal_order": ["fovea_a7", "a2_p6", "a3_p6"],
            "required_constraint_template_keys": ["r1", "p6"],
        })
        self.assertEqual(cohort["designs"]["fovea_a7"]["endpoint_root"], {
            "attribute": "w2_endpoint_root=r1",
            "stable_prefix": "w2_endpoint_link__r1",
        })
        for design in ("a2_p6", "a3_p6"):
            self.assertEqual(cohort["designs"][design]["endpoint_root"], {
                "attribute": "w2_endpoint_root=p6",
                "stable_prefix": "w2_endpoint_link__p6",
            })

    def test_tracked_registry_blocks_launch_until_committed_manifest_authorities(self):
        self.module.load_contracts = self.original_load_contracts
        with self.assertRaisesRegex(self.module.PlanError, "committed techmap manifest"):
            self.module.load_contracts()

    def test_committed_blob_gate_uses_exact_git_object(self):
        repository = self.root / "repo"
        repository.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        manifest = repository / "manifest.json"
        manifest.write_text('{"frozen":true}\n')
        subprocess.run(["git", "add", "manifest.json"], cwd=repository, check=True)
        subprocess.run([
            "git", "-c", "user.name=W2 Fixture", "-c",
            "user.email=w2@example.invalid", "commit", "-q", "-m", "fixture",
        ], cwd=repository, check=True)
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
        expected = digest(manifest)
        self.original_verify_committed_blob(manifest, commit, expected)
        manifest.write_text('{"frozen":false}\n')
        with self.assertRaisesRegex(self.module.PlanError, "committed Git blob"):
            self.original_verify_committed_blob(manifest, commit, digest(manifest))

    def test_v3_cross_bound_plan_allows_legitimate_extra_top_cells(self):
        path, _ = self.plan()
        bindings = self.module.validate_plan(path)
        self.assertEqual(len(bindings), 3)
        self.assertTrue(all(row.top.startswith("w2_") for row in bindings))
        receipt = json.loads(bindings[0].producer_path.read_text())
        endpoint = receipt["endpoint_leaf_inventory"]["leaf_counts"]
        whole = receipt["mapped_inventory"]["mapped_cell_types"]
        self.assertGreater(whole["DFFRHQX1"], endpoint["DFFRHQX1"])
        self.assertGreater(whole["MX2X1"], endpoint["MX2X1"])
        self.assertGreater(whole["TLATNTSCAX2"], endpoint["TLATNTSCAX2"])
        self.assertEqual(whole["DFFNSRX1"], endpoint["DFFNSRX1"])

    def test_old_top_cross_binding_and_canonical_manifest_mutations_are_rejected(self):
        mutations = (
            lambda doc: doc["runs"][1].update({"top": "k2_w2_a2_p6_top"}),
            lambda doc: doc["runs"][1]["producer"].update({"kind": "genus_receipt_v2"}),
            lambda doc: doc["staged_manifest"].update({"sha256": "0" * 64}),
            lambda doc: doc["server_environment"].update({"sha256": "1" * 64}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                path, document = self.plan()
                mutate(document)
                path.write_text(json.dumps(document))
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_plan(path)
        for mutation in ("old_schema", "endpoint_root", "strict_sdc"):
            with self.subTest(mutation=mutation):
                manifest_path, manifest = self.write_manifest()
                if mutation == "old_schema":
                    manifest["schema"] = "w2-physical-staging-v2"
                elif mutation == "endpoint_root":
                    manifest["designs"]["a2_p6"]["endpoint_root"][
                        "stable_prefix"] = "wrong_endpoint_root"
                else:
                    manifest["designs"]["a3_p6"]["strict_sdc"][
                        "sha256"] = "0" * 64
                manifest_path.write_text(json.dumps(manifest))
                self.ready_registry["committed_techmap_manifest"]["sha256"] = \
                    digest(manifest_path)
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_staged_manifest(
                        self.bound(manifest_path), self.ready_registry,
                        self.authority)

    def test_receipt_inventory_scan_and_recovery_mutations_are_rejected(self):
        for mutation in ("schema", "scan", "recovery", "manifest"):
            with self.subTest(mutation=mutation):
                path, document = self.plan()
                receipt_path = Path(document["runs"][0]["producer"]["receipt"]["path"])
                receipt = json.loads(receipt_path.read_text())
                if mutation == "schema": receipt["schema"] = "k2_w2_genus_receipt_v2"
                if mutation == "scan": receipt["mapped_inventory"]["scan_cell_types"] = ["SDFFX1"]
                if mutation == "recovery": receipt["checks"]["dffnsrx1_rx_mapping"] = "FAIL"
                if mutation == "manifest": receipt["staged_manifest"]["sha256"] = "f" * 64
                receipt_path.write_text(json.dumps(receipt))
                document["runs"][0]["producer"]["receipt"] = self.bound(receipt_path)
                path.write_text(json.dumps(document))
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_plan(path)

    def test_netlist_dff_binding_and_sdc_classes_are_rejected(self):
        for target, old, new in (
            ("mapped_netlist", ".SN(1'b1)", ".SN(rst_n)"),
            ("mapped_netlist", ".CKN(link_clk_o)", ".CKN(ref_clk_i)"),
            ("mapped_netlist", ".RN(rst_n)", ".RN(1'b1)"),
            ("mapped_netlist", "DFFNSRX1 w2_ep_neg_0",
             "SDFFX1 w2_ep_neg_0"),
            ("mapped_sdc", "set_input_transition", "removed_transition"),
            ("mapped_sdc", "-clock_fall", "-clock_rise"),
        ):
            with self.subTest(target=target, new=new):
                path, document = self.plan()
                run = document["runs"][0]
                artifact = Path(run[target]["path"])
                artifact.write_text(artifact.read_text().replace(old, new))
                run[target] = self.bound(artifact)
                receipt_path = Path(run["producer"]["receipt"]["path"])
                receipt = json.loads(receipt_path.read_text())
                if target == "mapped_netlist":
                    receipt["mapped_inventory"]["mapped_netlist_sha256"] = digest(artifact)
                else:
                    receipt["mapped_sdc_sha256"] = digest(artifact)
                receipt_path.write_text(json.dumps(receipt))
                run["producer"]["receipt"] = self.bound(receipt_path)
                path.write_text(json.dumps(document))
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_plan(path)

    def test_final_top_alias_mutations_are_rejected_even_when_rehashed(self):
        for canonical, alias in (("source_pending_i", "load_i"),
                                 ("protocol_error_o", "protocol_fault_o")):
            with self.subTest(alias=alias):
                path, document = self.plan()
                run = document["runs"][0]
                netlist = Path(run["mapped_netlist"]["path"])
                netlist.write_text(netlist.read_text().replace(canonical, alias))
                run["mapped_netlist"] = self.bound(netlist)
                handoff_path = Path(run["producer"]["innovus_handoff"]["path"])
                handoff = json.loads(handoff_path.read_text())
                handoff["mapped_netlist_sha256"] = digest(netlist)
                handoff_path.write_text(json.dumps(handoff))
                run["producer"]["innovus_handoff"] = self.bound(handoff_path)
                functional_path = Path(run["producer"]["mapped_functional_gate"]["path"])
                functional = json.loads(functional_path.read_text())
                functional["mapped_netlist_sha256"] = digest(netlist)
                functional_path.write_text(json.dumps(functional))
                run["producer"]["mapped_functional_gate"] = self.bound(functional_path)
                receipt_path = Path(run["producer"]["receipt"]["path"])
                receipt = json.loads(receipt_path.read_text())
                receipt["mapped_inventory"]["mapped_netlist_sha256"] = digest(netlist)
                receipt["innovus_handoff_sha256"] = digest(handoff_path)
                receipt["mapped_functional_gate_sha256"] = digest(functional_path)
                receipt_path.write_text(json.dumps(receipt))
                run["producer"]["receipt"] = self.bound(receipt_path)
                path.write_text(json.dumps(document))
                with self.assertRaisesRegex(self.module.PlanError, "canonical port"):
                    self.module.validate_plan(path)

    def test_mapped_functional_gate_mutation_is_rejected(self):
        path, document = self.plan()
        run = document["runs"][1]
        functional_path = Path(run["producer"]["mapped_functional_gate"]["path"])
        functional = json.loads(functional_path.read_text())
        functional["checks"]["global_order"] = "SKIPPED"
        functional_path.write_text(json.dumps(functional))
        run["producer"]["mapped_functional_gate"] = self.bound(functional_path)
        receipt_path = Path(run["producer"]["receipt"]["path"])
        receipt = json.loads(receipt_path.read_text())
        receipt["mapped_functional_gate_sha256"] = digest(functional_path)
        receipt_path.write_text(json.dumps(receipt))
        run["producer"]["receipt"] = self.bound(receipt_path)
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(self.module.PlanError, "functional gate"):
            self.module.validate_plan(path)

    def test_exact_provider_and_consumer_cli_help_contracts(self):
        self.assertTrue(GENUS_PROVIDER_REPO.is_dir())
        provider = subprocess.run([
            "git", "-C", str(GENUS_PROVIDER_REPO), "show",
            f"{GENUS_PROVIDER_COMMIT}:physical/k2_w2_genus/run_genus.py",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.assertEqual(hashlib.sha256(provider.stdout).hexdigest(),
                         "28b4249da0b8128777adb3325cbc8da84499f919eff83321f0c3be7fb0ed51fe")
        for source, local in (
                ("constraints/r1_multiclock_strict.sdc",
                 ROOT / "constraints/r1_multiclock.sdc"),
                ("constraints/p6_multiclock_strict.sdc",
                 ROOT / "constraints/p6_multiclock.sdc")):
            canonical = subprocess.run([
                "git", "-C", str(GENUS_PROVIDER_REPO), "show",
                f"{GENUS_PROVIDER_COMMIT}:{source}",
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            self.assertEqual(local.read_bytes(), canonical.stdout)
        provider_script = self.root / "run_genus.py"
        provider_script.write_bytes(provider.stdout)
        help_result = subprocess.run(
            [sys.executable, str(provider_script), "--help"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for option in ("--hold-library", "--cell-lef", "--shared-qrc"):
            self.assertIn(option, help_result.stdout)
        self.assertNotIn("--activity-receipt", help_result.stdout)
        source = provider.stdout.decode()
        self.assertIn('"schema": "k2_w2_genus_exact_three_endpoint_receipt_v3"', source)
        consumer_help = subprocess.run(
            [sys.executable, str(RUNNER), "--help"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(consumer_help.returncode, 0, consumer_help.stderr)
        for option in ("--plan", "--validate-only", "--execute"):
            self.assertIn(option, consumer_help.stdout)
        for unsupported in ("--expected-plan-sha-file", "--environment-receipt",
                            "--calibration-receipt", "--cohort"):
            self.assertNotIn(unsupported, consumer_help.stdout)

    def test_execute_uses_plan_owned_hashed_descriptor(self):
        path, _ = self.plan()
        bindings = self.module.validate_plan(path)
        completed = self.module.subprocess.CompletedProcess([], 0)
        observed = []
        def invoke(*args, **kwargs):
            environment = kwargs["env"]
            descriptor = Path(environment["AER_W2_EXECUTION_DESCRIPTOR"])
            payload = descriptor.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(),
                             environment["AER_W2_EXECUTION_DESCRIPTOR_SHA256"])
            self.assertEqual(descriptor.stat().st_mode & 0o222, 0)
            observed.append(json.loads(payload)["binding"]["top"])
            return completed
        with mock.patch.object(self.module.subprocess, "run", side_effect=invoke):
            self.module.execute_plan(bindings)
        self.assertEqual(observed, [row.top for row in bindings])

    def test_direct_shell_sentinel_bypass_is_gone(self):
        text = SHELL.read_text()
        self.assertNotIn("AER_W2_PLAN_VALIDATED", text)
        self.assertIn("--verify-descriptor", text)
        result = subprocess.run([str(SHELL)], env={"PATH": os.environ["PATH"]},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing AER_TOP", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
