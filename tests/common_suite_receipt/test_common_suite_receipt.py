import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import common_suite_attempt as attempt
import common_suite_official as official
import common_suite_receipt as receipt


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class SuiteFixture:
    def __init__(self, root: Path, suite: str, names: tuple[str, ...]):
        self.root, self.suite, self.names = root, suite, names
        self.traces, self.artifacts = root / "traces", root / "artifacts"
        self.traces.mkdir(); self.artifacts.mkdir()
        self.manifest_name = f"manifest.{suite}.json"
        manifest_runs, index_runs, artifact_runs = [], [], []
        self.trace_hashes = {}
        for position, name in enumerate(names):
            workload = ("pairwise_contention" if name.startswith("pairwise_contention") else
                        "mixed_phase_always_ready" if name.startswith("mixed_phase_always_ready") else
                        "uniform")
            config = {"name": name, "workload": workload, "seed": 1000 + position,
                      "geometry": {"width": 4, "height": 4}, "load": 1.0,
                      "stim_cycles": 8, "parameters": {"fixed_polarity": 1}}
            manifest_runs.append(config)
            canonical = dict(config); canonical["load"] = "1.0"; canonical["sink"] = {"mode": "always"}
            trace_payload = (json.dumps({"name": name}, separators=(",", ":")) + "\n").encode()
            trace_sha = digest(trace_payload); self.trace_hashes[name] = trace_sha
            (self.traces / f"{name}.events.jsonl").write_bytes(trace_payload)
            report_group = receipt._report_group(canonical)
            metadata = {
                "schema_version": 1, "generator_version": "test-4.0", "run": canonical,
                "report_group": report_group, "trace_file": f"{name}.events.jsonl",
                "trace_sha256": trace_sha, "event_identity_mode": "address_only",
                "dut_address_fields": ["logical_source"], "dut_payload_fields": [],
                "logical_source_permutation": list(range(16)),
            }
            index_runs.append(metadata)
            (self.traces / f"{name}.manifest.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
            run_dir = self.artifacts / name; run_dir.mkdir()
            marker = run_dir / "freshness.marker"; marker.write_bytes(b"")
            marker_ns = 1_700_000_000_000_000_000 + position * 10_000
            os.utime(marker, ns=(marker_ns, marker_ns))
            result = f"candidate,test,seed\ndut,{report_group},{1000 + position}\n".encode()
            result_path = run_dir / "trace.events.csv"; result_path.write_bytes(result)
            os.utime(result_path, ns=(marker_ns + 1_000, marker_ns + 1_000))
            artifact = {"name": name, "freshness_marker": f"{name}/freshness.marker",
                        "result": {"path": f"{name}/trace.events.csv", "sha256": digest(result)}}
            if workload == "pairwise_contention":
                analyzer = {"candidate": "dut", "test": report_group, "seed": str(1000 + position),
                            "trace_sha256": trace_sha, "generator_version": "test-4.0",
                            "logical_source_permutation": list(range(16)), "pair_count": 3,
                            "evaluable_pairs": 3, "dropped_pairs": 0, "censored_pairs": 0,
                            "nonevaluable_pairs": 0, "measurement_state": "COMPLETE"}
            elif workload == "mixed_phase_always_ready":
                analyzer = {"schema_version": 1, "candidate": "dut", "test": report_group,
                            "seed": str(1000 + position), "trace_sha256": trace_sha,
                            "event_identity_mode": "address_only", "sink_mode": "always",
                            "provenance_validation": {"status": "pass", "trace_sha256": True,
                                "phase_boundaries": True, "address_only_identity": True,
                                "source_local_order": True, "complete_uncensored_event_accounting": True}}
                analyzer["classification"] = {"analysis_status": "pass",
                                                "correctness_status": "qualified_pass"}
            else:
                analyzer = None
            if analyzer is not None:
                payload = (json.dumps(analyzer, sort_keys=True) + "\n").encode()
                path = run_dir / "analysis.json"; path.write_bytes(payload)
                os.utime(path, ns=(marker_ns + 2_000, marker_ns + 2_000))
                artifact["analyzer"] = {"path": f"{name}/analysis.json", "sha256": digest(payload)}
            artifact_runs.append(artifact)
        manifest = {"schema_version": 1, "runs": manifest_runs}
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        self.manifest = root / self.manifest_name; self.manifest.write_bytes(manifest_bytes)
        self.index = self.traces / "generation-index.json"
        self.index.write_text(json.dumps({"schema_version": 1, "generator_version": "test-4.0",
            "input_manifest": self.manifest_name, "runs": index_runs}, indent=2, sort_keys=True) + "\n")
        self.artifact_doc = {"schema_version": 2, "suite": suite, "runs": artifact_runs}
        self.artifact_path = root / "artifacts.json"; self.write_artifacts()
        self.suites = {suite: {"manifest_name": self.manifest_name,
            "manifest_sha256": digest(manifest_bytes), "names": names}}

    def write_artifacts(self):
        self.artifact_path.write_text(json.dumps(self.artifact_doc, indent=2, sort_keys=True) + "\n")

    def validate(self):
        return receipt.validate(self.index, self.manifest, self.suite, self.artifact_path,
                                self.artifacts, self.suites, self.trace_hashes, "test-4.0")


class CommonSuiteReceiptTest(unittest.TestCase):
    def test_frozen_official_sets_are_exact_committed_50_and_22(self):
        self.assertEqual(len(official.FULL50), 50)
        self.assertEqual(len(official.CAPACITY22), 22)
        self.assertEqual(set(official.FULL50), set(official.TRACE_SHA256))
        self.assertTrue(set(official.CAPACITY22) < set(official.FULL50))
        self.assertEqual(official.SOURCE_COMMIT, "abd6a721b515ded8a9ef76cb96129b7e0af21e2b")
        self.assertEqual(official.FULL50[-2:], ("mixed_phase_always_ready_identity",
                                               "mixed_phase_always_ready_bit_reverse"))

    def _integration(self, suite, names):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), suite, names)
            result = fixture.validate()
            self.assertEqual(result["validated_run_count"], len(names))
            self.assertEqual(result["candidate"], "dut")
            analyzers = [row for row in result["runs"] if "analyzer" in row]
            self.assertEqual(len(analyzers), 4)
            for row in result["runs"]:
                self.assertIn("run_manifest", row)

    def test_full50_integration(self):
        self._integration("full50", official.FULL50)

    def test_capacity22_integration(self):
        self._integration("capacity22", official.CAPACITY22)

    def test_rejects_changed_embedded_or_per_run_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "full50", official.FULL50)
            doc = json.loads(fixture.index.read_text()); doc["runs"][0]["run"]["seed"] = 7
            fixture.index.write_text(json.dumps(doc))
            with self.assertRaisesRegex(receipt.ReceiptError, "embedded run config"):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            path = fixture.traces / f"{official.CAPACITY22[0]}.manifest.json"
            path.write_text(path.read_text() + " ")
            with self.assertRaisesRegex(receipt.ReceiptError, "bytes/content"):
                fixture.validate()

    def test_analyzers_are_required_only_for_pairwise_and_mixed(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            ordinary = next(row for row in fixture.artifact_doc["runs"] if row["name"].startswith("uniform"))
            ordinary["analyzer"] = {"path": ordinary["result"]["path"], "sha256": ordinary["result"]["sha256"]}
            fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "must not declare"):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            pair = next(row for row in fixture.artifact_doc["runs"] if row["name"] == "pairwise_contention_identity")
            del pair["analyzer"]; fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "analyzer must be an object"):
                fixture.validate()

    def test_rejects_pairwise_evaluable_zero_and_csv_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            name = "pairwise_contention_identity"; path = fixture.artifacts / name / "analysis.json"
            doc = json.loads(path.read_text()); doc.update(measurement_state="NO_EVALUABLE_PAIRS", evaluable_pairs=0)
            payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
            row = next(row for row in fixture.artifact_doc["runs"] if row["name"] == name)
            row["analyzer"]["sha256"] = digest(payload); fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "incomplete or censored"):
                fixture.validate()

    def test_rejects_duplicate_missing_extra_and_wrong_official_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            doc = json.loads(fixture.index.read_text())
            doc["runs"][1]["run"]["name"] = doc["runs"][0]["run"]["name"]
            fixture.index.write_text(json.dumps(doc))
            with self.assertRaisesRegex(receipt.ReceiptError, "duplicate run name"):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            fixture.artifact_doc["runs"].pop(); fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "missing="):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            fixture.artifact_doc["runs"].append({"name": "extra"}); fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "extra="):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            fixture.manifest.write_bytes(fixture.manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(receipt.ReceiptError, "byte SHA256 mismatch"):
                fixture.validate()

    def test_rejects_stale_result_and_actual_analyzer_provenance_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            name = official.CAPACITY22[0]
            marker = fixture.artifacts / name / "freshness.marker"
            result = fixture.artifacts / name / "trace.events.csv"
            os.utime(result, ns=(marker.stat().st_mtime_ns, marker.stat().st_mtime_ns))
            with self.assertRaisesRegex(receipt.ReceiptError, "not newer"):
                fixture.validate()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SuiteFixture(Path(temporary), "capacity22", official.CAPACITY22)
            name = "mixed_phase_always_ready_identity"
            path = fixture.artifacts / name / "analysis.json"
            doc = json.loads(path.read_text()); doc["candidate"] = "other"
            payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
            row = next(row for row in fixture.artifact_doc["runs"] if row["name"] == name)
            row["analyzer"]["sha256"] = digest(payload); fixture.write_artifacts()
            with self.assertRaisesRegex(receipt.ReceiptError, "analyzer provenance mismatch"):
                fixture.validate()

    def test_publish_is_no_overwrite_and_attempt_namespaces_are_unique(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root / "receipt.json"
            receipt.publish_new_atomic(output, b"one")
            with self.assertRaisesRegex(receipt.ReceiptError, "refusing to overwrite"):
                receipt.publish_new_atomic(output, b"two")
            self.assertEqual(output.read_bytes(), b"one")
            first = attempt.create(root, "full50", "dut")
            second = attempt.create(root, "full50", "dut")
            self.assertNotEqual(first, second)
            self.assertTrue((first / "attempt.json").is_file())
            self.assertTrue((second / "attempt.json").is_file())


if __name__ == "__main__":
    unittest.main()
