import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
import common_suite_attempt as attempt
import common_suite_execution_sidecar as sidecar_tool
import common_suite_official as official
import common_suite_receipt as receipt


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


class OfficialSuiteFixture:
    """Real committed manifest bytes with small deterministic execution fixtures."""

    def __init__(self, root: Path, suite: str):
        self.root, self.suite, self.candidate = root, suite, "dut"
        self.manifest = FIXTURES / official.SUITES[suite]["manifest_name"]
        manifest = json.loads(self.manifest.read_text())
        self.configs = {row["name"]: row for row in manifest["runs"]}
        self.names = tuple(official.SUITES[suite]["names"])
        self.trace_root = root / "traces"; self.trace_root.mkdir()

        candidate_manifest = root / "candidate.json"
        candidate_manifest.write_text(json.dumps({"schema_version": 1, "candidate": "dut",
                                                   "commit_sha": "1" * 40}) + "\n")
        self.candidate_manifest = candidate_manifest
        tools = {}
        for name in ("runner", "pairwise_contention", "mixed_phase_always_ready",
                     "phase_transition", "timing_pair"):
            path = root / f"{name}.tool"
            path.write_text(f"official-test-tool:{name}\n")
            tools[name] = path
        self.tool_paths = tools
        self.attempt = attempt.create(root / "output", suite, self.candidate,
                                      candidate_manifest, tools)
        self.attempt_doc = json.loads((self.attempt / "attempt.json").read_text())
        self.attempt_sha = digest((self.attempt / "attempt.json").read_bytes())
        self.trace_hashes, index_runs, artifact_runs = {}, [], []

        for position, name in enumerate(self.names):
            config = self.configs[name]
            canonical = {
                "name": name, "workload": config["workload"], "seed": config["seed"],
                "geometry": config["geometry"],
                "load": str(receipt.Decimal(str(config["load"]))),
                "stim_cycles": config["stim_cycles"], "parameters": config.get("parameters", {}),
                "sink": config.get("sink", {"mode": "always"}),
            }
            trace = (json.dumps({"official_run_name": name}, separators=(",", ":")) + "\n").encode()
            trace_sha = digest(trace); self.trace_hashes[name] = trace_sha
            (self.trace_root / f"{name}.events.jsonl").write_bytes(trace)
            metadata = {
                "schema_version": 1, "generator_version": official.GENERATOR_VERSION,
                "run": canonical, "report_group": receipt._report_group(canonical),
                "trace_file": f"{name}.events.jsonl", "trace_sha256": trace_sha,
                "event_identity_mode": "address_only", "dut_address_fields": ["logical_source"],
                "dut_payload_fields": [], "logical_source_permutation": list(range(16)),
            }
            index_runs.append(metadata)
            run_manifest_bytes = (json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()
            (self.trace_root / f"{name}.manifest.json").write_bytes(run_manifest_bytes)

            run_root = self.attempt / "runs" / name; run_root.mkdir()
            marker = run_root / "freshness.marker"; marker.write_bytes(b"")
            marker_ns = 1_700_000_000_000_000_000 + position * 100_000
            os.utime(marker, ns=(marker_ns, marker_ns))
            report_group = metadata["report_group"]
            result = ("candidate,test,seed,run_evidence\n"
                      f"{self.candidate},{report_group},{config['seed']},{name}\n").encode()
            result_path = run_root / "trace.events.csv"; result_path.write_bytes(result)
            os.utime(result_path, ns=(marker_ns + 10_000, marker_ns + 10_000))
            row = {"name": name, "freshness_marker": f"runs/{name}/freshness.marker",
                   "result": {"path": f"runs/{name}/trace.events.csv", "sha256": digest(result)}}

            analyzer = self._analyzer(metadata)
            analyzer_sha = None
            if analyzer is not None:
                analyzer_bytes = (json.dumps(analyzer, sort_keys=True) + "\n").encode()
                analyzer_path = run_root / "analysis.json"; analyzer_path.write_bytes(analyzer_bytes)
                os.utime(analyzer_path, ns=(marker_ns + 20_000, marker_ns + 20_000))
                analyzer_sha = digest(analyzer_bytes)
                row["analyzer"] = {"path": f"runs/{name}/analysis.json", "sha256": analyzer_sha}

            tool_binding = {"runner": self._tool("runner")}
            if analyzer is not None:
                tool_binding[config["workload"]] = self._tool(config["workload"])
            sidecar = {
                "schema_version": 1, "suite": suite,
                "attempt_id": self.attempt_doc["attempt_id"], "candidate": self.candidate,
                "run_name": name, "trace_sha256": trace_sha,
                "run_manifest_sha256": digest(run_manifest_bytes),
                "candidate_manifest_sha256": self.attempt_doc["candidate_manifest"]["sha256"],
                "tools": tool_binding, "result_sha256": digest(result),
                "analyzer_sha256": analyzer_sha,
            }
            sidecar_bytes = (json.dumps(sidecar, sort_keys=True) + "\n").encode()
            sidecar_path = run_root / "execution.sidecar.json"; sidecar_path.write_bytes(sidecar_bytes)
            os.utime(sidecar_path, ns=(marker_ns + 30_000, marker_ns + 30_000))
            row["execution_sidecar"] = {
                "path": f"runs/{name}/execution.sidecar.json", "sha256": digest(sidecar_bytes)}
            artifact_runs.append(row)

        self.index = self.trace_root / "generation-index.json"
        self.index.write_text(json.dumps({"schema_version": 1,
            "generator_version": official.GENERATOR_VERSION,
            "input_manifest": official.SUITES[suite]["manifest_name"],
            "runs": index_runs}, indent=2, sort_keys=True) + "\n")
        self.artifact_doc = {"schema_version": 3, "suite": suite, "candidate": self.candidate,
                             "attempt": {"path": "attempt.json", "sha256": self.attempt_sha},
                             "runs": artifact_runs}
        self.artifact_path = self.attempt / "artifacts.json"
        self.write_artifacts()

    def _tool(self, name):
        row = self.attempt_doc["tools"][name]
        return {"identity": row["identity"], "sha256": row["sha256"]}

    def _analyzer(self, metadata):
        name, workload = metadata["run"]["name"], metadata["run"]["workload"]
        common = {"candidate": self.candidate, "test": metadata["report_group"],
                  "seed": str(metadata["run"]["seed"]), "trace_sha256": metadata["trace_sha256"]}
        if workload == "pairwise_contention":
            return {**common, "generator_version": official.GENERATOR_VERSION,
                    "logical_source_permutation": list(range(16)), "pair_count": 3,
                    "evaluable_pairs": 3, "dropped_pairs": 0, "censored_pairs": 0,
                    "nonevaluable_pairs": 0, "measurement_state": "COMPLETE"}
        if workload == "mixed_phase_always_ready":
            return {**common, "schema_version": 1, "event_identity_mode": "address_only",
                    "sink_mode": "always", "provenance_validation": {"status": "pass",
                        "trace_sha256": True, "phase_boundaries": True,
                        "address_only_identity": True, "source_local_order": True,
                        "complete_uncensored_event_accounting": True},
                    "classification": {"analysis_status": "pass",
                                       "correctness_status": "qualified_pass"}}
        if workload == "phase_transition":
            phases = [{"phase": phase, "generated": 1, "source_overrun": 0,
                       "accepted": 1, "delivered_by_occurrence_phase": 1,
                       "delivered_in_phase_window": 1, "backlog_peak": 1,
                       "backlog_at_end": 0} for phase in receipt.PHASE_NAMES]
            return {**common, "tb_cycle_offset": 1, "recovery_to_zero_cycles": 0,
                    "recovery_censored": False, "recovery_lossless": True, "phases": phases}
        if workload == "timing_pair":
            return {**common, "pair_count": 4, "evaluable_pairs": 3,
                    "dropped_pairs": 1, "censored_pairs": 0,
                    "mean_pair_timing_error_cycles": 1.0}
        return None

    def row(self, name):
        return next(row for row in self.artifact_doc["runs"] if row["name"] == name)

    def write_artifacts(self):
        self.artifact_path.write_text(json.dumps(self.artifact_doc, indent=2, sort_keys=True) + "\n")

    def rewrite_sidecar(self, name, mutate):
        row = self.row(name); path = self.attempt / row["execution_sidecar"]["path"]
        doc = json.loads(path.read_text()); mutate(doc)
        payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
        marker = self.attempt / row["freshness_marker"]
        os.utime(path, ns=(marker.stat().st_mtime_ns + 40_000,) * 2)
        row["execution_sidecar"]["sha256"] = digest(payload); self.write_artifacts()

    def validate(self):
        return receipt.validate(self.index, self.manifest, self.suite, self.artifact_path,
                                self.attempt, trace_hashes=self.trace_hashes)


class CommonSuiteReceiptTest(unittest.TestCase):
    def fixture(self, suite="capacity22"):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return OfficialSuiteFixture(Path(temporary.name), suite)

    def test_real_official_full50_manifest_integration(self):
        fixture = self.fixture("full50"); result = fixture.validate()
        self.assertEqual(result["validated_run_count"], 50)
        self.assertEqual(sum("analyzer" in row for row in result["runs"]), 8)
        self.assertEqual(digest(fixture.manifest.read_bytes()),
                         official.SUITES["full50"]["manifest_sha256"])

    def test_real_official_capacity22_manifest_integration(self):
        fixture = self.fixture("capacity22"); result = fixture.validate()
        self.assertEqual(result["validated_run_count"], 22)
        self.assertEqual(sum("analyzer" in row for row in result["runs"]), 6)
        self.assertEqual(digest(fixture.manifest.read_bytes()),
                         official.SUITES["capacity22"]["manifest_sha256"])

    def test_sidecar_binds_exact_run_trace_manifest_result_candidate_and_tools(self):
        fixture = self.fixture(); name = "pairwise_contention_identity"
        mutations = [
            lambda doc: doc.__setitem__("run_name", "pairwise_contention_affine"),
            lambda doc: doc.__setitem__("trace_sha256", "0" * 64),
            lambda doc: doc.__setitem__("run_manifest_sha256", "0" * 64),
            lambda doc: doc.__setitem__("result_sha256", "0" * 64),
            lambda doc: doc.__setitem__("candidate_manifest_sha256", "0" * 64),
            lambda doc: doc["tools"]["runner"].__setitem__("sha256", "0" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fresh = self.fixture(); fresh.rewrite_sidecar(name, mutate)
                with self.assertRaisesRegex(receipt.ReceiptError, "sidecar binding mismatch"):
                    fresh.validate()

    def test_sidecar_helper_reproduces_bound_content(self):
        fixture = self.fixture(); name = "phase_transition_s3501"; row = fixture.row(name)
        built = sidecar_tool.build(
            fixture.attempt, fixture.trace_root / f"{name}.manifest.json",
            fixture.trace_root / f"{name}.events.jsonl",
            fixture.attempt / row["result"]["path"],
            fixture.attempt / row["analyzer"]["path"],
        )
        expected = json.loads((fixture.attempt / row["execution_sidecar"]["path"]).read_text())
        self.assertEqual(built, expected)

    def test_rejects_swapped_sidecars(self):
        fixture = self.fixture(); left, right = "uniform_l1p00_s2001", "uniform_l1p00_s2002"
        left_row, right_row = fixture.row(left), fixture.row(right)
        left_path = fixture.attempt / left_row["execution_sidecar"]["path"]
        right_path = fixture.attempt / right_row["execution_sidecar"]["path"]
        left_bytes, right_bytes = left_path.read_bytes(), right_path.read_bytes()
        left_path.write_bytes(right_bytes); right_path.write_bytes(left_bytes)
        left_row["execution_sidecar"]["sha256"] = digest(right_bytes)
        right_row["execution_sidecar"]["sha256"] = digest(left_bytes)
        for path, row in ((left_path, left_row), (right_path, right_row)):
            marker = fixture.attempt / row["freshness_marker"]
            os.utime(path, ns=(marker.stat().st_mtime_ns + 40_000,) * 2)
        fixture.write_artifacts()
        with self.assertRaisesRegex(receipt.ReceiptError, "sidecar binding mismatch"):
            fixture.validate()

    def test_rejects_swapped_results_even_when_csv_candidate_test_seed_match(self):
        fixture = self.fixture(); left, right = "uniform_l1p00_s2001", "uniform_l1p25_s2001"
        left_row, right_row = fixture.row(left), fixture.row(right)
        left_row["result"], right_row["result"] = right_row["result"], left_row["result"]
        newest = max((fixture.attempt / left_row["result"]["path"]).stat().st_mtime_ns,
                     (fixture.attempt / right_row["result"]["path"]).stat().st_mtime_ns) + 1_000
        for row in (left_row, right_row):
            path = fixture.attempt / row["execution_sidecar"]["path"]
            os.utime(path, ns=(newest, newest))
        fixture.write_artifacts()
        with self.assertRaisesRegex(receipt.ReceiptError, "sidecar binding mismatch"):
            fixture.validate()

    def test_rejects_hardlinked_result_inode(self):
        fixture = self.fixture(); left, right = "uniform_l1p00_s2001", "uniform_l1p00_s2002"
        left_path = fixture.attempt / fixture.row(left)["result"]["path"]
        right_path = fixture.attempt / fixture.row(right)["result"]["path"]
        right_path.unlink(); os.link(left_path, right_path)
        with self.assertRaisesRegex(receipt.ReceiptError, "hard-linked inode"):
            fixture.validate()

    def test_rejects_reused_result_sha_with_distinct_inodes(self):
        fixture = self.fixture(); left, right = "uniform_l1p00_s2001", "uniform_l1p00_s2002"
        left_path = fixture.attempt / fixture.row(left)["result"]["path"]
        right_row = fixture.row(right); right_path = fixture.attempt / right_row["result"]["path"]
        payload = left_path.read_bytes(); right_path.write_bytes(payload)
        right_row["result"]["sha256"] = digest(payload)
        marker = fixture.attempt / right_row["freshness_marker"]
        os.utime(right_path, ns=(marker.stat().st_mtime_ns + 10_000,) * 2)
        fixture.rewrite_sidecar(right, lambda doc: doc.__setitem__("result_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "reuses a result SHA256"):
            fixture.validate()

    def test_rejects_missing_attempt_json(self):
        fixture = self.fixture(); (fixture.attempt / "attempt.json").unlink()
        with self.assertRaisesRegex(receipt.ReceiptError, "cannot read attempt manifest"):
            fixture.validate()

    def test_rejects_mixed_candidate_even_when_hashes_and_sidecar_are_rebound(self):
        fixture = self.fixture(); name = "mixed_phase_always_ready_identity"; row = fixture.row(name)
        path = fixture.attempt / row["result"]["path"]
        payload = path.read_bytes().replace(b"dut,mixed_phase", b"other,mixed_phase")
        path.write_bytes(payload); row["result"]["sha256"] = digest(payload)
        marker = fixture.attempt / row["freshness_marker"]
        os.utime(path, ns=(marker.stat().st_mtime_ns + 10_000,) * 2)
        fixture.rewrite_sidecar(name, lambda doc: doc.__setitem__("result_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "result candidate/test/seed provenance mismatch"):
            fixture.validate()

    def test_phase_and_timing_analyzer_schema_fail_closed(self):
        phase = self.fixture(); phase_name = "phase_transition_s3501"
        row = phase.row(phase_name); path = phase.attempt / row["analyzer"]["path"]
        doc = json.loads(path.read_text()); doc["recovery_censored"] = True
        payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
        row["analyzer"]["sha256"] = digest(payload)
        phase.rewrite_sidecar(phase_name, lambda sidecar: sidecar.__setitem__("analyzer_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "phase-transition analyzer schema"):
            phase.validate()

        timing = self.fixture("full50"); timing_name = "timing_pair_s3901"
        row = timing.row(timing_name); path = timing.attempt / row["analyzer"]["path"]
        doc = json.loads(path.read_text()); doc["censored_pairs"] = 1
        payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
        row["analyzer"]["sha256"] = digest(payload)
        timing.rewrite_sidecar(timing_name, lambda sidecar: sidecar.__setitem__("analyzer_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "timing-pair analyzer schema"):
            timing.validate()

    def test_attempt_namespace_is_unique_and_required_shape(self):
        fixture = self.fixture()
        second = attempt.create(fixture.root / "output", fixture.suite, fixture.candidate,
                                fixture.candidate_manifest, fixture.tool_paths)
        self.assertNotEqual(fixture.attempt, second)
        for path in (fixture.attempt, second):
            self.assertEqual(path.parent.name, fixture.candidate)
            self.assertEqual(path.parent.parent.name, fixture.suite)
            self.assertEqual(path.parent.parent.parent.name, "attempts")

    def test_rejects_tampered_candidate_or_tool_snapshot(self):
        fixture = self.fixture()
        candidate_path = fixture.attempt / fixture.attempt_doc["candidate_manifest"]["path"]
        candidate_path.chmod(0o600); candidate_path.write_text('{"schema_version":1,"candidate":"dut"}\n')
        with self.assertRaisesRegex(receipt.ReceiptError, "candidate manifest identity mismatch"):
            fixture.validate()

        fixture = self.fixture(); tool = fixture.attempt_doc["tools"]["runner"]
        tool_path = fixture.attempt / tool["path"]
        tool_path.chmod(0o600); tool_path.write_text("changed runner\n")
        with self.assertRaisesRegex(receipt.ReceiptError, "tool runner identity mismatch"):
            fixture.validate()

    def test_atomic_publish_refuses_overwrite(self):
        fixture = self.fixture(); result = fixture.validate()
        output = fixture.attempt / "receipt.json"
        payload = (json.dumps(result, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(output, payload)
        with self.assertRaisesRegex(receipt.ReceiptError, "refusing to overwrite"):
            receipt.publish_new_atomic(output, b"replacement")
        self.assertEqual(output.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
