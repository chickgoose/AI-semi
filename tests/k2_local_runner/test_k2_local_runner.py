import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "k2_local_runner.py"
VERIFIER = ROOT / "scripts" / "k2_local_receipt.py"
FAKE_TOOL = Path(__file__).resolve().parent / "fake_k2_tool.py"


def plan_document():
    def suite(name, path, optional=False):
        return {
            "name": name,
            "optional": optional,
            "argv": ["@K2_TOOL:driver@", "suite", name, "@K2_OUTPUT:image@",
                     f"@K2_OUTPUT:{name}_result@"],
            "outputs": [{
                "name": f"{name}_result",
                "path": path,
                "role": "suite_result",
                "kind": "file",
            }],
        }
    return {
        "schema_version": 1,
        "tools": {"driver": {"version_argv": ["--version"]}},
        "stages": [{
            "name": "compile",
            "optional": False,
            "argv": ["@K2_TOOL:driver@", "compile", "@K2_OUTPUT:image@",
                     "@K2_TOP@", "@K2_FILELIST@", "@K2_DEFINES@", "@K2_PARAMS@"],
            "outputs": [{
                "name": "image",
                "path": "artifacts/build/image.bin",
                "role": "build",
                "kind": "file",
            }],
        }, suite("directed_trace", "artifacts/directed/result.json"),
            suite("reset_drain", "artifacts/reset/result.json"),
            suite("full50", "artifacts/full50/result.json", True),
            suite("capacity22", "artifacts/capacity22/result.json", True)],
    }


class K2LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "candidate.sv"
        self.source.write_text("module candidate_top; endmodule\n", encoding="utf-8")
        self.filelist = self.root / "candidate.f"
        self.filelist.write_text("candidate.sv\n", encoding="utf-8")
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(plan_document(), indent=2) + "\n", encoding="utf-8")
        self.output = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *extra):
        return [sys.executable, str(RUNNER), "--candidate", "candidate-a2",
                "--top", "candidate_top", "--filelist", str(self.filelist),
                "--define", "K2_PROMOTION=1", "--param", "RETIRE_LANES=2",
                "--param", "NUM_SOURCES=16", "--tool", f"driver={FAKE_TOOL}",
                "--command-plan", str(self.plan), "--output-root", str(self.output), *extra]

    def invoke(self, mode="pass", *extra, mutate_path=None):
        environment = os.environ.copy()
        runner_extra = list(extra)
        if mode != "pass":
            runner_extra.extend(["--env", f"FAKE_K2_MODE={mode}"])
        if mutate_path is not None:
            runner_extra.extend(["--env", f"FAKE_K2_MUTATE_PATH={mutate_path}"])
        return subprocess.run(self.command(*runner_extra), text=True, capture_output=True,
                              env=environment)

    def attempts(self):
        return sorted(self.output.glob("attempt-*")) if self.output.exists() else []

    def assert_failed_closed(self, completed):
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        for attempt in self.attempts():
            self.assertFalse((attempt / "receipt.json").exists())
        if self.output.exists():
            for attempt in self.output.glob(".incomplete-*"):
                self.assertFalse((attempt / "receipt.json").exists())
                self.assertTrue((attempt / "failure.json").is_file())

    def test_success_and_optional_hooks_are_ordered(self):
        completed = self.invoke("pass", "--enable-suite", "full50",
                                "--enable-suite", "capacity22")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        bundle = self.attempts()[0]
        verified = subprocess.run([sys.executable, str(VERIFIER), str(bundle)],
                                  text=True, capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        document = json.loads((bundle / "receipt.json").read_text())
        self.assertEqual([row["stage"] for row in document["commands"]],
                         ["compile", "directed_trace", "reset_drain", "full50", "capacity22"])
        self.assertEqual(document["candidate"]["retire_lanes"], 2)
        digest = json.loads(completed.stdout.split("K2_LOCAL_RUN_PASS ", 1)[1])["receipt_sha256"]
        detached = subprocess.run(
            [sys.executable, str(VERIFIER), str(bundle),
             "--expected-receipt-sha256", digest], text=True, capture_output=True)
        self.assertEqual(detached.returncode, 0, detached.stdout + detached.stderr)

    def test_missing_source_is_rejected(self):
        self.source.unlink()
        self.assert_failed_closed(self.invoke())

    def test_source_changed_during_compile_is_rejected(self):
        self.assert_failed_closed(self.invoke(mutate_path=self.source))

    def test_nonzero_compile_is_rejected(self):
        self.assert_failed_closed(self.invoke("compile_fail"))

    def test_nonzero_run_is_rejected(self):
        self.assert_failed_closed(self.invoke("run_fail"))

    def test_hung_compile_is_killed_without_receipt(self):
        self.assert_failed_closed(self.invoke("hang", "--timeout-seconds", "1"))

    def test_stale_output_is_rejected(self):
        self.assert_failed_closed(self.invoke("stale"))

    def test_sentinel_only_fake_is_rejected(self):
        self.assert_failed_closed(self.invoke("sentinel"))

    def test_fabricated_binding_is_rejected(self):
        self.assert_failed_closed(self.invoke("fabricated"))

    def test_partial_bundle_is_rejected(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        bundle = self.attempts()[0]
        (bundle / "artifacts/reset/result.json").unlink()
        verified = subprocess.run([sys.executable, str(VERIFIER), str(bundle)],
                                  text=True, capture_output=True)
        self.assertEqual(verified.returncode, 2)
        self.assertIn("cannot read manifest file", verified.stderr)

    def test_runner_refuses_partial_outputs(self):
        self.assert_failed_closed(self.invoke("partial"))

    def test_unattached_hash_or_file_is_rejected(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        bundle = self.attempts()[0]
        (bundle / "invented.log").write_text("K2_PASS\n", encoding="utf-8")
        verified = subprocess.run([sys.executable, str(VERIFIER), str(bundle)],
                                  text=True, capture_output=True)
        self.assertEqual(verified.returncode, 2)
        self.assertIn("unmanifested bundle", verified.stderr)

    def test_retire_lanes_must_be_explicitly_two(self):
        command = self.command()
        position = command.index("RETIRE_LANES=2")
        command[position] = "RETIRE_LANES=1"
        completed = subprocess.run(command, text=True, capture_output=True)
        self.assert_failed_closed(completed)

    def test_plan_cannot_omit_explicit_candidate_inputs(self):
        document = plan_document()
        document["stages"][0]["argv"].remove("@K2_FILELIST@")
        self.plan.write_text(json.dumps(document) + "\n", encoding="utf-8")
        completed = self.invoke()
        self.assert_failed_closed(completed)
        self.assertIn("must consume explicit candidate tokens", completed.stderr)

    def test_suites_must_consume_compiled_output(self):
        document = plan_document()
        document["stages"][1]["argv"].remove("@K2_OUTPUT:image@")
        self.plan.write_text(json.dumps(document) + "\n", encoding="utf-8")
        completed = self.invoke()
        self.assert_failed_closed(completed)
        self.assertIn("does not consume a compiled build output", completed.stderr)


if __name__ == "__main__":
    unittest.main()
