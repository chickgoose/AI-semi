from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "physical/k2_w4_server_cohort"
LAUNCHER = HERE / "launch_server_cohort.py"
CONTRACT_PATH = HERE / "contract.json"
ENV_CONTRACT = ROOT / "physical/k2_w2_server_env/contract.json"
ENV_PREFLIGHT = ROOT / "physical/k2_w2_server_env/preflight.py"

SPEC = importlib.util.spec_from_file_location("fixture_preflight", ENV_PREFLIGHT)
assert SPEC and SPEC.loader
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return sha(payload)


def write_json(path: Path, value: dict) -> str:
    return write(path, canonical(value))


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.output = root / "output"
        self.output.mkdir()
        self.contract = json.loads(CONTRACT_PATH.read_text())
        self.environment = root / "environment.json"
        self.environment_sha = self._environment()
        self.receipts = {
            candidate: self._genus(candidate)
            for candidate in self.contract["candidate_order"]
        }

    def _environment(self) -> str:
        contract_sha = sha(ENV_CONTRACT.read_bytes())
        gates = {
            "source_archives": {"status": "PROVEN", "evidence": {"fixture": True}},
            "tool_executables": {"status": "PROVEN", "evidence": {"fixture": True}},
            "technology_files": {"status": "PROVEN", "evidence": {"fixture": True}},
            "library_semantics": {"status": "PROVEN", "evidence": {"fixture": True}},
            "site_and_cell_availability": {"status": "PROVEN", "evidence": {"fixture": True}},
            "rc_policy": {"status": "PROVEN_WITH_LIMITATION", "evidence": {"fixture": True}},
        }
        value = {
            "schema": "k2_w2_server_env_result_v1",
            "contract_sha256": contract_sha,
            "qualification_status": "PROVEN_ENVIRONMENT",
            "campaign_launch_allowed": True,
            "unresolved_environment_evidence": [],
            "gates": gates,
        }
        PREFLIGHT.finalize_result(value)
        payload = canonical(value)
        self.environment.write_bytes(payload)
        return sha(payload)

    def _genus(self, candidate: str) -> Path:
        top = self.contract["designs"][candidate]["top"]
        attempt = self.root / "genus" / candidate
        work = attempt / "work"
        log = (f"Version: 23.14-s090_1\nW2_GENUS_PASS top={top}\n"
               "Info=11, Warn=2, Error=0, Fatal=0\nNormal exit.\n").encode()
        write(attempt / "logs/genus.log", log)
        write(attempt / "logs/mapped-functional.log", b"MAPPED_FUNCTIONAL_PASS\n")
        netlist_sha = write(work / f"{top}_netlist.v",
                            f"module {top}; endmodule\n".encode())
        sdf_sha = write(work / f"{top}.sdf", f"(DELAYFILE ({top}))\n".encode())
        mapped_sdc_sha = write(work / f"{top}_out.sdc", b"create_clock -period 5\n")
        reports = {}
        for kind in ("area", "gtiming", "gpower"):
            name = f"{top}_{kind}.rpt"
            reports[name] = write(work / name, f"{candidate} {kind} report\n".encode())
        endpoint = {
            "schema": "k2_w2_endpoint_connectivity_map_v1", "design": candidate,
            "top": top, "mapped_netlist_sha256": netlist_sha,
            "leaf_counts": self.contract["designs"][candidate]["endpoint_leaf_counts"],
            "instances": [{"fixture": True}],
        }
        endpoint_sha = write_json(attempt / "endpoint-connectivity-map.json", endpoint)
        gate = {
            "schema": "k2_w2_mapped_functional_gate_v1", "status": "PASS",
            "design": candidate, "top": top,
            "mapped_netlist_sha256": netlist_sha, "sdf_status": "ANNOTATED",
        }
        gate_sha = write_json(attempt / "mapped-functional-gate.json", gate)
        handoff = {
            "schema": "k2_w2_innovus_strict_sdc_handoff_v1",
            "design": candidate, "top": top,
            "mapped_netlist_sha256": netlist_sha,
            "mapped_sdf_sha256": sdf_sha, "mapped_sdc_sha256": mapped_sdc_sha,
            "innovus_consumption_status": "PENDING_REQUIRES_EXACT_HASH_RECEIPT",
        }
        handoff_sha = write_json(attempt / "innovus-handoff.json", handoff)
        flow = {
            "physical/k2_w2_genus/run_genus.py": self.contract["genus"][
                "producer_sha256"],
            "physical/k2_w2_genus/designs.json": self.contract["genus"][
                "registry_sha256"],
        }
        staged_manifest = {"schema": "k2_w2_tech_staged_compositions_v1",
                           "sha256": "5" * 64}
        technology_authorities = {"environment": "fixture-exact"}
        attempt_doc = {
            "schema": self.contract["genus"]["attempt_schema"],
            "attempt": f"fixture-{candidate}", "design": candidate, "top": top,
            "boundary_cohort": self.contract["genus"]["boundary_cohort"],
            "source_origin": self.contract["genus"]["source_origin"],
            "ranking_policy": self.contract["genus"]["ranking_policy"],
            "flow_git_head": self.contract["genus"]["producer_commit"],
            "source_commit": self.contract["genus"]["source_commit"],
            "staged_manifest": staged_manifest,
            "technology_authorities": technology_authorities,
            "proven_environment": {"sha256": self.environment_sha},
            "flow_files_sha256": flow,
        }
        attempt_sha = write_json(attempt / "attempt.json", attempt_doc)
        receipt = {
            "schema": self.contract["genus"]["receipt_schema"],
            "status": self.contract["genus"]["receipt_status"],
            "design": candidate, "top": top, "attempt_sha256": attempt_sha,
            "boundary_cohort": self.contract["genus"]["boundary_cohort"],
            "source_origin": self.contract["genus"]["source_origin"],
            "ranking_policy": self.contract["genus"]["ranking_policy"],
            "staged_manifest": staged_manifest,
            "technology_authorities": technology_authorities,
            "evidence_cohorts": {"goal_execution": {
                "cohort": self.contract["genus"]["boundary_cohort"],
                "design": candidate, "top": top,
                "source_origin": self.contract["genus"]["source_origin"],
                "source_commit": self.contract["genus"]["source_commit"],
                "ranking_policy": self.contract["genus"]["ranking_policy"]}},
            "mapped_inventory": {"mapped_netlist_sha256": netlist_sha,
                                 "mapped_cell_count": 42},
            "endpoint_leaf_inventory": {
                "connectivity_map_sha256": endpoint_sha,
                "leaf_counts": self.contract["designs"][candidate]["endpoint_leaf_counts"],
            },
            "mapped_sdf_sha256": sdf_sha, "mapped_sdc_sha256": mapped_sdc_sha,
            "report_sha256": reports, "mapped_functional_gate_sha256": gate_sha,
            "innovus_handoff_sha256": handoff_sha,
            "checks": copy.deepcopy(self.contract["genus"]["required_checks"]),
            "claim_boundary": self.contract["genus"]["claim_boundary"],
        }
        receipt_path = attempt / "receipt.json"
        write_json(receipt_path, receipt)
        return receipt_path

    def command(self, attempt: str = "campaign-001") -> list[str]:
        command = ["python3", str(LAUNCHER),
                   "--environment-receipt", str(self.environment)]
        for candidate in self.contract["candidate_order"]:
            command += ["--genus-receipt", f"{candidate}={self.receipts[candidate]}"]
        command += ["--output-parent", str(self.output), "--attempt", attempt]
        return command

    def run(self, attempt: str = "campaign-001") -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.command(attempt), cwd=ROOT, text=True,
                              capture_output=True, check=False)


class ServerCohortTest(unittest.TestCase):
    def with_fixture(self):
        temporary = tempfile.TemporaryDirectory()
        return temporary, Fixture(Path(temporary.name))

    def test_exact_three_sequential_package_passes_without_eda(self):
        temporary, fixture = self.with_fixture()
        with temporary:
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            root = fixture.output / "campaign-001"
            manifest = json.loads((root / "cohort-manifest.json").read_text())
            self.assertEqual(manifest["status"],
                             "PACKAGED_EXACT_THREE_GENUS_RECEIPTS")
            self.assertFalse(manifest["eda_launch_performed"])
            self.assertEqual(manifest["candidate_order"],
                             ["fovea_a7", "a2_p6", "a3_p6"])
            self.assertEqual([row["index"] for row in manifest["candidates"]],
                             [1, 2, 3])
            for index, candidate in enumerate(manifest["candidate_order"], 1):
                self.assertTrue((root / f"gates/{index:02d}-{candidate}.json").is_file())
                row = manifest["candidates"][index - 1]
                inventory = (root / row["artifact_inventory_path"]).read_bytes()
                self.assertEqual(sha(inventory), row["artifact_inventory_sha256"])
                self.assertEqual(len(json.loads(inventory)), row["artifact_count"])
                self.assertEqual(
                    (root / f"genus/{candidate}/logs/genus.log").read_bytes(),
                    (fixture.receipts[candidate].parent / "logs/genus.log").read_bytes())
            log = (root / "logs/launcher.log").read_text()
            self.assertLess(log.index("candidate=fovea_a7"), log.index("candidate=a2_p6"))
            self.assertLess(log.index("candidate=a2_p6"), log.index("candidate=a3_p6"))
            self.assertIn("physical_qualification=HOLD", log)

    def test_existing_unique_root_is_never_reused(self):
        temporary, fixture = self.with_fixture()
        with temporary:
            self.assertEqual(fixture.run().returncode, 0)
            before = (fixture.output / "campaign-001/cohort-manifest.json").read_bytes()
            second = fixture.run()
            self.assertEqual(second.returncode, 2)
            self.assertEqual((fixture.output / "campaign-001/cohort-manifest.json").read_bytes(),
                             before)

    def test_environment_hold_fails_before_any_candidate_gate(self):
        temporary, fixture = self.with_fixture()
        with temporary:
            value = json.loads(fixture.environment.read_text())
            value["qualification_status"] = "HOLD"
            fixture.environment.write_bytes(canonical(value))
            result = fixture.run()
            self.assertEqual(result.returncode, 2)
            root = fixture.output / "campaign-001"
            self.assertEqual(json.loads((root / "failure.json").read_text())["failed_stage"],
                             "environment")
            self.assertFalse((root / "gates").exists())

    def test_a2_artifact_mutation_stops_a3_and_preserves_failure_log(self):
        temporary, fixture = self.with_fixture()
        with temporary:
            top = fixture.contract["designs"]["a2_p6"]["top"]
            (fixture.receipts["a2_p6"].parent / f"work/{top}_netlist.v").write_text(
                "mutated\n")
            result = fixture.run()
            self.assertEqual(result.returncode, 2)
            root = fixture.output / "campaign-001"
            self.assertTrue((root / "gates/01-fovea_a7.json").is_file())
            self.assertFalse((root / "gates/02-a2_p6.json").exists())
            self.assertFalse((root / "gates/03-a3_p6.json").exists())
            self.assertTrue((root /
                             "failure-source-logs/a2_p6/genus.log").is_file())

    def test_wrong_check_and_flow_split_are_rejected(self):
        for mutation in ("check", "flow"):
            temporary, fixture = self.with_fixture()
            with temporary, self.subTest(mutation=mutation):
                attempt = fixture.receipts["a3_p6"].parent
                if mutation == "check":
                    receipt = json.loads(fixture.receipts["a3_p6"].read_text())
                    receipt["checks"]["mapped_functional_gate"] = "SKIPPED"
                    fixture.receipts["a3_p6"].write_bytes(canonical(receipt))
                else:
                    document = json.loads((attempt / "attempt.json").read_text())
                    document["flow_git_head"] = "4" * 40
                    attempt_sha = write_json(attempt / "attempt.json", document)
                    receipt = json.loads(fixture.receipts["a3_p6"].read_text())
                    receipt["attempt_sha256"] = attempt_sha
                    fixture.receipts["a3_p6"].write_bytes(canonical(receipt))
                result = fixture.run()
                self.assertEqual(result.returncode, 2)
                self.assertFalse((fixture.output /
                                  "campaign-001/cohort-manifest.json").exists())

    def test_legacy_receipt_and_unpinned_producer_are_rejected(self):
        for mutation in ("legacy", "producer"):
            temporary, fixture = self.with_fixture()
            with temporary, self.subTest(mutation=mutation):
                attempt = fixture.receipts["fovea_a7"].parent
                if mutation == "legacy":
                    receipt = json.loads(fixture.receipts["fovea_a7"].read_text())
                    receipt["schema"] = "k2_w2_genus_receipt_v1"
                    receipt["status"] = "PASS"
                    fixture.receipts["fovea_a7"].write_bytes(canonical(receipt))
                else:
                    document = json.loads((attempt / "attempt.json").read_text())
                    document["flow_files_sha256"][
                        "physical/k2_w2_genus/run_genus.py"] = "9" * 64
                    attempt_sha = write_json(attempt / "attempt.json", document)
                    receipt = json.loads(fixture.receipts["fovea_a7"].read_text())
                    receipt["attempt_sha256"] = attempt_sha
                    fixture.receipts["fovea_a7"].write_bytes(canonical(receipt))
                self.assertEqual(fixture.run().returncode, 2)
                self.assertFalse((fixture.output /
                                  "campaign-001/cohort-manifest.json").exists())

    def test_symlink_and_hardlink_attacks_fail_closed(self):
        for attack in ("symlink", "hardlink"):
            temporary, fixture = self.with_fixture()
            with temporary, self.subTest(attack=attack):
                if attack == "symlink":
                    original = fixture.receipts["a3_p6"]
                    link = original.parent.parent / "a3-receipt-link.json"
                    link.symlink_to(original)
                    fixture.receipts["a3_p6"] = link
                else:
                    source = fixture.receipts["a2_p6"].parent / "logs/genus.log"
                    os.link(source, source.parent / "reused.log")
                result = fixture.run()
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("PACKAGED", result.stdout)

    def test_order_and_missing_receipt_are_not_bypassable(self):
        temporary, fixture = self.with_fixture()
        with temporary:
            command = fixture.command()
            a2_index = command.index(
                f"a2_p6={fixture.receipts['a2_p6']}")
            a3_index = command.index(
                f"a3_p6={fixture.receipts['a3_p6']}")
            command[a2_index], command[a3_index] = command[a3_index], command[a2_index]
            result = subprocess.run(command, cwd=ROOT, text=True,
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            root = fixture.output / "campaign-001"
            self.assertTrue((root / "failure.json").is_file())
            self.assertFalse((root / "cohort-manifest.json").exists())
            missing = ["python3", str(LAUNCHER), "--environment-receipt",
                       str(fixture.environment)]
            for candidate in fixture.contract["candidate_order"][:2]:
                missing += ["--genus-receipt",
                            f"{candidate}={fixture.receipts[candidate]}"]
            missing += ["--output-parent", str(fixture.output),
                        "--attempt", "campaign-missing"]
            missing_result = subprocess.run(missing, cwd=ROOT, text=True,
                                            capture_output=True, check=False)
            self.assertEqual(missing_result.returncode, 2)
            self.assertTrue((fixture.output /
                             "campaign-missing/failure.json").is_file())

    def test_static_scope_has_no_eda_or_bypass_interface(self):
        text = LAUNCHER.read_text()
        for token in ("subprocess", "syn_generic", "place_opt_design",
                      "routeDesign", "--force", "--skip", "--allow-hold"):
            self.assertNotIn(token, text)
        self.assertEqual(sha(ENV_CONTRACT.read_bytes()),
                         json.loads(CONTRACT_PATH.read_text())["environment"][
                             "contract_sha256"])
        contract = json.loads(CONTRACT_PATH.read_text())
        commit = contract["genus"]["producer_commit"]
        for path, field in (("physical/k2_w2_genus/run_genus.py", "producer_sha256"),
                            ("physical/k2_w2_genus/designs.json", "registry_sha256")):
            payload = subprocess.check_output(["git", "show", f"{commit}:{path}"],
                                              cwd=ROOT)
            self.assertEqual(sha(payload), contract["genus"][field])


if __name__ == "__main__":
    unittest.main()
