#!/usr/bin/env python3
"""Executed equivalence and fail-closed selection gates for A9 W5."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RTL = ROOT / "rtl/candidates/a9_w5_ddr_technology_boundary"
TESTS = ROOT / "tests/a9_w5_ddr_technology_boundary"
A7_COMMIT = "42377ca81340951bfcd453b3bd664e673091f9f3"
A7_FILES = {
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_launch_qualifier.sv":
        "8b648695368116170d44bba10b633039a3a1e143c5959a2178800da510c66c7d",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_icg_boundary.sv":
        "0d6aaccc9105b302838ebb82730064b91de6831a3029cd38ccb095450aef2be9",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_tx.sv":
        "88e183d324e8569e4a081bb9bf501bf6ebddd9e4d46788d656b7ef07d4fa1197",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_ddr_rx.sv":
        "7e6b6fb4d85ce7490b0d6d3d9d631c590b45ae93b5cd61c75eb4335a28ca6d06",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_retire_observer.sv":
        "2a1086a1502aa57c589c9166debcc531ca042943159267ec3eac1c644432474f",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint.sv":
        "c689b3307559c633eed4ad44ff1242b5761fa41516ca1427f5fd3f47a4281b03",
    "rtl/candidates/a7_r1_candidate_endpoint/a7_r1_parallel_reference_top.sv":
        "151046ee203e9e667726c7279704b297fb6d19696673e43b8d63e6ab418f0748",
    "tb/candidates/a7_r1_candidate_endpoint/a7_r1_candidate_endpoint_tb.sv":
        "b3af920caa6e7242820f1428bf655045fcf2bd8911e4e41c5ae92d4b0c87e950",
}
W5_SOURCES = [
    RTL / "a9_w5_launch_qualifier.sv",
    RTL / "a9_w5_retire_observer.sv",
    RTL / "a9_w5_clock_gate.sv",
    RTL / "a9_w5_tx_launch.sv",
    RTL / "a9_w5_rx_capture.sv",
    RTL / "a9_w5_ddr_tx_endpoint.sv",
    RTL / "a9_w5_ddr_rx_endpoint.sv",
    RTL / "a9_w5_ddr_link.sv",
]


def find_tool(env_name: str, name: str) -> str:
    override = os.environ.get(env_name)
    candidates = [override, shutil.which(name), f"/tmp/a7-sim-bin/{name}"]
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_file():
            return str(pathlib.Path(candidate).resolve())
    raise RuntimeError(f"required {name} not found; set {env_name}")


class TechnologyBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.iverilog = find_tool("IVERILOG", "iverilog")
        cls.vvp = find_tool("VVP", "vvp")
        cls.git = shutil.which("git")
        if not cls.git:
            raise RuntimeError("git is required to bind the exact A7 reference")
        cls.a7_repo = pathlib.Path(
            os.environ.get("A7_REPO", str(ROOT.parent / "a7"))
        ).resolve()
        if not (cls.a7_repo / ".git").exists():
            raise RuntimeError(f"A7_REPO is not a git worktree: {cls.a7_repo}")

    def materialize_a7(self, output: pathlib.Path) -> list[pathlib.Path]:
        materialized = []
        for index, (repo_path, expected_hash) in enumerate(A7_FILES.items()):
            result = subprocess.run(
                [self.git, "-C", str(self.a7_repo), "show",
                 f"{A7_COMMIT}:{repo_path}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            actual_hash = hashlib.sha256(result.stdout).hexdigest()
            self.assertEqual(actual_hash, expected_hash, repo_path)
            target = output / f"a7_exact_{index}_{pathlib.Path(repo_path).name}"
            target.write_bytes(result.stdout)
            materialized.append(target)
        return materialized

    def compile(self, work: pathlib.Path, defines: list[str], extras: list[pathlib.Path],
                top: str, expect_success: bool) -> subprocess.CompletedProcess[str]:
        command = [
            self.iverilog, "-g2012", "-I", str(RTL),
            *[f"-D{define}" for define in defines],
            "-s", top, "-o", str(work / "simv"),
            *[str(path) for path in extras],
            *[str(path) for path in W5_SOURCES],
        ]
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        if expect_success and result.returncode != 0:
            self.fail(f"compile unexpectedly failed:\n{result.stdout}")
        if not expect_success and result.returncode == 0:
            self.fail("compile unexpectedly accepted an invalid technology closure")
        return result

    def run_equivalence(self, macro: str, mock: pathlib.Path | None) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-") as directory:
            work = pathlib.Path(directory)
            a7_sources = self.materialize_a7(work)
            extras = ([mock] if mock else []) + a7_sources + [TESTS / "a9_w5_equivalence_tb.sv"]
            self.compile(work, [macro], extras, "a9_w5_equivalence_tb", True)
            result = subprocess.run([self.vvp, str(work / "simv")], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("A9_W5_42377CA_EQUIVALENCE_PASS", result.stdout)

    def test_generic_matches_exact_a7(self) -> None:
        self.run_equivalence("A9_W5_TECH_GENERIC", None)

    def test_asic_adapter_contract_matches_exact_a7(self) -> None:
        self.run_equivalence("A9_W5_TECH_ASIC", TESTS / "mock_asic_cells.sv")

    def test_xilinx_primitive_contract_matches_exact_a7(self) -> None:
        self.run_equivalence(
            "A9_W5_TECH_XILINX_7SERIES", TESTS / "mock_xilinx_unisim.sv"
        )

    def test_bound_a7_production_regression(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-a7-production-") as directory:
            work = pathlib.Path(directory)
            sources = self.materialize_a7(work)
            command = [self.iverilog, "-g2012", "-s",
                       "a7_r1_candidate_endpoint_tb", "-o", str(work / "a7_simv"),
                       *[str(path) for path in sources]]
            compile_result = subprocess.run(
                command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout)
            run_result = subprocess.run(
                [self.vvp, str(work / "a7_simv")], text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            self.assertEqual(run_result.returncode, 0, run_result.stdout)
            self.assertIn("A7_R1_ENDPOINT_REGRESSION_PASS", run_result.stdout)

    def test_no_selection_fails_compile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-negative-") as directory:
            self.compile(pathlib.Path(directory), [], [], "a9_w5_ddr_link", False)

    def test_multiple_selection_fails_compile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-negative-") as directory:
            self.compile(pathlib.Path(directory),
                         ["A9_W5_TECH_GENERIC", "A9_W5_TECH_ASIC"],
                         [], "a9_w5_ddr_link", False)

    def test_asic_without_target_adapter_fails_compile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-negative-") as directory:
            self.compile(pathlib.Path(directory), ["A9_W5_TECH_ASIC"], [],
                         "a9_w5_ddr_link", False)

    def test_xilinx_without_unisim_fails_compile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a9-w5-negative-") as directory:
            self.compile(pathlib.Path(directory),
                         ["A9_W5_TECH_XILINX_7SERIES"], [],
                         "a9_w5_ddr_link", False)

    def test_manifest_filelists_and_constraint_boundary(self) -> None:
        manifest = json.loads((RTL / "a9_w5_mapping_manifest.json").read_text())
        self.assertEqual(manifest["reference"]["commit"], A7_COMMIT)
        self.assertEqual(
            manifest["reference"]["source_sha256"],
            {pathlib.Path(path).name: digest for path, digest in A7_FILES.items()},
        )
        self.assertTrue(manifest["selection"]["exactly_one_required"])
        self.assertFalse(manifest["physical_ppa_executed"])
        owner = manifest["owner_charged_generic_reference"]
        self.assertEqual(owner["ddr2"], {
            "pins": 3, "state_bits": 20, "charged_functional_cells": 29
        })
        self.assertEqual(owner["parallel4"], {
            "pins": 5, "state_bits": 18, "charged_functional_cells": 27
        })
        filelists = "\n".join(
            path.read_text() for path in sorted((RTL / "filelists").glob("*.f"))
        )
        self.assertNotIn("mock_asic_cells.sv", filelists)
        self.assertNotIn("mock_xilinx_unisim.sv", filelists)
        self.assertNotIn("a9_w5_equivalence_tb.sv", filelists)
        sdc = (ROOT / "constraints/a9_w5_ddr_technology_boundary.sdc").read_text()
        for required in (
            "create_generated_clock", "-clock_fall", "set_load",
            "A9_W5_DATA_PAD_LOAD", "A9_W5_CLOCK_PAD_LOAD",
            "A9_W5_RX_SETUP_BUDGET_NS", "A9_W5_RX_HOLD_BUDGET_NS",
            "A9_W5_REF_OUTPUT_DELAY_NS",
        ):
            self.assertIn(required, sdc)


if __name__ == "__main__":
    unittest.main()
