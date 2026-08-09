from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
        self._write_fixture()
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.inputs = benchmark_release.ReleaseInputs(
            generator="bench/generator.py",
            preparer="bench/preparer.py",
            testbench="tb/clean_tb.sv",
            runners=("scripts/run_clean.sh", "scripts/run_capacity.sh"),
            full_manifest="bench/manifest.full.json",
            capacity_manifest="bench/manifest.capacity.json",
            golden="bench/golden.json",
            analyzers=("bench/pairwise.py",),
            test_evidence=(("self_check", "SELF_CHECK_PASS"),),
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

    @staticmethod
    def runs(count: int) -> list[dict[str, object]]:
        return [
            {
                "name": f"run_{index:02d}",
                "workload": "uniform",
                "seed": index + 1,
                "geometry": {"width": 4, "height": 4},
                "load": 0.5,
                "stim_cycles": 64,
                "parameters": {},
            }
            for index in range(count)
        ]

    def _write_fixture(self) -> None:
        full_runs = self.runs(48)
        self.write(
            "bench/generator.py",
            'GENERATOR_VERSION = "3.0"\n\n'
            "def metadata():\n"
            "    return {\n"
            '        "event_identity_mode": "address_only",\n'
            '        "dut_address_fields": ["logical_source"],\n'
            '        "dut_payload_fields": [],\n'
            "    }\n",
        )
        self.write("bench/preparer.py", "TRACE_ABI_VERSION = 4\n")
        self.write("bench/pairwise.py", "def analyze():\n    return {}\n")
        self.write("tb/clean_tb.sv", "module clean_tb; localparam ABI = 4; endmodule\n")
        self.write("scripts/run_clean.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write("scripts/run_capacity.sh", "#!/usr/bin/env bash\nexit 0\n")
        self.write("bench/manifest.full.json", json.dumps({"runs": full_runs}) + "\n")
        self.write(
            "bench/manifest.capacity.json",
            json.dumps({"runs": full_runs[:20]}) + "\n",
        )
        self.write(
            "bench/golden.json",
            json.dumps({
                "generator_version": "3.0",
                "suite": "manifest.full.json",
                "runs": [{"name": run["name"]} for run in full_runs],
            }) + "\n",
        )

    def generate(self, kind: str = "commit") -> tuple[Path, dict[str, object]]:
        output = self.base / f"release-{kind}.json"
        manifest = benchmark_release.generate_manifest(
            self.repo, output, kind, self.inputs
        )
        return output, manifest

    def test_commit_release_binds_git_blobs_and_validates(self) -> None:
        output, manifest = self.generate()
        self.assertEqual(manifest["binding"]["kind"], "commit")
        self.assertEqual(manifest["binding"]["commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(
            manifest["binding"]["tree"], self.git("rev-parse", "HEAD^{tree}")
        )
        self.assertEqual(manifest["trace_abi"], benchmark_release.TRACE_ABI)
        self.assertNotIn("release_manifest", json.dumps(manifest))
        benchmark_release.validate_manifest(
            self.repo, benchmark_release.load_manifest(output)
        )

    def test_cli_generate_and_validate_round_trip(self) -> None:
        output = self.base / "cli-release.json"
        command = [
            sys.executable, str(ROOT / "benchmark_release.py"), "generate",
            "--repo", str(self.repo), "--output", str(output),
            "--generator", self.inputs.generator,
            "--preparer", self.inputs.preparer,
            "--testbench", self.inputs.testbench,
            "--full-manifest", self.inputs.full_manifest,
            "--capacity-manifest", self.inputs.capacity_manifest,
            "--golden", self.inputs.golden,
            "--test-evidence", "self_check=SELF_CHECK_PASS",
        ]
        for runner in self.inputs.runners:
            command.extend(("--runner", runner))
        for analyzer in self.inputs.analyzers:
            command.extend(("--analyzer", analyzer))
        generated = subprocess.run(
            command, check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self.assertIn("BENCHMARK_RELEASE_GENERATED", generated.stdout)
        validated = subprocess.run(
            [
                sys.executable, str(ROOT / "benchmark_release.py"), "validate",
                "--repo", str(self.repo), "--manifest", str(output),
            ],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertIn("BENCHMARK_RELEASE_VALID", validated.stdout)

    def test_tree_release_has_no_commit_or_self_hash(self) -> None:
        output, manifest = self.generate("tree")
        self.assertIsNone(manifest["binding"]["commit"])
        self.assertEqual(manifest["binding"]["tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertNotIn(output.name, json.dumps(manifest))
        self.assertNotIn("manifest_sha256", json.dumps(manifest))

    def test_dirty_tracked_state_is_rejected(self) -> None:
        self.write("scripts/run_clean.sh", "#!/usr/bin/env bash\nexit 1\n")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "dirty"):
            self.generate()

    def test_dirty_untracked_state_is_rejected(self) -> None:
        self.write("scratch.tmp", "not release evidence\n")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "dirty"):
            self.generate()

    def test_sidecar_must_be_outside_repository(self) -> None:
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "outside"):
            benchmark_release.generate_manifest(
                self.repo, self.repo / "release.json", "commit", self.inputs
            )

    def test_hash_tampering_is_rejected(self) -> None:
        _, manifest = self.generate()
        tampered = copy.deepcopy(manifest)
        tampered["preparer"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "hash mismatch"):
            benchmark_release.validate_manifest(self.repo, tampered)

    def test_abi_tampering_is_rejected(self) -> None:
        _, manifest = self.generate()
        tampered = copy.deepcopy(manifest)
        tampered["trace_abi"]["version"] = 3
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "ABI"):
            benchmark_release.validate_manifest(self.repo, tampered)

    def test_manifest_counts_are_fail_closed(self) -> None:
        _, manifest = self.generate()
        tampered = copy.deepcopy(manifest)
        tampered["official_manifests"]["capacity_n16"]["run_count"] = 19
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "count must be 20"):
            benchmark_release.validate_manifest(self.repo, tampered)

    def test_capacity_must_be_exact_subset_of_full(self) -> None:
        document = json.loads((self.repo / "bench/manifest.capacity.json").read_text())
        document["runs"][0]["load"] = 0.75
        self.write("bench/manifest.capacity.json", json.dumps(document) + "\n")
        self.git("add", "bench/manifest.capacity.json")
        self.git("commit", "-qm", "break subset")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "byte-equivalent"):
            self.generate()

    def test_golden_must_name_official_full_manifest(self) -> None:
        document = json.loads((self.repo / "bench/golden.json").read_text())
        document["suite"] = "some-other-manifest.json"
        self.write("bench/golden.json", json.dumps(document) + "\n")
        self.git("add", "bench/golden.json")
        self.git("commit", "-qm", "break golden suite")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "golden suite"):
            self.generate()

    def test_non_address_only_generator_is_rejected(self) -> None:
        source = (self.repo / "bench/generator.py").read_text()
        self.write(
            "bench/generator.py",
            source.replace('"address_only"', '"address_plus_payload"'),
        )
        self.git("add", "bench/generator.py")
        self.git("commit", "-qm", "break identity")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "address-only"):
            self.generate()

    def test_results_and_log_artifacts_are_rejected(self) -> None:
        self.write("results/runner.py", "pass\n")
        self.git("add", "results/runner.py")
        self.git("commit", "-qm", "add forbidden result")
        bad = benchmark_release.ReleaseInputs(
            **{**self.inputs.__dict__, "runners": ("results/runner.py",)}
        )
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "forbidden"):
            benchmark_release.generate_manifest(
                self.repo, self.base / "bad.json", "commit", bad
            )
        bad_log = benchmark_release.ReleaseInputs(
            **{**self.inputs.__dict__, "runners": ("reports/run.log",)}
        )
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "forbidden"):
            benchmark_release.generate_manifest(
                self.repo, self.base / "bad-log.json", "commit", bad_log
            )

    def test_non_pass_evidence_is_rejected(self) -> None:
        _, manifest = self.generate()
        tampered = copy.deepcopy(manifest)
        tampered["test_evidence"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "not PASS"):
            benchmark_release.validate_manifest(self.repo, tampered)

    def test_schema_is_strict_and_forbids_result_paths(self) -> None:
        schema = json.loads((ROOT / "benchmark_release.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        pattern = schema["$defs"]["safePath"]["not"]["pattern"]
        self.assertRegex("results/run.csv", pattern)
        self.assertRegex("reports/run.log", pattern)
        if importlib.util.find_spec("jsonschema") is not None:
            import jsonschema

            jsonschema.Draft202012Validator.check_schema(schema)
            _, manifest = self.generate()
            jsonschema.Draft202012Validator(schema).validate(manifest)


if __name__ == "__main__":
    unittest.main()
