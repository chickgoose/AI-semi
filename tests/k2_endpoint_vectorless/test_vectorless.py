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
FLOW = ROOT / "physical/k2_endpoint_vectorless/vectorless.py"
SPEC = importlib.util.spec_from_file_location("k2_endpoint_vectorless", FLOW)
assert SPEC and SPEC.loader
vectorless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vectorless)


def write(path: Path, payload: bytes) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"path": path.name, "sha256": hashlib.sha256(payload).hexdigest()}


def valid_environment(contract: dict) -> dict:
    tool = contract["tool"]
    technology = contract["technology"]
    environment = {
        "schema": "k2_w2_server_env_result_v1",
        "contract_sha256": contract["reuse"]["server_environment_contract"]["sha256"],
        "qualification_status": "PROVEN_ENVIRONMENT",
        "campaign_launch_allowed": True,
        "unresolved_environment_evidence": [],
        "gates": {
            "source_archives": {"status": "PROVEN", "evidence": {}},
            "tool_executables": {"status": "PROVEN", "evidence": {
                "genus": {"path": tool["observed_path"],
                          "resolved_path": tool["resolved_path"],
                          "sha256": tool["sha256"],
                          "parsed_version": tool["version"]},
            }},
            "technology_files": {"status": "PROVEN", "evidence": {
                "setup_liberty": {"sha256": technology["setup_liberty"]["sha256"]},
                "hold_liberty": {"sha256": technology["hold_liberty"]["sha256"]},
                "macro_lef": {"sha256": technology["macro_lef_sha256"]},
                "setup_qrc": {"sha256": technology["shared_typical_qrc_sha256"]},
                "hold_qrc": {"sha256": technology["shared_typical_qrc_sha256"]},
            }},
            "library_semantics": {"status": "PROVEN", "evidence": {}},
            "site_and_cell_availability": {"status": "PROVEN", "evidence": {}},
            "rc_policy": {"status": "PROVEN_WITH_LIMITATION", "evidence": {}},
        },
    }
    vectorless.server_preflight.finalize_result(environment)
    return environment


def build_valid_evidence(root: Path) -> Path:
    contract = vectorless.expected_contract()
    registry = vectorless.validate_contract(ROOT, contract)
    evidence_root = root / "evidence"
    evidence_root.mkdir()
    environment_payload = vectorless.canonical(valid_environment(contract))
    environment = write(evidence_root / "server-environment.json", environment_payload)
    driver_payload = vectorless.derived_driver(ROOT, contract)
    rows = []
    for index, key in enumerate(contract["candidate_order"]):
        design = registry["designs"][key]
        top = design["top"]
        prefix = f"{index}-{key}"
        driver = write(evidence_root / f"{prefix}-driver.tcl", driver_payload)
        sdc_payload = vectorless.run_genus.materialize_sdc(
            ROOT, design, registry["selected_timing_cohort"])
        sdc = write(evidence_root / f"{prefix}-constraints.sdc", sdc_payload)
        report_payload = (f"""Instance: /{top}
Power Unit: W
    Category         Leakage     Internal    Switching        Total
    Subtotal     1.00000e-09  1.00000e-06  1.00000e-06  2.00100e-06
""").encode()
        report = write(evidence_root / f"{prefix}-gpower.rpt", report_payload)
        log = write(evidence_root / f"{prefix}-genus.log",
                    f"Version: 23.14-s090_1\nW2_GENUS_PASS top={top}\n".encode())
        attempt_document = {
            "schema": "k2_w2_genus_exact_three_endpoint_attempt_v3",
            "design": key, "top": top,
            "boundary_cohort": design["boundary_cohort"],
            "ranking_policy": registry["ranking_policy"],
            "staged_manifest": registry["staged_manifest_identity"],
            "timing_cohort": registry["selected_timing_cohort"],
            "driver_tcl_sha256": driver["sha256"],
            "constraints_sha256": sdc["sha256"],
            "library_source_sha256": contract["technology"]["setup_liberty"]["sha256"],
            "hold_library_sha256": contract["technology"]["hold_liberty"]["sha256"],
            "cell_lef_sha256": contract["technology"]["macro_lef_sha256"],
            "shared_typical_qrc_sha256":
                contract["technology"]["shared_typical_qrc_sha256"],
            "genus": {
                "requested_path": contract["tool"]["observed_path"],
                "resolved_path": contract["tool"]["resolved_path"],
                "sha256": contract["tool"]["sha256"],
                "parsed_version": contract["tool"]["version"],
            },
        }
        attempt_payload = vectorless.canonical(attempt_document)
        attempt = write(evidence_root / f"{prefix}-attempt.json", attempt_payload)
        receipt_document = {
            "schema": "k2_w2_genus_exact_three_endpoint_receipt_v3",
            "status": "PASS_EXACT_THREE_ENDPOINT_GENUS_TIMING_POWER_HOLD",
            "design": key, "top": top,
            "boundary_cohort": design["boundary_cohort"],
            "ranking_policy": registry["ranking_policy"],
            "attempt_sha256": attempt["sha256"],
            "staged_manifest": registry["staged_manifest_identity"],
            "timing_cohort": registry["selected_timing_cohort"],
            "materialized_sdc_sha256": sdc["sha256"],
            "report_sha256": {f"{top}_gpower.rpt": report["sha256"]},
            "mapped_inventory": {"mapped_netlist_sha256":
                                 hashlib.sha256(key.encode()).hexdigest()},
        }
        receipt = write(evidence_root / f"{prefix}-receipt.json",
                        vectorless.canonical(receipt_document))
        rows.append({
            "design": key, "top": top, "attempt": attempt, "receipt": receipt,
            "power_report": report, "driver_tcl": driver,
            "constraints_sdc": sdc, "genus_log": log,
            "server_environment": environment,
        })
    evidence = {
        "schema": "k2_endpoint_vectorless_evidence_v1",
        "execution_class": "REAL_SERVER_CADENCE",
        "evidence_class": contract["evidence_class"],
        "candidate_order": contract["candidate_order"],
        "comparability_policy": contract["comparability_policy"],
        "bindings": vectorless.contract_identity(ROOT, contract, registry),
        "rows": rows,
    }
    evidence_path = evidence_root / "vectorless-evidence.json"
    evidence_path.write_bytes(vectorless.canonical(evidence))
    return evidence_path


class VectorlessContractTest(unittest.TestCase):
    def test_contract_reuses_existing_flow_and_exact_three_endpoints(self) -> None:
        contract = vectorless.expected_contract()
        registry = vectorless.validate_contract(ROOT, contract)
        self.assertEqual(registry["goal_order"], ["fovea_a7", "a2_p6", "a3_p6"])
        self.assertEqual(registry["selected_timing_cohort"]["period_ns"], 6.5)
        self.assertEqual(contract["technology"]["power_corner"],
                         {"voltage_v": 0.9, "temperature_c": 125.0})
        self.assertEqual(contract["reuse"]["genus_runner"]["path"],
                         "physical/k2_w2_genus/run_genus.py")

    def test_derived_driver_adds_only_vectorless_assumptions(self) -> None:
        contract = vectorless.expected_contract()
        base = (ROOT / contract["reuse"]["base_driver"]["path"]).read_bytes()
        derived = vectorless.derived_driver(ROOT, contract)
        stanza = vectorless.switching_stanza(contract)
        self.assertEqual(derived, base.replace(
            b"report_power  > $OUT_DIR/${DESIGN}_gpower.rpt\n",
            stanza + b"report_power  > $OUT_DIR/${DESIGN}_gpower.rpt\n"))
        self.assertEqual(derived.count(b"report_power  >"), 1)
        self.assertNotIn(b"read_vcd", derived.lower())
        self.assertNotIn(b"read_saif", derived.lower())
        for port in (b"ref_clk_i", b"sample_clk_i", b"source_pending_i", b"rst_n"):
            self.assertIn(port, stanza)

    def test_mutated_comparability_and_toggle_contracts_reject(self) -> None:
        mutations = []
        period = copy.deepcopy(vectorless.expected_contract())
        period["period_ns"] = 5.7
        mutations.append(period)
        toggle = copy.deepcopy(vectorless.expected_contract())
        toggle["vectorless_assumptions"]["source_pending_i"][
            "toggle_rate_per_period"] = 0.1
        mutations.append(toggle)
        roster = copy.deepcopy(vectorless.expected_contract())
        roster["candidate_order"] = ["a2_p6", "a3_p6"]
        mutations.append(roster)
        for mutated in mutations:
            with self.subTest(mutated=mutated), self.assertRaises(
                    vectorless.VectorlessError):
                vectorless.validate_contract(ROOT, mutated)

    def test_preflight_is_cadence_free_honest_hold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "preflight.json"
            result = subprocess.run(
                ["python3", str(FLOW), "preflight", "--repo-root", str(ROOT),
                 "--output", str(output)], text=True, capture_output=True,
                check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["status"], "HOLD_NO_REAL_SERVER_ARTIFACTS")
            self.assertFalse(receipt["comparison_ready"])
            self.assertFalse(receipt["candidate_go"])
            self.assertIn("Cadence was not invoked", receipt["reason"])
            self.assertEqual(receipt["candidate_order"],
                             ["fovea_a7", "a2_p6", "a3_p6"])


class PowerReportTest(unittest.TestCase):
    TOP = "w2_a2_p6_physical_staging_top"

    def report(self, extra: str = "") -> bytes:
        return (f"""Instance: /{self.TOP}
Power Unit: W
    Category         Leakage     Internal    Switching        Total
    Subtotal     1.00000e-09  1.00000e-06  1.00000e-06  2.00100e-06
{extra}""").encode()

    def test_parses_vectorless_genus_power(self) -> None:
        result = vectorless.parse_power_report(self.report(), self.TOP)
        self.assertAlmostEqual(result["total_mw"], 0.002001)
        self.assertAlmostEqual(result["leakage_mw"], 0.000001)

    def test_rejects_vcd_saif_and_user_activity(self) -> None:
        reports = (
            self.report("* Activity File: waves/run.vcd\n"),
            self.report("read_saif waves/run.saif\n"),
            self.report("* User-Defined Activity : Imported\n"),
        )
        for report in reports:
            with self.subTest(report=report), self.assertRaisesRegex(
                    vectorless.VectorlessError, "activity|Activity|VCD|SAIF"):
                vectorless.parse_power_report(report, self.TOP)

    def test_rejects_wrong_top_units_and_bad_sum(self) -> None:
        wrong_top = self.report().replace(self.TOP.encode(), b"wrong_top")
        wrong_unit = self.report().replace(b"Power Unit: W", b"Power Unit: mW")
        bad_sum = self.report().replace(b"2.00100e-06", b"9.00000e-06")
        for report in (wrong_top, wrong_unit, bad_sum):
            with self.subTest(report=report), self.assertRaises(
                    vectorless.VectorlessError):
                vectorless.parse_power_report(report, self.TOP)

    def test_contract_and_driver_hashes_are_current(self) -> None:
        contract = vectorless.expected_contract()
        for row in contract["reuse"].values():
            payload = (ROOT / row["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), row["sha256"])


class OfflineQualifierTest(unittest.TestCase):
    def test_complete_three_candidate_fixture_qualifies_without_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = build_valid_evidence(root)
            output = root / "qualification.json"
            vectorless.qualify(evidence, output)
            result = json.loads(output.read_text())
            self.assertEqual(result["status"], "QUALIFIED_VECTORLESS_POWER")
            self.assertTrue(result["comparison_ready"])
            self.assertFalse(result["activity_annotated"])
            self.assertFalse(result["activity_power_eligible"])
            self.assertEqual([row["design"] for row in result["rows"]],
                             ["fovea_a7", "a2_p6", "a3_p6"])

    def test_candidate_sdc_mismatch_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_path = build_valid_evidence(root)
            evidence = json.loads(evidence_path.read_text())
            row = evidence["rows"][1]
            sdc_path = evidence_path.parent / row["constraints_sdc"]["path"]
            payload = sdc_path.read_bytes() + b"# mutation\n"
            sdc_path.write_bytes(payload)
            row["constraints_sdc"]["sha256"] = hashlib.sha256(payload).hexdigest()
            evidence_path.write_bytes(vectorless.canonical(evidence))
            with self.assertRaisesRegex(vectorless.VectorlessError,
                                        "SDC/settings"):
                vectorless.qualify(evidence_path, root / "qualification.json")


if __name__ == "__main__":
    unittest.main()
