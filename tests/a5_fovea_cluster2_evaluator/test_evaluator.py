from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "a5_w7_evaluator", HERE / "evaluate_fovea_cluster2.py"
)
assert SPEC and SPEC.loader
E = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = E
SPEC.loader.exec_module(E)

A1 = Path("/home/chickgoose/projects/a1")
GENERATOR = A1 / "benchmarks/clean_slate_aer/generate_trace.py"
MANIFEST_ROOT = HERE.parent / "common_suite_receipt/fixtures"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def schedule(trace: tuple[dict, ...], lanes: int) -> list[dict[str, object]]:
    by_cycle: dict[int, list[dict]] = {}
    for event in trace:
        by_cycle.setdefault(int(event["occurrence_cycle"]), []).append(event)
    pending: dict[int, dict] = {}
    result: list[dict[str, object] | None] = [None] * len(trace)
    cycle = 0
    last_occurrence = max((int(event["occurrence_cycle"]) for event in trace), default=0)
    while cycle <= last_occurrence or pending:
        for event in by_cycle.get(cycle, []):
            source = int(event["logical_source"])
            event_id = int(event["tb_only_event_id"])
            if source in pending:
                result[event_id] = {"state": "source_overrun", "accept": None, "delivery": None}
            else:
                pending[source] = event
        # Deterministic test fixture only: lower source first and at most native lanes.
        for source in sorted(pending)[:lanes]:
            event = pending.pop(source)
            event_id = int(event["tb_only_event_id"])
            result[event_id] = {"state": "delivered", "accept": cycle, "delivery": cycle + 2}
        cycle += 1
    assert all(item is not None for item in result)
    return [item for item in result if item is not None]


def make_candidate(root: Path, candidate_id: str, architecture: str, lanes: int,
                   official: dict[str, dict[str, object]]) -> None:
    root.mkdir()
    identity_artifacts = {}
    identity_hashes = {}
    for label in ("source_bundle", "binding", "runner", "simulator"):
        path = root / "identity" / f"{label}.bin"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"TEST-ONLY {candidate_id} {label}\n".encode())
        identity_artifacts[label] = {
            "path": str(path.relative_to(root)), "sha256": digest(path),
        }
        identity_hashes[label] = digest(path)
    source_sha = identity_hashes["source_bundle"]
    run_descriptors: dict[str, list[dict[str, object]]] = {}
    created: dict[str, tuple[Path, Path]] = {}
    for suite in ("full50", "capacity22"):
        descriptors = []
        for name, run in official[suite].items():
            if name not in created:
                run_root = root / "runs" / name
                run_root.mkdir(parents=True)
                events_path = run_root / "trace.events.csv"
                summary_path = run_root / "trace.csv"
                scheduled = schedule(run.trace, lanes)
                observation_end = max(
                    [int(item["delivery"]) for item in scheduled if item["delivery"] is not None]
                    + [run.stim_cycles]
                ) + 1
                load_pct = (int(run.load * 1000) + 5) // 10
                delivered = sum(item["state"] == "delivered" for item in scheduled)
                overrun = len(scheduled) - delivered
                measured = sum(
                    item["state"] == "delivered" and int(item["delivery"]) < run.stim_cycles
                    for item in scheduled
                )
                with events_path.open("w", newline="", encoding="utf-8") as stream:
                    fields = [
                        "candidate", "test", "seed", "load_pct", "tb_only_event_id",
                        "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
                        "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
                    ]
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    for trace_event, item in zip(run.trace, scheduled):
                        writer.writerow({
                            "candidate": candidate_id, "test": name, "seed": run.seed,
                            "load_pct": load_pct,
                            "tb_only_event_id": trace_event["tb_only_event_id"],
                            "logical_source": trace_event["logical_source"], "source_count": 16,
                            "occurrence_cycle": trace_event["occurrence_cycle"],
                            "accept_cycle": "" if item["accept"] is None else item["accept"],
                            "delivery_cycle": "" if item["delivery"] is None else item["delivery"],
                            "deadline_cycle": trace_event.get("deadline", ""),
                            "observation_end_cycle": observation_end,
                            "event_state": item["state"],
                        })
                with summary_path.open("w", newline="", encoding="utf-8") as stream:
                    fields = [
                        "candidate", "test", "seed", "load_pct", "stim_cycles", "generated",
                        "source_overrun", "accepted", "delivered", "errors", "total_cycles",
                        "throughput", "measurement_delivered", "measurement_cycles",
                    ]
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerow({
                        "candidate": candidate_id, "test": name, "seed": run.seed,
                        "load_pct": load_pct, "stim_cycles": run.stim_cycles,
                        "generated": len(scheduled), "source_overrun": overrun,
                        "accepted": delivered, "delivered": delivered, "errors": 0,
                        "total_cycles": observation_end,
                        "throughput": f"{measured / run.stim_cycles:.9f}",
                        "measurement_delivered": measured, "measurement_cycles": run.stim_cycles,
                    })
                created[name] = (summary_path, events_path)
            summary_path, events_path = created[name]
            descriptors.append({
                "name": name, "trace_sha256": run.trace_sha256,
                "summary": {"path": str(summary_path.relative_to(root)),
                            "sha256": digest(summary_path)},
                "events": {"path": str(events_path.relative_to(root)),
                           "sha256": digest(events_path)},
            })
        run_descriptors[suite] = descriptors

    reset = root / "reset.json"
    write_json(reset, {
        "schema": E.RESET_SCHEMA, "candidate_id": candidate_id,
        "source_sha256": source_sha, "generated": 8, "accepted": 8, "delivered": 8,
        "errors": 0, "quiet_cycles": 8,
        **{key: True for key in E.RESET_TRUE},
    })
    policy = root / "policy.json"
    write_json(policy, {
        "schema": E.POLICY_SCHEMA, "candidate_id": candidate_id,
        "source_sha256": source_sha, "stimulus": "continuous_all_16_sources",
        "cycles": 120,
        "row_service_events": [10, 50, 50, 10] if architecture == "fovea" else [60, 60, 60, 60],
    })
    evidence = {
        "schema": E.EVIDENCE_SCHEMA,
        "candidate": {
            "id": candidate_id, "architecture": architecture,
            "top": ("aer_tx16_trad_rowcol_fovea" if architecture == "fovea"
                    else "aer_tx16_trad_rowcol_fovea_cluster2"),
            "source_sha256": source_sha,
            "binding_sha256": identity_hashes["binding"],
            "runner_sha256": identity_hashes["runner"],
            "simulator_sha256": identity_hashes["simulator"],
            "source_count": 16, "retire_lanes": lanes, "address_only": True,
        },
        "identity_artifacts": identity_artifacts,
        "suites": {
            suite: {"manifest_sha256": E.OFFICIAL[suite]["sha256"],
                    "runs": run_descriptors[suite]}
            for suite in E.OFFICIAL
        },
        "reset": {"path": "reset.json", "sha256": digest(reset)},
        "policy": {"path": "policy.json", "sha256": digest(policy)},
    }
    write_json(root / "evidence.json", evidence)


class EvaluatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not GENERATOR.is_file():
            raise unittest.SkipTest("A1 generator checkout is required for official integration")
        cls.temporary = tempfile.TemporaryDirectory(prefix="a5-w7-test-")
        cls.root = Path(cls.temporary.name)
        cls.generated = cls.root / "generated"
        cls.generated.mkdir()
        cls.official = E.materialize_official(GENERATOR, MANIFEST_ROOT, cls.generated)
        cls.fovea = cls.root / "fovea"
        cls.cluster2 = cls.root / "cluster2"
        make_candidate(cls.fovea, "fovea-native", "fovea", 1, cls.official)
        make_candidate(cls.cluster2, "cluster2-native", "cluster2", 8, cls.official)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @contextmanager
    def mutate_json_artifact(self, candidate: Path, key: str, mutation):
        evidence_path = candidate / "evidence.json"
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        artifact_path = candidate / evidence[key]["path"]
        artifact_bytes = artifact_path.read_bytes()
        document = json.loads(artifact_bytes)
        mutation(document)
        write_json(artifact_path, document)
        evidence[key]["sha256"] = digest(artifact_path)
        write_json(evidence_path, evidence)
        try:
            yield
        finally:
            artifact_path.write_bytes(artifact_bytes)
            evidence_path.write_bytes(evidence_bytes)

    @contextmanager
    def mutate_run(self, candidate: Path, suite: str, name: str, kind: str, mutation):
        evidence_path = candidate / "evidence.json"
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
        descriptor = next(row for row in evidence["suites"][suite]["runs"] if row["name"] == name)
        path = candidate / descriptor[kind]["path"]
        artifact_bytes = path.read_bytes()
        mutation(path)
        new_sha = digest(path)
        for suite_doc in evidence["suites"].values():
            for row in suite_doc["runs"]:
                if row[kind]["path"] == descriptor[kind]["path"]:
                    row[kind]["sha256"] = new_sha
        write_json(evidence_path, evidence)
        try:
            yield
        finally:
            path.write_bytes(artifact_bytes)
            evidence_path.write_bytes(evidence_bytes)

    def test_official_full50_capacity22_evaluation(self) -> None:
        document = E.evaluate(self.fovea, self.cluster2, GENERATOR, MANIFEST_ROOT)
        self.assertEqual("LOCAL_EVALUATION_COMPLETE", document["status"])
        self.assertEqual(50, document["official"]["full50"]["run_count"])
        self.assertEqual(22, document["official"]["capacity22"]["run_count"])
        self.assertTrue(document["metrics"]["fovea-native"]["row_policy"]["preserves_1_5_5_1"])
        self.assertFalse(document["metrics"]["cluster2-native"]["row_policy"]["preserves_1_5_5_1"])
        self.assertEqual(240, document["metrics"]["fovea-native"]["pairwise_mapping"]["identity"]["relations"])
        self.assertTrue(document["pareto"]["frontier"])

    def test_cli_atomic_refuses_overwrite(self) -> None:
        output = self.root / "cli-output.json"
        if output.exists():
            output.unlink()
        command = [
            sys.executable, str(HERE / "evaluate_fovea_cluster2.py"),
            "--fovea", str(self.fovea), "--cluster2", str(self.cluster2),
            "--generator", str(GENERATOR), "--manifest-root", str(MANIFEST_ROOT),
            "--output", str(output),
        ]
        first = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertIn("A5_W7_FOVEA_CLUSTER2_EVALUATION_PASS", first.stdout)
        original = output.read_bytes()
        second = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(2, second.returncode)
        self.assertEqual(original, output.read_bytes())

    def test_mutant_duplicate_event_id_is_killed(self) -> None:
        def mutation(path: Path) -> None:
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[1]["tb_only_event_id"] = rows[0]["tb_only_event_id"]
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
        with self.mutate_run(self.cluster2, "full50", "core_sparse_identity", "events", mutation):
            with self.assertRaisesRegex(E.EvaluationError, "duplicate/reordered event ID"):
                E.validate_candidate(self.cluster2, self.official)

    def test_mutant_phantom_delivery_is_killed(self) -> None:
        def mutation(path: Path) -> None:
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            victim = next(row for row in rows if row["event_state"] == "source_overrun")
            victim["event_state"] = "delivered"; victim["accept_cycle"] = "0"; victim["delivery_cycle"] = "2"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
        with self.mutate_run(self.fovea, "full50", "uniform_l2p00_s2001", "events", mutation):
            with self.assertRaises(E.EvaluationError):
                E.validate_candidate(self.fovea, self.official)

    def test_mutant_fixed_window_summary_is_killed(self) -> None:
        def mutation(path: Path) -> None:
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["measurement_delivered"] = str(int(rows[0]["measurement_delivered"]) + 1)
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
        with self.mutate_run(self.cluster2, "full50", "uniform_l2p00_s2001", "summary", mutation):
            with self.assertRaisesRegex(E.EvaluationError, "measurement_delivered mismatch"):
                E.validate_candidate(self.cluster2, self.official)

    def test_mutant_stale_reset_false_pass_is_killed(self) -> None:
        with self.mutate_json_artifact(
                self.cluster2, "reset", lambda doc: doc.__setitem__("no_stale_completion", False)):
            with self.assertRaisesRegex(E.EvaluationError, "no_stale_completion"):
                E.validate_candidate(self.cluster2, self.official)

    def test_mutant_fovea_policy_false_pass_is_killed(self) -> None:
        with self.mutate_json_artifact(
                self.fovea, "policy", lambda doc: doc.__setitem__("row_service_events", [30] * 4)):
            with self.assertRaisesRegex(E.EvaluationError, "1:5:5:1"):
                E.validate_candidate(self.fovea, self.official)

    def test_mutant_trace_and_candidate_provenance_are_killed(self) -> None:
        path = self.cluster2 / "evidence.json"
        original = path.read_bytes()
        document = json.loads(original)
        document["suites"]["full50"]["runs"][0]["trace_sha256"] = "0" * 64
        write_json(path, document)
        try:
            with self.assertRaisesRegex(E.EvaluationError, "trace provenance mismatch"):
                E.validate_candidate(self.cluster2, self.official)
        finally:
            path.write_bytes(original)

    def test_mutant_capacity22_overlap_copy_is_killed(self) -> None:
        evidence_path = self.cluster2 / "evidence.json"
        original = evidence_path.read_bytes()
        document = json.loads(original)
        descriptor = next(
            row for row in document["suites"]["capacity22"]["runs"]
            if row["name"] == "core_simultaneous_identity"
        )
        source = self.cluster2 / descriptor["summary"]["path"]
        copied = source.with_name("capacity22-copy.csv")
        copied.write_bytes(source.read_bytes() + b"\n")
        descriptor["summary"] = {
            "path": str(copied.relative_to(self.cluster2)), "sha256": digest(copied),
        }
        write_json(evidence_path, document)
        try:
            with self.assertRaisesRegex(E.EvaluationError, "overlap differs"):
                E.validate_candidate(self.cluster2, self.official)
        finally:
            evidence_path.write_bytes(original)
            copied.unlink()

    def test_mutant_identity_artifact_swap_is_killed(self) -> None:
        evidence_path = self.cluster2 / "evidence.json"
        document = json.loads(evidence_path.read_bytes())
        source = self.cluster2 / document["identity_artifacts"]["source_bundle"]["path"]
        original = source.read_bytes()
        source.write_bytes(b"swapped source bytes\n")
        try:
            with self.assertRaisesRegex(E.EvaluationError, "SHA mismatch"):
                E.validate_candidate(self.cluster2, self.official)
        finally:
            source.write_bytes(original)

    def test_pareto_does_not_rank_correctness_failure(self) -> None:
        # Correctness failures are rejected before pareto(); this verifies no
        # favorable performance vector is generated for a failed reset bundle.
        with self.mutate_json_artifact(
                self.fovea, "reset", lambda doc: doc.__setitem__("no_duplicate", False)):
            with self.assertRaises(E.EvaluationError):
                E.evaluate(self.fovea, self.cluster2, GENERATOR, MANIFEST_ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
