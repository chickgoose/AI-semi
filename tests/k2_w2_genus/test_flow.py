from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "physical/k2_w2_genus/run_genus.py"
COHORT_FLOW = ROOT / "physical/k2_w2_genus/run_goal_cohort.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_GENUS = FIXTURES / "fake_genus.py"
LIBRARY = FIXTURES / "slow_vdd1v0_basicCells.lib"
HOLD_LIBRARY = FIXTURES / "fast_vdd1v0_basicCells.lib"
CELL_LEF = FIXTURES / "gsclib045_macro.lef"
SHARED_QRC = FIXTURES / "gpdk045.tch"
FUNCTIONAL_HOOK = FIXTURES / "mapped_functional_gate.py"
GOLDEN_ARCHIVE = Path("/tmp/ganghee-pnr-golden-20260813.tar.gz")
RAW_GOLDEN_ARCHIVE = Path("/tmp/ganghee-pnr-raw-golden-20260813.tar.gz")
FUNCTIONAL_LOSS_ARCHIVE = Path("/tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz")


def load_flow():
    spec = importlib.util.spec_from_file_location("k2_w2_genus", FLOW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GenusFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_flow()

    def blocked_command(self, cohort: bool, output: Path) -> list[str]:
        entrypoint = COHORT_FLOW if cohort else FLOW
        command = [
            "python3", "-B", str(entrypoint), "--repo-root", str(ROOT),
            "--genus", str(FAKE_GENUS), "--library", str(LIBRARY),
            "--hold-library", str(HOLD_LIBRARY), "--cell-lef", str(CELL_LEF),
            "--shared-qrc", str(SHARED_QRC),
            "--golden-archive", str(GOLDEN_ARCHIVE),
            "--raw-golden-archive", str(RAW_GOLDEN_ARCHIVE),
            "--functional-loss-archive", str(FUNCTIONAL_LOSS_ARCHIVE),
            "--server-environment-receipt",
            str(ROOT / "physical/k2_w2_server_env/canonical_campaign_env.json"),
            "--mapped-functional-hook", str(FUNCTIONAL_HOOK),
            "--functional-model", str(FIXTURES / "gsclib045_functional.v"),
            "--output-root", str(output),
        ]
        if cohort:
            command.extend(["--attempt-prefix", "blocked-tech-stage"])
        else:
            command.extend(["--design", "fovea_a7", "--attempt", "blocked"])
        return command

    def make_staged_fixture(self, root: Path):
        registry = self.module.load_registry_document()
        registry = copy.deepcopy(registry)
        registry["staged_manifest"] = {
            "required_schema": "k2_w2_tech_staged_compositions_v1",
            "required_status": "READY_FOR_GENUS_AND_INNOVUS",
            "path": "rtl/technology/physical_staging/physical_staging_manifest.json",
            "sha256": None,
            "source_commit": "1" * 40,
            "publication_commit": "2" * 40,
        }
        timing_paths = {
            row["strict_sdc"]["path"] for row in
            registry["design_expectations"].values()
        }
        timing_paths.add(registry["mmmc_template"]["path"])
        for relative in timing_paths:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())
        common_inputs = registry["required_common_inputs"]
        common_outputs = registry["required_common_outputs"]
        common_ports = (
            common_inputs + common_outputs[:1] + [{
                "direction": "output", "name": "link_clk_o", "width": 1,
            }, {
                "direction": "output", "name": "link_data_o",
                "width_by_design": {"fovea_a7": 2, "a2_p6": 5, "a3_p6": 5},
            }] + common_outputs[1:]
        )
        manifest = {
            "schema": "k2_w2_tech_staged_compositions_v1",
            "status": "READY_FOR_GENUS_AND_INNOVUS",
            "repository_commit": "1" * 40,
            "goal_order": registry["goal_order"],
            "common_ports": common_ports,
            "technology_authorities": copy.deepcopy(
                registry["required_technology_authorities"]),
            "constraint_templates": {
                "ref_period_ns": 5.0, "sample_period_ns": 5.0,
                "sample_waveform_ns": [1.25, 3.75],
                "clock_uncertainty_ns": 0.25,
                "input_delay_ns": 0.5, "output_delay_ns": 0.5,
                "output_load_pf": 0.01,
                "generated_link_clock_required": True,
                "both_link_edges_required": True,
                "ref_and_sample_are_phase_related": True,
            },
            "designs": {},
            "source_hashes": {},
            "test_policy": {
                "acceptance_sample": "posedge_ref_active_region_pre_NBA",
                "pending_hold": "through_charged_posedge",
                "protocol_error_must_equal_zero": True,
                "epoch_accepted_equals_retired": True,
                "cell_models_test_only": True,
            },
            "consumer_contract": {
                "consumers": ["genus", "innovus"],
                "manifest_path": registry["staged_manifest"]["path"],
                "required_schema": registry["staged_manifest"]["required_schema"],
                "required_status": registry["staged_manifest"]["required_status"],
                "require_repository_commit": True,
                "require_literal_common_port_signature": True,
                "require_endpoint_path_and_leaf_provenance": True,
                "forbidden_port_aliases": [
                    "load_i", "pending_i", "source_ready_o", "protocol_fault_o",
                    "link_enable", "link_enable_i", "burst_clk_o", "burst_data_o",
                    "p6_clk_o", "p6_data_o",
                ],
            },
        }
        for key in registry["goal_order"]:
            expectation = registry["design_expectations"][key]
            top = expectation["staged_top"]
            source_name = f"rtl/technology/physical_staging/{top}.sv"
            source = root / source_name
            source.parent.mkdir(parents=True, exist_ok=True)
            ports = common_inputs + common_outputs[:1] + expectation["link_outputs"] + \
                common_outputs[1:]
            declarations = []
            for port in ports:
                width = "" if port["width"] == 1 else f" [{port['width'] - 1}:0]"
                declarations.append(
                    f"  {port['direction']} logic{width} {port['name']}")
            source.write_text(
                f"module {top} (\n" + ",\n".join(declarations) +
                "\n);\nendmodule\n")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            stem = "fovea" if key == "fovea_a7" else key.removesuffix("_p6")
            filelist_name = (
                f"rtl/technology/physical_staging/filelists/{stem}_gsclib045.f")
            filelist = root / filelist_name
            filelist.parent.mkdir(parents=True, exist_ok=True)
            filelist.write_text(
                "+incdir+rtl/technology/p6\n"
                "+define+W2_P6_TECH_GSCLIB045\n" + source_name + "\n")
            port_signature = [
                port["name"] if port["width"] == 1 else
                f"{port['name']}[{port['width'] - 1}:0]" for port in ports
            ]
            endpoint = "r1" if key == "fovea_a7" else "p6"
            manifest["designs"][key] = {
                "top": top,
                "filelists": {
                    "generic": (
                        f"rtl/technology/physical_staging/filelists/{stem}_generic.f"),
                    "gsclib045": filelist_name,
                },
                "port_signature": port_signature,
                "endpoint_root": {
                    "attribute": f"w2_endpoint_root={endpoint}",
                    "stable_prefix": f"w2_endpoint_link__{endpoint}",
                },
                "endpoint_leaf_contract": {
                    "path_segment": f"w2_endpoint_link__{endpoint}",
                    "leaf_counts": copy.deepcopy(
                        expectation["endpoint_expected_inventory"]),
                    "preserved_name_prefixes": copy.deepcopy(
                        expectation["endpoint_preserved_name_prefixes"]),
                },
                "whole_top_observed_totals": {
                    "status": "PENDING_DEDICATED_GENUS_RUN", "records": [],
                },
            }
            manifest["source_hashes"][source_name] = source_hash
        manifest_path = root / registry["staged_manifest"]["path"]
        manifest_payload = (json.dumps(manifest, indent=2) + "\n").encode()
        manifest_path.write_bytes(manifest_payload)
        registry["staged_manifest"]["sha256"] = hashlib.sha256(
            manifest_payload).hexdigest()
        return registry, manifest, manifest_path

    def rewrite_manifest(self, registry, manifest, path):
        payload = (json.dumps(manifest, indent=2) + "\n").encode()
        path.write_bytes(payload)
        registry["staged_manifest"]["sha256"] = hashlib.sha256(payload).hexdigest()

    def test_final_registry_binds_exact_published_staged_manifest(self):
        registry = self.module.load_registry_document()
        self.assertEqual(registry["goal_order"], ["fovea_a7", "a2_p6", "a3_p6"])
        self.assertEqual(registry["integration_state"], "ready")
        self.assertEqual(registry["staged_manifest"], {
            "required_schema": "k2_w2_tech_staged_compositions_v1",
            "required_status": "READY_FOR_GENUS_AND_INNOVUS",
            "path": "rtl/technology/physical_staging/physical_staging_manifest.json",
            "sha256":
                "fdeaa0fc7cf2fc50e3fc4bc4faf869b11d9fe8a9a17bc32d7b5ca820d3d6f37e",
            "source_commit": "fa48ed6e15debce6b0cda49a93d8f36a767bc63c",
            "publication_commit": "d64b905e2c1026fee7b4781472f0911f92b1d925",
        })
        published = subprocess.run(
            ["git", "show", f"{registry['staged_manifest']['publication_commit']}:"
             f"{registry['staged_manifest']['path']}"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
        self.assertEqual(hashlib.sha256(published).hexdigest(),
                         registry["staged_manifest"]["sha256"])

    def test_runner_and_launcher_fail_before_creating_results(self):
        for cohort in (False, True):
            with self.subTest(cohort=cohort), tempfile.TemporaryDirectory(
                    prefix="k2-w2-blocked-") as directory:
                output = Path(directory) / "must-not-exist"
                result = subprocess.run(
                    self.blocked_command(cohort, output), cwd=ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, check=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                if output.exists():
                    self.assertEqual(list(output.rglob("receipt.json")), [])
                    self.assertEqual(list(output.rglob("goal-publication.json")), [])

    def test_diagnostic_registries_cannot_be_final_or_ranked(self):
        final = self.module.load_registry_document()
        diagnostics = json.loads((ROOT /
            "physical/k2_w2_genus/diagnostic_designs.json").read_text())
        components = json.loads((ROOT /
            "physical/k2_w2_genus/component_diagnostics.json").read_text())
        generic = json.loads((ROOT / "physical/k2_w2_tops/designs.json").read_text())
        for document in (diagnostics, components, generic):
            self.assertIs(document["ranking_eligible"], False)
            self.assertIs(document["final_server_execution_eligible"], False)
        forbidden = set(final["forbidden_final_tops"])
        self.assertIn("a2_batched_iwrr_k2", forbidden)
        self.assertIn("a3_exact_scalar_prefix_k2", forbidden)
        self.assertIn("k2_w2_fovea_a7_top", forbidden)
        boundaries = json.loads((ROOT / "physical/k2_w2_boundaries.json").read_text())
        staged = next(row for row in boundaries["cohorts"]
                      if row["id"] == "tech_staged_complete_compositions")
        self.assertIsNone(staged["tops"])
        self.assertEqual(staged["integration_state"],
                         "blocked_missing_tech_staged_manifest")

    def test_raw_final_mixing_and_stale_five_row_labels_are_rejected(self):
        registry = copy.deepcopy(self.module.load_registry_document())
        registry["goal_order"] = [
            "raw_fovea", "raw_cluster2", "fovea_a7", "a2_p6", "a3_p6"]
        registry["design_expectations"]["raw_fovea"] = {}
        registry["design_expectations"]["raw_cluster2"] = {}
        with self.assertRaisesRegex(self.module.FlowError,
                                    "three staged compositions"):
            self.module.validate_final_registry_document(registry)
        final_files = [
            ROOT / "physical/k2_w2_genus/run_genus.py",
            ROOT / "physical/k2_w2_genus/run_goal_cohort.py",
            ROOT / "physical/k2_w2_genus/README.md",
        ]
        stale = ("PASS_EXACT_FIVE_ROWS", "exact five-row", "exact five W2 goal")
        for path in final_files:
            text = path.read_text()
            self.assertFalse(any(token in text for token in stale), path)
        diagnostics = json.loads((ROOT /
            "physical/k2_w2_genus/diagnostic_designs.json").read_text())
        self.assertEqual(diagnostics["goal_order"][:2], ["raw_fovea", "raw_cluster2"])
        self.assertFalse(diagnostics["final_server_execution_eligible"])

    def test_exact_synthetic_staged_manifest_resolves_three_fair_rows(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, _, _ = self.make_staged_fixture(root)
            runtime = self.module.resolve_staged_registry(root, registry)
            self.assertEqual(set(runtime["designs"]), set(registry["goal_order"]))
            common = [port["name"] for port in registry["required_common_outputs"]]
            for key, design in runtime["designs"].items():
                self.assertEqual(design["boundary_cohort"],
                                 "tech_staged_complete_compositions")
                self.assertEqual(design["outputs"][:len(common)], common)
                self.assertNotIn(design["top"], registry["forbidden_final_tops"])
            self.assertEqual(runtime["designs"]["fovea_a7"]["outputs"][-2:],
                             ["link_clk_o", "link_data_o"])
            self.assertEqual(runtime["designs"]["a2_p6"]["outputs"][-2:],
                             ["link_clk_o", "link_data_o"])

    def test_manifest_sha_and_source_hash_are_mandatory(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            registry["staged_manifest"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(self.module.FlowError, "manifest SHA"):
                self.module.resolve_staged_registry(root, registry)
            registry, manifest, path = self.make_staged_fixture(root)
            source = next(iter(manifest["source_hashes"]))
            manifest["source_hashes"][source] = "0" * 64
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "source-hash inventory"):
                self.module.resolve_staged_registry(root, registry)

    def test_generic_top_and_generic_wrapper_source_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["designs"]["fovea_a7"]["top"] = "k2_w2_fovea_a7_top"
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "forbidden or wrong"):
                self.module.resolve_staged_registry(root, registry)

        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["designs"]["fovea_a7"]["filelists"]["gsclib045"] = (
                "physical/k2_w2_tops/filelists/fovea.f")
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError,
                                        "forbidden or wrong"):
                self.module.resolve_staged_registry(root, registry)

    def test_actual_extra_non_link_port_and_r1_width_mutations_are_rejected(self):
        for mutation in ("extra", "r1_width"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix="k2-w2-staged-") as directory:
                root = Path(directory)
                registry, manifest, path = self.make_staged_fixture(root)
                row = manifest["designs"]["fovea_a7"]
                source_name = (
                    f"rtl/technology/physical_staging/{row['top']}.sv")
                source = root / source_name
                text = source.read_text()
                if mutation == "extra":
                    text = text.replace(
                        "  output logic link_clk_o,",
                        "  output logic debug_o,\n  output logic link_clk_o,")
                else:
                    text = text.replace(
                        "output logic [1:0] link_data_o",
                        "output logic [2:0] link_data_o")
                source.write_text(text)
                manifest["source_hashes"][source_name] = hashlib.sha256(
                    source.read_bytes()).hexdigest()
                self.rewrite_manifest(registry, manifest, path)
                with self.assertRaisesRegex(self.module.FlowError, "top boundary mismatch"):
                    self.module.resolve_staged_registry(root, registry)

    def test_declared_common_boundary_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["common_ports"][4]["width"] = 15
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "top boundary mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_technology_authority_rebinding_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["technology_authorities"]["raw_golden"]["sha256"] = "0" * 64
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError,
                                        "technology authority mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_staged_source_byte_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, _ = self.make_staged_fixture(root)
            source_name = next(iter(manifest["source_hashes"]))
            (root / source_name).write_text("rebound-staged-source\n")
            with self.assertRaisesRegex(self.module.FlowError,
                                        "source-hash inventory mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_publication_git_commit_path_type_and_blob_are_exact(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-authority-git-") as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "fixture"], cwd=root, check=True)
            relative = "rtl/technology/physical_staging/physical_staging_manifest.json"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"status":"HOLD","repository_commit":null}\n')
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "source"], cwd=root, check=True)
            source_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, stdout=subprocess.PIPE).stdout.strip()
            path.write_text(
                '{"status":"READY_FOR_GENUS_AND_INNOVUS",'
                f'"repository_commit":"{source_commit}"}}\n')
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "publication"], cwd=root,
                           check=True)
            publication_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, stdout=subprocess.PIPE).stdout.strip()
            identity = {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_commit": source_commit,
                "publication_commit": publication_commit,
            }
            registry = {"repository_commit": source_commit,
                        "staged_manifest_identity": identity}
            self.module.verify_source_commit(root, registry)

            wrong = copy.deepcopy(registry)
            wrong["staged_manifest_identity"]["publication_commit"] = "f" * 40
            with self.assertRaisesRegex(self.module.FlowError, "publication commit"):
                self.module.verify_source_commit(root, wrong)
            wrong = copy.deepcopy(registry)
            wrong["staged_manifest_identity"]["path"] = (
                "rtl/technology/physical_staging")
            with self.assertRaisesRegex(self.module.FlowError,
                                        "published staged manifest object is not a blob"):
                self.module.verify_source_commit(root, wrong)
            wrong = copy.deepcopy(registry)
            wrong["staged_manifest_identity"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(self.module.FlowError,
                                        "published staged manifest commit/blob mismatch"):
                self.module.verify_source_commit(root, wrong)

    def test_contract_bit_counts_are_50_for_r1_and_53_for_p6(self):
        registry = self.module.load_registry_document()
        common = sum(row["width"] for row in
                     registry["required_common_inputs"] +
                     registry["required_common_outputs"])
        self.assertEqual(common, 47)
        self.assertEqual(common + registry["design_expectations"]["fovea_a7"]["link_bits"], 50)
        self.assertEqual(common + registry["design_expectations"]["a2_p6"]["link_bits"], 53)
        self.assertEqual(common + registry["design_expectations"]["a3_p6"]["link_bits"], 53)

    def test_strict_r1_p6_sdc_classes_and_hashes(self):
        registry = self.module.load_registry_document()
        for key in registry["goal_order"]:
            timing = registry["design_expectations"][key]["strict_sdc"]
            payload = self.module.materialize_sdc(ROOT, {"strict_sdc": timing})
            self.assertEqual(hashlib.sha256(payload).hexdigest(), timing["sha256"])
        r1 = (ROOT / "constraints/r1_multiclock_strict.sdc").read_text()
        p6 = (ROOT / "constraints/p6_multiclock_strict.sdc").read_text()
        self.assertIn("exactly two DDR data ports", r1)
        self.assertIn("exactly five DDR data ports", p6)
        for text in (r1, p6):
            for token in (
                    "-clock_fall -add_delay", "set_input_delay -min",
                    "set_input_delay -max", "set_output_delay -min",
                    "set_output_delay -max", "set_clock_gating_check",
                    "set_min_pulse_width -high", "set_min_pulse_width -low",
                    "set_driving_cell",
                    "set_input_transition", "set_load", "all_registers -clock"):
                self.assertIn(token, text)
            self.assertIn(
                "get_pins -hierarchical *w2_ep_icg_0/ECK", text)
            self.assertIn(
                "-source $sample_port -divide_by 1 $link_icg_eck", text)
            self.assertIn(
                "set_clock_gating_check -setup $gate_setup "
                "-hold $gate_hold $sample_clock", text)
            self.assertIn("set reset_release_rise $half", text)
            self.assertIn("set reset_release_fall $three_quarter", text)
            self.assertIn(
                "set ref_registers [w2_some ref_registers "
                "[all_registers -clock $ref_clock]]", text)
            self.assertIn(
                "set link_registers [w2_some link_registers "
                "[all_registers -clock $link_clock]]", text)
            self.assertNotIn("get_timing_arcs", text)
            self.assertNotIn("-divide_by 1 $link_clock_port", text)
            self.assertNotIn("$gate_enable", text)
            self.assertEqual(text.count("set_false_path"), 1)
            self.assertIn(
                "set_false_path -from $reset_port -to $nonlink_outputs", text)
            self.assertNotIn("set_multicycle_path", text)

    def test_each_strict_sdc_timing_class_omission_is_rejected(self):
        original = (ROOT / "constraints/r1_multiclock_strict.sdc").read_text()
        tokens = (
            "create_generated_clock", "-clock_fall -add_delay",
            "set_input_delay -min", "set_input_delay -max",
            "set_output_delay -min", "set_output_delay -max",
            "set_clock_gating_check", "set_min_pulse_width -high",
            "set_min_pulse_width -low", "set_driving_cell",
            "set_input_transition", "set_load",
            "all_registers -clock", "*w2_ep_icg_0/ECK",
            "-divide_by 1 $link_icg_eck", "-hold $gate_hold $sample_clock",
            "set ref_registers [w2_some ref_registers",
            "set link_registers [w2_some link_registers",
            "set async_reset_pins [w2_some async_reset_endpoints",
            "set_false_path -from $reset_port -to $nonlink_outputs",
            "set reset_release_rise $half", "set reset_release_fall $three_quarter",
        )
        with tempfile.TemporaryDirectory(prefix="k2-w2-sdc-mutations-") as directory:
            root = Path(directory)
            path = root / "candidate.sdc"
            for token in tokens:
                with self.subTest(removed=token):
                    self.assertIn(token, original)
                    payload = original.replace(token, "").encode()
                    path.write_bytes(payload)
                    design = {"strict_sdc": {
                        "path": "candidate.sdc",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }}
                    with self.assertRaisesRegex(
                            self.module.FlowError,
                            "strict SDC omits timing constraint class"):
                        self.module.materialize_sdc(root, design)
            for forbidden in (
                    "get_timing_arcs", "-divide_by 1 $link_clock_port",
                    "-hold $gate_hold $gate_enable",
                    "set_false_path -from $reset_port -to $async_reset_pins"):
                with self.subTest(inserted=forbidden):
                    payload = (original + "\n# mutation\n" + forbidden + "\n").encode()
                    path.write_bytes(payload)
                    design = {"strict_sdc": {
                        "path": "candidate.sdc",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }}
                    with self.assertRaisesRegex(
                            self.module.FlowError,
                            "forbidden timing exception|reset exception"):
                        self.module.materialize_sdc(root, design)

    def test_shared_qrc_mmmc_discloses_single_typical_rc(self):
        registry = self.module.load_registry_document()
        mmmc = registry["mmmc_template"]
        payload = (ROOT / mmmc["path"]).read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), mmmc["sha256"])
        text = payload.decode()
        self.assertEqual(text.count("-qrc_tech $shared_qrc"), 2)
        self.assertNotIn("setup and hold RC conditions are identical", text)
        self.assertIn("shared_typical_qrc=1", text)

    def test_dffnsrx1_liberty_lef_preflight_and_mutations(self):
        positive = self.module.dffnsrx1_preflight(LIBRARY, CELL_LEF, "setup")
        self.assertEqual(positive["clocked_on"], "(!CKN)")
        self.assertTrue(positive["recovery_removal_nonzero"])
        with tempfile.TemporaryDirectory(prefix="k2-w2-dff-") as directory:
            root = Path(directory)
            multiline = root / "multiline.lib"
            multiline.write_text(LIBRARY.read_text().replace(
                'values ("0.12")',
                'values ( \\\n+                  "0.12, 0.13", \\\n+                  "0.14, 0.15" \\\n+                )'))
            self.assertTrue(self.module.dffnsrx1_preflight(
                multiline, CELL_LEF, "multiline")["recovery_removal_nonzero"])
            for label, old, new, message in (
                    ("edge", 'clocked_on : "(!CKN)"', 'clocked_on : "CKN"', "contract missing"),
                    ("recovery", 'values ("0.12")', 'values ("0.0")', "zero/NaN recovery")):
                lib = root / f"{label}.lib"
                lib.write_text(LIBRARY.read_text().replace(old, new))
                with self.assertRaisesRegex(self.module.FlowError, message):
                    self.module.dffnsrx1_preflight(lib, CELL_LEF, label)
            lef = root / "bad.lef"
            lef.write_text(CELL_LEF.read_text().replace("  PIN SN\n", "  PIN BAD\n"))
            with self.assertRaisesRegex(self.module.FlowError, "pin set mismatch"):
                self.module.dffnsrx1_preflight(LIBRARY, lef, "lef")

    def test_exact_dffnsrx1_mapped_connections_and_count(self):
        contract = self.module.load_registry_document()["design_expectations"][
            "fovea_a7"]["mapped_rx_contract"]
        def netlist(clock="clock_i", reset="rst_ni", preset="1'b1", count=5):
            return "\n".join(
                f"DFFNSRX1 u{index} (.CKN({clock}), .RN({reset}), .SN({preset}), "
                f".D(d{index}), .Q(q{index}), .QN(qn{index}));"
                for index in range(count))
        self.module.verify_mapped_rx_contract(netlist(), contract)
        for text, message in (
                (netlist(clock="wrong_clk"), "CKN binding"),
                (netlist(reset="wrong_reset"), "RN binding"),
                (netlist(preset="1'b0"), "SN binding"),
                (netlist(count=4), "count mismatch")):
            with self.assertRaisesRegex(self.module.FlowError, message):
                self.module.verify_mapped_rx_contract(text, contract)

    def test_mapped_endpoint_hierarchy_allows_owner_extras_and_rejects_mutations(self):
        expectation = self.module.load_registry_document()["design_expectations"][
            "fovea_a7"]

        def netlist(rx_clock="clock_i", pos_clock="clock_i",
                    state_cell="DFFX1", extra_endpoint_mux=False):
            tx = (
                "module w2_r1_ddr_tx_tech(input clock_i,enable_i,rst_n,data0_i,data1_i,select_i,output clock_o,data_o);\n"
                "  TLATNTSCAX2 w2_ep_icg_0 (.E(enable_i & rst_n), .SE(1'b0), .CK(clock_i), .ECK(clock_o));\n" +
                "".join(f"  MX2X1 w2_ep_mux_{index} (.A(data0_i), .B(data1_i), .S0(select_i), .Y(data_o));\n"
                        for index in range(2 + int(extra_endpoint_mux))) +
                "endmodule\n")
            rx = (
                "module w2_r1_ddr_rx_tech(input clock_i,burst_clk_i,rst_n,rst_ni,D);\n" +
                "".join(
                    f"  DFFRHQX1 w2_ep_pos_{index} (.CK({pos_clock}), .RN(rst_ni), "
                    f".D(D), .Q(posq{index}));\n" for index in range(2)) +
                "".join(
                    f"  DFFNSRX1 w2_ep_neg_{index} (.CKN({rx_clock}), .RN(rst_ni), .SN(1'b1), "
                    f".D(D), .Q(q{index}), .QN(qn{index}));\n" for index in range(5)) +
                "endmodule\n")
            top = (
                "module staged_top(input CK,D,rst_n,link_clk_o,output Q);\n"
                f"  {state_cell} state (.CK(CK), .D(D), .Q(Q));\n"
                "  DFFRHQX1 owner_extra (.RN(rst_n), .CK(CK), .D(D), .Q());\n"
                "  MX2X1 owner_mux (.A(D), .B(D), .S0(D), .Y());\n"
                "  TLATNTSCAX2 owner_icg (.E(D), .SE(1'b0), .CK(CK), .ECK());\n"
                "  DFFNSRX1 owner_neg (.CKN(CK), .RN(rst_n), .SN(1'b1), "
                ".D(D), .Q(), .QN());\n"
                "  w2_r1_ddr_tx_tech tx (.clock_i(CK), .enable_i(D), .rst_n(rst_n), .data0_i(D), .data1_i(D), .select_i(D), .clock_o(), .data_o());\n"
                "  w2_r1_ddr_rx_tech rx (.clock_i(link_clk_o), "
                ".burst_clk_i(link_clk_o), .rst_n(rst_n), .rst_ni(rst_n), .D(D));\n"
                "endmodule\n")
            return top + tx + rx

        with tempfile.TemporaryDirectory(prefix="k2-w2-mapped-") as directory:
            mapped = Path(directory) / "mapped.v"
            mapped.write_text(netlist())
            inventory = self.module.mapped_inventory(
                mapped, LIBRARY, "staged_top", expectation["mapped_rx_contract"],
                expectation["mapped_posedge_contract"],
                expectation["endpoint_expected_inventory"],
                expectation["endpoint_link_roots"],
                expectation["endpoint_preserved_name_prefixes"])
            self.assertEqual(inventory["endpoint_cell_types"], {
                "DFFNSRX1": 5, "DFFRHQX1": 2, "MX2X1": 2,
                "TLATNTSCAX2": 1})
            self.assertEqual(inventory["mapped_cell_types"]["DFFRHQX1"], 3)
            self.assertEqual(inventory["mapped_cell_types"]["MX2X1"], 3)
            self.assertEqual(inventory["mapped_cell_types"]["TLATNTSCAX2"], 2)
            self.assertEqual(inventory["mapped_cell_types"]["DFFNSRX1"], 6)
            self.assertFalse(inventory["dffnsrx1_global_exclusivity_proven"])
            records = inventory["endpoint_instances"]
            self.assertEqual(len(records), 10)
            self.assertTrue(all(row["mapped_instance"].startswith("w2_ep_")
                                for row in records))
            self.assertTrue(all(row["hierarchy"] and row["pin_bindings"]
                                for row in records))

            for payload, message in (
                    (netlist(rx_clock="wrong_clock"), "CKN binding"),
                    (netlist(pos_clock="wrong_clock"), "DFFRHQX1 CK binding"),
                    (netlist(extra_endpoint_mux=True), "endpoint mapped inventory"),
                    (netlist().replace(".CK(clock_i), .ECK(clock_o)",
                                       ".CK(wrong_clock), .ECK(clock_o)"),
                     "TLATNTSCAX2 exact pin binding"),
                    (netlist().replace(".S0(select_i)", ".S0(wrong_select)"),
                     "MX2X1 exact pin binding"),
                    (netlist().replace("w2_ep_neg_0", "capture_cell_0"),
                     "lost preserved leaf name"),
                    (netlist(state_cell="SDFFX1"), "scan cells are forbidden")):
                mapped.write_text(payload)
                with self.assertRaisesRegex(self.module.FlowError, message):
                    self.module.mapped_inventory(
                        mapped, LIBRARY, "staged_top",
                        expectation["mapped_rx_contract"],
                        expectation["mapped_posedge_contract"],
                        expectation["endpoint_expected_inventory"],
                        expectation["endpoint_link_roots"],
                        expectation["endpoint_preserved_name_prefixes"])

    def test_flattened_genus_endpoint_names_remain_exact_and_fail_closed(self):
        expectation = self.module.load_registry_document()["design_expectations"][
            "fovea_a7"]

        def flattened() -> str:
            return (
                "module staged_top(input ref_clk_i,sample_clk_i,rst_n,frame_active,"
                "input [3:0] word,output link_clk_o,output [1:0] link_data_o);\n"
                "  wire icg_e; wire [1:0] low; wire [4:0] close;\n"
                "  AND2X1 endpoint_enable (.A(frame_active),.B(rst_n),.Y(icg_e));\n"
                "  TLATNTSCAX2 w2_endpoint_link__r1_tx_w2_ep_icg_0 "
                "(.E(icg_e),.SE(1'b0),.CK(sample_clk_i),.ECK(link_clk_o));\n"
                "  MX2X1 w2_endpoint_link__r1_tx_w2_ep_mux_0 "
                "(.A(word[2]),.B(word[0]),.S0(ref_clk_i),.Y(link_data_o[0]));\n"
                "  MX2X1 w2_endpoint_link__r1_tx_w2_ep_mux_1 "
                "(.A(word[3]),.B(word[1]),.S0(ref_clk_i),.Y(link_data_o[1]));\n" +
                "".join(
                    f"  DFFRHQX1 w2_endpoint_link__r1_rx_w2_ep_pos_{index} "
                    f"(.RN(rst_n),.CK(link_clk_o),.D(link_data_o[{index}]),.Q(low[{index}]));\n"
                    for index in range(2)) +
                "".join(
                    f"  DFFNSRX1 w2_endpoint_link__r1_rx_w2_ep_neg_{index} "
                    f"(.RN(rst_n),.SN(1'b1),.CKN(link_clk_o),.D(word[0]),"
                    f".Q(close[{index}]),.QN());\n" for index in range(5)) +
                "  DFFRHQX1 owner_extra (.RN(rst_n),.CK(ref_clk_i),.D(word[0]),.Q());\n"
                "endmodule\n")

        with tempfile.TemporaryDirectory(prefix="k2-w2-flat-mapped-") as directory:
            mapped = Path(directory) / "mapped.v"
            mapped.write_text(flattened())
            inventory = self.module.mapped_inventory(
                mapped, LIBRARY, "staged_top", expectation["mapped_rx_contract"],
                expectation["mapped_posedge_contract"],
                expectation["endpoint_expected_inventory"],
                expectation["endpoint_link_roots"],
                expectation["endpoint_preserved_name_prefixes"])
            self.assertEqual(inventory["endpoint_cell_types"],
                             expectation["endpoint_expected_inventory"])
            self.assertEqual(len(inventory["endpoint_instances"]), 10)
            self.assertTrue(all(
                row["provenance_root"] == "w2_endpoint_link__r1"
                for row in inventory["endpoint_instances"]))
            for old, new, message in (
                    ("w2_endpoint_link__r1_tx_w2_ep_mux_0",
                     "w2_endpoint_link__r1_tx_mux_0", "flattened endpoint mapped inventory"),
                    (".CKN(link_clk_o)", ".CKN(ref_clk_i)", "DFFNSRX1 pin binding"),
                    (".B(rst_n),.Y(icg_e)", ".B(word[0]),.Y(icg_e)",
                     "ICG enable fanin"),
                    (".S0(ref_clk_i)", ".S0(sample_clk_i)", "MX2X1 pin binding")):
                mapped.write_text(flattened().replace(old, new, 1))
                with self.assertRaisesRegex(self.module.FlowError, message):
                    self.module.mapped_inventory(
                        mapped, LIBRARY, "staged_top",
                        expectation["mapped_rx_contract"],
                        expectation["mapped_posedge_contract"],
                        expectation["endpoint_expected_inventory"],
                        expectation["endpoint_link_roots"],
                        expectation["endpoint_preserved_name_prefixes"])

    def test_tool_identity_ignores_volatile_banner_and_tmpdir(self):
        probes = [
            subprocess.CompletedProcess(
                [], 0,
                stdout=("Configured Lic search path (23.02-s006)\n"
                        "Cadence Genus(TM) Synthesis Solution Version: "
                        "23.14-s090_1\nTMPDIR=/dev/shm/first\n")),
            subprocess.CompletedProcess(
                [], 0,
                stdout=("license checkout succeeded\nTMPDIR=/tmp/second\n"
                        "Version: 23.14-s090_1, different build banner\n")),
        ]
        with mock.patch.object(self.module.subprocess, "run", side_effect=probes):
            first = self.module.tool_identity(FAKE_GENUS)
            second = self.module.tool_identity(FAKE_GENUS)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {
            "requested_path", "resolved_path", "sha256", "parsed_version",
        })
        self.assertEqual(first["parsed_version"], "23.14-s090_1")

    def test_mapped_functional_gate_binds_netlist_sdf_models_and_mutations(self):
        design = copy.deepcopy(self.module.load_registry_document()[
            "design_expectations"]["fovea_a7"])
        design["top"] = design["staged_top"]
        design["defines"] = ["SYNTHESIS", "W2_TECH_STAGED"]
        identity = self.module.tool_identity(FAKE_GENUS)
        xrun = {"resolved_path": identity["resolved_path"],
                "sha256": identity["sha256"],
                "parsed_version": identity["parsed_version"]}

        def invoke(mutation=""):
            directory = tempfile.TemporaryDirectory(prefix="k2-w2-functional-")
            self.addCleanup(directory.cleanup)
            attempt = Path(directory.name)
            (attempt / "bundle/sources/rtl").mkdir(parents=True)
            (attempt / "work").mkdir()
            (attempt / "logs").mkdir()
            source = attempt / "bundle/sources/rtl/staged.sv"
            source.write_text("module staged; endmodule\n")
            netlist = attempt / "work/mapped.v"
            netlist.write_text("module mapped; endmodule\n")
            sdf = attempt / "work/mapped.sdf"
            sdf.write_text('(DELAYFILE (DESIGN "mapped"))\n')
            model = attempt / "vendor_model.v"
            model.write_text("module vendor; endmodule\n")
            environment = ({"W2_FUNCTIONAL_FIXTURE_MUTATION": mutation}
                           if mutation else {})
            with mock.patch.dict(os.environ, environment, clear=False):
                result = self.module.run_mapped_functional_gate(
                    FUNCTIONAL_HOOK, attempt, "fovea_a7", design,
                    netlist, sdf, [model],
                    [{"path": "rtl/staged.sv", "sha256":
                      hashlib.sha256(source.read_bytes()).hexdigest()}], xrun)
            return attempt, result

        attempt, (document, result_hash) = invoke()
        published = attempt / "mapped-functional-gate.json"
        self.assertEqual(hashlib.sha256(published.read_bytes()).hexdigest(),
                         result_hash)
        self.assertEqual(document["sdf_status"], "ANNOTATED")
        self.assertEqual(document["scenarios"],
                         ["held_pending", "conservation", "reset", "drain"])
        for mutation in ("unbound_netlist", "bad_sdf", "bad_scenarios",
                         "fabricated_log"):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    self.module.FlowError, "functional gate mismatch"):
                invoke(mutation)

        with tempfile.TemporaryDirectory(prefix="k2-w2-no-model-") as directory:
            attempt = Path(directory)
            with self.assertRaisesRegex(self.module.FlowError,
                                        "requires vendor functional models"):
                self.module.run_mapped_functional_gate(
                    FUNCTIONAL_HOOK, attempt, "fovea_a7", design,
                    attempt / "missing.v", attempt / "missing.sdf", [], [], xrun)

    def test_timing_report_and_coverage_negative_classes(self):
        good = "Path 1: MET (100 ps) Setup Check\n             Slack:=     100\n"
        self.module.parse_timing_rows(good, "good", "Setup")
        external = (
            "Path 1: MET (3 ps) Late External Delay Assertion at pin link_data_o[0]\n"
            "             Slack:=       3\n")
        self.assertEqual(
            self.module.parse_timing_rows(external, "actual Genus external delay"),
            {"path_count": 1, "minimum_slack_ps": 3.0})
        mutations = (
            (good.replace("MET", "VIOLATED"), "VIOLATED"),
            (good.replace("100 ps", "-1 ps").replace("     100", "     -1"), "negative"),
            (good.replace("100 ps", "NaN ps").replace("     100", "     NaN"), "NaN"),
            (good.split("Slack:=")[0], "truncated"),
        )
        for payload, message in mutations:
            with self.assertRaisesRegex(self.module.FlowError, message):
                self.module.parse_timing_rows(payload, "bad", "Setup")
        qor = "WNS (ps): 100\nTNS (ps): 0\nUnconstrained Paths: 0\n"
        self.module.parse_qor(qor, 100.0)
        actual_qor = """\
           Cost              Critical         Violating
           Group            Path Slack  TNS     Paths
--------------------------------------------------------
cg_enable_group_r1_ref_clk       492.6   0.0          0
r1_link_clk                       18.9   0.0          0
r1_ref_clk                        85.9   0.0          0
--------------------------------------------------------
Total                                    0.0          0
"""
        parsed = self.module.parse_qor(actual_qor, 19.0)
        self.assertEqual(parsed["wns_ps"], 18.9)
        self.assertEqual(parsed["violating_paths"], 0)
        for bad_qor in (
                qor.replace("WNS (ps): 100", "WNS (ps): -1"),
                qor.replace("TNS (ps): 0", "TNS (ps): -1"),
                qor.replace("Unconstrained Paths: 0", "Unconstrained Paths: 1")):
            with self.assertRaisesRegex(self.module.FlowError, "QoR"):
                self.module.parse_qor(bad_qor, 100.0)

    def test_authoritative_buffered_and_raw_archives(self):
        self.assertTrue(GOLDEN_ARCHIVE.is_file())
        self.assertTrue(RAW_GOLDEN_ARCHIVE.is_file())
        golden = self.module.load_golden_reference()
        raw = self.module.load_raw_golden_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            root = Path(directory)
            buffered_identity = self.module.verify_golden_archive(
                GOLDEN_ARCHIVE, root / golden["archive_filename"], golden)
            raw_identity = self.module.verify_raw_golden_archive(
                RAW_GOLDEN_ARCHIVE, root / raw["archive_filename"], raw)
            self.module.verify_reference_cohort_separation(
                raw_identity, buffered_identity)
            self.assertEqual(buffered_identity["anchor_count"], 25)
            self.assertEqual(raw_identity["anchor_count"], 22)

    def test_functional_loss_archive_remains_loss_only_not_ppa(self):
        self.assertTrue(FUNCTIONAL_LOSS_ARCHIVE.is_file())
        reference = self.module.load_functional_loss_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-functional-") as directory:
            identity = self.module.verify_functional_loss_archive(
                FUNCTIONAL_LOSS_ARCHIVE,
                Path(directory) / reference["archive_filename"], reference)
            self.assertEqual(identity["ppa_use"], "FORBIDDEN")
            self.assertEqual(identity["full50_loss_totals"]["fovea"]["accepted"], 78229)
            self.assertEqual(identity["full50_loss_totals"]["cluster2"]["accepted"], 94157)

    def test_archive_byte_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-mutated-") as directory:
            root = Path(directory)
            fake = root / "ganghee-pnr-raw-golden-20260813.tar.gz"
            shutil.copyfile(RAW_GOLDEN_ARCHIVE, fake)
            payload = bytearray(fake.read_bytes())
            payload[-1] ^= 1
            fake.write_bytes(payload)
            raw = self.module.load_raw_golden_reference()
            with self.assertRaisesRegex(self.module.FlowError, "raw golden archive SHA"):
                self.module.verify_raw_golden_archive(
                    fake, root / "snapshot.tar.gz", raw)

    def test_driver_retains_golden_order_and_empty_define_path(self):
        golden = self.module.load_golden_reference()
        self.module.verify_driver_contract(golden)
        driver = (ROOT / "physical/k2_w2_genus/genus_driver.tcl").read_text()
        self.assertIn('if {$defines eq ""}', driver)
        self.assertIn("read_hdl -v {*}$::env(W2_SOURCES_V)", driver)


if __name__ == "__main__":
    unittest.main()
