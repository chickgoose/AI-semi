from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    CONSUMER_DEPENDENCY_PATHS,
    Stage3ExecutionAuthorityError,
    build_stage3_execution_input,
    build_stage3_label_authority,
    build_stage3_scoring_join_receipt,
    verify_stage3_execution_input,
    verify_stage3_label_authority,
    verify_stage3_scoring_join_receipt,
)
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    pose_timestamp_to_cycle,
    run_cycle_model,
)


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_MODULE = "benchmarks.redred_mc_wtb_predictor_stage3.execution_authority"
ZERO_SHA = "0" * 64
ONE_SHA = "1" * 64
TWO_SHA = "2" * 64
THREE_SHA = "3" * 64


@dataclass(frozen=True)
class Registry:
    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int


@dataclass(frozen=True)
class NeutralEvent:
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: tuple
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool


@dataclass(frozen=True)
class NeutralPose:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: tuple
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool


@dataclass(frozen=True)
class LogicalIngressProfile:
    profile_id: str = "TEST_LOGICAL_INGRESS_8X2_V1"
    raw_ingress_lanes: int = 8
    ingress_staging_entries: int = 8
    event_service_lanes: int = 2
    scope: str = "MODEL_ONLY_LOGICAL_REPLAY"

    def to_mapping(self):
        return {
            "schema": "test.logical_ingress_profile/v1",
            "profile_id": self.profile_id,
            "raw_ingress_lanes": self.raw_ingress_lanes,
            "ingress_staging_entries": self.ingress_staging_entries,
            "event_service_lanes": self.event_service_lanes,
            "scope": self.scope,
        }


def logical_cycle_runner(**arguments):
    arguments.pop("ingress_profile")
    return run_cycle_model(**arguments)


def pose(window_start_ns: int, pose_id: int, timestamp_ns: int) -> NeutralPose:
    quaternion = (0.0, 0.0, 0.0, 1.0)
    return NeutralPose(
        pose_id,
        timestamp_ns,
        pose_timestamp_to_cycle(timestamp_ns, window_start_ns),
        quaternion,
        canonical_pose_value_sha256(pose_id, timestamp_ns, quaternion),
        True,
        True,
    )


def event(event_id: int, timestamp_ns: int, query_start_ns: int, causal_pose_id: int) -> NeutralEvent:
    ray = (1.0, 0.0, 0.0)
    is_query = timestamp_ns >= query_start_ns
    polarity = event_id % 2
    return NeutralEvent(
        event_id,
        timestamp_ns,
        polarity,
        is_query,
        ray,
        causal_pose_id,
        canonical_event_content_sha256(
            event_id,
            timestamp_ns,
            polarity,
            is_query,
            ray,
            causal_pose_id,
            True,
        ),
        True,
    )


def neutral_fixture():
    registry = (
        Registry("w0", 0, 50_000_000, 51_000_000),
        Registry("w1", 10_000_000, 60_000_000, 61_000_000),
    )
    poses = {
        "w0": (
            pose(0, 0, 0),
            pose(0, 1, 19_000_000),
            pose(0, 2, 49_000_000),
        ),
        "w1": (
            pose(10_000_000, 0, 0),
            pose(10_000_000, 1, 19_000_000),
            pose(10_000_000, 2, 49_000_000),
            pose(10_000_000, 3, 59_000_000),
        ),
    }
    events = {
        "w0": (
            event(100, 20_000_000, 50_000_000, 1),
            event(101, 50_100_000, 50_000_000, 2),
        ),
        # Event 100 is intentionally repeated as a pre-roll occurrence.  Its
        # source identity must not be confused with a second unique event.
        "w1": (
            event(100, 20_000_000, 60_000_000, 1),
            event(102, 60_100_000, 60_000_000, 3),
        ),
    }
    return registry, events, poses


def reseal(mapping):
    mapping["aggregate_sha256"] = canonical_sha256({
        key: value for key, value in mapping.items() if key != "aggregate_sha256"
    })


def source_authority():
    return {
        "source_events_path": "external/new108/events.txt",
        "source_events_sha256": ZERO_SHA,
        "source_events_size_bytes": 1234,
        "source_events_line_count": 4,
    }


def selector_authority():
    return {
        "stage12_freeze_receipt_sha256": ZERO_SHA,
        "stage12_source_split_plan_sha256": ONE_SHA,
        "selector_registry_sha256": TWO_SHA,
        "selector_implementation_sha256": THREE_SHA,
    }


def labels():
    return {
        "w0": {
            "window_id": "w0",
            "axis": "X",
            "sign": "POSITIVE",
            "motion_bin": "MID",
            "rotation_vector_rad": [0.01, 0.0, 0.0],
            "purity": 1.0,
            "motion_proxy": 0.01,
            "rank_sha256": ONE_SHA,
        },
        "w1": {
            "window_id": "w1",
            "axis": "Z",
            "sign": "NEGATIVE",
            "motion_bin": "HIGH",
            "rotation_vector_rad": [0.0, 0.0, -0.04],
            "purity": 0.95,
            "motion_proxy": 0.04,
            "rank_sha256": TWO_SHA,
        },
    }


def candidate_output(execution):
    windows = []
    for window in execution["windows"]:
        events = [
            {
                "event_id": event_row["event_id"],
                "world_ray": list(event_row["sensor_ray"]),
                "route": "current_cav",
            }
            for event_row in window["events"]
        ]
        windows.append({
            "window_id": window["window_id"],
            "events": events,
            "events_sha256": canonical_sha256(events),
        })
    body = {
        "schema": "test.stage3_candidate_output/v1",
        "candidate_id": "TEST_NATIVE",
        "adapter_aggregate_sha256": execution["aggregate_sha256"],
        "neutral_input_sha256": execution["neutral_input_sha256"],
        "windows": windows,
    }
    body["aggregate_sha256"] = canonical_sha256(body)
    return body


class ExecutionAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry, events, poses = neutral_fixture()
        cls.execution = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
            cycle_profile=LogicalIngressProfile(),
            cycle_runner=logical_cycle_runner,
            repo_root=ROOT,
        )
        cls.label_authority = build_stage3_label_authority(
            cls.execution,
            labels(),
            selector_authority=selector_authority(),
            repo_root=ROOT,
        )
        cls.output = candidate_output(cls.execution)
        cls.join = build_stage3_scoring_join_receipt(
            cls.execution,
            cls.output,
            cls.label_authority,
            repo_root=ROOT,
        )

    def test_execution_is_exact_50ms_label_free_and_overlap_safe(self):
        execution = self.execution
        self.assertEqual(execution["window_count"], 2)
        self.assertEqual(execution["source_event_occurrence_count"], 4)
        self.assertEqual(execution["unique_source_event_count"], 3)
        self.assertEqual(execution["warmup_event_occurrence_count"], 2)
        self.assertEqual(execution["query_event_count"], 2)
        self.assertEqual(execution["timing_authority"]["pre_roll_ns"], 50_000_000)
        self.assertEqual(
            execution["logical_ingress_profile"]["scope"],
            "MODEL_ONLY_LOGICAL_REPLAY",
        )
        self.assertEqual(
            verify_stage3_execution_input(
                execution,
                expected_aggregate_sha256=execution["aggregate_sha256"],
                repo_root=ROOT,
            ),
            execution["aggregate_sha256"],
        )
        encoded = json.dumps(execution, sort_keys=True).lower()
        for forbidden in (
            '"label"', '"labels"', '"rank_sha256"', '"selector"',
            '"evaluator"', '"scorer"',
        ):
            self.assertNotIn(forbidden, encoded)

    def test_dependency_closure_has_no_authority_or_scoring_modules(self):
        paths = [row["path"] for row in self.execution["consumer_dependency_manifest"]]
        self.assertEqual(paths, list(CONSUMER_DEPENDENCY_PATHS))
        self.assertNotIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
            paths,
        )
        for path in paths:
            lowered = path.lower()
            self.assertNotIn("selector", lowered)
            self.assertNotIn("evaluator", lowered)
            self.assertNotIn("screen", lowered)

    def test_clean_import_does_not_load_label_or_scoring_dependencies(self):
        script = (
            "import json,sys; import " + AUTHORITY_MODULE + "; "
            "bad=[m for m in sys.modules if (m.endswith('.selector') or "
            "m.endswith('.evaluator') or m.endswith('.screen108') or "
            "'causal_reference' in m or 'motion_qualification' in m)]; "
            "print(json.dumps(sorted(bad)))"
        )
        completed = subprocess.run(
            [sys.executable, "-S", "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout), [])

    def test_closed_execution_rejects_label_or_unknown_fields(self):
        mutated = deepcopy(self.execution)
        mutated["selector_rank_sha256"] = ZERO_SHA
        reseal(mutated)
        with self.assertRaises(Stage3ExecutionAuthorityError):
            verify_stage3_execution_input(mutated, repo_root=ROOT)

    def test_exact_preroll_mutation_fails_even_with_new_top_seal(self):
        mutated = deepcopy(self.execution)
        mutated["neutral_registry"][0]["warmup_start_ns_inclusive"] = 1
        mutated["neutral_registry_sha256"] = canonical_sha256(mutated["neutral_registry"])
        reseal(mutated)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "50 ms"):
            verify_stage3_execution_input(mutated, repo_root=ROOT)

    def test_dependency_path_alias_and_digest_mutations_fail(self):
        for replacement in (
            "./benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
        ):
            mutated = deepcopy(self.execution)
            mutated["consumer_dependency_manifest"][0]["path"] = replacement
            mutated["consumer_dependency_aggregate_sha256"] = canonical_sha256(
                mutated["consumer_dependency_manifest"]
            )
            reseal(mutated)
            with self.assertRaises(Stage3ExecutionAuthorityError):
                verify_stage3_execution_input(mutated, repo_root=ROOT)
        source_alias = deepcopy(self.execution)
        source_alias["source_events_authority"]["source_events_path"] = (
            "../private/labels.json"
        )
        reseal(source_alias)
        with self.assertRaises(Stage3ExecutionAuthorityError):
            verify_stage3_execution_input(source_alias, repo_root=ROOT)

    def test_forbidden_import_mutation_fails_dependency_closure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in CONSUMER_DEPENDENCY_PATHS:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            framework_path = root / "benchmarks/redred_mc_wtb_predictor_stage3/framework.py"
            framework_path.write_text(
                "from benchmarks.redred_mc_wtb_predictor_stage3.evaluator import score\n"
                + framework_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            mutated = deepcopy(self.execution)
            import hashlib
            for dependency in mutated["consumer_dependency_manifest"]:
                dependency["sha256"] = hashlib.sha256(
                    (root / dependency["path"]).read_bytes()
                ).hexdigest()
            mutated["consumer_dependency_aggregate_sha256"] = canonical_sha256(
                mutated["consumer_dependency_manifest"]
            )
            reseal(mutated)
            with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "forbidden"):
                verify_stage3_execution_input(mutated, repo_root=root)

    def test_event_pose_and_current_cav_mutations_fail(self):
        mutations = []
        ray = deepcopy(self.execution)
        ray["windows"][0]["events"][0]["sensor_ray"] = [0.0, 1.0, 0.0]
        reseal(ray)
        mutations.append(ray)
        pose_row = deepcopy(self.execution)
        pose_row["windows"][0]["poses"][0]["commit_cycle"] = -1
        reseal(pose_row)
        mutations.append(pose_row)
        trace = deepcopy(self.execution)
        trace["score_free_current_cav_trace"]["windows"][0]["simulation"]["records"][0]["disposition_reason"] = "stale_pose"
        reseal(trace)
        mutations.append(trace)
        for mutated in mutations:
            with self.assertRaises(Stage3ExecutionAuthorityError):
                verify_stage3_execution_input(mutated, repo_root=ROOT)

    def test_fully_resealed_current_cav_edge_mutation_still_fails_semantics(self):
        mutated = deepcopy(self.execution)
        trace = mutated["score_free_current_cav_trace"]
        record = trace["windows"][0]["simulation"]["records"][0]
        record["occurrence_cycle"] += 1
        record["decision_sha256"] = canonical_sha256({
            key: value for key, value in record.items() if key != "decision_sha256"
        })
        simulation = trace["windows"][0]["simulation"]
        simulation["decision_records_sha256"] = canonical_sha256(
            simulation["records"]
        )
        trace_window = trace["windows"][0]
        trace_window["window_sha256"] = canonical_sha256({
            key: value for key, value in trace_window.items()
            if key != "window_sha256"
        })
        mutated["windows"][0]["current_cav_window_trace_sha256"] = (
            trace_window["window_sha256"]
        )
        decision_windows = [{
            "window_id": window["registry"]["window_id"],
            "decisions": window["simulation"]["records"],
        } for window in trace["windows"]]
        trace["baseline_decisions_sha256"] = canonical_sha256({
            "schema": trace["baseline_schema"],
            "windows": decision_windows,
        })
        trace["aggregate_sha256"] = canonical_sha256({
            key: value for key, value in trace.items() if key != "aggregate_sha256"
        })
        mutated["score_free_current_cav_trace_sha256"] = trace["aggregate_sha256"]
        mutated["windows_sha256"] = canonical_sha256(mutated["windows"])
        reseal(mutated)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "causal decision"):
            verify_stage3_execution_input(mutated, repo_root=ROOT)

    def test_label_authority_is_separate_and_cross_bound(self):
        authority = self.label_authority
        self.assertEqual(
            authority["execution_input_aggregate_sha256"],
            self.execution["aggregate_sha256"],
        )
        self.assertEqual(
            verify_stage3_label_authority(
                authority,
                self.execution,
                expected_labels_sidecar_sha256=authority["selector_labels_sidecar_sha256"],
                repo_root=ROOT,
            ),
            authority["aggregate_sha256"],
        )
        mutated = deepcopy(authority)
        mutated["labels"][0]["motion_bin"] = "HIGH"
        reseal(mutated)
        with self.assertRaises(Stage3ExecutionAuthorityError):
            verify_stage3_label_authority(mutated, self.execution, repo_root=ROOT)

    def test_join_requires_already_sealed_conserved_candidate_output(self):
        unsealed = deepcopy(self.output)
        del unsealed["aggregate_sha256"]
        with self.assertRaises(Stage3ExecutionAuthorityError):
            build_stage3_scoring_join_receipt(
                self.execution, unsealed, self.label_authority, repo_root=ROOT
            )
        stale = deepcopy(self.output)
        stale["windows"][0]["events"][0]["world_ray"] = [0.0, 1.0, 0.0]
        with self.assertRaises(Stage3ExecutionAuthorityError):
            build_stage3_scoring_join_receipt(
                self.execution, stale, self.label_authority, repo_root=ROOT
            )
        reordered = deepcopy(self.output)
        reordered["windows"][0]["events"].reverse()
        reordered["windows"][0]["events_sha256"] = canonical_sha256(
            reordered["windows"][0]["events"]
        )
        reseal(reordered)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "conservation"):
            build_stage3_scoring_join_receipt(
                self.execution, reordered, self.label_authority, repo_root=ROOT
            )

    def test_scoring_join_receipt_binds_all_three_seals(self):
        self.assertEqual(
            verify_stage3_scoring_join_receipt(
                self.join,
                self.execution,
                self.output,
                self.label_authority,
                expected_aggregate_sha256=self.join["aggregate_sha256"],
                repo_root=ROOT,
            ),
            self.join["aggregate_sha256"],
        )
        mutated = deepcopy(self.join)
        mutated["candidate_output_aggregate_sha256"] = ZERO_SHA
        reseal(mutated)
        with self.assertRaises(Stage3ExecutionAuthorityError):
            verify_stage3_scoring_join_receipt(
                mutated,
                self.execution,
                self.output,
                self.label_authority,
                repo_root=ROOT,
            )

    def test_exact_json_schemas_validate_all_artifacts_and_reject_unknowns(self):
        cases = (
            ("stage3_execution_input.schema.json", self.execution),
            ("stage3_label_authority.schema.json", self.label_authority),
            ("stage3_scoring_join_receipt.schema.json", self.join),
        )
        schema_root = ROOT / "benchmarks/redred_mc_wtb_predictor_stage3"
        for filename, artifact in cases:
            schema = json.loads((schema_root / filename).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            self.assertEqual(list(validator.iter_errors(artifact)), [])
            mutated = deepcopy(artifact)
            mutated["unexpected"] = True
            self.assertTrue(list(validator.iter_errors(mutated)))


if __name__ == "__main__":
    unittest.main()
