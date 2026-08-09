import hashlib
import itertools
import json
import os
import subprocess
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


def v4_common_root():
    explicit = os.environ.get("AER_V4_COMMON_ROOT")
    candidates = ([Path(explicit)] if explicit else []) + [
        ROOT.parent / "a1/benchmarks/clean_slate_aer",
        ROOT / "benchmarks/clean_slate_aer",
    ]
    for candidate in candidates:
        if (candidate / "generate_trace.py").is_file():
            return candidate
    raise AssertionError("real v4 common generator not found; set AER_V4_COMMON_ROOT")


class OfficialSuiteFixture:
    """Official manifest plus immutable synthetic execution evidence."""

    def __init__(self, root: Path, suite: str, *, real_v4_generation: bool = False):
        self.root, self.suite, self.candidate = root, suite, "dut"
        self.manifest = FIXTURES / official.SUITES[suite]["manifest_name"]
        manifest = json.loads(self.manifest.read_text())
        self.configs = {row["name"]: row for row in manifest["runs"]}
        self.names = tuple(official.SUITES[suite]["names"])
        self.trace_root = root / "traces"
        if real_v4_generation:
            subprocess.run([sys.executable, str(v4_common_root() / "generate_trace.py"),
                            "--manifest", str(self.manifest), "--output-dir", str(self.trace_root)],
                           check=True, stdout=subprocess.DEVNULL)
            generated_index = json.loads((self.trace_root / "generation-index.json").read_text())
            index_runs = generated_index["runs"]
            self.trace_hashes = official.TRACE_SHA256
        else:
            self.trace_root.mkdir()
            index_runs, self.trace_hashes = [], {}
            for name in self.names:
                config = self.configs[name]
                canonical = {
                    "name": name, "workload": config["workload"], "seed": config["seed"],
                    "geometry": config["geometry"], "load": str(receipt.Decimal(str(config["load"]))),
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
                (self.trace_root / f"{name}.manifest.json").write_text(
                    json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
            (self.trace_root / "generation-index.json").write_text(json.dumps({
                "schema_version": 1, "generator_version": official.GENERATOR_VERSION,
                "input_manifest": official.SUITES[suite]["manifest_name"], "runs": index_runs,
            }, indent=2, sort_keys=True) + "\n")

        self.index = self.trace_root / "generation-index.json"
        by_name = {row["run"]["name"]: row for row in index_runs}
        rtl_root = root / "rtl"; rtl_root.mkdir()
        (rtl_root / "dut.sv").write_text("module dut_top; endmodule\n")
        (rtl_root / "support.sv").write_text("package support; endpackage\n")
        candidate_files = [
            {"path": "rtl/dut.sv", "sha256": digest((rtl_root / "dut.sv").read_bytes())},
            {"path": "rtl/support.sv", "sha256": digest((rtl_root / "support.sv").read_bytes())},
        ]
        candidate_manifest = root / "candidate.json"
        candidate_manifest.write_text(json.dumps({
            "schema_version": 2, "candidate": self.candidate, "commit_sha": "1" * 40,
            "bundle_sha256": receipt._canonical_sha(candidate_files), "filelist": candidate_files,
            "top": "dut_top", "parameters": {"DEPTH": 4}, "defines": {"ADDRESS_ONLY": 1},
            "includes": ["rtl/include"], "source_count": 16, "retire_lanes": 1,
        }, sort_keys=True) + "\n")
        self.candidate_manifest = candidate_manifest

        dependency = root / "aggregate.py"; dependency.write_text("# transitive dependency\n")
        tools = {}
        for name in ("runner", "generator", "pairwise_contention", "mixed_phase_always_ready",
                     "phase_transition", "timing_pair"):
            path = root / f"{name}.tool"; path.write_text(f"official-test-tool:{name}\n")
            tools[name] = path
        self.tool_paths = tools
        self.tool_dependencies = {name: [dependency] for name in tools}
        self.simulator_executable = root / "simulator.bin"; self.simulator_executable.write_bytes(b"simulator-binary\n")
        self.simulator_version = root / "simulator.version"; self.simulator_version.write_text("TestSim 1.0\n")
        self.attempt = attempt.create(
            root / "output", suite, self.candidate, candidate_manifest, tools,
            tool_dependencies=self.tool_dependencies, simulator_name="testsim",
            simulator_executable=self.simulator_executable, simulator_version=self.simulator_version)
        self.attempt_doc = json.loads((self.attempt / "attempt.json").read_text())
        self.attempt_sha = digest((self.attempt / "attempt.json").read_bytes())

        artifact_runs = []
        for position, name in enumerate(self.names):
            metadata, config = by_name[name], self.configs[name]
            trace_sha = metadata["trace_sha256"]
            run_manifest_bytes = (self.trace_root / f"{name}.manifest.json").read_bytes()
            run_root = self.attempt / "runs" / name; run_root.mkdir()
            marker = run_root / "freshness.marker"; marker.write_bytes(b"")
            marker_ns = 1_700_000_000_000_000_000 + position * 100_000
            os.utime(marker, ns=(marker_ns, marker_ns))
            result = ("candidate,test,seed,load_pct,run_evidence\n"
                      f"{self.candidate},{metadata['report_group']},{config['seed']},"
                      f"{receipt.Decimal(str(config['load'])) * 100},{name}\n").encode()
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
            tool_binding = {key: self._tool(key) for key in ("runner", "generator")}
            if analyzer is not None:
                tool_binding[config["workload"]] = self._tool(config["workload"])
            sidecar = {
                "schema_version": receipt.SIDECAR_SCHEMA_VERSION, "suite": suite,
                "attempt_id": self.attempt_doc["attempt_id"], "candidate": self.candidate,
                "run_name": name, "trace_sha256": trace_sha,
                "run_manifest_sha256": digest(run_manifest_bytes),
                "candidate_manifest_sha256": self.attempt_doc["candidate_manifest"]["sha256"],
                "tools": tool_binding, "simulator": self._simulator(),
                "result_sha256": digest(result), "analyzer_sha256": analyzer_sha,
            }
            sidecar_bytes = (json.dumps(sidecar, sort_keys=True) + "\n").encode()
            sidecar_path = run_root / "execution.sidecar.json"; sidecar_path.write_bytes(sidecar_bytes)
            os.utime(sidecar_path, ns=(marker_ns + 30_000, marker_ns + 30_000))
            row["execution_sidecar"] = {"path": f"runs/{name}/execution.sidecar.json",
                                         "sha256": digest(sidecar_bytes)}
            artifact_runs.append(row)

        self.artifact_doc = {"schema_version": receipt.SCHEMA_VERSION, "suite": suite,
                             "candidate": self.candidate,
                             "attempt": {"path": "attempt.json", "sha256": self.attempt_sha},
                             "runs": artifact_runs}
        self.artifact_path = self.attempt / "artifacts.json"; self.write_artifacts()

    def _tool(self, name):
        row = self.attempt_doc["tools"][name]
        return {"identity": row["identity"], "bundle_sha256": row["bundle_sha256"]}

    def _simulator(self):
        row = self.attempt_doc["simulator"]
        return {"identity": row["identity"], "executable_sha256": row["executable"]["sha256"],
                "version_sha256": row["version"]["sha256"]}

    def _analyzer(self, metadata):
        workload = metadata["run"]["workload"]
        common = {"candidate": self.candidate, "test": metadata["report_group"],
                  "seed": str(metadata["run"]["seed"]), "trace_sha256": metadata["trace_sha256"]}
        if workload == "pairwise_contention":
            aggregates = [{"canonical_source_a": a, "canonical_source_b": b,
                           "physical_source_a": metadata["logical_source_permutation"][a],
                           "physical_source_b": metadata["logical_source_permutation"][b], "trial_count": 2,
                           "evaluable_trials": 2, "dropped_trials": 0, "censored_trials": 0,
                           "overlap_trials": 0, "mean_completion_latency_cycles": 2.0,
                           "max_completion_latency_cycles": 2, "mean_service_skew_cycles": 1.0,
                           "max_service_skew_cycles": 1} for a, b in itertools.combinations(range(16), 2)]
            pairs = list(itertools.combinations(range(16), 2))
            trials = []
            for relation_id in range(240):
                a, b = pairs[relation_id % 120]
                pa, pb = metadata["logical_source_permutation"][a], metadata["logical_source_permutation"][b]
                trials.append({"relation_id": relation_id, "repeat_index": relation_id // 120,
                    "canonical_source_a": a, "canonical_source_b": b, "physical_source_a": pa,
                    "physical_source_b": pb, "overlaps_previous_pair": False,
                    "overlapping_prior_pair_count": 0, "event_state_a": "delivered",
                    "event_state_b": "delivered", "source_a": pa, "source_b": pb,
                    "delivery_a": 1, "delivery_b": 2, "completion_latency_cycles": 2,
                    "service_skew_cycles": 1, "result": "evaluable"})
            return {**common, "generator_version": official.GENERATOR_VERSION,
                    "logical_source_permutation": metadata["logical_source_permutation"],
                    "pair_count": 240, "evaluable_pairs": 240, "dropped_pairs": 0,
                    "censored_pairs": 0, "nonevaluable_pairs": 0, "measurement_state": "COMPLETE",
                    "a_first_pairs": 120, "b_first_pairs": 120, "same_cycle_pairs": 0,
                    "overlap_pairs": 0, "max_overlapping_prior_pairs": 0,
                    "isolation_state": "QUIESCENT", "worst_completion_pair": {"relation_id": 0},
                    "worst_skew_pair": {"relation_id": 0},
                    "mean_pair_completion_latency_cycles": 2.0, "p95_pair_completion_latency_cycles": 2,
                    "max_pair_completion_latency_cycles": 2, "mean_pair_service_skew_cycles": 1.0,
                    "p95_pair_service_skew_cycles": 1, "max_pair_service_skew_cycles": 1,
                    "pair_aggregates": aggregates, "trials": trials}
        if workload == "mixed_phase_always_ready":
            phases = []
            for phase, start, end in receipt.MIXED_PHASE_BOUNDS:
                phases.append({"phase": phase, "start_cycle": start, "end_cycle_exclusive": end,
                    "cycles": end - start, "generated": 1, "source_overrun": 0, "accepted": 1,
                    "delivered": 1, "offered_events_per_cycle": 1 / (end - start),
                    "accepted_events_per_cycle": 1 / (end - start),
                    "delivered_by_occurrence_events_per_cycle": 1 / (end - start),
                    "delivered_in_window": 1, "retire_throughput_events_per_cycle": 1 / (end - start),
                    "capacity_loss_ratio": 0.0,
                    "latency_cycles": {"samples": 1, "mean": 1.0, "p50": 1, "p95": 1, "p99": 1, "max": 1},
                    "service_gap_cycles": {"active_sources": 1, "delivered_sources": 1,
                        "unobserved_active_sources": 0, "samples": 0, "p95_cycles": None,
                        "p99_cycles": None, "max_cycles": None},
                    "backlog_at_start": 0, "backlog_peak": 1, "backlog_at_end": 0,
                    "backlog_recovery_to_zero_cycles": 0,
                    "phase_origin_last_delivery_after_boundary_cycles": 0})
            return {**common, "schema_version": 1, "event_identity_mode": "address_only", "sink_mode": "always",
                    "tb_cycle_offset": 1, "observation_end_cycle": 5000,
                    "provenance_validation": {"status": "pass", "trace_sha256": True,
                        "phase_boundaries": True, "address_only_identity": True, "source_local_order": True,
                        "complete_uncensored_event_accounting": True},
                    "matched_trace_validation": {"status": "pass",
                        "uniform_exact_event_count_and_source_histogram": True,
                        "sustained_exact_event_source_and_fan_in_histograms": True,
                        "sustained_frozen_dwell_and_rotation": True, "hotspot_derived_rank_stream": True,
                        "hotspot_a_replay_exact_physical_replay": True},
                    "summary_evidence": {"status": "qualified_pass", "correctness_qualified": True,
                        "scoreboard_errors": 0, "conservation_validated": True,
                        "generated_equals_overrun_plus_accepted": True, "accepted_equals_delivered": True},
                    "classification": {"analysis_status": "pass", "correctness_status": "qualified_pass",
                        "correctness_scope": "common summary errors plus exact event conservation",
                        "capacity_status": "lossless", "capacity_loss_events": 0,
                        "capacity_loss_ratio": 0.0, "censored_events": 0},
                    "phases": phases, "matched_pair_deltas": [{"pair": pair, "left_phase": left,
                        "right_phase": right, "sign_convention": "left_minus_right", "generated_delta": 0,
                        "capacity_loss_events_delta": 0, "capacity_loss_ratio_delta": 0.0,
                        "retire_throughput_delta": 0.0, "p95_latency_cycles_delta": 0,
                        "p99_latency_cycles_delta": 0, "max_service_gap_cycles_delta": None,
                        "backlog_peak_delta": 0, "backlog_recovery_cycles_delta": 0}
                        for pair, left, right in (("uniform_temporal", "u_bernoulli", "u_smooth"),
                            ("sustained_temporal", "s_persistent", "s_rotating"),
                            ("spatial_b_vs_a", "h_b", "h_a"),
                            ("spatial_replay_vs_a", "h_a_replay", "h_a"))]}
        if workload == "phase_transition":
            stim = metadata["run"]["stim_cycles"]; eighth = stim // 8
            bounds = [(0, 2*eighth), (2*eighth, 4*eighth), (4*eighth, 6*eighth),
                      (6*eighth, 7*eighth), (7*eighth, 8*eighth)]
            phases = [{"phase": phase, "start_cycle": start, "end_cycle_exclusive": end,
                       "generated": 1, "source_overrun": 0, "accepted": 1,
                       "delivered_by_occurrence_phase": 1, "delivered_in_phase_window": 1,
                       "completion_per_phase_cycle": 1 / (end-start), "p95_e2e_latency_cycles": 1,
                       "backlog_peak": 1, "backlog_at_end": 0, "cumulative_overrun_at_end": 0,
                       "loss_adjusted_pressure_peak": 1}
                      for phase, (start, end) in zip(receipt.PHASE_NAMES, bounds)]
            return {**common, "tb_cycle_offset": 1, "recovery_to_zero_cycles": 0,
                    "recovery_censored": False, "recovery_lossless": True, "phases": phases}
        if workload == "timing_pair":
            return {**common, "pair_count": 128, "evaluable_pairs": 127, "dropped_pairs": 1,
                    "censored_pairs": 0, "mean_pair_timing_error_cycles": 1.0,
                    "p95_pair_timing_error_cycles": 1, "p99_pair_timing_error_cycles": 2,
                    "max_pair_timing_error_cycles": 2}
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

    def replace_analyzer(self, name, mutate):
        row = self.row(name); path = self.attempt / row["analyzer"]["path"]
        doc = json.loads(path.read_text()); mutate(doc)
        payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
        row["analyzer"]["sha256"] = digest(payload)
        marker = self.attempt / row["freshness_marker"]
        os.utime(path, ns=(marker.stat().st_mtime_ns + 20_000,) * 2)
        self.rewrite_sidecar(name, lambda sidecar: sidecar.__setitem__("analyzer_sha256", digest(payload)))

    def validate(self):
        kwargs = {} if self.trace_hashes is official.TRACE_SHA256 else {"trace_hashes": self.trace_hashes}
        return receipt.validate(self.index, self.manifest, self.suite, self.artifact_path,
                                self.attempt, **kwargs)


class CommonSuiteReceiptTest(unittest.TestCase):
    def fixture(self, suite="capacity22", *, real_v4_generation=False):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return OfficialSuiteFixture(Path(temporary.name), suite, real_v4_generation=real_v4_generation)

    def test_real_v4_full50_generation_and_receipt_integration(self):
        fixture = self.fixture("full50", real_v4_generation=True); result = fixture.validate()
        self.assertEqual(result["validated_run_count"], 50)
        self.assertEqual(sum("analyzer" in row for row in result["runs"]), 8)
        self.assertEqual({row["run"]["name"]: row["trace_sha256"]
                          for row in json.loads(fixture.index.read_text())["runs"]}, official.TRACE_SHA256)

    def test_real_v4_capacity22_generation_and_receipt_integration(self):
        fixture = self.fixture("capacity22", real_v4_generation=True); result = fixture.validate()
        self.assertEqual(result["validated_run_count"], 22)
        self.assertEqual(sum("analyzer" in row for row in result["runs"]), 6)
        generated = json.loads(fixture.index.read_text())["runs"]
        self.assertEqual(tuple(row["run"]["name"] for row in generated), official.CAPACITY22)
        self.assertTrue(all(row["trace_sha256"] == official.TRACE_SHA256[row["run"]["name"]]
                            for row in generated))

    def test_sidecar_binds_run_candidate_tool_bundle_and_simulator(self):
        name = "pairwise_contention_identity"
        mutations = [
            lambda doc: doc.__setitem__("run_name", "pairwise_contention_affine"),
            lambda doc: doc.__setitem__("trace_sha256", "0" * 64),
            lambda doc: doc.__setitem__("run_manifest_sha256", "0" * 64),
            lambda doc: doc.__setitem__("result_sha256", "0" * 64),
            lambda doc: doc.__setitem__("candidate_manifest_sha256", "0" * 64),
            lambda doc: doc["tools"]["runner"].__setitem__("bundle_sha256", "0" * 64),
            lambda doc: doc["simulator"].__setitem__("version_sha256", "0" * 64),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fixture = self.fixture(); fixture.rewrite_sidecar(name, mutate)
                with self.assertRaisesRegex(receipt.ReceiptError, "sidecar binding mismatch"):
                    fixture.validate()

    def test_sidecar_helper_reproduces_bound_content(self):
        fixture = self.fixture(); name = "phase_transition_s3501"; row = fixture.row(name)
        built = sidecar_tool.build(fixture.attempt, fixture.trace_root / f"{name}.manifest.json",
            fixture.trace_root / f"{name}.events.jsonl", fixture.attempt / row["result"]["path"],
            fixture.attempt / row["analyzer"]["path"])
        self.assertEqual(built, json.loads((fixture.attempt / row["execution_sidecar"]["path"]).read_text()))

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

    def test_rejects_swapped_run_even_with_rebound_sidecars(self):
        fixture = self.fixture(); left, right = "uniform_l1p00_s2001", "uniform_l1p25_s2001"
        left_row, right_row = fixture.row(left), fixture.row(right)
        left_path, right_path = fixture.attempt / left_row["result"]["path"], fixture.attempt / right_row["result"]["path"]
        left_bytes, right_bytes = left_path.read_bytes(), right_path.read_bytes()
        left_path.write_bytes(right_bytes); right_path.write_bytes(left_bytes)
        for name, row, payload in ((left, left_row, right_bytes), (right, right_row, left_bytes)):
            row["result"]["sha256"] = digest(payload)
            marker = fixture.attempt / row["freshness_marker"]
            os.utime(fixture.attempt / row["result"]["path"], ns=(marker.stat().st_mtime_ns + 10_000,) * 2)
            fixture.rewrite_sidecar(name, lambda sidecar, sha=digest(payload): sidecar.__setitem__("result_sha256", sha))
        with self.assertRaisesRegex(receipt.ReceiptError, "load_pct provenance mismatch"):
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
        payload = (fixture.attempt / fixture.row(left)["result"]["path"]).read_bytes()
        row = fixture.row(right); path = fixture.attempt / row["result"]["path"]
        path.write_bytes(payload); row["result"]["sha256"] = digest(payload)
        marker = fixture.attempt / row["freshness_marker"]
        os.utime(path, ns=(marker.stat().st_mtime_ns + 10_000,) * 2)
        fixture.rewrite_sidecar(right, lambda doc: doc.__setitem__("result_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "reuses a result SHA256"):
            fixture.validate()

    def test_rejects_missing_attempt_json(self):
        fixture = self.fixture(); (fixture.attempt / "attempt.json").unlink()
        with self.assertRaisesRegex(receipt.ReceiptError, "cannot read attempt manifest"):
            fixture.validate()

    def test_rejects_mixed_candidate_even_when_sidecar_is_rebound(self):
        fixture = self.fixture(); name = "mixed_phase_always_ready_identity"; row = fixture.row(name)
        path = fixture.attempt / row["result"]["path"]
        payload = path.read_bytes().replace(b"dut,mixed_phase", b"other,mixed_phase")
        path.write_bytes(payload); row["result"]["sha256"] = digest(payload)
        marker = fixture.attempt / row["freshness_marker"]
        os.utime(path, ns=(marker.stat().st_mtime_ns + 10_000,) * 2)
        fixture.rewrite_sidecar(name, lambda doc: doc.__setitem__("result_sha256", digest(payload)))
        with self.assertRaisesRegex(receipt.ReceiptError, "candidate/test/seed provenance mismatch"):
            fixture.validate()

    def test_analyzer_cardinality_and_ranges_fail_closed(self):
        cases = [
            ("capacity22", "pairwise_contention_identity", lambda doc: doc.__setitem__("pair_count", 239), "pairwise"),
            ("capacity22", "mixed_phase_always_ready_identity",
             lambda doc: doc["phases"][0].__setitem__("capacity_loss_ratio", 1.1), "mixed"),
            ("capacity22", "phase_transition_s3501",
             lambda doc: doc["phases"][0].__setitem__("accepted", 2), "sparse.accepted"),
            ("full50", "timing_pair_s3901", lambda doc: doc.__setitem__("pair_count", 127), "timing-pair"),
        ]
        for suite, name, mutate, message in cases:
            with self.subTest(name=name):
                fixture = self.fixture(suite); fixture.replace_analyzer(name, mutate)
                with self.assertRaisesRegex(receipt.ReceiptError, message):
                    fixture.validate()

    def test_candidate_manifest_requires_every_immutable_contract_field(self):
        for key in ("commit_sha", "bundle_sha256", "filelist", "top", "parameters", "defines",
                    "includes", "source_count", "retire_lanes"):
            with self.subTest(key=key):
                fixture = self.fixture()
                path = fixture.attempt / fixture.attempt_doc["candidate_manifest"]["path"]
                path.chmod(0o600); doc = json.loads(path.read_text()); del doc[key]
                payload = (json.dumps(doc, sort_keys=True) + "\n").encode(); path.write_bytes(payload)
                fixture.attempt_doc["candidate_manifest"]["sha256"] = digest(payload)
                attempt_path = fixture.attempt / "attempt.json"; attempt_path.chmod(0o600)
                attempt_path.write_text(json.dumps(fixture.attempt_doc, indent=2, sort_keys=True) + "\n")
                fixture.artifact_doc["attempt"]["sha256"] = digest(attempt_path.read_bytes()); fixture.write_artifacts()
                with self.assertRaisesRegex(receipt.ReceiptError, "candidate manifest"):
                    fixture.validate()

    def test_rejects_tampered_transitive_tool_or_simulator_snapshot(self):
        fixture = self.fixture(); candidate_file = fixture.attempt_doc["candidate_manifest"]["bundle_files"][0]
        path = fixture.attempt / candidate_file["path"]; path.chmod(0o600); path.write_text("changed\n")
        with self.assertRaisesRegex(receipt.ReceiptError, r"candidate bundle file\[0\] identity mismatch"):
            fixture.validate()
        fixture = self.fixture(); dependency = fixture.attempt_doc["tools"]["runner"]["dependencies"][0]
        path = fixture.attempt / dependency["path"]; path.chmod(0o600); path.write_text("changed\n")
        with self.assertRaisesRegex(receipt.ReceiptError, "tool runner identity mismatch"):
            fixture.validate()
        fixture = self.fixture(); executable = fixture.attempt_doc["simulator"]["executable"]
        path = fixture.attempt / executable["path"]; path.chmod(0o600); path.write_bytes(b"changed\n")
        with self.assertRaisesRegex(receipt.ReceiptError, "simulator executable identity mismatch"):
            fixture.validate()

    def test_attempt_namespace_is_unique(self):
        fixture = self.fixture()
        second = attempt.create(fixture.root / "output", fixture.suite, fixture.candidate,
            fixture.candidate_manifest, fixture.tool_paths, tool_dependencies=fixture.tool_dependencies,
            simulator_name="testsim", simulator_executable=fixture.simulator_executable,
            simulator_version=fixture.simulator_version)
        self.assertNotEqual(fixture.attempt, second)

    def test_atomic_publish_refuses_overwrite(self):
        fixture = self.fixture(); result = fixture.validate(); output = fixture.attempt / "receipt.json"
        payload = (json.dumps(result, sort_keys=True) + "\n").encode()
        receipt.publish_new_atomic(output, payload)
        with self.assertRaisesRegex(receipt.ReceiptError, "refusing to overwrite"):
            receipt.publish_new_atomic(output, b"replacement")
        self.assertEqual(output.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
