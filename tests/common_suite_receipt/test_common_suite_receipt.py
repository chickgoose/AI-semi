import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import common_suite_receipt as receipt


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class CommonSuiteReceiptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.trace_root = self.root / "traces"
        self.artifact_root = self.root / "artifacts"
        self.trace_root.mkdir()
        self.artifact_root.mkdir()
        self.names = ["alpha", "beta"]
        self.index_runs = []
        self.expected_runs = []
        self.artifact_runs = []

        for position, name in enumerate(self.names):
            trace_payload = f'{{"run":"{name}"}}\n'.encode()
            trace_file = f"{name}.events.jsonl"
            (self.trace_root / trace_file).write_bytes(trace_payload)
            trace_sha = sha(trace_payload)
            self.index_runs.append(
                {
                    "run": {"name": name},
                    "trace_file": trace_file,
                    "trace_sha256": trace_sha,
                }
            )
            self.expected_runs.append(
                {"name": name, "trace_file": trace_file, "trace_sha256": trace_sha}
            )

            run_root = self.artifact_root / name
            run_root.mkdir()
            marker = run_root / "freshness.marker"
            marker.write_bytes(b"")
            marker_ns = 1_700_000_000_000_000_000 + position * 10_000
            os.utime(marker, ns=(marker_ns, marker_ns))

            result_payload = f"candidate,test\nfixture,{name}\n".encode()
            result_path = run_root / "trace.events.csv"
            result_path.write_bytes(result_payload)
            result_ns = marker_ns + 1_000
            os.utime(result_path, ns=(result_ns, result_ns))
            result_sha = sha(result_payload)

            analyzer_payload = json.dumps(
                {
                    "metric": 1,
                    receipt.PROVENANCE_KEY: {
                        "schema_version": 1,
                        "run_name": name,
                        "trace_sha256": trace_sha,
                        "result_sha256": result_sha,
                    },
                },
                sort_keys=True,
            ).encode()
            analyzer_path = run_root / "analysis.json"
            analyzer_path.write_bytes(analyzer_payload)
            analyzer_ns = marker_ns + 2_000
            os.utime(analyzer_path, ns=(analyzer_ns, analyzer_ns))
            self.artifact_runs.append(
                {
                    "name": name,
                    "freshness_marker": f"{name}/freshness.marker",
                    "result": {
                        "path": f"{name}/trace.events.csv",
                        "sha256": result_sha,
                    },
                    "analyzer": {
                        "path": f"{name}/analysis.json",
                        "sha256": sha(analyzer_payload),
                    },
                }
            )

        self.index_path = self.trace_root / "generation-index.json"
        self.expected_path = self.root / "expected.json"
        self.artifacts_path = self.root / "artifacts.json"
        self._write_inputs()

    def tearDown(self):
        self.temporary.cleanup()

    def _write_inputs(self):
        self.index_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generator_version": "fixture-v1",
                    "input_manifest": "suite.json",
                    "runs": self.index_runs,
                }
            )
        )
        self.expected_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "suite_id": "fixture-suite",
                    "expected_run_count": 2,
                    "index_provenance": {
                        "generator_version": "fixture-v1",
                        "input_manifest": "suite.json",
                    },
                    "runs": self.expected_runs,
                }
            )
        )
        self.artifacts_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "suite_id": "fixture-suite",
                    "runs": self.artifact_runs,
                }
            )
        )

    def validate(self):
        return receipt.validate(
            self.index_path,
            self.expected_path,
            self.artifacts_path,
            self.artifact_root,
        )

    def assert_rejected(self, pattern):
        self._write_inputs()
        with self.assertRaisesRegex(receipt.ReceiptError, pattern):
            self.validate()

    def test_validates_exact_suite_and_atomically_publishes_once(self):
        result = self.validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["validated_run_count"], 2)
        self.assertEqual([row["name"] for row in result["runs"]], self.names)

        output = self.root / "receipt.json"
        payload = (json.dumps(result, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(output, payload)
        self.assertEqual(output.read_bytes(), payload)
        with self.assertRaisesRegex(receipt.ReceiptError, "refusing to overwrite"):
            receipt.publish_new_atomic(output, b"replacement")
        self.assertEqual(output.read_bytes(), payload)
        self.assertEqual(list(self.root.glob(".receipt.json.*.tmp")), [])

    def test_cli_failure_publishes_no_receipt(self):
        self.artifact_runs[0]["result"]["sha256"] = "0" * 64
        self._write_inputs()
        output = self.root / "must-not-exist.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = receipt.main(
                [
                    "--generation-index",
                    str(self.index_path),
                    "--expected-runs",
                    str(self.expected_path),
                    "--artifacts",
                    str(self.artifacts_path),
                    "--artifact-root",
                    str(self.artifact_root),
                    "--output",
                    str(output),
                ]
            )
        self.assertEqual(status, 2)
        self.assertFalse(output.exists())

    def test_rejects_duplicate_index_run(self):
        self.index_runs[1] = dict(self.index_runs[0])
        self.assert_rejected("duplicate run name")

    def test_rejects_missing_and_extra_run_sets(self):
        self.index_runs[1]["run"]["name"] = "extra"
        self.assert_rejected("run set mismatch")

    def test_rejects_missing_artifact_run(self):
        self.artifact_runs.pop()
        self.assert_rejected("artifact manifest contains 1 runs")

    def test_rejects_extra_artifact_run(self):
        self.artifact_runs[1]["name"] = "extra"
        self.assert_rejected("artifact manifest run set mismatch")

    def test_rejects_wrong_index_provenance(self):
        self._write_inputs()
        index = json.loads(self.index_path.read_text())
        index["input_manifest"] = "wrong.json"
        self.index_path.write_text(json.dumps(index))
        with self.assertRaisesRegex(receipt.ReceiptError, "provenance mismatch"):
            self.validate()

    def test_rejects_trace_content_sha_mismatch(self):
        (self.trace_root / "alpha.events.jsonl").write_text("changed\n")
        self.assert_rejected("trace content SHA256 mismatch")

    def test_rejects_stale_result_or_analyzer(self):
        marker = self.artifact_root / "alpha/freshness.marker"
        stale = self.artifact_root / "alpha/trace.events.csv"
        os.utime(stale, ns=(marker.stat().st_mtime_ns, marker.stat().st_mtime_ns))
        self.assert_rejected("result is not newer")

        self.setUp_fresh_artifact_mtimes("alpha")
        analyzer = self.artifact_root / "alpha/analysis.json"
        os.utime(analyzer, ns=(marker.stat().st_mtime_ns, marker.stat().st_mtime_ns))
        self.assert_rejected("analyzer is not newer")

    def setUp_fresh_artifact_mtimes(self, name):
        marker_ns = (self.artifact_root / name / "freshness.marker").stat().st_mtime_ns
        for offset, filename in ((1_000, "trace.events.csv"), (2_000, "analysis.json")):
            path = self.artifact_root / name / filename
            os.utime(path, ns=(marker_ns + offset, marker_ns + offset))

    def test_rejects_artifact_hash_mismatch(self):
        self.artifact_runs[0]["result"]["sha256"] = "0" * 64
        self.assert_rejected("result SHA256 mismatch")

    def test_rejects_analyzer_provenance_mismatch(self):
        analyzer_path = self.artifact_root / "alpha/analysis.json"
        analyzer = json.loads(analyzer_path.read_text())
        analyzer[receipt.PROVENANCE_KEY]["run_name"] = "beta"
        payload = json.dumps(analyzer, sort_keys=True).encode()
        analyzer_path.write_bytes(payload)
        self.setUp_fresh_artifact_mtimes("alpha")
        self.artifact_runs[0]["analyzer"]["sha256"] = sha(payload)
        self.assert_rejected("analyzer provenance mismatch")

    def test_rejects_duplicate_artifact_path(self):
        self.artifact_runs[1]["result"] = dict(self.artifact_runs[0]["result"])
        self.assert_rejected("duplicate artifact path")

    def test_rejects_path_escape(self):
        self.artifact_runs[0]["result"]["path"] = "../outside.csv"
        self.assert_rejected("contained relative path")

    def test_rejects_symlink_artifact(self):
        result_path = self.artifact_root / "alpha/trace.events.csv"
        target_path = self.artifact_root / "alpha/real.events.csv"
        result_path.rename(target_path)
        result_path.symlink_to(target_path.name)
        self.assert_rejected("contains a symlink")


if __name__ == "__main__":
    unittest.main()
