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


ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "physical/k2_w2_genus/run_genus.py"
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

    def invoke(self, output: Path, design: str = "a2_k2", attempt: str = "attempt-1",
               mode: str = "pass",
               smoke: Path | None = SMOKE,
               golden_archive: Path | None = GOLDEN_ARCHIVE,
               raw_golden_archive: Path | None = RAW_GOLDEN_ARCHIVE,
               functional_loss_archive: Path | None = FUNCTIONAL_LOSS_ARCHIVE,
               ) -> subprocess.CompletedProcess[str]:
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
        registry["integration_state"] = "ready"
        registry["staged_manifest"] = {
            "required_schema": "w2-physical-staging-v2",
            "required_status": "GO_FOR_SERVER_STAGING",
            "path": "physical/staged/manifest.json",
            "sha256": None,
            "repository_commit": "1" * 40,
        }
        registry["required_technology_authorities"]["r1"] = {
            "repository_commit": "2" * 40,
            "manifest_path": "rtl/technology/r1/r1_tech_manifest.json",
            "manifest_sha256": "3" * 64,
        }
        for index, key in enumerate(("r1", "p6"), start=1):
            authority = registry["required_technology_authorities"][key]
            authority["repository_commit"] = str(index + 1) * 40
            authority["manifest_path"] = f"rtl/technology/{key}/{key}_tech_manifest.json"
            authority_path = root / authority["manifest_path"]
            authority_path.parent.mkdir(parents=True, exist_ok=True)
            authority_path.write_text(f"{key}-tech-fixture\n")
            authority["manifest_sha256"] = hashlib.sha256(
                authority_path.read_bytes()).hexdigest()
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
        manifest = {
            "schema": "w2-physical-staging-v2",
            "status": "GO_FOR_SERVER_STAGING",
            "repository_commit": "1" * 40,
            "goal_order": registry["goal_order"],
            "technology_authorities": copy.deepcopy(
                registry["required_technology_authorities"]),
            "constraint_templates": {
                "r1": copy.deepcopy(registry["design_expectations"][
                    "fovea_a7"]["strict_sdc"]),
                "p6": copy.deepcopy(registry["design_expectations"][
                    "a2_p6"]["strict_sdc"]),
            },
            "tops": {},
        }
        tops = {
            key: registry["design_expectations"][key]["staged_top"]
            for key in registry["goal_order"]
        }
        for key in registry["goal_order"]:
            expectation = registry["design_expectations"][key]
            top = tops[key]
            source_name = f"physical/staged/{top}.sv"
            source = root / source_name
            source.parent.mkdir(parents=True, exist_ok=True)
            ports = common_inputs + common_outputs + expectation["link_outputs"]
            declarations = []
            for port in ports:
                width = "" if port["width"] == 1 else f" [{port['width'] - 1}:0]"
                declarations.append(
                    f"  {port['direction']} logic{width} {port['name']}")
            source.write_text(
                f"module {top} (\n" + ",\n".join(declarations) +
                "\n);\nendmodule\n")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            filelist_name = f"physical/staged/{key}.f"
            filelist = root / filelist_name
            filelist.write_text(source_name + "\n")
            manifest["tops"][key] = {
                "staged_top": top,
                "top_source": source_name,
                "filelist": filelist_name,
                "filelist_sha256": hashlib.sha256(filelist.read_bytes()).hexdigest(),
                "technology_stage": expectation["technology_stage"],
                "link_kind": expectation["link_kind"],
                "mapped_rx_contract": copy.deepcopy(expectation["mapped_rx_contract"]),
                "mapped_posedge_contract": copy.deepcopy(
                    expectation["mapped_posedge_contract"]),
                "endpoint_expected_inventory": copy.deepcopy(
                    expectation["endpoint_expected_inventory"]),
                "endpoint_link_roots": copy.deepcopy(
                    expectation["endpoint_link_roots"]),
                "endpoint_preserved_name_prefixes": copy.deepcopy(
                    expectation["endpoint_preserved_name_prefixes"]),
                "no_other_negedge_state_proven": expectation[
                    "no_other_negedge_state_proven"],
                "required_ports": copy.deepcopy(common_inputs + common_outputs),
                "link_pins": copy.deepcopy(expectation["link_outputs"]),
                "defines": ["SYNTHESIS", "W2_TECH_STAGED"],
                "parameters": {},
                "sources": [{"path": source_name, "sha256": source_hash}],
            }
        manifest_path = root / registry["staged_manifest"]["path"]
        manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        manifest_path.write_bytes(manifest_payload)
        registry["staged_manifest"]["sha256"] = hashlib.sha256(
            manifest_payload).hexdigest()
        return registry, manifest, manifest_path

    def rewrite_manifest(self, registry, manifest, path):
        payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(payload)
        registry["staged_manifest"]["sha256"] = hashlib.sha256(payload).hexdigest()

    def test_final_registry_is_deliberately_blocked_without_staged_hashes(self):
        registry = self.module.load_registry_document()
        self.assertEqual(registry["goal_order"], ["fovea_a7", "a2_p6", "a3_p6"])
        self.assertEqual(registry["integration_state"],
                         "blocked_missing_tech_staged_manifest")
        self.assertEqual(registry["staged_manifest"], {
            "required_schema": "w2-physical-staging-v2",
            "required_status": "GO_FOR_SERVER_STAGING",
            "path": None, "sha256": None, "repository_commit": None})
        with self.assertRaisesRegex(self.module.FlowError,
                                    "tech-staged composition manifest is missing"):
            self.module.load_registry(ROOT)
        p6_commit = registry["required_technology_authorities"]["p6"][
            "repository_commit"]
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{p6_commit}^{{commit}}"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(present.returncode, 0,
                            "registry must not become GO before P6 authority object is integrated")

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
                self.assertIn("generic/native substitution forbidden", result.stdout)
                self.assertFalse(output.exists())

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
            manifest["tops"]["a2_p6"]["sources"][0]["sha256"] = "0" * 64
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "source SHA/path"):
                self.module.resolve_staged_registry(root, registry)

    def test_generic_top_and_generic_wrapper_source_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["tops"]["fovea_a7"]["staged_top"] = "k2_w2_fovea_a7_top"
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "forbidden or wrong"):
                self.module.resolve_staged_registry(root, registry)

        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, _ = self.make_staged_fixture(root)
            manifest["tops"]["fovea_a7"]["top_source"] = (
                "physical/k2_w2_tops/rtl/k2_w2_fovea_a7_top.sv")
            with self.assertRaisesRegex(self.module.FlowError,
                                        "generic wrapper substituted"):
                self.module.validate_staged_manifest(root, registry, manifest)

    def test_actual_extra_non_link_port_and_r1_width_mutations_are_rejected(self):
        for mutation in ("extra", "r1_width"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(
                    prefix="k2-w2-staged-") as directory:
                root = Path(directory)
                registry, manifest, path = self.make_staged_fixture(root)
                row = manifest["tops"]["fovea_a7"]
                source = root / row["top_source"]
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
                row["sources"][0]["sha256"] = hashlib.sha256(
                    source.read_bytes()).hexdigest()
                self.rewrite_manifest(registry, manifest, path)
                with self.assertRaisesRegex(self.module.FlowError, "top boundary mismatch"):
                    self.module.resolve_staged_registry(root, registry)

    def test_declared_common_boundary_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["tops"]["fovea_a7"]["required_ports"][4]["width"] = 15
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError, "top boundary mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_technology_authority_rebinding_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, manifest, path = self.make_staged_fixture(root)
            manifest["technology_authorities"]["p6"]["manifest_sha256"] = "0" * 64
            self.rewrite_manifest(registry, manifest, path)
            with self.assertRaisesRegex(self.module.FlowError,
                                        "technology authority mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_technology_authority_blob_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-staged-") as directory:
            root = Path(directory)
            registry, _, _ = self.make_staged_fixture(root)
            authority = registry["required_technology_authorities"]["r1"]
            (root / authority["manifest_path"]).write_text("rebound-tech-manifest\n")
            with self.assertRaisesRegex(self.module.FlowError,
                                        "r1 technology manifest SHA mismatch"):
                self.module.resolve_staged_registry(root, registry)

    def test_authority_git_commit_path_type_and_blob_are_exact(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-authority-git-") as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "fixture"], cwd=root, check=True)
            identities = {}
            for key in ("r1", "p6"):
                relative = f"rtl/technology/{key}/{key}_tech_manifest.json"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{key}-authority\n")
                identities[key] = {
                    "manifest_path": relative,
                    "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "authority"], cwd=root, check=True)
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True,
                text=True, stdout=subprocess.PIPE).stdout.strip()
            for row in identities.values():
                row["repository_commit"] = commit
            registry = {"repository_commit": commit,
                        "technology_authority_identities": identities}
            self.module.verify_source_commit(root, registry)

            wrong = copy.deepcopy(registry)
            wrong["technology_authority_identities"]["r1"]["repository_commit"] = "f" * 40
            with self.assertRaisesRegex(self.module.FlowError, "git cat-file"):
                self.module.verify_source_commit(root, wrong)
            wrong = copy.deepcopy(registry)
            wrong["technology_authority_identities"]["r1"]["manifest_path"] = (
                "rtl/technology/r1")
            with self.assertRaisesRegex(self.module.FlowError, "not a blob"):
                self.module.verify_source_commit(root, wrong)
            wrong = copy.deepcopy(registry)
            wrong["technology_authority_identities"]["r1"]["manifest_sha256"] = "0" * 64
            with self.assertRaisesRegex(self.module.FlowError, "commit/blob mismatch"):
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
                    "recovery_falling", "removal_falling", "set_driving_cell",
                    "set_input_transition", "set_load", "all_registers -clock"):
                self.assertIn(token, text)
            self.assertNotIn("set_false_path", text)
            self.assertNotIn("set_multicycle_path", text)

    def test_each_strict_sdc_timing_class_omission_is_rejected(self):
        original = (ROOT / "constraints/r1_multiclock_strict.sdc").read_text()
        tokens = (
            "create_generated_clock", "-clock_fall -add_delay",
            "set_input_delay -min", "set_input_delay -max",
            "set_output_delay -min", "set_output_delay -max",
            "set_clock_gating_check", "set_min_pulse_width -high",
            "set_min_pulse_width -low", "recovery_falling", "removal_falling",
            "set_driving_cell", "set_input_transition", "set_load",
            "all_registers -clock",
        )

    def test_registry_exact_five_and_all_sources_match_commit(self):
        self.assertTrue(GOLDEN_ARCHIVE.is_file(), "authoritative archive is required")
        self.assertTrue(RAW_GOLDEN_ARCHIVE.is_file(), "authoritative raw archive is required")
        self.assertTrue(
            FUNCTIONAL_LOSS_ARCHIVE.is_file(), "functional loss archive is required")
        registry = self.module.load_registry()
        self.assertEqual(set(registry["designs"]), {
            "a2_k2", "a3_k2", "p6_endpoint", "a2_p6", "a3_p6"})
        self.module.verify_source_commit(ROOT, registry)
        for design in registry["designs"]:
            self.module.verify_design(ROOT, registry, design)

    def test_dffnsrx1_liberty_lef_preflight_and_mutations(self):
        positive = self.module.dffnsrx1_preflight(LIBRARY, CELL_LEF, "setup")
        self.assertEqual(positive["clocked_on"], "(!CKN)")
        self.assertTrue(positive["recovery_removal_nonzero"])
        with tempfile.TemporaryDirectory(prefix="k2-w2-dff-") as directory:
            root = Path(directory)
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

    def test_mapped_functional_gate_binds_netlist_sdf_models_and_mutations(self):
        design = copy.deepcopy(self.module.load_registry_document()[
            "design_expectations"]["fovea_a7"])
        design["top"] = design["staged_top"]
        design["defines"] = ["SYNTHESIS", "W2_TECH_STAGED"]
        identity = self.module.tool_identity(FAKE_GENUS)
        xrun = {"resolved_path": identity["resolved_path"],
                "sha256": identity["sha256"],
                "parsed_version": identity["version_output"]}

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
        coverage = "".join(f"{name}: 0\n" for name in (
            "no_clock", "constant_clock", "no_input_delay", "no_output_delay",
            "no_drive", "no_load", "unconstrained"))
        self.module.parse_qor_and_coverage(qor, coverage, 100.0)
        for name in ("no_drive", "no_load"):
            with self.assertRaisesRegex(self.module.FlowError, "coverage hole"):
                self.module.parse_qor_and_coverage(
                    qor, coverage.replace(f"{name}: 0", f"{name}: 1"), 100.0)
        for bad_qor in (
                qor.replace("WNS (ps): 100", "WNS (ps): -1"),
                qor.replace("TNS (ps): 0", "TNS (ps): -1"),
                qor.replace("Unconstrained Paths: 0", "Unconstrained Paths: 1")):
            with self.assertRaisesRegex(self.module.FlowError, "QoR"):
                self.module.parse_qor_and_coverage(bad_qor, coverage, 100.0)

    def test_authoritative_buffered_and_raw_archives(self):
        self.assertTrue(GOLDEN_ARCHIVE.is_file())
        self.assertTrue(RAW_GOLDEN_ARCHIVE.is_file())
        golden = self.module.load_golden_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            snapshot = Path(directory) / golden["archive_filename"]
            identity = self.module.verify_golden_archive(
                GOLDEN_ARCHIVE, snapshot, golden)
            self.assertEqual(identity["archive_sha256"], golden["archive_sha256"])
            self.assertEqual(identity["anchor_count"], 25)
            self.assertEqual(identity["genus_version"], "23.14-s090_1")

    def test_authoritative_raw_archive_netlists_reports_and_cohort_separation(self):
        raw = self.module.load_raw_golden_reference()
        buffered = self.module.load_golden_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-golden-") as directory:
            root = Path(directory)
            raw_identity = self.module.verify_raw_golden_archive(
                RAW_GOLDEN_ARCHIVE, root / raw["archive_filename"], raw)
            buffered_identity = self.module.verify_golden_archive(
                GOLDEN_ARCHIVE, root / buffered["archive_filename"], buffered)
            self.module.verify_reference_cohort_separation(
                raw_identity, buffered_identity)
            self.assertEqual(raw_identity["archive_sha256"], raw["archive_sha256"])
            self.assertEqual(raw_identity["anchor_count"], 22)
            self.assertEqual(
                raw_identity["artifact_completeness"],
                "TCL_LOG_REPORT_NETLIST_SDC_SOURCE_COMPLETE",
            )

    def test_functional_loss_archive_exact_ledger_logs_and_totals(self):
        reference = self.module.load_functional_loss_reference()
        with tempfile.TemporaryDirectory(prefix="k2-w2-functional-loss-") as directory:
            identity = self.module.verify_functional_loss_archive(
                FUNCTIONAL_LOSS_ARCHIVE,
                Path(directory) / reference["archive_filename"], reference,
            )
            self.assertEqual(identity["ledger"], "PASS_338_OF_338_EXACT_PREFIX")
            self.assertEqual(identity["outer_driver_log"], "EXCLUDED_STALE")
            self.assertEqual(identity["ppa_use"], "FORBIDDEN")
            self.assertEqual(identity["full50_loss_totals"]["fovea"], {
                "generated": 106416, "accepted": 78229,
                "delivered": 78229, "overrun": 28187})
            self.assertEqual(identity["full50_loss_totals"]["cluster2"], {
                "generated": 106416, "accepted": 94157,
                "delivered": 94157, "overrun": 12259})

    def test_all_five_designs_publish_bound_receipts(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            for index, design in enumerate((
                    "a2_k2", "a3_k2", "p6_endpoint", "a2_p6", "a3_p6")):
                attempt = f"positive-{index}-{design}"
                result = self.invoke(output, design, attempt)
                self.assertEqual(result.returncode, 0, result.stdout)
                receipt = json.loads((output / attempt / "receipt.json").read_text())
                self.assertEqual(receipt["status"], "PASS")
                self.assertEqual(receipt["design"], design)
                self.assertEqual(receipt["mapped_inventory"]["mapped_cell_count"], 1)
                self.assertEqual(receipt["mapped_smoke"]["status"], "PASS")
                cohorts = receipt["evidence_cohorts"]
                self.assertEqual(set(cohorts), {
                    "raw_reference", "buffered_reference", "endpoint_candidate",
                    "functional_loss_reference"})
                self.assertNotEqual(
                    cohorts["raw_reference"]["cohort"],
                    cohorts["buffered_reference"]["cohort"],
                )
                self.assertEqual(
                    receipt["checks"]["report_only_publication"],
                    "REJECTED_REQUIRES_SOURCE_TOOL_NETLIST_SDC_INVENTORY_SMOKE",
                )
                self.assertEqual(
                    cohorts["functional_loss_reference"]["ppa_use"], "FORBIDDEN")

    def test_existing_attempt_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            first = self.invoke(output)
            self.assertEqual(first.returncode, 0, first.stdout)
            receipt = (output / "attempt-1/receipt.json").read_bytes()
            second = self.invoke(output)
            self.assertNotEqual(second.returncode, 0, second.stdout)
            self.assertEqual((output / "attempt-1/receipt.json").read_bytes(), receipt)

    def test_unresolved_blackbox_is_rejected_without_receipt(self):
        for mode in ("blackbox", "defined_blackbox"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                    prefix="k2-w2-genus-") as directory:
                output = Path(directory)
                result = self.invoke(output, mode=mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("blackbox", result.stdout)
                self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_scan_cell_is_rejected_without_receipt(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            result = self.invoke(output, mode="scan")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("scan cells are forbidden", result.stdout)
            self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_missing_or_fabricated_actual_report_and_log_are_rejected(self):
        for mode in ("missing_report", "bad_report", "bad_summary", "missing_pass"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(
                    prefix="k2-w2-genus-") as directory:
                output = Path(directory)
                result = self.invoke(output, mode=mode)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_missing_or_mutated_golden_archive_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            result = self.invoke(Path(directory), golden_archive=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("--golden-archive", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-golden-") as directory:
            root = Path(directory)
            fake = root / "ganghee-pnr-golden-20260813.tar.gz"
            shutil.copyfile(GOLDEN_ARCHIVE, fake)
            payload = bytearray(fake.read_bytes())
            payload[-1] ^= 1
            fake.write_bytes(payload)
            result = self.invoke(root / "out", golden_archive=fake)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("golden archive SHA mismatch", result.stdout)
            self.assertFalse((root / "out/attempt-1/receipt.json").exists())

    def test_missing_mutated_or_report_only_raw_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-golden-") as directory:
            result = self.invoke(Path(directory), raw_golden_archive=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("--raw-golden-archive", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-golden-") as directory:
            root = Path(directory)
            fake = root / "ganghee-pnr-raw-golden-20260813.tar.gz"
            shutil.copyfile(RAW_GOLDEN_ARCHIVE, fake)
            payload = bytearray(fake.read_bytes())
            payload[-1] ^= 1
            fake.write_bytes(payload)
            result = self.invoke(root / "out", raw_golden_archive=fake)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("raw golden archive SHA mismatch", result.stdout)
            self.assertFalse((root / "out/attempt-1/receipt.json").exists())
        with tempfile.TemporaryDirectory(prefix="k2-w2-report-only-") as directory:
            output = Path(directory)
            result = self.invoke(output, mode="report_only")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_missing_mutated_or_rebound_functional_loss_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-functional-loss-") as directory:
            result = self.invoke(Path(directory), functional_loss_archive=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("--functional-loss-archive", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-functional-loss-") as directory:
            root = Path(directory)
            fake = root / "eval-fovea-cluster2.yZr1kmYL.tar.gz"
            shutil.copyfile(FUNCTIONAL_LOSS_ARCHIVE, fake)
            payload = bytearray(fake.read_bytes())
            payload[-1] ^= 1
            fake.write_bytes(payload)
            result = self.invoke(root / "out", functional_loss_archive=fake)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("functional loss archive SHA mismatch", result.stdout)
            self.assertFalse((root / "out/attempt-1/receipt.json").exists())
        with tempfile.TemporaryDirectory(prefix="k2-w2-functional-loss-") as directory:
            reference = json.loads(json.dumps(
                self.module.load_functional_loss_reference()))
            reference["ledger_prefix"] = "/tmp/stale-0FfaT8kp/"
            with self.assertRaisesRegex(self.module.FlowError, "provenance mismatch"):
                self.module.verify_functional_loss_archive(
                    FUNCTIONAL_LOSS_ARCHIVE,
                    Path(directory) / reference["archive_filename"], reference,
                )

    def test_raw_tool_library_and_source_setting_mutations_are_rejected(self):
        mutations = (
            ("library_path", "/tmp/local-substitute.lib", "exact library/source"),
            ("genus_version", "99.99-fabricated", "log format/status"),
        )
        for field, value, message in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory(
                    prefix="k2-w2-raw-setting-") as directory:
                raw = json.loads(json.dumps(self.module.load_raw_golden_reference()))
                raw[field] = value
                with self.assertRaisesRegex(self.module.FlowError, message):
                    self.module.verify_raw_golden_archive(
                        RAW_GOLDEN_ARCHIVE,
                        Path(directory) / raw["archive_filename"], raw,
                    )
        with tempfile.TemporaryDirectory(prefix="k2-w2-raw-setting-") as directory:
            raw = json.loads(json.dumps(self.module.load_raw_golden_reference()))
            raw["runs"]["fovea_raw"]["read_hdl"] = (
                "read_hdl -v {rtl/local_substitute.v}")
            with self.assertRaisesRegex(self.module.FlowError, "exact library/source"):
                self.module.verify_raw_golden_archive(
                    RAW_GOLDEN_ARCHIVE,
                    Path(directory) / raw["archive_filename"], raw,
                )

    def test_smoke_is_mandatory_and_fabricated_hash_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            result = self.invoke(Path(directory), smoke=None)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("smoke hook is required", result.stdout)
        with tempfile.TemporaryDirectory(prefix="k2-w2-genus-") as directory:
            output = Path(directory)
            result = self.invoke(output, smoke=FABRICATED_SMOKE)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("not bound to the mapped netlist/library/top", result.stdout)
            self.assertFalse((output / "attempt-1/receipt.json").exists())

    def test_filelist_and_source_hash_mutations_are_rejected(self):
        registry = self.module.load_registry()
        design = registry["designs"]["a2_k2"]
        original = design["filelist_sha256"]
        design["filelist_sha256"] = "0" * 64
        with self.assertRaisesRegex(self.module.FlowError, "filelist SHA"):
            self.module.verify_design(ROOT, registry, "a2_k2")
        design["filelist_sha256"] = original
        design["sources"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(self.module.FlowError, "source byte mismatch"):
            self.module.verify_design(ROOT, registry, "a2_k2")


if __name__ == "__main__":
    unittest.main()
