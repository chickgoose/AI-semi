#!/usr/bin/env python3
"""Mutation tests for the elaborated REDRED single-edge CDC/RDC contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts/redred_single_edge_cdc_rdc"
VERIFY_PATH = CONTRACT_DIR / "verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_cdc_verifier", VERIFY_PATH)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class SingleEdgeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.binding = verifier.load_json(CONTRACT_DIR / "source_binding.json")
        cls.verilator = verifier.find_verilator(None)
        cls.commit = cls.binding["repository_commit"]
        cls.blobs = {
            item["path"]: verifier.git_blob(ROOT, cls.commit, item["path"])
            for item in cls.binding["files"]
        }

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(VERIFY_PATH), *arguments], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )

    def write_json(self, directory: Path, name: str, document: object) -> Path:
        path = directory / name
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def analyze_mutant(self, replacements: dict[str, tuple[bytes, bytes]],
                       design_name: str = "a2") -> None:
        with tempfile.TemporaryDirectory(prefix="redred-cdc-mutant-") as directory:
            work = Path(directory)
            sources = []
            for index, (relative, original) in enumerate(self.blobs.items()):
                data = original
                if relative in replacements:
                    old, new = replacements[relative]
                    self.assertEqual(data.count(old), 1, f"mutation anchor {old!r}")
                    data = data.replace(old, new, 1)
                path = work / f"{index:02d}_{Path(relative).name}"
                path.write_bytes(data)
                sources.append(path)
            design = verifier.parse_design(
                self.binding["designs"][design_name], f"designs.{design_name}")
            parsed, _ = verifier.elaborate(
                self.verilator, ROOT, sources, design, work)
            parsed.validate_hierarchy(design)

    def assert_mutant_fails(self, replacements: dict[str, tuple[bytes, bytes]],
                            pattern: str, design_name: str = "a2") -> None:
        with self.assertRaisesRegex(verifier.ContractError, pattern):
            self.analyze_mutant(replacements, design_name)

    def test_canonical_pinned_a2_a3_pass(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('"repository_commit": "a57943adba759fc955b4506e99703c1dd9736fba"',
                      result.stdout)
        self.assertIn('"integration_commit": "a57943adba759fc955b4506e99703c1dd9736fba"',
                      result.stdout)
        self.assertIn('"reset_assertion_precondition": "drain_idle_o == 1"',
                      result.stdout)
        self.assertIn("REDRED_SINGLE_EDGE_CDC_RDC_PASS designs=a2,a3 domains=1",
                      result.stdout)
        self.assertNotIn("_HOLD reason=", result.stdout)

    def test_explicit_unbound_alternate_contract_holds_without_pass(self) -> None:
        document = verifier.load_json(CONTRACT_DIR / "contract.json")
        document["decision"] = verifier.HOLD
        document["a2_source_set"] = None
        with tempfile.TemporaryDirectory() as directory:
            contract = self.write_json(Path(directory), "contract.json", document)
            result = self.run_cli("--contract", str(contract))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("REDRED_SINGLE_EDGE_CDC_RDC_HOLD reason=A2_SOURCE_SET_UNBOUND",
                      result.stdout)
        self.assertNotIn("REDRED_SINGLE_EDGE_CDC_RDC_PASS", result.stdout)

    def test_fake_contract_pass_decision_fails(self) -> None:
        document = verifier.load_json(CONTRACT_DIR / "contract.json")
        document["decision"] = "PASS"
        with tempfile.TemporaryDirectory() as directory:
            contract = self.write_json(Path(directory), "contract.json", document)
            result = self.run_cli("--contract", str(contract))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bound contract decision must require verification", result.stdout)

    def test_stale_source_hash_fails_before_elaboration(self) -> None:
        document = copy.deepcopy(self.binding)
        document["files"][0]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            binding = self.write_json(Path(directory), "binding.json", document)
            result = self.run_cli("--binding", str(binding))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binding document SHA-256 differs", result.stdout)
        self.assertNotIn("REDRED_SINGLE_EDGE_CDC_RDC_PASS", result.stdout)

    def test_wrong_or_missing_commit_fails(self) -> None:
        document = copy.deepcopy(self.binding)
        document["repository_commit"] = "0" * 40
        with tempfile.TemporaryDirectory() as directory:
            binding = self.write_json(Path(directory), "binding.json", document)
            result = self.run_cli("--binding", str(binding))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("binding document SHA-256 differs", result.stdout)

    def test_path_traversal_and_duplicate_sources_fail(self) -> None:
        for mutate, pattern in (
            (lambda doc: doc["files"][0].__setitem__("path", "../escape.sv"),
             "unsafe source path"),
            (lambda doc: doc["files"].append(copy.deepcopy(doc["files"][0])),
             "duplicate source path"),
        ):
            document = copy.deepcopy(self.binding)
            mutate(document)
            with tempfile.TemporaryDirectory() as directory:
                binding = self.write_json(Path(directory), "binding.json", document)
                result = self.run_cli("--binding", str(binding))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("binding document SHA-256 differs", result.stdout)

    def test_unknown_module_fails_elaboration(self) -> None:
        path = "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv"
        self.assert_mutant_fails(
            {path: (b"w2_single_edge_exact_pair_endpoint endpoint (",
                    b"unknown_single_edge_endpoint endpoint (")},
            "unknown module")

    def test_negedge_process_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"always_ff @(posedge clk_i)", b"always_ff @(negedge clk_i)")},
            "sequential event must be direct posedge clock input")

    def test_async_reset_event_control_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"always_ff @(posedge clk_i)",
                    b"always_ff @(posedge clk_i or posedge rst_i)")},
            "sequential event must be direct posedge clock input")

    def test_gated_clock_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"always_ff @(posedge clk_i)",
                    b"wire gated_clk = clk_i & link_enable_i;\n"
                    b"  always_ff @(posedge gated_clk)")},
            "sequential event must be direct posedge clock input")

    def test_forwarded_child_clock_alias_fails_source_check(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv"
        self.assert_mutant_fails(
            {path: (b"w2_single_edge_pair_tx tx (\n    .clk_i,",
                    b"wire forwarded_clk = clk_i;\n\n"
                    b"  w2_single_edge_pair_tx tx (\n    .clk_i(forwarded_clk),")},
            "clock port connection is derived/forwarded")

    def test_explicit_latch_fails_source_check(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"logic protocol_error_q;",
                    b"logic protocol_error_q;\n"
                    b"  always_latch protocol_error_q <= protocol_error_q;")},
            "latch process is forbidden")

    def test_second_clock_domain_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"always_ff @(posedge clk_i)",
                    b"always_ff @(posedge link_enable_i)")},
            "sequential event must be direct posedge clock input")

    def test_forwarded_clock_use_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"assign input_ready_o = !rst_i",
                    b"assign input_ready_o = clk_i && !rst_i")},
            "clock clk_i is used as data")

    def test_missing_synchronous_reset_guard_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_rx.sv"
        self.assert_mutant_fails(
            {path: (b"if (rst_i) begin", b"if (link_valid_i) begin")},
            "reset|inconsistent")

    def test_tx_valid_reset_must_be_quiescent(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"link_valid_o <= 1'b0;", b"link_valid_o <= 1'b1;")},
            "TX valid is not reset to quiescent zero")

    def test_tx_must_be_registered(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_pair_tx.sv"
        self.assert_mutant_fails(
            {path: (b"always_ff @(posedge clk_i) begin",
                    b"assign link_valid_o = input_commit_o;\n"
                    b"  always_ff @(posedge clk_i) begin")},
            "TX port link_valid_o has a combinational driver")

    def test_rx_bypass_fails(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv"
        self.assert_mutant_fails(
            {path: (b".link_valid_i(link_valid_o)",
                    b".link_valid_i(input_count_i[0])")},
            "does not share one direct net")

    def test_drain_must_cover_rx_pending_state(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv"
        self.assert_mutant_fails(
            {path: (b"!link_valid_o &&\n                        (retire_valid_o == 2'b00)",
                    b"!link_valid_o")},
            "endpoint drain omits TX/RX in-flight state")

    def test_drain_or_logic_cannot_weaken_empty_requirement(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv"
        self.assert_mutant_fails(
            {path: (b"!link_valid_o &&\n                        (retire_valid_o == 2'b00)",
                    b"!link_valid_o || (retire_valid_o == 2'b00)")},
            "endpoint drain can assert with in-flight state")

    def test_clean_drain_requires_no_protocol_error(self) -> None:
        path = "rtl/technology/single_edge/w2_single_edge_exact_pair_endpoint.sv"
        self.assert_mutant_fails(
            {path: (b"!protocol_error_o && !link_valid_o &&",
                    b"!link_valid_o &&")},
            "endpoint clean drain does not require !protocol_error_o")

    def test_top_drain_must_include_endpoint(self) -> None:
        path = "rtl/candidates/a2_batched_iwrr_single_edge/a2_batched_iwrr_single_edge_top.sv"
        self.assert_mutant_fails(
            {path: (b"scheduler_idle && !buffer_valid_q && endpoint_idle &&",
                    b"scheduler_idle && !buffer_valid_q &&")},
            "top drain does not depend on endpoint drain state")

    def test_binding_cannot_lie_about_primary_clock_or_roles(self) -> None:
        for field, value, pattern in (
            ("primary_clock", "link_enable_i", "unknown/second primary clock"),
            ("tx_instance", "rx", "TX and RX instances must be distinct"),
            ("scope_drain_port", "protocol_error_o", "bypass/fanout driver"),
        ):
            document = copy.deepcopy(self.binding)
            document["designs"]["a2"][field] = value
            with tempfile.TemporaryDirectory() as directory:
                binding = self.write_json(Path(directory), "binding.json", document)
                result = self.run_cli("--binding", str(binding))
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("binding document SHA-256 differs", result.stdout)


if __name__ == "__main__":
    unittest.main()
