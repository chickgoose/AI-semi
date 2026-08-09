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
        runs = [
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
        if len(runs) >= 2:
            runs[0]["name"] = "mixed_phase_always_ready_identity"
            runs[0]["workload"] = "mixed_phase_always_ready"
            runs[1]["name"] = "mixed_phase_always_ready_bit_reverse"
            runs[1]["workload"] = "mixed_phase_always_ready"
        return runs

    def _write_fixture(self) -> None:
        full_runs = self.runs(50)
        self.write(
            "bench/generator.py",
            'GENERATOR_VERSION = "4.0"\n\n'
            "def metadata():\n"
            "    return {\n"
            '        "event_identity_mode": "address_only",\n'
            '        "dut_address_fields": ["logical_source"],\n'
            '        "dut_payload_fields": [],\n'
            "    }\n",
        )
        self.write(
            "bench/preparer.py",
            "def prepare(metadata, output, encoded):\n"
            '    identity_mode = metadata.get("event_identity_mode")\n'
            '    if identity_mode != "address_only":\n'
            '        raise ValueError("address_only required")\n'
            "    y = x = 0\n"
            "    width = 4\n"
            "    event_address = y * width + x\n"
            "    stim = source_count = load = sink = arg0 = arg1 = seed = 0\n"
            "    occurrence = trace_id = source = deadline = 0\n"
            "    output.write(\n"
            '        f"4 {len(encoded)} {stim} {source_count} {int(load)} "\n'
            '        f"{sink} {arg0} {arg1} {seed}\\n"\n'
            "    )\n"
            '    output.write(f"{occurrence} {trace_id} {source} "\n'
            '                 f"{event_address} {deadline}\\n")\n',
        )
        self.write("bench/pairwise.py", "def analyze():\n    return {}\n")
        self.write(
            "tb/clean_tb.sv",
            "module clean_tb;\n"
            '  initial $fscanf(fd, "%d %d %d %d %d %d %d %d %s\\n",\n'
            "                  trace_version, a, b, c, d, e, f, g, h);\n"
            "  always_comb begin\n"
            "    if (trace_version != 4) $fatal;\n"
            "    if (trace_address[trace_index] != trace_source[trace_index]) $fatal;\n"
            "  end\n"
            "endmodule\n",
        )
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
                "generator_version": "4.0",
                "suite": "manifest.full.json",
                "runs": [{"name": run["name"]} for run in full_runs],
            }) + "\n",
        )

    def generate(
        self, kind: str = "commit", release_kind: str = "current"
    ) -> tuple[Path, dict[str, object]]:
        output = self.base / f"release-{kind}-{release_kind}.json"
        manifest = benchmark_release.generate_manifest(
            self.repo, output, kind, self.inputs, release_kind
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
            "--release-kind", "current",
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
        tampered["official_manifests"]["capacity_n16"]["run_count"] = 21
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "declared capacity"):
            benchmark_release.validate_manifest(self.repo, tampered)

    def test_stale_48_20_suite_is_rejected(self) -> None:
        stale_runs = self.runs(48)
        self.write("bench/manifest.full.json", json.dumps({"runs": stale_runs}) + "\n")
        self.write(
            "bench/manifest.capacity.json",
            json.dumps({"runs": stale_runs[:20]}) + "\n",
        )
        self.write(
            "bench/golden.json",
            json.dumps({
                "generator_version": "4.0",
                "suite": "manifest.full.json",
                "runs": [{"name": run["name"]} for run in stale_runs],
            }) + "\n",
        )
        self.git("add", "bench")
        self.git("commit", "-qm", "restore stale counts")
        with self.assertRaisesRegex(
            benchmark_release.ReleaseError, "expected 50, got 48"
        ):
            self.generate()

    def test_preparer_must_implement_v4_address_only_semantics(self) -> None:
        source = (self.repo / "bench/preparer.py").read_text()
        self.write("bench/preparer.py", source.replace('f"4 {len(encoded)}',
                                                        'f"3 {len(encoded)}'))
        self.git("add", "bench/preparer.py")
        self.git("commit", "-qm", "break preparer ABI")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "preparer"):
            self.generate()

    def test_testbench_must_enforce_v4_address_only_semantics(self) -> None:
        source = (self.repo / "tb/clean_tb.sv").read_text()
        self.write(
            "tb/clean_tb.sv",
            source.replace("trace_address[trace_index] != trace_source[trace_index]",
                           "trace_address[trace_index] != 0"),
        )
        self.git("add", "tb/clean_tb.sv")
        self.git("commit", "-qm", "break TB ABI")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "testbench"):
            self.generate()

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

    def test_both_mixed_phase_runs_are_required_in_both_suites(self) -> None:
        full = json.loads((self.repo / "bench/manifest.full.json").read_text())
        capacity = json.loads(
            (self.repo / "bench/manifest.capacity.json").read_text()
        )
        full["runs"][0]["name"] = "stale_phase_run"
        capacity["runs"][0]["name"] = "stale_phase_run"
        self.write("bench/manifest.full.json", json.dumps(full) + "\n")
        self.write("bench/manifest.capacity.json", json.dumps(capacity) + "\n")
        self.git("add", "bench/manifest.full.json", "bench/manifest.capacity.json")
        self.git("commit", "-qm", "remove mixed phase")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "required run"):
            self.generate()

        full = json.loads((self.repo / "bench/manifest.full.json").read_text())
        capacity = json.loads(
            (self.repo / "bench/manifest.capacity.json").read_text()
        )
        full["runs"][0]["name"] = "mixed_phase_always_ready_identity"
        capacity["runs"][0]["name"] = "mixed_phase_always_ready_identity"
        full["runs"][1]["name"] = "stale_second_phase_run"
        capacity["runs"][1]["name"] = "stale_second_phase_run"
        self.write("bench/manifest.full.json", json.dumps(full) + "\n")
        self.write("bench/manifest.capacity.json", json.dumps(capacity) + "\n")
        self.git("add", "bench/manifest.full.json", "bench/manifest.capacity.json")
        self.git("commit", "-qm", "remove second mixed phase")
        with self.assertRaisesRegex(benchmark_release.ReleaseError, "required run"):
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

    def test_stale_generator_3_is_rejected_for_current_release(self) -> None:
        source = (self.repo / "bench/generator.py").read_text()
        self.write("bench/generator.py", source.replace('"4.0"', '"3.0"'))
        golden = json.loads((self.repo / "bench/golden.json").read_text())
        golden["generator_version"] = "3.0"
        self.write("bench/golden.json", json.dumps(golden) + "\n")
        self.git("add", "bench/generator.py", "bench/golden.json")
        self.git("commit", "-qm", "bind stale generator")
        with self.assertRaisesRegex(
            benchmark_release.ReleaseError,
            "current release generator version must be 4.0, got 3.0",
        ):
            self.generate()

    def test_generator_3_requires_explicit_historical_release(self) -> None:
        source = (self.repo / "bench/generator.py").read_text()
        self.write("bench/generator.py", source.replace('"4.0"', '"3.0"'))
        golden = json.loads((self.repo / "bench/golden.json").read_text())
        golden["generator_version"] = "3.0"
        self.write("bench/golden.json", json.dumps(golden) + "\n")
        self.git("add", "bench/generator.py", "bench/golden.json")
        self.git("commit", "-qm", "bind historical generator")
        _, manifest = self.generate(release_kind="historical")
        self.assertEqual(manifest["release_kind"], "historical")
        self.assertEqual(manifest["generator"]["version"], "3.0")
        benchmark_release.validate_manifest(self.repo, manifest)

    def test_declared_generator_version_mismatch_is_rejected(self) -> None:
        _, manifest = self.generate()
        tampered = copy.deepcopy(manifest)
        tampered["generator"]["version"] = "3.0"
        with self.assertRaisesRegex(
            benchmark_release.ReleaseError, "generator.version mismatch"
        ):
            benchmark_release.validate_manifest(self.repo, tampered)

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
