#!/usr/bin/env python3
"""Non-EDA contract and mutation tests for W2 immutable activity."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
ACTIVITY = REPO / "physical/k2_w2_5ns_common_activity"
STAGED = Path("/tmp/k2-phys-w2-techmap")

spec = importlib.util.spec_from_file_location("w2_activity_lib_test", ACTIVITY / "activity_lib.py")
assert spec is not None and spec.loader is not None
lib = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lib
spec.loader.exec_module(lib)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = lib.load_registry(REPO)

    def test_registry_repository_and_staged_closure(self) -> None:
        lib.verify_repository_inputs(REPO, self.registry)
        if not STAGED.is_dir():
            self.skipTest("runtime staged worktree is not mounted")
        closures = lib.verify_staged_inputs(STAGED, self.registry)
        self.assertEqual(set(closures), {"fovea", "a2", "a3"})
        self.assertTrue(all(closures.values()))

    def test_exact_candidate_and_clock_contract(self) -> None:
        self.assertEqual(set(self.registry["candidates"]), {"fovea", "a2", "a3"})
        self.assertEqual(
            self.registry["clock"],
            {
                "ref_period_ps": 5000,
                "sample_period_ps": 5000,
                "sample_phase_ps": 1250,
                "clock_source": "tb_only_bound_force",
                "physical_clock_claim": False,
            },
        )
        expected = {
            "fovea": ("w2_fovea_r1_physical_staging_top", "fovea_a7"),
            "a2": ("w2_a2_p6_physical_staging_top", "a2_p6"),
            "a3": ("w2_a3_p6_physical_staging_top", "a3_p6"),
        }
        for name, (top, candidate_id) in expected.items():
            binding = (REPO / self.registry["candidates"][name]["binding"]).read_text()
            self.assertIn(f"{top} dut", binding)
            self.assertIn(f'.CANDIDATE_ID("{candidate_id}")', binding)
            self.assertIn("#3.75 sample_clk_i = 1'b1", binding)
            self.assertIn("forever #2.5 sample_clk_i", binding)
            self.assertNotIn("logic [ADDR_WIDTH-1:0] queue", binding)

    def test_tb_filelists_have_exact_order(self) -> None:
        for name, row in self.registry["candidates"].items():
            lines = (REPO / row["tb_filelist"]).read_text().splitlines()
            self.assertEqual(
                lines,
                [
                    "tb/clean/aer_bench_if.sv",
                    "physical/k2_w2_5ns_common_activity/tb/w2_5ns_clock_override.sv",
                    "physical/k2_w2_5ns_common_activity/tb/w2_activity_probe.sv",
                    row["binding"],
                    "tb/clean/aer_clean_assertions.sv",
                    "tb/clean/aer_clean_tb.sv",
                ],
                name,
            )

    def test_tb_probe_is_observational_and_exact_scope(self) -> None:
        probe = (ACTIVITY / "tb/w2_activity_probe.sv").read_text()
        clock = (ACTIVITY / "tb/w2_5ns_clock_override.sv").read_text()
        self.assertIn("$dumpvars(0, aer_clean_tb.candidate.dut)", probe)
        self.assertIn("W2_ACTIVITY_REF_PERIOD_NOT_5NS", probe)
        self.assertIn("W2_ACTIVITY_SAMPLE_PHASE_NOT_1P25NS", probe)
        self.assertNotIn("force", probe)
        self.assertIn("force aer_clean_tb.clk = ref_clk_5ns", clock)
        frozen = (REPO / "tb/clean/aer_clean_tb.sv").read_text()
        self.assertIn("always #5 clk = ~clk", frozen)

    def test_producer_has_no_physical_flow_or_vectorless_path(self) -> None:
        source = (ACTIVITY / "produce_activity.py").read_text().lower()
        self.assertNotIn("genus", source)
        self.assertNotIn("innovus", source)
        self.assertNotIn("synthetic_activity", source)
        self.assertIn('"vectorless": false', source)
        self.assertEqual(source.count("subprocess.run("), 1)

    def test_registry_mutations_fail_closed(self) -> None:
        for mutation in ("candidate", "clock", "vectorless"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path = root / lib.REGISTRY_RELATIVE
                path.parent.mkdir(parents=True)
                registry = copy.deepcopy(self.registry)
                if mutation == "candidate":
                    registry["candidates"]["extra"] = registry["candidates"]["a2"]
                elif mutation == "clock":
                    registry["clock"]["ref_period_ps"] = 10000
                else:
                    registry["forbidden_modes"].remove("vectorless")
                path.write_text(json.dumps(registry))
                with self.assertRaises(lib.ActivityError):
                    lib.load_registry(root)

    def test_exclusive_write_rejects_overwrite_and_symlink_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "receipt"
            lib.write_exclusive(target, b"first")
            with self.assertRaises(lib.ActivityError):
                lib.write_exclusive(target, b"second")
            lib.seal_receipt(target)
            lib.require_sealed_receipt(target)
            target.chmod(0o644)
            with self.assertRaises(lib.ActivityError):
                lib.require_sealed_receipt(target)
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(lib.ActivityError):
                lib.stable_bytes(link)

    def test_artifact_record_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "actual.log"
            target.write_bytes(b"observed bytes\n")
            record = lib.artifact(target, root)
            lib.verify_artifact(root, record)
            for field, value in (
                ("sha256", "0" * 64),
                ("size_bytes", record["size_bytes"] + 1),
                ("path", "../escape"),
            ):
                mutated = {**record, field: value}
                with self.subTest(field=field), self.assertRaises(lib.ActivityError):
                    lib.verify_artifact(root, mutated)


class SuiteIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = lib.load_registry(REPO)
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.official = lib.load_official(REPO, cls.registry)
        cls.generated = {}
        for suite, identity in cls.registry["official_suites"].items():
            output = cls.root / suite
            subprocess.run(
                [
                    sys.executable,
                    str(REPO / "benchmarks/clean_slate_aer/generate_trace.py"),
                    "--manifest",
                    str(REPO / identity["manifest"]),
                    "--output-dir",
                    str(output),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TMPDIR": os.environ.get("TMPDIR", "/dev/shm")},
            )
            cls.generated[suite] = lib.validate_generation(
                output, suite, REPO / identity["manifest"], cls.official
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_full50_and_capacity22_exact_identity(self) -> None:
        self.assertEqual(len(self.generated["full50"]), 50)
        self.assertEqual(len(self.generated["capacity22"]), 22)
        proof = lib.prove_capacity_subset(
            self.generated["full50"], self.generated["capacity22"], self.official
        )
        self.assertEqual(proof["additional_executions"], 0)
        self.assertEqual(list(proof["trace_sha256"]), list(self.official.CAPACITY22))

    def test_capacity_trace_byte_mutation_is_rejected(self) -> None:
        name = next(iter(self.generated["capacity22"]))
        mutated = copy.deepcopy(self.generated["capacity22"])
        target = self.root / "mutated.jsonl"
        shutil.copyfile(mutated[name]["trace"], target)
        target.write_bytes(target.read_bytes() + b"\n")
        mutated[name]["trace"] = target
        with self.assertRaises(lib.ActivityError):
            lib.prove_capacity_subset(self.generated["full50"], mutated, self.official)

    def test_capacity_order_mutation_is_rejected(self) -> None:
        reversed_subset = dict(reversed(list(self.generated["capacity22"].items())))
        with self.assertRaises(lib.ActivityError):
            lib.prove_capacity_subset(
                self.generated["full50"], reversed_subset, self.official
            )

    def test_activity_workload_is_same_bytes_in_both_suites(self) -> None:
        contract = self.registry["activity_workload"]
        for suite in ("full50", "capacity22"):
            row = self.generated[suite][contract["name"]]
            self.assertEqual(lib.digest(row["trace"]), contract["trace_sha256"])
            self.assertEqual(lib.digest(row["manifest"]), contract["run_manifest_sha256"])


class EvidenceMutationTests(unittest.TestCase):
    def test_window_exact_duration_and_mutation(self) -> None:
        good = (
            "schema=w2_5ns_activity_window_v1\n"
            "candidate=a2_p6\n"
            "scope=aer_clean_tb.candidate.dut\n"
            "start_tick_1ps=100\n"
            "end_tick_1ps=20485100\n"
            "ref_period_ps=5000\n"
            "sample_period_ps=5000\n"
            "sample_phase_ps=1250\n"
            "ref_rises=4097\n"
            "sample_rises=4097\n"
            "accepted_edges=2\n"
            "retired_edges=2\n"
            "drain_idle_at_window_end=0\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "window"
            path.write_text(good)
            observed = lib.validate_window(path, "a2_p6", 4096)
            self.assertEqual(observed["duration_tick_1ps"], 20_485_000)
            path.write_text(good.replace("sample_phase_ps=1250", "sample_phase_ps=0"))
            with self.assertRaises(lib.ActivityError):
                lib.validate_window(path, "a2_p6", 4096)

    def test_vcd_to_real_per_bit_saif_and_mutations(self) -> None:
        header = (
            "$timescale 1 ps $end\n"
            "$scope module aer_clean_tb $end\n"
            "$scope module candidate $end\n"
            "$scope module dut $end\n"
            "$var wire 1 ! active $end\n"
            "$upscope $end\n$upscope $end\n$upscope $end\n"
            "$enddefinitions $end\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            raw_vcd = root / "raw.vcd"
            vcd = root / "activity.vcd"
            saif = root / "activity.saif"
            raw_vcd.write_text(header + "#100\n0!\n#110\n1!\n#120\n0!\n#130\n")
            lib.rebase_vcd(raw_vcd, vcd, 100, 130)
            stats = lib.vcd_to_saif(vcd, saif, "UNIT_TEST_ONLY")
            self.assertEqual(stats, {"duration_tick_1ps": 30, "net_bits": 1, "transitions": 2})
            self.assertIn("(TC 2)", saif.read_text())

            unknown = root / "unknown.vcd"
            unknown.write_text(header + "#0\nx!\n#30\n")
            with self.assertRaises(lib.ActivityError):
                lib.vcd_to_saif(unknown, root / "unknown.saif", "UNIT_TEST_ONLY")

            outside = root / "outside.vcd"
            outside.write_text(header.replace("dut", "not_dut") + "#0\n0!\n#30\n")
            with self.assertRaises(lib.ActivityError):
                lib.vcd_to_saif(outside, root / "outside.saif", "UNIT_TEST_ONLY")

    def test_summary_conservation_mutation(self) -> None:
        csv = (
            "candidate,test,generated,source_overrun,accepted,delivered,errors,measurement_delivered,measurement_cycles\n"
            "a3_p6,mixed_phase_always_ready_identity,10,2,8,8,0,7,4096\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "summary.csv"
            path.write_text(csv)
            lib.parse_summary(path, "a3_p6")
            path.write_text(csv.replace(",10,2,8,8,", ",11,2,8,8,"))
            with self.assertRaises(lib.ActivityError):
                lib.parse_summary(path, "a3_p6")


if __name__ == "__main__":
    unittest.main()
