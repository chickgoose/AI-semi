import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_RUNNER = Path(__file__).resolve().parent / "fake_suite_runner.py"
FAKE_ANALYZER = Path(__file__).resolve().parent / "fake_official_analyzer.py"
sys.path.insert(0, str(ROOT / "scripts"))
import common_suite_official as official
import common_suite_official_wrapper as wrapper
import common_suite_receipt as receipt


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def v4_generator():
    explicit = os.environ.get("AER_V4_COMMON_ROOT")
    candidates = ([Path(explicit)] if explicit else []) + [
        ROOT.parent / "a1/benchmarks/clean_slate_aer", ROOT / "benchmarks/clean_slate_aer"]
    for common in candidates:
        generator = common / "generate_trace.py"
        if generator.is_file() and "pairwise_contention" in generator.read_text():
            return generator
    raise AssertionError("real v4 common generator not found; set AER_V4_COMMON_ROOT")


class OfficialWrapperTest(unittest.TestCase):
    def root(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def candidate(self, root):
        rtl = root / "rtl"; rtl.mkdir()
        source = rtl / "dut.sv"; source.write_text("module dut_top; endmodule\n")
        filelist = [{"path": "rtl/dut.sv", "sha256": digest(source.read_bytes())}]
        manifest = root / "candidate.json"
        manifest.write_text(json.dumps({"schema_version": 2, "candidate": "wrapper-dut",
            "commit_sha": "a" * 40, "bundle_sha256": receipt._canonical_sha(filelist),
            "filelist": filelist, "top": "dut_top", "parameters": {}, "defines": {},
            "includes": [], "source_count": 16, "retire_lanes": 1}, sort_keys=True) + "\n")
        return manifest

    def invocation(self, root, *, fail=False):
        output = root / "existing-output"; output.mkdir(); (output / "sentinel.txt").write_text("keep\n")
        version = root / "simulator.version"; version.write_text("FakeSim 1.0\n")
        simulator = root / "simulator.bin"; simulator.write_bytes(b"fake-simulator\n")
        args = ["run", "--suite", "capacity22", "--output-root", str(output),
            "--candidate-manifest", str(self.candidate(root)), "--official-manifest",
            str(FIXTURES / official.SUITES["capacity22"]["manifest_name"]),
            "--generator", str(v4_generator()), "--runner", str(FAKE_RUNNER),
            "--result-pattern", "runner-output/results/{run}/trace.events.csv",
            "--summary-pattern", "runner-output/results/{run}/trace.csv",
            "--simulator-name", "fakesim", "--simulator-executable", str(simulator),
            "--simulator-version", str(version)]
        for workload in ("pairwise_contention", "mixed_phase_always_ready", "phase_transition"):
            args += ["--analyzer", f"{workload}={FAKE_ANALYZER}"]
        if fail:
            args += ["--runner-env", "FAKE_RUNNER_FAIL=1"]
        return output, args

    def test_fake_runner_end_to_end_publishes_immutable_receipt(self):
        root = self.root(); output, args = self.invocation(root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wrapper.main(args), 0)
        receipts = list(output.glob("attempts/capacity22/wrapper-dut/*/common-suite.receipt.json"))
        self.assertEqual(len(receipts), 1)
        attempt_root = receipts[0].parent
        document = json.loads(receipts[0].read_text())
        self.assertEqual((document["status"], document["validated_run_count"]), ("PASS", 22))
        self.assertEqual(len(list((attempt_root / "runs").glob("*/execution.sidecar.json"))), 22)
        self.assertEqual(len(list((attempt_root / "runs").glob("*/analysis.json"))), 6)
        attempt = json.loads((attempt_root / "attempt.json").read_text())
        runner_dependencies = {row["logical_name"] for row in attempt["tools"]["runner"]["dependencies"]}
        self.assertIn("common_suite_official_wrapper.py", runner_dependencies)
        self.assertIn("execution-plan.json", runner_dependencies)
        self.assertEqual((output / "sentinel.txt").read_text(), "keep\n")
        with self.assertRaises(receipt.ReceiptError):
            receipt.publish_new_atomic(receipts[0], b"replacement")

    def test_runner_failure_is_nonzero_and_preserves_existing_output(self):
        root = self.root(); output, args = self.invocation(root, fail=True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wrapper.main(args), 2)
        self.assertEqual((output / "sentinel.txt").read_text(), "keep\n")
        self.assertFalse(list(output.glob("attempts/capacity22/wrapper-dut/*/common-suite.receipt.json")))
        self.assertEqual(len(list(output.glob("attempts/capacity22/wrapper-dut/*"))), 1)

    def test_real_v4_generate_only_smoke_full50_and_capacity22(self):
        root = self.root()
        for suite in ("full50", "capacity22"):
            output = root / suite
            with contextlib.redirect_stdout(io.StringIO()):
                status = wrapper.main(["generate-only", "--suite", suite, "--official-manifest",
                    str(FIXTURES / official.SUITES[suite]["manifest_name"]),
                    "--generator", str(v4_generator()), "--output-dir", str(output)])
            self.assertEqual(status, 0)
            generated = receipt.validate_official_generation(output / "generation-index.json",
                FIXTURES / official.SUITES[suite]["manifest_name"], suite)
            self.assertEqual(len(generated["names"]), 50 if suite == "full50" else 22)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(wrapper.main(["generate-only", "--suite", suite, "--official-manifest",
                    str(FIXTURES / official.SUITES[suite]["manifest_name"]),
                    "--generator", str(v4_generator()), "--output-dir", str(output)]), 2)


if __name__ == "__main__":
    unittest.main()
