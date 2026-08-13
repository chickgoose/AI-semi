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
    "W2_GENUS_PROVIDER_REPO", str(ROOT)))
GENUS_PROVIDER_COMMIT = os.environ.get("W2_GENUS_PROVIDER_COMMIT", "HEAD")


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

    def timing(self, profile_id: str) -> tuple[dict, dict, dict]:
        binding = self.module.timing_profile_binding(
            self.ready_registry, profile_id)
        manifest_path = ROOT / self.ready_registry["timing_cohort_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text())
        profile = copy.deepcopy(manifest["cohorts"][profile_id])
        selected = {
            **profile,
            "id": profile_id,
            "manifest_path": self.ready_registry["timing_cohort_manifest"]["path"],
            "manifest_sha256": binding["genus_timing_manifest_sha256"],
            "profile_sha256": binding["genus_timing_profile_sha256"],
        }
        identity = {
            "path": self.ready_registry["timing_cohort_manifest"]["path"],
            "sha256": binding["genus_timing_manifest_sha256"],
            "required_schema": "k2_w2_genus_timing_cohorts_v1",
        }
        return binding, selected, identity

    def write_manifest(self) -> tuple[Path, dict]:
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        common_ports = self.module.staged_common_ports(cohort)
        technology = self.authority["technology"]
        document = {
            "schema": "k2_w2_tech_staged_compositions_v1",
            "status": "READY_FOR_GENUS_AND_INNOVUS",
            "repository_commit": "1" * 40,
            "goal_order": cohort["exact_design_set"],
            "common_ports": common_ports,
            "technology_authorities": {
                "raw_golden": {
                    "path": "/tmp/ganghee-pnr-raw-golden-20260813.tar.gz",
                    "sha256": "7989dd65c220b4b58d131cda0a49678e915c2422b2f6d321b960dd2213118cd3",
                },
                "buffered_golden": {
                    "path": "/tmp/ganghee-pnr-golden-20260813.tar.gz",
                    "sha256": "1f01904669b159190bdf8497c62e68dff87214ddecb8f05fb20a226289c2ac5f",
                },
                "live_gsclib045": {
                    "liberty": "/fixture/" + technology["setup_liberty"]["relative_path"],
                    "technology_lef": "/fixture/" + technology["tech_lef"]["relative_path"],
                    "macro_lef": "/fixture/" + technology["macro_lef"]["relative_path"],
                    "qrc": "/fixture/" + technology["shared_qrc"]["relative_path"],
                    "dffnsrx1_cell_and_interface_verified": True,
                    "liberty_timing_arcs_claimed_by_manifest": False,
                },
                "cells": {
                    "TLATNTSCAX2": {"ports": ["CK", "E", "SE", "ECK"]},
                    "MX2X1": {"ports": ["A", "B", "S0", "Y"]},
                    "DFFRHQX1": {"ports": ["RN", "CK", "D", "Q"]},
                    "DFFNSRX1": {"ports": ["CKN", "D", "RN", "SN", "Q", "QN"]},
                },
            },
            "constraint_templates": self.registry["staged_manifest_contract"][
                "constraint_templates"],
            "designs": {
                design: {
                    "top": cohort["designs"][design]["top"],
                    "filelists": copy.deepcopy(
                        cohort["designs"][design]["staged_filelists"]),
                    "port_signature": self.module.staged_port_signature(
                        common_ports, design),
                    "endpoint_root": copy.deepcopy(
                        cohort["designs"][design]["endpoint_root"]),
                    "endpoint_leaf_contract": {
                        "path_segment": cohort["designs"][design]["endpoint_root"][
                            "stable_prefix"],
                        "leaf_counts": copy.deepcopy(cohort["designs"][design][
                            "endpoint_leaf_contract"]["leaf_counts"]),
                        "preserved_name_prefixes": copy.deepcopy(
                            cohort["designs"][design]["endpoint_leaf_contract"][
                                "preserved_name_prefixes"]),
                    },
                    "whole_top_observed_totals": {
                        "status": "PENDING_DEDICATED_GENUS_RUN", "records": []},
                }
                for design in cohort["exact_design_set"]
            },
            "source_hashes": {"rtl/fixture.sv": "a" * 64},
            "test_policy": {
                "acceptance_sample": "posedge_ref_active_region_pre_NBA",
                "pending_hold": "through_charged_posedge",
                "protocol_error_must_equal_zero": True,
                "epoch_accepted_equals_retired": True,
                "cell_models_test_only": True,
            },
            "consumer_contract": {
                "consumers": ["genus", "innovus"],
                "manifest_path":
                    "rtl/technology/physical_staging/physical_staging_manifest.json",
                "required_schema": "k2_w2_tech_staged_compositions_v1",
                "required_status": "READY_FOR_GENUS_AND_INNOVUS",
                "require_repository_commit": True,
                "require_literal_common_port_signature": True,
                "require_endpoint_path_and_leaf_provenance": True,
                "forbidden_port_aliases": [
                    "load_i", "pending_i", "source_ready_o", "protocol_fault_o",
                    "link_enable", "link_enable_i", "burst_clk_o", "burst_data_o",
                    "p6_clk_o", "p6_data_o"],
            },
        }
        path = self.root / "staged-manifest.json"
        path.write_text(json.dumps(document))
        self.ready_registry["committed_techmap_manifest"] = {
            "source_repository_commit": document["repository_commit"],
            "publication_repository_commit": "2" * 40,
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
                    "setup_liberty": {"path": "/server/slow.lib",
                                      "sha256": tech["setup_liberty"]["sha256"]},
                    "hold_liberty": {"path": "/server/fast.lib",
                                     "sha256": tech["hold_liberty"]["sha256"]},
                    "tech_lef": {"path": "/server/tech.lef",
                                 "sha256": tech["tech_lef"]["sha256"]},
                    "macro_lef": {"path": "/server/cells.lef",
                                  "sha256": tech["macro_lef"]["sha256"]},
                    "setup_qrc": {"path": "/server/gpdk045.tch",
                                  "sha256": tech["shared_qrc"]["sha256"]},
                    "hold_qrc": {"path": "/server/gpdk045.tch",
                                 "sha256": tech["shared_qrc"]["sha256"]},
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
                  environment_bound: dict, period: str = "5.0",
                  profile_id: str = "three_endpoint_5p0ns") -> dict:
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
        endpoint_contract = contract["endpoint_leaf_contract"]
        inventory = endpoint_contract["leaf_counts"]
        prefixes = endpoint_contract["preserved_name_prefixes"]
        endpoint_roots = endpoint_contract["endpoint_link_roots"]
        root = contract["endpoint_root"]["stable_prefix"]
        width = contract["link_pins"][1]["width"]
        cells = []
        endpoint_records = []
        def record(cell: str, name: str, pins: dict[str, str]) -> None:
            endpoint_records.append({
                "hierarchy": f"{top}.{name}",
                "mapped_instance": name,
                "cell_type": cell,
                "pin_bindings": dict(sorted(pins.items())),
                "provenance_root": root,
            })

        cells.append(
            f"AND2XL {root}_endpoint_enable "
            f"(.A({root}_frame_active), .B(rst_n), .Y({root}_icg_e));")
        for index in range(inventory["TLATNTSCAX2"]):
            name = f"{root}_tx_clock_boundary_{prefixes['TLATNTSCAX2']}{index}"
            pins = {"E": f"{root}_icg_e", "SE": "1'b0",
                    "CK": "sample_clk_i", "ECK": "link_clk_o"}
            cells.append(
                f"TLATNTSCAX2 {name} (.E({root}_icg_e), .SE(1'b0), "
                ".CK(sample_clk_i), .ECK(link_clk_o));")
            record("TLATNTSCAX2", name, pins)
        for index in range(inventory["MX2X1"]):
            name = f"{root}_tx_serialize_gen_lane[{index}].{prefixes['MX2X1']}bit"
            mux_output = f"mux_y[{index}]" if index == 0 else f"link_data_o[{index}]"
            pins = {"A": f"frame_hi[{index}]", "B": f"frame_lo[{index}]",
                    "S0": "ref_clk_i", "Y": mux_output}
            cells.append(
                f"MX2X1 \\{name}  (.A(frame_hi[{index}]), "
                f".B(frame_lo[{index}]), .S0(ref_clk_i), "
                f".Y({mux_output}));")
            record("MX2X1", name, pins)
            if index == 0:
                cells.append(
                    "BUFX2 endpoint_mux_buffer (.A(mux_y[0]), "
                    ".Y(link_data_o[0]));")
        for index in range(inventory["DFFRHQX1"]):
            name = f"{root}_rx_low_symbol_capture_gen_capture[{index}].{prefixes['DFFRHQX1']}bit"
            pins = {"CK": "link_clk_o", "D": f"link_data_o[{index}]",
                    "RN": "rst_n", "Q": f"low_symbol_q[{index}]"}
            cells.append(
                f"DFFRHQX1 \\{name}  (.CK(link_clk_o), "
                f".D(link_data_o[{index}]), .RN(rst_n), "
                f".Q(low_symbol_q[{index}]));")
            record("DFFRHQX1", name, pins)
        for index in range(inventory["DFFNSRX1"]):
            name = f"{root}_rx_closing_capture_gen_capture[{index}].{prefixes['DFFNSRX1']}bit"
            data = "toggle_feedback" if index == 0 else f"closing_state_d[{index}]"
            pins = {"CKN": "link_clk_o", "D": data,
                    "SN": "1'b1", "RN": "rst_n",
                    "Q": f"closing_state_q[{index}]"}
            qn = ", .QN(toggle_feedback)" if index == 0 else ""
            if index == 0:
                pins["QN"] = "toggle_feedback"
            cells.append(
                f"DFFNSRX1 \\{name}  (.CKN(link_clk_o), "
                f".D({data}), .SN(1'b1), .RN(rst_n), "
                f".Q(closing_state_q[{index}]){qn});")
            record("DFFNSRX1", name, pins)
        # Legitimate flattened scheduler/observer logic is accounted globally,
        # never charged as endpoint leaf inventory.
        cells += [
            "RC_CG_MOD scheduler_icg (.enable(1'b1), .ck_in(ref_clk_i), "
            ".ck_out(), .test(1'b0));",
            "MX2X1 scheduler_mux0 (.A(1'b0), .B(1'b1), .S0(1'b0), .Y());",
            "MX2X1 scheduler_mux1 (.A(1'b0), .B(1'b1), .S0(1'b0), .Y());",
            "DFFRHQX1 scheduler_state0 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
            "DFFRHQX1 scheduler_state1 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
            "DFFRHQX1 scheduler_state2 (.CK(ref_clk_i), .D(1'b0), .RN(rst_n), .Q());",
        ]
        internal = [
            f"  wire {root}_frame_active, {root}_icg_e;",
            f"  wire [{width - 1}:0] frame_hi, frame_lo, low_symbol_q;",
            f"  wire [{width - 1}:0] mux_y;",
            f"  wire [{inventory['DFFNSRX1'] - 1}:0] closing_state_d, closing_state_q;",
        ]
        netlist.write_text(f"module {top};\n" +
                           "\n".join(declarations + internal + cells) +
                           "\nendmodule\n" +
                           "module RC_CG_MOD(enable, ck_in, ck_out, test);\n"
                           "  input enable, ck_in, test; output ck_out;\n"
                           "  TLATNTSCAX2 RC_CGIC_INST (.E(enable), .SE(test), "
                           ".CK(ck_in), .ECK(ck_out));\n"
                           "endmodule\n")
        timing_binding, selected_timing, timing_identity = self.timing(profile_id)
        waveforms = selected_timing["clock_waveforms_ns"]
        sdc = self.root / f"{design}.sdc"
        lines = [
            f"current_design {top}",
            f"create_clock -name w2_ref_clk -period {period} "
            f"-waveform {{{waveforms['ref_clk'][0]} {waveforms['ref_clk'][1]}}} "
            "[get_ports ref_clk_i]",
            f"create_clock -name w2_sample_clk -period {period} "
            f"-waveform {{{waveforms['sample_clk'][0]} {waveforms['sample_clk'][1]}}} "
            "[get_ports sample_clk_i]",
            f"create_clock -name w2_reset_release_clk -period {period} "
            f"-waveform {{{waveforms['reset_release_clk'][0]} "
            f"{waveforms['reset_release_clk'][1]}}}",
        ]
        lines += [
            "create_generated_clock -name w2_forwarded_link_clk "
            "-source [get_ports sample_clk_i] -divide_by 1 "
            f"[get_pins {root}_tx_clock_boundary_w2_ep_icg_0/ECK]",
            "set_clock_gating_check -setup 0.0",
            f"set_clock_gating_check -setup 0.10 -hold 0.05 "
            f"[get_pins {root}_tx_clock_boundary_w2_ep_icg_0/E]",
            "set_clock_uncertainty 0.25 [all_clocks]",
            "set_input_delay -min 0.10 -clock w2_ref_clk [all_inputs]",
            "set_input_delay -max 0.50 -clock w2_ref_clk [all_inputs]",
            "set_input_transition 0.05 [all_inputs]",
            "set_output_delay -min 0.1 -clock link [get_ports link_data_o*]",
            "set_output_delay -max 0.5 -clock link [get_ports link_data_o*]",
            "set_output_delay -min 0.1 -clock link -clock_fall [get_ports link_data_o*]",
            "set_output_delay -max 0.5 -clock link -clock_fall [get_ports link_data_o*]",
            "set_min_pulse_width -high 0.50 [all_clocks]",
            "set_min_pulse_width -low 0.50 [all_clocks]",
            "set_load 0.01 [all_outputs]",
        ]
        sdc.write_text("\n".join(lines) + "\n")
        template = self.authority["constraint_templates"][contract["constraint_template"]]
        endpoint_map = self.root / f"{design}-endpoint-map.json"
        endpoint_map.write_text(json.dumps({
            "schema": "k2_w2_endpoint_connectivity_map_v1",
            "design": design, "top": top,
            "mapped_netlist_sha256": digest(netlist),
            "endpoint_link_roots": endpoint_roots,
            "preserved_name_prefixes": prefixes,
            "leaf_counts": inventory,
            "no_other_negedge_state_proven": True,
            "instances": endpoint_records,
        }))
        handoff = self.root / f"{design}-innovus-handoff.json"
        tech = self.authority["technology"]
        mapped_sdf_sha = "d" * 64
        handoff.write_text(json.dumps({
            "schema": "k2_w2_innovus_strict_sdc_handoff_v1",
            "design": design, "top": top,
            "mapped_netlist_sha256": digest(netlist),
            "mapped_sdf_sha256": mapped_sdf_sha,
            "mapped_sdc_sha256": digest(sdc),
            "strict_input_sdc_sha256": template["sha256"],
            "materialized_input_sdc_path": "bundle/constraints.sdc",
            "materialized_input_sdc_sha256": "e" * 64,
            "timing_cohort_manifest": timing_identity,
            "timing_cohort": selected_timing,
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
            "sdf_status": "ANNOTATED",
            "sdf_sha256": mapped_sdf_sha,
        }))
        receipt = self.root / f"{design}-receipt.json"
        pointer = self.ready_registry["committed_techmap_manifest"]
        receipt.write_text(json.dumps({
            "schema": "k2_w2_genus_exact_three_endpoint_receipt_v3",
            "status": "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD",
            "design": design, "top": top,
            "boundary_cohort": "tech_staged_complete_compositions",
            "source_origin": "tech_staged_repository_exact",
            "ranking_policy":
                "ONLY_THREE_TECH_STAGED_COMPLETE_COMPOSITIONS_COMPARABLE",
            "staged_manifest": {
                "path": pointer["path"],
                "sha256": staged_bound["sha256"],
                "source_commit": pointer["source_repository_commit"],
                "publication_commit": pointer["publication_repository_commit"],
            },
            "technology_authorities": copy.deepcopy(
                staged["technology_authorities"]),
            "timing_cohort_manifest": timing_identity,
            "timing_cohort": selected_timing,
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
            "mapped_sdf_sha256": mapped_sdf_sha,
            "mapped_sdc_sha256": digest(sdc),
            "strict_sdc_sha256": template["sha256"],
            "materialized_sdc_sha256": "e" * 64,
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

    def plan(self, profile_id: str = "three_endpoint_5p0ns") -> tuple[Path, dict]:
        staged_path, staged = self.write_manifest()
        environment_path = self.write_environment()
        staged_bound = self.bound(staged_path)
        environment_bound = self.bound(environment_path)
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        timing, _, _ = self.timing(profile_id)
        document = {
            "schema": "k2_w2_innovus_plan_v3",
            "cohort": "tech_staged_complete_compositions",
            "purpose": "final_physical_comparison", "ranking_eligible": True,
            "timing_profile": {
                "id": profile_id,
                "profile_sha256": timing["innovus_timing_profile_sha256"],
            },
            "staged_manifest": staged_bound,
            "server_environment": environment_bound,
            "runs": [self.write_run(
                d, staged, staged_bound, environment_bound,
                timing["period_ns"], profile_id)
                     for d in cohort["exact_design_set"]],
        }
        path = self.root / "plan.json"
        path.write_text(json.dumps(document))
        return path, document

    def rewrite_bound(self, document: dict, path: Path, key: str) -> None:
        document[key] = self.bound(path)

    def validate_run_netlist(self, document: dict, index: int = 0) -> dict[str, int]:
        run = document["runs"][index]
        cohort = self.registry["cohorts"]["tech_staged_complete_compositions"]
        contract = cohort["designs"][run["design"]]
        netlist = Path(run["mapped_netlist"]["path"])
        endpoint_map = Path(
            run["producer"]["endpoint_connectivity_map"]["path"])
        return self.module.validate_netlist(
            netlist.read_bytes(), run["top"], contract,
            cohort["common_ports"], contract["endpoint_leaf_contract"],
            json.loads(endpoint_map.read_text()))

    def test_registry_is_only_three_tech_staged_normalized_tops(self):
        self.assertEqual(self.registry["schema"],
                         "k2_w2_innovus_cohort_registry_v4")
        self.assertEqual(self.registry["timing_profile_order"],
                         ["three_endpoint_5p0ns", "three_endpoint_5p7ns"])
        self.assertFalse(self.registry["timing_profiles"][
            "three_endpoint_5p0ns"]["hold_fix_allow_setup_tns_degrade"])
        self.assertTrue(self.registry["timing_profiles"][
            "three_endpoint_5p7ns"]["hold_fix_allow_setup_tns_degrade"])
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
        staged = self.registry["staged_manifest_contract"]
        self.assertEqual(staged["schema"], "k2_w2_tech_staged_compositions_v1")
        self.assertEqual(staged["status"], "READY_FOR_GENUS_AND_INNOVUS")
        self.assertEqual(staged["goal_order"], ["fovea_a7", "a2_p6", "a3_p6"])
        self.assertEqual(staged["constraint_templates"]["ref_period_ns"], 5.0)
        self.assertEqual(cohort["designs"]["fovea_a7"]["endpoint_root"], {
            "attribute": "w2_endpoint_root=r1",
            "stable_prefix": "w2_endpoint_link__r1",
        })
        self.assertEqual(cohort["designs"]["fovea_a7"]["endpoint_leaf_contract"][
            "endpoint_link_roots"], ["w2_r1_ddr_tx_tech", "w2_r1_ddr_rx_tech"])
        for design in ("a2_p6", "a3_p6"):
            self.assertEqual(cohort["designs"][design]["endpoint_root"], {
                "attribute": "w2_endpoint_root=p6",
                "stable_prefix": "w2_endpoint_link__p6",
            })
            self.assertEqual(cohort["designs"][design]["endpoint_leaf_contract"][
                "endpoint_link_roots"],
                ["w2_p6_pair_tx_tech", "w2_p6_pair_rx_tech"])

    def test_tracked_registry_pins_exact_source_publication_and_manifest(self):
        self.module.load_contracts = self.original_load_contracts
        registry, _ = self.module.load_contracts()
        self.assertEqual(registry["committed_techmap_manifest"], {
            "source_repository_commit":
                "ba0116029bb79573dca23c3957845885837f4b82",
            "publication_repository_commit":
                "823b768ba3dad82b3de0febd3d5f2c556c0643be",
            "path": "rtl/technology/physical_staging/physical_staging_manifest.json",
            "sha256":
                "4ffda1982bdc3925723cd22821d06b27a273c9e9e4acc05db6150c9fe84d7d9d",
        })

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

    def test_5p7_profile_binds_three_runs_and_hold_policy(self):
        path, document = self.plan("three_endpoint_5p7ns")
        bindings = self.module.validate_plan(path)
        self.assertEqual(document["schema"], "k2_w2_innovus_plan_v3")
        self.assertEqual(document["timing_profile"]["id"],
                         "three_endpoint_5p7ns")
        self.assertEqual([row.period_ns for row in bindings], ["5.7"] * 3)
        self.assertTrue(all(row.timing_profile_id ==
                            "three_endpoint_5p7ns" for row in bindings))
        self.assertTrue(all(row.hold_fix_allow_setup_tns_degrade
                            for row in bindings))
        policy = self.registry["timing_profiles"]["three_endpoint_5p7ns"]
        self.assertEqual(policy["activity_timestamp_ratio"],
                         {"numerator": 57, "denominator": 100})
        self.assertEqual(policy["forwarded_link_clock"], {
            "master_source_port": "sample_clk_i",
            "forward_source_pin": "*w2_ep_icg_0/ECK",
            "target_port": "link_clk_o",
            "divide_by": 1, "false_path": "FORBIDDEN",
        })

    def test_5p7_profile_sdc_forwarded_clock_and_receipt_mutations_reject(self):
        for mutation in ("profile_sha", "period", "waveform", "input_delay",
                         "eck_source", "divide", "false_path", "receipt_profile"):
            with self.subTest(mutation=mutation):
                path, document = self.plan("three_endpoint_5p7ns")
                run = document["runs"][0]
                if mutation == "profile_sha":
                    document["timing_profile"]["profile_sha256"] = "0" * 64
                elif mutation == "period":
                    run["period_ns"] = "5.0"
                elif mutation in {"waveform", "input_delay", "eck_source",
                                  "divide", "false_path"}:
                    sdc = Path(run["mapped_sdc"]["path"])
                    text = sdc.read_text()
                    if mutation == "waveform":
                        text = text.replace("{0.0 2.85}", "{0.0 2.80}", 1)
                    elif mutation == "input_delay":
                        text = text.replace("set_input_delay -max 0.50",
                                            "set_input_delay -max 0.60", 1)
                    elif mutation == "eck_source":
                        text = text.replace("/ECK]", "/E]", 1)
                    elif mutation == "divide":
                        text = text.replace("-divide_by 1", "-divide_by 2")
                    else:
                        text += "set_false_path -from [get_ports sample_clk_i] " \
                                "-to [get_ports link_clk_o]\n"
                    sdc.write_text(text)
                    run["mapped_sdc"] = self.bound(sdc)
                else:
                    receipt_path = Path(run["producer"]["receipt"]["path"])
                    receipt = json.loads(receipt_path.read_text())
                    receipt["timing_cohort"]["id"] = "three_endpoint_5p0ns"
                    receipt_path.write_text(json.dumps(receipt))
                    run["producer"]["receipt"] = self.bound(receipt_path)
                path.write_text(json.dumps(document))
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_plan(path)

    def test_genus_positional_equal_pulse_width_is_accepted_fail_closed(self):
        path, document = self.plan("three_endpoint_5p7ns")
        for run in document["runs"]:
            sdc = Path(run["mapped_sdc"]["path"])
            text = sdc.read_text()
            text = text.replace(
                "set_min_pulse_width -high 0.50 [all_clocks]\n"
                "set_min_pulse_width -low 0.50 [all_clocks]",
                "set_min_pulse_width 0.5 [all_clocks]")
            text = text.replace(
                "set_clock_uncertainty 0.25 [all_clocks]",
                "set_clock_uncertainty -setup 0.25 [all_clocks]\n"
                "set_clock_uncertainty -hold 0.25 [all_clocks]")
            sdc.write_text(text)
            contract = self.registry["cohorts"][
                "tech_staged_complete_compositions"]["designs"][run["design"]]
            policy = self.registry["timing_profiles"]["three_endpoint_5p7ns"]
            self.module.validate_sdc(
                sdc.read_bytes(), run["top"], contract, "5.7", policy)

        sdc = Path(document["runs"][0]["mapped_sdc"]["path"])
        sdc.write_text(sdc.read_text().replace(
            "set_min_pulse_width 0.5", "set_min_pulse_width 0.6", 1))
        run = document["runs"][0]
        contract = self.registry["cohorts"][
            "tech_staged_complete_compositions"]["designs"][run["design"]]
        policy = self.registry["timing_profiles"]["three_endpoint_5p7ns"]
        with self.assertRaisesRegex(self.module.PlanError, "pulse-width"):
            self.module.validate_sdc(
                sdc.read_bytes(), run["top"], contract, "5.7", policy)

    def test_actual_genus_flattened_endpoint_map_schema_is_consumed(self):
        _, document = self.plan()
        whole = self.validate_run_netlist(document)
        run = document["runs"][0]
        endpoint_map = json.loads(Path(
            run["producer"]["endpoint_connectivity_map"]["path"]).read_text())
        self.assertEqual(set(endpoint_map), {
            "schema", "design", "top", "mapped_netlist_sha256",
            "endpoint_link_roots", "preserved_name_prefixes", "leaf_counts",
            "no_other_negedge_state_proven", "instances",
        })
        expected_row_fields = {
            "hierarchy", "mapped_instance", "cell_type", "pin_bindings",
            "provenance_root",
        }
        self.assertTrue(endpoint_map["instances"])
        self.assertTrue(all(set(row) == expected_row_fields
                            for row in endpoint_map["instances"]))
        root = "w2_endpoint_link__r1"
        self.assertTrue(all(root in row["mapped_instance"] and
                            row["provenance_root"] == root
                            for row in endpoint_map["instances"]))
        self.assertGreater(whole["DFFRHQX1"],
                           endpoint_map["leaf_counts"]["DFFRHQX1"])

    def test_flattened_endpoint_map_sha_root_role_count_and_pins_fail_closed(self):
        mutations = ("map_sha", "link_roots", "root", "role", "count", "pin")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                _, document = self.plan()
                run = document["runs"][0]
                netlist_path = Path(run["mapped_netlist"]["path"])
                map_path = Path(run["producer"]["endpoint_connectivity_map"]["path"])
                endpoint_map = json.loads(map_path.read_text())
                text = netlist_path.read_text()
                if mutation == "map_sha":
                    endpoint_map["mapped_netlist_sha256"] = "0" * 64
                elif mutation == "link_roots":
                    endpoint_map["endpoint_link_roots"][0] = "wrong_endpoint_root"
                elif mutation == "root":
                    text = text.replace(
                        "w2_endpoint_link__r1_rx_low_symbol_capture_gen_capture[0].w2_ep_pos_bit",
                        "owner_rx_low_symbol_capture_gen_capture[0].w2_ep_pos_bit", 1)
                    netlist_path.write_text(text)
                    endpoint_map["mapped_netlist_sha256"] = digest(netlist_path)
                elif mutation == "role":
                    text = text.replace(".w2_ep_pos_bit", ".w2_ep_neg_bit", 1)
                    netlist_path.write_text(text)
                    endpoint_map["mapped_netlist_sha256"] = digest(netlist_path)
                elif mutation == "count":
                    text = text.replace("MX2X1 \\w2_endpoint_link__r1",
                                        "OAI2BB1X4 \\w2_endpoint_link__r1", 1)
                    netlist_path.write_text(text)
                    endpoint_map["mapped_netlist_sha256"] = digest(netlist_path)
                else:
                    text = text.replace(".S0(ref_clk_i)", ".S0(sample_clk_i)", 1)
                    netlist_path.write_text(text)
                    endpoint_map["mapped_netlist_sha256"] = digest(netlist_path)
                    contract = self.registry["cohorts"][
                        "tech_staged_complete_compositions"]["designs"]["fovea_a7"]
                    records, _ = self.module.flattened_endpoint_records(
                        run["top"], text, contract["endpoint_root"]["stable_prefix"],
                        contract["endpoint_leaf_contract"]["preserved_name_prefixes"])
                    endpoint_map["instances"] = records
                map_path.write_text(json.dumps(endpoint_map))
                with self.assertRaises(self.module.PlanError):
                    self.validate_run_netlist(document)

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
        for mutation in ("old_schema", "endpoint_root", "filelist"):
            with self.subTest(mutation=mutation):
                manifest_path, manifest = self.write_manifest()
                if mutation == "old_schema":
                    manifest["schema"] = "w2-physical-staging-v2"
                elif mutation == "endpoint_root":
                    manifest["designs"]["a2_p6"]["endpoint_root"][
                        "stable_prefix"] = "wrong_endpoint_root"
                else:
                    manifest["designs"]["a3_p6"]["filelists"][
                        "gsclib045"] = "rtl/technology/physical_staging/filelists/wrong.f"
                manifest_path.write_text(json.dumps(manifest))
                self.ready_registry["committed_techmap_manifest"]["sha256"] = \
                    digest(manifest_path)
                with self.assertRaises(self.module.PlanError):
                    self.module.validate_staged_manifest(
                        self.bound(manifest_path), self.ready_registry,
                        self.authority)

    def test_receipt_inventory_scan_and_recovery_mutations_are_rejected(self):
        for mutation in ("schema", "scan", "recovery", "manifest",
                         "source_commit", "publication_commit", "technology"):
            with self.subTest(mutation=mutation):
                path, document = self.plan()
                receipt_path = Path(document["runs"][0]["producer"]["receipt"]["path"])
                receipt = json.loads(receipt_path.read_text())
                if mutation == "schema": receipt["schema"] = "k2_w2_genus_receipt_v2"
                if mutation == "scan": receipt["mapped_inventory"]["scan_cell_types"] = ["SDFFX1"]
                if mutation == "recovery": receipt["checks"]["dffnsrx1_rx_mapping"] = "FAIL"
                if mutation == "manifest": receipt["staged_manifest"]["sha256"] = "f" * 64
                if mutation == "source_commit":
                    receipt["staged_manifest"]["source_commit"] = "e" * 40
                if mutation == "publication_commit":
                    receipt["staged_manifest"]["publication_commit"] = "d" * 40
                if mutation == "technology":
                    receipt["technology_authorities"]["cells"]["DFFNSRX1"][
                        "ports"] = ["CKN", "D", "RN", "Q"]
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
            ("mapped_netlist",
             "DFFNSRX1 \\w2_endpoint_link__r1_rx_closing_capture_gen_capture[0].w2_ep_neg_bit",
             "SDFFX1 \\w2_endpoint_link__r1_rx_closing_capture_gen_capture[0].w2_ep_neg_bit"),
            ("mapped_sdc", "set_input_transition", "removed_transition"),
            ("mapped_sdc", "-clock_fall", "-clock_rise"),
            ("mapped_sdc",
             "w2_endpoint_link__r1_tx_clock_boundary_w2_ep_icg_0/E",
             "w2_endpoint_link__r1_tx_gen_symbol_mux[0].w2_ep_mux_bit/S0"),
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
                         "e8920eecb76e8c77a0dbb3988350196fbd89b9b6a7e81c67c8ff067fcd6e0c1c")
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
        self.assertIn("--timing-cohort", help_result.stdout)
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
            document = json.loads(payload)
            self.assertEqual(document["schema"],
                             "k2_w2_innovus_execution_descriptor_v2")
            binding = document["binding"]
            self.assertEqual(binding["timing_profile_id"],
                             "three_endpoint_5p0ns")
            self.assertEqual(binding["period_ns"], "5.0")
            self.assertRegex(binding["genus_timing_manifest_sha256"],
                             r"^[0-9a-f]{64}$")
            self.assertRegex(binding["genus_timing_profile_sha256"],
                             r"^[0-9a-f]{64}$")
            self.assertRegex(binding["innovus_timing_profile_sha256"],
                             r"^[0-9a-f]{64}$")
            observed.append(binding["top"])
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

    def test_shell_isolates_each_innovus_process_under_its_output(self):
        text = SHELL.read_text()
        self.assertIn('mkdir -p "$AER_PNR_OUTPUT_DIR/status" "$AER_PNR_OUTPUT_DIR/work"', text)
        self.assertIn('"$AER_PNR_OUTPUT_DIR/tmp"', text)
        self.assertIn('export TMPDIR="$AER_PNR_OUTPUT_DIR/tmp"', text)
        self.assertIn('(cd "$AER_PNR_OUTPUT_DIR/work" && "$INNOVUS_BIN"', text)
        self.assertNotIn('cd "$PROJECT_ROOT"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
