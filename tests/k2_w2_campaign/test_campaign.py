from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_PATH = ROOT / "physical/k2_w2_campaign/campaign.json"
LAUNCHER = ROOT / "physical/k2_w2_campaign/launch_campaign.py"
SPEC = importlib.util.spec_from_file_location("k2_w2_campaign", LAUNCHER)
assert SPEC and SPEC.loader
campaign_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_module)


def campaign() -> dict:
    return json.loads(CAMPAIGN_PATH.read_text(encoding="utf-8"))


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write(path: Path, payload: bytes = b"fixture\n") -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": str(path.resolve()), "sha256": sha(payload)}


def write_json(path: Path, value: dict) -> dict[str, str]:
    return write(path, campaign_module.canonical(value))


def environment_fixture(root: Path, doc: dict) -> dict[str, str]:
    tools = {
        name: {"path": row["path"], "parsed_version": row["version"],
               "sha256": row["sha256"]}
        for name, row in doc["server_environment"]["tools"].items()
    }
    technology = {}
    for name, row in doc["server_environment"]["technology"].items():
        if name in {"second_qrc_required", "rc_disclosure"}:
            continue
        technology["macro_lef" if name == "cell_lef" else name] = copy.deepcopy(row)
    archives = {
        "raw_core": {name: doc["authority"]["raw_server_archive"][name]
                     for name in ("path", "sha256")},
        "buffered_extension": {
            name: doc["authority"]["buffered_server_archive"][name]
            for name in ("path", "sha256")},
    }
    receipt = {
        "schema": "k2_w2_server_env_result_v1",
        "qualification_status": "PROVEN_ENVIRONMENT",
        "campaign_launch_allowed": True,
        "unresolved_environment_evidence": [],
        "gates": {
            "source_archives": {"status": "PROVEN", "evidence": archives},
            "tool_executables": {"status": "PROVEN", "evidence": tools},
            "technology_files": {"status": "PROVEN", "evidence": technology},
            "library_semantics": {"status": "PROVEN"},
            "site_and_cell_availability": {"status": "PROVEN"},
            "rc_policy": {"status": "PROVEN_WITH_LIMITATION"},
        },
        "receipt": {
            "schema": "k2_w2_server_env_go_receipt_v1", "decision": "GO",
            "evidence_status": "PROVEN_SERVER_ENV",
        },
    }
    receipt["receipt_sha256"] = sha(campaign_module.canonical(receipt))
    return write_json(root / "environment.json", receipt)


def calibration_fixture(root: Path, doc: dict, environment_sha: str) -> dict[str, str]:
    reports = []
    for index in range(10):
        reports.append(write(root / f"native-{index}.rpt",
                             f"native-report-{index}\n".encode()))
        reports[-1]["name"] = f"native-{index}"
    summary = write(root / "machine-summary.json", b'{"summary":"server-owned"}\n')
    classes = {"unresolved": 0, "multiple_driver": 0, "floating": 0}
    evidence = {
        "command_status": "PASS",
        "native_report_sha256": reports[0]["sha256"],
        "machine_summary_sha256": summary["sha256"],
        "class_inventory_sha256": sha(campaign_module.canonical(classes)),
        "class_counts": classes,
        "total_nonzero_classes": 0,
    }
    receipt = {
        "schema": "k2_w2_native_report_calibration_v1", "status": "PASS",
        "purpose": "REPORT_FORMAT_CALIBRATION_ONLY", "ranking_eligible": False,
        "environment_receipt_sha256": environment_sha,
        "innovus": copy.deepcopy(doc["server_environment"]["tools"]["innovus"]),
        "pnr_tcl_sha256": doc["tool_providers"]["innovus_pnr_tcl"]["sha256"],
        "verifier_sha256": doc["tool_providers"]["innovus_verifier"]["sha256"],
        "native_reports": reports,
        "machine_summary": {**summary, "schema": "k2_w2_innovus_machine_summary_v1"},
        "check_design_all_class_inventory": sorted(classes),
        "check_design_all": {
            "pre_place": copy.deepcopy(evidence),
            "post_route": copy.deepcopy(evidence),
        },
    }
    return write_json(root / "calibration.json", receipt)


class StaticContractTest(unittest.TestCase):
    def test_current_package_is_honestly_hold(self) -> None:
        blockers = campaign_module.validate_campaign(campaign(), ROOT)
        self.assertGreaterEqual(len(blockers), 10)
        self.assertTrue(any("canonical" in item and "manifest" in item for item in blockers))
        self.assertTrue(any("power" in item for item in blockers))
        self.assertTrue(any("qualifier CLI" in item for item in blockers))

    def test_check_is_nonzero_hold_never_package_pass(self) -> None:
        result = subprocess.run(
            ["python3", str(LAUNCHER), "check", "--repo-root", str(ROOT)],
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("K2_W2_CAMPAIGN_HOLD", result.stderr)
        self.assertNotIn("PACKAGE_PASS", result.stdout + result.stderr)

    def test_exact_cohorts_and_forbidden_generic_tops(self) -> None:
        doc = campaign()
        self.assertEqual(doc["cohorts"]["raw_diagnostic"]["exact_design_order"],
                         ["fovea_raw", "cluster2_raw"])
        self.assertFalse(doc["cohorts"]["raw_diagnostic"]["ranking_eligible"])
        self.assertEqual(doc["cohorts"]["fair_endpoints"]["exact_design_order"],
                         ["fovea_a7", "a2_p6", "a3_p6"])
        self.assertFalse(doc["cohorts"]["fair_endpoints"][
            "generic_unequal_debug_wrappers_eligible"])
        forbidden = doc["staged_wrapper_expectation"]["forbidden_final_tops"]
        self.assertIn("k2_w2_a2_p6_top", forbidden)
        self.assertIn("a2_batched_iwrr_p6_top", forbidden)

    def test_canonical_schema_boundary_and_alias_rejection(self) -> None:
        doc = campaign()
        expectation = doc["staged_wrapper_expectation"]
        self.assertEqual(expectation["schema"], "k2_w2_tech_staged_compositions_v1")
        self.assertEqual([row["name"] for row in expectation["common_inputs"]],
                         ["ref_clk_i", "sample_clk_i", "rst_n", "source_pending_i"])
        self.assertEqual([row["name"] for row in expectation["common_observation"]],
                         ["source_accept_o", "retire_valid_o", "retire_addr0_o",
                          "retire_addr1_o", "drain_idle_o", "protocol_error_o"])
        for alias in ("load_i", "pending_i", "source_ready_o", "protocol_fault_o"):
            mutated = copy.deepcopy(doc)
            target = mutated["staged_wrapper_expectation"]["common_inputs"][-1]
            if alias.endswith("_o"):
                target = mutated["staged_wrapper_expectation"]["common_observation"][-1]
            target["name"] = alias
            with self.subTest(alias=alias), self.assertRaises(campaign_module.CampaignError):
                campaign_module.validate_campaign(mutated, ROOT)

    def test_alternate_staging_schemas_are_rejected(self) -> None:
        for schema in ("w2-physical-staging-v2", "w2-physical-staging-shared-v2"):
            mutated = campaign()
            mutated["staged_wrapper_expectation"]["schema"] = schema
            with self.subTest(schema=schema), self.assertRaisesRegex(
                    campaign_module.CampaignError, "alternate technology-staging"):
                campaign_module.validate_campaign(mutated, ROOT)

    def test_link_inventory_is_counted_once(self) -> None:
        doc = campaign()
        expected = {"fovea_a7": (50, 3), "a2_p6": (53, 6), "a3_p6": (53, 6)}
        for key, (total, link) in expected.items():
            accounting = doc["staged_wrapper_expectation"]["designs"][key]["accounting"]
            self.assertEqual(accounting["native_nonlink_physical_bits"], 47)
            self.assertEqual(accounting["link_physical_bits"], link)
            self.assertEqual(accounting["total_physical_bits"], total)
            self.assertEqual(accounting["combine_rule"],
                             "disjoint_native_nonlink_plus_link_once")

    def test_common_nonlink_bit_counts_are_identical(self) -> None:
        expectation = campaign()["staged_wrapper_expectation"]
        self.assertEqual(sum(row["width"] for row in expectation["common_inputs"]), 19)
        self.assertEqual(sum(row["width"] for row in expectation["common_observation"]), 28)
        for design in expectation["designs"].values():
            self.assertEqual(design["accounting"]["native_nonlink_physical_bits"], 47)

    def test_link_double_count_and_omission_mutations_fail(self) -> None:
        double = campaign()
        double["staged_wrapper_expectation"]["designs"]["fovea_a7"]["accounting"][
            "combine_rule"] = "native_plus_link"
        with self.assertRaisesRegex(campaign_module.CampaignError, "exactly-once"):
            campaign_module.validate_campaign(double, ROOT)
        omitted = campaign()
        omitted["staged_wrapper_expectation"]["designs"]["a2_p6"]["link_ports"].pop()
        with self.assertRaisesRegex(campaign_module.CampaignError, "omitted or duplicated"):
            campaign_module.validate_campaign(omitted, ROOT)

    def test_link_role_and_width_mutations_fail(self) -> None:
        role = campaign()
        role["staged_wrapper_expectation"]["designs"]["a3_p6"]["link_ports"][0][
            "role"] = "functional"
        with self.assertRaisesRegex(campaign_module.CampaignError, "cut mapping"):
            campaign_module.validate_campaign(role, ROOT)
        width = campaign()
        width["staged_wrapper_expectation"]["designs"]["a2_p6"]["link_ports"][1][
            "width"] = 4
        with self.assertRaisesRegex(campaign_module.CampaignError, "cut mapping"):
            campaign_module.validate_campaign(width, ROOT)

    def test_exact_endpoint_cell_inventory(self) -> None:
        designs = campaign()["staged_wrapper_expectation"]["designs"]
        self.assertEqual(designs["fovea_a7"]["endpoint_hierarchy_inventory"],
                         {"ICG": 1, "MX2": 2, "posedge_DFFRH": 2,
                          "negedge_DFFNS": 5})
        for key in ("a2_p6", "a3_p6"):
            self.assertEqual(designs[key]["endpoint_hierarchy_inventory"],
                             {"ICG": 1, "MX2": 5, "posedge_DFFRH": 5,
                              "negedge_DFFNS": 12})

    def test_old_negedge_inventory_and_clock_shrink_fail(self) -> None:
        old = campaign()
        old["staged_wrapper_expectation"]["designs"]["a2_p6"][
            "endpoint_hierarchy_inventory"]["negedge_DFFNS"] = 5
        with self.assertRaisesRegex(campaign_module.CampaignError, "hierarchy inventory"):
            campaign_module.validate_campaign(old, ROOT)
        clock = campaign()
        clock["staged_wrapper_expectation"]["designs"]["fovea_a7"]["clock_contract"][
            "primary_inputs"].pop()
        with self.assertRaisesRegex(campaign_module.CampaignError, "ref/sample"):
            campaign_module.validate_campaign(clock, ROOT)

    def test_generated_clock_retarget_and_async_mutations_fail(self) -> None:
        target = campaign()
        target["staged_wrapper_expectation"]["designs"]["a2_p6"]["clock_contract"][
            "generated_gated"][0]["source_port"] = "ref_clk_i"
        with self.assertRaisesRegex(campaign_module.CampaignError, "generated/gated"):
            campaign_module.validate_campaign(target, ROOT)
        asynchronous = campaign()
        asynchronous["staged_wrapper_expectation"]["designs"]["a3_p6"][
            "clock_contract"]["asynchronous_clock_groups"] = True
        with self.assertRaisesRegex(campaign_module.CampaignError, "related/gating/pulse"):
            campaign_module.validate_campaign(asynchronous, ROOT)

    def test_frozen_workload_spelling_and_pipeline_order(self) -> None:
        doc = campaign()
        self.assertEqual(doc["functional_activity_contract"]["workloads"],
                         ["full50", "capacity22"])
        self.assertNotIn("cap22", json.dumps(doc))
        self.assertEqual(doc["execution_policy"]["sealed_stage_order"], [
            "proven_environment", "canonical_stage", "common_activity", "genus",
            "mapped_proof", "innovus", "postroute_power", "qualifier",
            "final_receipt"])

    def test_hidden_queue_and_synthetic_replay_mutations_fail(self) -> None:
        for field in ("hidden_candidate_queue_allowed", "synthetic_replay_allowed"):
            mutated = campaign()
            mutated["functional_activity_contract"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                    campaign_module.CampaignError, "frozen common TB/activity"):
                campaign_module.validate_campaign(mutated, ROOT)

    def test_raw_ranking_leak_and_candidate_order_mutations_fail(self) -> None:
        ranking = campaign()
        ranking["cohorts"]["raw_diagnostic"]["ranking_eligible"] = True
        with self.assertRaisesRegex(campaign_module.CampaignError, "cohort separation"):
            campaign_module.validate_campaign(ranking, ROOT)
        order = campaign()
        order["cohorts"]["fair_endpoints"]["exact_design_order"] = [
            "a2_p6", "fovea_a7", "a3_p6"]
        with self.assertRaisesRegex(campaign_module.CampaignError, "cohort separation"):
            campaign_module.validate_campaign(order, ROOT)

    def test_duplicate_genus_receipt_contract_is_rejected(self) -> None:
        mutated = campaign()
        mutated["staged_wrapper_expectation"]["shared_consumer_contract"][
            "required_genus_receipt_schema"] = "campaign_private_receipt_v1"
        with self.assertRaisesRegex(campaign_module.CampaignError, "duplicate Genus receipt"):
            campaign_module.validate_campaign(mutated, ROOT)

    def test_shared_qrc_and_split_liberty_campaign_mutations_fail(self) -> None:
        qrc = campaign()
        qrc["server_environment"]["technology"]["hold_qrc"]["path"] = "/fake/hold.tch"
        with self.assertRaisesRegex(campaign_module.CampaignError, "shared typical QRC"):
            campaign_module.validate_campaign(qrc, ROOT)
        liberty = campaign()
        liberty["server_environment"]["technology"]["hold_liberty"]["path"] = \
            liberty["server_environment"]["technology"]["setup_liberty"]["path"]
        with self.assertRaisesRegex(campaign_module.CampaignError, "Liberty views"):
            campaign_module.validate_campaign(liberty, ROOT)

    def test_no_flow_logic_or_direct_original_shortcut(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        for command in ("place_opt_design", "clock_opt_design", "routeDesign",
                        "verifyConnectivity", "syn_generic"):
            self.assertNotIn(command, text)
        self.assertNotIn("a7_weighted_fovea_ddr", text)


class ReceiptValidationTest(unittest.TestCase):
    def test_real_environment_shape_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = environment_fixture(root, doc)
            _, observed, _ = campaign_module.validate_environment(row, doc)
            self.assertEqual(observed, row["sha256"])

    def test_environment_fixture_has_distinct_pinned_xrun_hash(self) -> None:
        doc = campaign()
        tools = doc["server_environment"]["tools"]
        self.assertRegex(tools["xrun"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(tools["xrun"]["sha256"], tools["genus"]["sha256"])

    def test_environment_receipt_self_hash_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = environment_fixture(root, doc)
            receipt = json.loads(Path(row["path"]).read_text())
            receipt["campaign_launch_allowed"] = False
            bad = write_json(root / "bad-self-hash.json", receipt)
            with self.assertRaisesRegex(campaign_module.CampaignError,
                                        "not PROVEN_ENVIRONMENT"):
                campaign_module.validate_environment(bad, doc)

    def test_environment_second_qrc_and_stale_tool_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = environment_fixture(root, doc)
            receipt = json.loads(Path(row["path"]).read_text())
            receipt["gates"]["technology_files"]["evidence"]["hold_qrc"] = {
                "path": "/fake/hold.tch", "sha256": "f" * 64}
            receipt.pop("receipt_sha256")
            receipt["receipt_sha256"] = sha(campaign_module.canonical(receipt))
            bad = write_json(root / "bad-qrc.json", receipt)
            with self.assertRaisesRegex(campaign_module.CampaignError, "second QRC"):
                campaign_module.validate_environment(bad, doc)

            receipt = json.loads(Path(row["path"]).read_text())
            receipt["gates"]["tool_executables"]["evidence"]["genus"]["sha256"] = "0" * 64
            receipt.pop("receipt_sha256")
            receipt["receipt_sha256"] = sha(campaign_module.canonical(receipt))
            bad = write_json(root / "bad-tool.json", receipt)
            with self.assertRaisesRegex(campaign_module.CampaignError, "tool identity"):
                campaign_module.validate_environment(bad, doc)

    def test_calibration_reads_native_and_summary_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = calibration_fixture(root, doc, "e" * 64)
            campaign_module.validate_calibration(row, "e" * 64, doc)
            receipt = json.loads(Path(row["path"]).read_text())
            Path(receipt["native_reports"][0]["path"]).write_text("tampered\n")
            with self.assertRaisesRegex(campaign_module.CampaignError, "native report SHA"):
                campaign_module.validate_calibration(row, "e" * 64, doc)

    def test_calibration_reads_machine_summary_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = calibration_fixture(root, doc, "e" * 64)
            receipt = json.loads(Path(row["path"]).read_text())
            Path(receipt["machine_summary"]["path"]).write_text("tampered\n")
            with self.assertRaisesRegex(campaign_module.CampaignError,
                                        "machine summary is not bound"):
                campaign_module.validate_calibration(row, "e" * 64, doc)

    def test_calibration_all_class_inventory_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = calibration_fixture(root, doc, "e" * 64)
            receipt = json.loads(Path(row["path"]).read_text())
            receipt["check_design_all"]["post_route"]["class_counts"].pop("floating")
            receipt["check_design_all"]["post_route"]["class_inventory_sha256"] = sha(
                campaign_module.canonical(
                    receipt["check_design_all"]["post_route"]["class_counts"]))
            bad = write_json(root / "bad-calibration.json", receipt)
            with self.assertRaisesRegex(campaign_module.CampaignError, "every error class"):
                campaign_module.validate_calibration(bad, "e" * 64, doc)

    def test_calibration_nonzero_class_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = calibration_fixture(root, doc, "e" * 64)
            receipt = json.loads(Path(row["path"]).read_text())
            counts = receipt["check_design_all"]["post_route"]["class_counts"]
            counts["floating"] = 1
            receipt["check_design_all"]["post_route"]["class_inventory_sha256"] = sha(
                campaign_module.canonical(counts))
            receipt["check_design_all"]["post_route"]["total_nonzero_classes"] = 1
            bad = write_json(root / "nonzero-calibration.json", receipt)
            with self.assertRaisesRegex(campaign_module.CampaignError, "every error class"):
                campaign_module.validate_calibration(bad, "e" * 64, doc)

    def test_calibration_missing_native_report_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            doc = campaign()
            row = calibration_fixture(root, doc, "e" * 64)
            receipt = json.loads(Path(row["path"]).read_text())
            Path(receipt["native_reports"][3]["path"]).unlink()
            with self.assertRaisesRegex(campaign_module.CampaignError, "cannot stat"):
                campaign_module.validate_calibration(row, "e" * 64, doc)

    def test_staged_ansi_port_parser_rejects_alias_and_extra_port(self) -> None:
        good = b"""module staged(
input logic ref_clk_i,
input logic sample_clk_i,
input logic rst_n,
input logic [15:0] source_pending_i,
output logic [15:0] source_accept_o,
output logic link_clk_o,
output logic [1:0] link_data_o
); endmodule\n"""
        ports = campaign_module.parse_ansi_ports(good, "staged")
        self.assertEqual(ports[3]["name"], "source_pending_i")
        bad = good.replace(b"source_pending_i", b"load_i")
        self.assertNotEqual(campaign_module.parse_ansi_ports(bad, "staged"), ports)

    def test_staged_ansi_port_parser_rejects_duplicate(self) -> None:
        duplicate = b"module staged(input logic a, output logic a); endmodule\n"
        with self.assertRaisesRegex(campaign_module.CampaignError, "duplicate ports"):
            campaign_module.parse_ansi_ports(duplicate, "staged")


class LauncherTest(unittest.TestCase):
    def invoke(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["python3", str(LAUNCHER), *args], cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_missing_integration_emits_only_failing_blocked_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "attempts"
            result = self.invoke("emit", "--repo-root", str(ROOT), "--attempt-root",
                                 str(output), "--attempt-id", "blocked")
            self.assertEqual(result.returncode, 2)
            plan = json.loads((output / "blocked/launch-plan.json").read_text())
            self.assertEqual(plan["status"], "BLOCKED")
            self.assertEqual(plan["steps"], [])
            script = output / "blocked/commands.sh"
            self.assertNotIn("--execute", script.read_text())
            self.assertEqual(subprocess.run([str(script)], check=False).returncode, 2)

    def test_integration_cannot_clear_campaign_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = write_json(root / "integration.json", {})
            result = self.invoke("emit", "--repo-root", str(ROOT), "--attempt-root",
                                 str(root / "attempts"), "--attempt-id", "hold",
                                 "--integration", fake["path"])
            self.assertEqual(result.returncode, 2)
            plan = json.loads((root / "attempts/hold/launch-plan.json").read_text())
            self.assertEqual(plan["status"], "HOLD")
            self.assertEqual(plan["steps"], [])

    def test_hold_commands_are_nonzero_and_contain_no_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = write_json(root / "integration.json", {})
            self.invoke("emit", "--repo-root", str(ROOT), "--attempt-root",
                        str(root / "attempts"), "--attempt-id", "hold-script",
                        "--integration", fake["path"])
            script = root / "attempts/hold-script/commands.sh"
            result = subprocess.run([str(script)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("K2_W2_CAMPAIGN_HOLD", result.stderr)
            self.assertNotIn("--execute", script.read_text())

    def test_attempt_root_is_unique_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            args = ("emit", "--repo-root", str(ROOT), "--attempt-root", temporary,
                    "--attempt-id", "same")
            self.invoke(*args)
            second = self.invoke(*args)
            self.assertEqual(second.returncode, 2)
            self.assertIn("File exists", second.stderr)

    def test_actual_provider_commit_help_contracts(self) -> None:
        doc = campaign()
        cases = (
            ("/tmp/k2-phys-w2-genus", "environment_preflight",
             ("--pdk-root", "--genus", "--innovus", "--xrun", "--output"), ()),
            ("/tmp/k2-phys-w2-genus", "genus_v2",
             ("--hold-library", "--cell-lef", "--shared-qrc"),
             ("--activity-receipt",)),
            ("/tmp/k2-phys-w2-innovus", "innovus_plan",
             ("--plan", "--validate-only", "--execute"),
             ("--expected-plan-sha-file", "--environment-receipt",
              "--calibration-receipt", "--cohort")),
            ("/tmp/k2-phys-w2-qualifier", "qualifier",
             ("--bundle-root", "--manifest", "--output"),
             ("--environment-receipt", "--plan", "--power-receipt")),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (repository, key, present, absent) in enumerate(cases):
                provider = doc["tool_providers"][key]
                blob = subprocess.run(
                    ["git", "-C", repository, "show",
                     f"{provider['repository_commit']}:{provider['path']}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertEqual(blob.returncode, 0, blob.stderr.decode())
                self.assertEqual(sha(blob.stdout), provider["sha256"])
                script = root / f"provider-{index}.py"
                script.write_bytes(blob.stdout)
                help_run = subprocess.run(["python3", str(script), "--help"], text=True,
                                          capture_output=True, check=False)
                self.assertEqual(help_run.returncode, 0, help_run.stderr)
                for option in present:
                    self.assertIn(option, help_run.stdout)
                for option in absent:
                    self.assertNotIn(option, help_run.stdout)

    def test_environment_receipt_verifier_help_from_exact_commit(self) -> None:
        doc = campaign()
        provider = doc["tool_providers"]["environment_receipt_verifier"]
        preflight = doc["tool_providers"]["environment_preflight"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for row in (provider, preflight):
                blob = subprocess.run(
                    ["git", "-C", "/tmp/k2-phys-w2-genus", "show",
                     f"{row['repository_commit']}:{row['path']}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertEqual(blob.returncode, 0, blob.stderr.decode())
                self.assertEqual(sha(blob.stdout), row["sha256"])
                (root / Path(row["path"]).name).write_bytes(blob.stdout)
            result = subprocess.run(
                ["python3", str(root / "require_go_receipt.py"), "--help"],
                text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--contract", result.stdout)
            self.assertIn("--receipt", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
