#!/usr/bin/env python3
"""Mutation tests for mapped/post-route single-edge CDC/RDC evidence."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts/redred_single_edge_mapped_cdc_rdc"
VERIFY_PATH = CONTRACT_DIR / "verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_mapped_cdc_verifier", VERIFY_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class MappedCdcContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = verifier.load_file(CONTRACT_DIR / "contract.json", "contract")[1]
        cls.binding = verifier.load_file(CONTRACT_DIR / "evidence_binding.json", "binding")[1]
        cls.semantics_doc = verifier.load_file(
            CONTRACT_DIR / "cell_semantics.json", "semantics")[1]
        cls.sequential, cls.combinational = verifier.validate_semantics(cls.semantics_doc)
        required = verifier.validate_binding(cls.binding)
        archive = cls.binding["archive"]
        cls.members = verifier.read_archive(
            ROOT / archive["path"], archive["size_bytes"], archive["sha256"], required)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(VERIFY_PATH), *arguments], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

    def analyze_a2_mapped(self, data: bytes) -> dict[str, object]:
        entry = self.binding["candidates"]["a2"]["mapped_netlist"]
        return verifier.analyze_netlist(
            data, self.binding["candidates"]["a2"]["top"], self.sequential,
            self.combinational, entry["instance_count"], entry["sequential_count"],
            "mutant:a2:mapped")

    def test_canonical_diagnostic_pass_cannot_promote_release(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('"mapped_cdc_rdc_diagnostic_status": "PASS"', result.stdout)
        self.assertIn('"final_cdc_rdc_gate": "HOLD"', result.stdout)
        self.assertIn('"producer_authenticated": false', result.stdout)
        self.assertIn('"setup_wns_ns": 0.0329976', result.stdout)
        self.assertIn('"total_power_mw": 0.07962095', result.stdout)
        self.assertIn('"setup_wns_ns": 0.0237889', result.stdout)
        self.assertIn('"total_power_mw": 0.06556542', result.stdout)
        self.assertIn(
            "REDRED_SINGLE_EDGE_MAPPED_CDC_RDC_DIAGNOSTIC_PASS_RELEASE_HOLD",
            result.stdout)

    def test_archive_byte_tamper_fails_before_analysis(self) -> None:
        original = (ROOT / self.binding["archive"]["path"]).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.tar"
            path.write_bytes(original + b"x")
            result = self.run_cli("--archive", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive byte identity differs", result.stdout)

    def test_gated_or_second_clock_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant = data.replace(b".CK (clk_i)", b".CK (n_0)", 1)
        self.assertNotEqual(mutant, data)
        with self.assertRaisesRegex(verifier.ContractError,
                                    "generated/gated/forwarded/second clock"):
            self.analyze_a2_mapped(mutant)

    def test_primary_clock_as_combinational_data_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant, count = re.subn(rb"\.A \([^)]+\)", b".A (clk_i)", data, count=1)
        self.assertEqual(count, 1)
        with self.assertRaisesRegex(verifier.ContractError, "primary clock used as data"):
            self.analyze_a2_mapped(mutant)

    def test_unknown_sequential_cell_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant = data.replace(b"DFFHQX1", b"DFFRHQX1", 1)
        self.assertNotEqual(mutant, data)
        with self.assertRaisesRegex(verifier.ContractError, "unknown or forbidden cell"):
            self.analyze_a2_mapped(mutant)

    def test_latch_cell_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant = data.replace(b"DFFHQX1", b"TLATX1", 1)
        self.assertNotEqual(mutant, data)
        with self.assertRaisesRegex(verifier.ContractError, "unknown or forbidden cell"):
            self.analyze_a2_mapped(mutant)

    def test_unknown_combinational_cell_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant = data.replace(b"INVX1", b"FAKECELL", 1)
        self.assertNotEqual(mutant, data)
        with self.assertRaisesRegex(verifier.ContractError, "unknown or forbidden cell"):
            self.analyze_a2_mapped(mutant)

    def test_unknown_sequential_pin_fails(self) -> None:
        data = self.members["a2/mapped.v"]
        mutant = data.replace(b".Q\n", b".QN\n", 1)
        if mutant == data:
            mutant = data.replace(b".Q (", b".QN (", 1)
        self.assertNotEqual(mutant, data)
        with self.assertRaisesRegex(verifier.ContractError, "unknown/missing sequential port"):
            self.analyze_a2_mapped(mutant)

    def test_async_control_semantics_are_rejected(self) -> None:
        document = copy.deepcopy(self.semantics_doc)
        document["sequential_cells"]["DFFHQX1"]["async_control_pins"] = ["RN"]
        with self.assertRaisesRegex(verifier.ContractError, "asynchronous controls are forbidden"):
            verifier.validate_semantics(document)

    def test_generated_clock_sdc_fails(self) -> None:
        data = self.members["a2/mapped.sdc"] + (
            b"\ncreate_generated_clock -name derived -source [get_ports clk_i] "
            b"[get_ports drain_idle_o]\n")
        with self.assertRaisesRegex(verifier.ContractError, "forbidden clock exception"):
            verifier.analyze_sdc(data, "a2-mutant")

    def test_second_primary_clock_sdc_fails(self) -> None:
        data = self.members["a2/mapped.sdc"] + (
            b"\ncreate_clock -name second -period 7.0 [get_ports rst_i]\n")
        with self.assertRaisesRegex(verifier.ContractError, "exactly one clock"):
            verifier.analyze_sdc(data, "a2-mutant")

    def test_instance_count_mismatch_fails(self) -> None:
        entry = self.binding["candidates"]["a2"]["mapped_netlist"]
        with self.assertRaisesRegex(verifier.ContractError, "instance count differs"):
            verifier.analyze_netlist(
                self.members[entry["member"]], self.binding["candidates"]["a2"]["top"],
                self.sequential, self.combinational, entry["instance_count"] + 1,
                entry["sequential_count"], "mutant:a2:mapped")

    def test_cohort_provenance_tamper_fails(self) -> None:
        members = dict(self.members)
        document = verifier.load_json_bytes(members["cohort/cohort.json"], "cohort")
        document["producer_authenticated"] = True
        members["cohort/cohort.json"] = json.dumps(document, sort_keys=True).encode()
        with self.assertRaisesRegex(verifier.ContractError, "cohort file hash differs"):
            verifier.verify_provenance(self.binding, members)

    def test_physical_power_report_tamper_fails(self) -> None:
        members = dict(self.members)
        member = self.binding["candidates"]["a2"]["physical_reports"]["power"]["member"]
        members[member] = members[member].replace(b"0.07962095", b"0.17962095", 1)
        with self.assertRaisesRegex(verifier.ContractError, "physical report hash differs"):
            verifier.verify_provenance(self.binding, members)

    def test_contract_cannot_raise_release_ceiling(self) -> None:
        document = copy.deepcopy(self.contract)
        document["maximum_decision"] = "PASS"
        with self.assertRaisesRegex(verifier.ContractError,
                                    "illegally changes the release ceiling"):
            verifier.validate_contract(document)


if __name__ == "__main__":
    unittest.main()
