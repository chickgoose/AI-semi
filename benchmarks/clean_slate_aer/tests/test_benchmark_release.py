from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_release


class BenchmarkReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "user.name", "Release Test")
        self.original_policy_hash = benchmark_release.TRUSTED_POLICY_SHA256
        self.addCleanup(
            setattr, benchmark_release, "TRUSTED_POLICY_SHA256",
            self.original_policy_hash,
        )
        self._write_fixture()
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.inputs = benchmark_release.ReleaseInputs(
            policy=benchmark_release.TRUSTED_POLICY_PATH,
            generator="bench/generator.py",
            preparer="bench/preparer.py",
            testbench="tb/clean_tb.sv",
            native_bindings=(
                "tb/clean/native/a7_parallel_event_compactor_binding.sv",
                "tb/clean/native/a7_replicated_selector_binding.sv",
                "tb/clean/native/aer_ganghee_cluster2_binding.sv",
                "tb/clean/native/aer_ganghee_native_binding.sv",
            ),
            ppa_registry="bench/ppa_registry.json",
            runners=("scripts/run_clean.sh", "scripts/run_capacity.sh"),
            full_manifest="bench/manifest.full.json",
            capacity_manifest="bench/manifest.capacity.json",
            golden="bench/golden.json",
            analyzers=("bench/analyzer.py",),
            test_receipts=("bench/receipts/self.json", "bench/receipts/neutrality.json"),
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def sha(self, relative: str) -> str:
        return hashlib.sha256((self.repo / relative).read_bytes()).hexdigest()

    @staticmethod
    def runs(count: int) -> list[dict[str, object]]:
        runs = [
            {
                "name": f"run_{index:02d}", "workload": "uniform",
                "seed": index + 1, "geometry": {"width": 4, "height": 4},
                "load": 0.5, "stim_cycles": 64, "parameters": {},
            }
            for index in range(count)
        ]
        runs[0].update(
            name="mixed_phase_always_ready_identity",
            workload="mixed_phase_always_ready",
        )
        runs[1].update(
            name="mixed_phase_always_ready_bit_reverse",
            workload="mixed_phase_always_ready",
        )
        return runs

    def artifact(self, path: str) -> dict[str, str]:
        return {"path": path, "sha256": self.sha(path)}

    def _write_fixture(self) -> None:
        full_runs = self.runs(50)
        self.write("bench/generator.py", 'GENERATOR_VERSION = "4.0"\n')
        self.write("bench/preparer.py", "# trusted v4 preparer fixture\n")
        self.write("tb/clean_tb.sv", "module clean_tb; endmodule\n")
        self.write("bench/analyzer.py", "def analyze(): return {}\n")
        self.write("scripts/run_clean.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write("scripts/run_capacity.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write("bench/manifest.full.json", json.dumps({"runs": full_runs}) + "\n")
        self.write(
            "bench/manifest.capacity.json",
            json.dumps({"runs": full_runs[:22]}) + "\n",
        )
        self.write(
            "bench/golden.json",
            json.dumps({
                "generator_version": "4.0", "suite": "manifest.full.json",
                "runs": [{"name": run["name"]} for run in full_runs],
            }) + "\n",
        )
        for name in (
            "a7_parallel_event_compactor_binding",
            "a7_replicated_selector_binding",
            "aer_ganghee_cluster2_binding",
            "aer_ganghee_native_binding",
        ):
            self.write(f"tb/clean/native/{name}.sv", f"module {name}; endmodule\n")
        self.write("rtl/design.sv", "module design; endmodule\n")
        for filelist in ("baseline.f", "a23_ee430.f", "a7_k4_structural.f"):
            self.write(f"tb/filelists/{filelist}", "rtl/design.sv\n")
        self.write(
            "tb/filelists/a7_parallel_event_compactor.f",
            "rtl/design.sv\n"
            "tb/clean/native/a7_parallel_event_compactor_binding.sv\n",
        )
        self.write("tb/clean/files.f", "rtl/design.sv\n")
        self.write("scripts/tool.tcl", "# trusted tool\n")
        self.write("scripts/tool.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write(
            "bench/self_test.py", "print('SELF_TEST_PASS fixture=1')\n"
        )
        self.write(
            "bench/neutrality_self_test.py",
            "print('NEUTRALITY_SELF_TEST_PASS fixture=1')\n",
        )
        self._write_registry()
        self._write_receipt(
            "bench/receipts/self.json", "self", "bench/self_test.py",
            b"SELF_TEST_PASS fixture=1\n", "SELF_TEST_PASS fixture=1",
        )
        self._write_receipt(
            "bench/receipts/neutrality.json", "neutrality",
            "bench/neutrality_self_test.py",
            b"NEUTRALITY_SELF_TEST_PASS fixture=1\n",
            "NEUTRALITY_SELF_TEST_PASS fixture=1",
        )
        self._write_policy()

    def _write_registry(self) -> None:
        contracts = benchmark_release.REQUIRED_PPA_CONTRACT
        filelists = {
            "baseline-n16": "tb/filelists/baseline.f",
            "a23-ee430-n16": "tb/filelists/a23_ee430.f",
            "a7-prefix-k4-n16": "tb/filelists/a7_k4_structural.f",
            "a7-replicated-k4-n16": "tb/filelists/a7_k4_structural.f",
        }
        candidates = []
        for name, (top, parameters, defines) in contracts.items():
            candidates.append({
                "name": name, "top": top, "parameters": parameters,
                "defines": defines, "filelist": self.artifact(filelists[name]),
                "tool_scripts": [
                    self.artifact("scripts/tool.tcl"),
                    self.artifact("scripts/tool.sh"),
                ],
                "sources": [self.artifact("rtl/design.sv")],
            })
        self.write(
            "bench/ppa_registry.json",
            json.dumps({
                "schema": "aer-candidate-ppa-registry-v1",
                "candidates": candidates,
            }, sort_keys=True) + "\n",
        )

    def _write_receipt(
        self, path: str, name: str, script: str, log: bytes, marker: str
    ) -> None:
        self.write(path, json.dumps({
            "schema": "aer-executed-test-receipt-v1", "name": name,
            "command": ["python3", script], "exit_code": 0,
            "log_sha256": hashlib.sha256(log).hexdigest(),
            "required_markers": [marker],
        }, sort_keys=True) + "\n")

    def _write_policy(self) -> None:
        policy = {
            "schema": "aer-a1-release-policy-v1",
            "generator_version": "4.0", "trace_abi_version": 4,
            "identity_mode": "address_only",
            "required_relation": "address == logical_source",
            "full_count": 50, "capacity_count": 22,
            "artifacts": {
                "generator": self.artifact("bench/generator.py"),
                "preparer": self.artifact("bench/preparer.py"),
                "testbench": self.artifact("tb/clean_tb.sv"),
                "full_manifest": self.artifact("bench/manifest.full.json"),
                "capacity_manifest": self.artifact("bench/manifest.capacity.json"),
                "golden": self.artifact("bench/golden.json"),
                "self_test": self.artifact("bench/self_test.py"),
                "neutrality_self_test": self.artifact(
                    "bench/neutrality_self_test.py"
                ),
            },
            "test_receipts": [
                self.artifact("bench/receipts/self.json"),
                self.artifact("bench/receipts/neutrality.json"),
            ],
            "ppa_registry": self.artifact("bench/ppa_registry.json"),
        }
        self.write(
            benchmark_release.TRUSTED_POLICY_PATH,
            json.dumps(policy, sort_keys=True) + "\n",
        )
        benchmark_release.TRUSTED_POLICY_SHA256 = self.sha(
            benchmark_release.TRUSTED_POLICY_PATH
        )

    def commit_refresh_policy(self, message: str) -> None:
        self._write_policy()
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def generate(self, kind: str = "commit") -> tuple[Path, dict[str, object]]:
        output = self.base / f"release-{kind}.json"
        manifest = benchmark_release.generate_manifest(
            self.repo, output, kind, self.inputs
        )
        return output, manifest

    def test_round_trip_binds_canonical_policy_receipts_and_registry(self) -> None:
        output, manifest = self.generate()
        self.assertEqual(manifest["schema"], benchmark_release.SCHEMA)
        self.assertEqual(manifest["generator"]["version"], "4.0")
        self.assertEqual(manifest["official_manifests"]["full_n16"]["run_count"], 50)
        self.assertEqual(
            manifest["official_manifests"]["capacity_n16"]["run_count"], 22
        )
        self.assertEqual(len(manifest["native_bindings"]), 4)
        self.assertEqual(len(manifest["test_receipts"]), 2)
        benchmark_release.validate_manifest(
            self.repo, benchmark_release.load_manifest(output)
        )

    def test_cli_cannot_override_trusted_policy_hash(self) -> None:
        output = self.base / "cli.json"
        command = [
            sys.executable, str(ROOT / "benchmark_release.py"), "generate",
            "--repo", str(self.repo), "--output", str(output),
            "--policy", self.inputs.policy,
            "--generator", self.inputs.generator,
            "--preparer", self.inputs.preparer,
            "--testbench", self.inputs.testbench,
            "--ppa-registry", self.inputs.ppa_registry,
            "--full-manifest", self.inputs.full_manifest,
            "--capacity-manifest", self.inputs.capacity_manifest,
            "--golden", self.inputs.golden,
        ]
        for option, values in (
            ("--native-binding", self.inputs.native_bindings),
            ("--runner", self.inputs.runners),
            ("--analyzer", self.inputs.analyzers),
            ("--test-receipt", self.inputs.test_receipts),
        ):
            for value in values:
                command.extend((option, value))
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            command, env=environment, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("trusted A1 release policy hash mismatch", result.stderr)

    def test_existing_output_is_rejected_without_overwrite(self) -> None:
        output = self.base / "existing.json"
        output.write_text("owner data\n")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "already exists"):
            benchmark_release.generate_manifest(
                self.repo, output, "commit", self.inputs
            )
        self.assertEqual(output.read_text(), "owner data\n")

    def test_dangling_output_symlink_is_rejected(self) -> None:
        output = self.base / "dangling.json"
        output.symlink_to(self.base / "missing-target")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "dangling symlink"):
            benchmark_release.generate_manifest(
                self.repo, output, "commit", self.inputs
            )
        self.assertTrue(output.is_symlink())

    def test_atomic_no_replace_loses_race_without_overwrite(self) -> None:
        output = self.base / "race.json"
        real_link = os.link

        def lose_race(source: object, destination: object) -> None:
            Path(destination).write_text("racer wins\n")
            raise FileExistsError

        with mock.patch.object(benchmark_release.os, "link", side_effect=lose_race):
            with self.assertRaisesRegex(benchmark_release.ReleaseError, "no-replace"):
                benchmark_release.generate_manifest(
                    self.repo, output, "commit", self.inputs
                )
        self.assertEqual(output.read_text(), "racer wins\n")
        self.assertFalse(any(self.base.glob(".race.json.*.tmp")))
        self.assertIsNotNone(real_link)

    def test_canonical_blob_rejects_dead_code_semantic_decoy(self) -> None:
        self.write(
            "bench/generator.py",
            'GENERATOR_VERSION = "4.0"\n'
            "def dead_code():\n"
            "    return {'event_identity_mode': 'address_only', "
            "'dut_address_fields': ['logical_source'], 'dut_payload_fields': []}\n"
            "def generate(): return 'payload-bypass'\n",
        )
        self.git("add", "bench/generator.py")
        self.git("commit", "-qm", "semantic decoy")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "canonical policy"):
            self.generate()

    def test_receipt_log_hash_is_checked_against_executed_command(self) -> None:
        receipt = json.loads((self.repo / "bench/receipts/self.json").read_text())
        receipt["log_sha256"] = "0" * 64
        self.write("bench/receipts/self.json", json.dumps(receipt) + "\n")
        self.commit_refresh_policy("forge receipt log")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "log hash mismatch"):
            self.generate()

    def test_receipt_command_cannot_be_replaced_by_self_declared_marker(self) -> None:
        receipt = json.loads((self.repo / "bench/receipts/self.json").read_text())
        receipt["command"] = ["python3", "bench/analyzer.py"]
        receipt["log_sha256"] = hashlib.sha256(b"").hexdigest()
        self.write("bench/receipts/self.json", json.dumps(receipt) + "\n")
        self.commit_refresh_policy("forge receipt command")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "not canonical"):
            self.generate()

    def test_stale_48_20_rejected_even_under_resigned_policy(self) -> None:
        runs = self.runs(48)
        self.write("bench/manifest.full.json", json.dumps({"runs": runs}) + "\n")
        self.write("bench/manifest.capacity.json", json.dumps({"runs": runs[:20]}) + "\n")
        self.write(
            "bench/golden.json",
            json.dumps({
                "generator_version": "4.0", "suite": "manifest.full.json",
                "runs": [{"name": run["name"]} for run in runs],
            }) + "\n",
        )
        self.commit_refresh_policy("resign stale counts")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "expected 50, got 48"):
            self.generate()

    def test_native_binding_omission_is_rejected(self) -> None:
        bad = benchmark_release.ReleaseInputs(
            **{**self.inputs.__dict__, "native_bindings": self.inputs.native_bindings[:-1]}
        )
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "exactly enumerate"):
            benchmark_release.generate_manifest(
                self.repo, self.base / "omit.json", "commit", bad
            )

    def test_ppa_binding_source_is_rejected_under_resigned_policy(self) -> None:
        registry = json.loads((self.repo / "bench/ppa_registry.json").read_text())
        candidate = registry["candidates"][0]
        candidate["filelist"] = self.artifact(
            "tb/filelists/a7_parallel_event_compactor.f"
        )
        candidate["sources"] = [
            self.artifact("rtl/design.sv"),
            self.artifact(
                "tb/clean/native/a7_parallel_event_compactor_binding.sv"
            ),
        ]
        self.write("bench/ppa_registry.json", json.dumps(registry) + "\n")
        self.commit_refresh_policy("resign binding-contaminated PPA registry")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "native binding"):
            self.generate()

    def test_ppa_source_closure_omission_is_rejected(self) -> None:
        registry = json.loads((self.repo / "bench/ppa_registry.json").read_text())
        registry["candidates"][0]["sources"] = []
        self.write("bench/ppa_registry.json", json.dumps(registry) + "\n")
        self.commit_refresh_policy("omit PPA source closure")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "closure is empty"):
            self.generate()

    def test_ppa_top_parameters_defines_are_exact(self) -> None:
        registry = json.loads((self.repo / "bench/ppa_registry.json").read_text())
        registry["candidates"][0]["parameters"]["NUM_SOURCES"] = 64
        self.write("bench/ppa_registry.json", json.dumps(registry) + "\n")
        self.commit_refresh_policy("change PPA parameters")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "top/parameters/defines"):
            self.generate()

    def test_ppa_tool_script_hash_is_verified(self) -> None:
        self.write("scripts/tool.tcl", "# replaced tool\n")
        self.git("add", "scripts/tool.tcl")
        self.git("commit", "-qm", "replace PPA tool")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "policy hash mismatch"):
            self.generate()

    def test_checked_in_policy_hash_constant_is_consistent(self) -> None:
        policy = ROOT / "a1_release_policy.json"
        self.assertEqual(
            hashlib.sha256(policy.read_bytes()).hexdigest(),
            self.original_policy_hash,
        )

    def test_dirty_and_result_artifact_bypasses_are_rejected(self) -> None:
        self.write("scratch.tmp", "dirty\n")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "dirty"):
            self.generate()
        (self.repo / "scratch.tmp").unlink()
        self.write("results/runner.py", "pass\n")
        self.git("add", "results/runner.py")
        self.git("commit", "-qm", "forbidden result")
        bad = benchmark_release.ReleaseInputs(
            **{**self.inputs.__dict__, "runners": ("results/runner.py",)}
        )
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "forbidden"):
            benchmark_release.generate_manifest(
                self.repo, self.base / "result.json", "commit", bad
            )

    def test_historical_release_is_not_authorized_by_current_policy(self) -> None:
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "current releases only"):
            benchmark_release.generate_manifest(
                self.repo, self.base / "historical.json", "commit", self.inputs,
                "historical",
            )

    def test_schema_is_strict(self) -> None:
        schema = json.loads((ROOT / "benchmark_release.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        if importlib.util.find_spec("jsonschema") is not None:
            import jsonschema

            jsonschema.Draft202012Validator.check_schema(schema)
            _, manifest = self.generate()
            jsonschema.Draft202012Validator(schema).validate(manifest)


if __name__ == "__main__":
    unittest.main()
