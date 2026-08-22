from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

from benchmarks.redred_mc_wtb_predictor_stage3 import execution_authority as authority_module
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
    CurrentCAVTraceError,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
    logical_replay_authority,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
    CANDIDATE_ID as PLL_CANDIDATE_ID,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_stage4_contract import canonical_json_bytes, canonical_sha256
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


def production_id_fixture():
    registry, events, poses = neutral_fixture()
    replacements = {
        "w0": "shapes_rotation/query_start_ns=50000000",
        "w1": "shapes_rotation/query_start_ns=60000000",
    }
    production_registry = tuple(Registry(
        replacements[row.window_id],
        row.warmup_start_ns_inclusive,
        row.query_start_ns_inclusive,
        row.query_end_ns_exclusive,
    ) for row in registry)
    return (
        production_registry,
        {replacements[key]: value for key, value in events.items()},
        {replacements[key]: value for key, value in poses.items()},
    )


def production_id_labels():
    replacements = {
        "w0": "shapes_rotation/query_start_ns=50000000",
        "w1": "shapes_rotation/query_start_ns=60000000",
    }
    result = {}
    for old_id, label in labels().items():
        row = dict(label)
        row["window_id"] = replacements[old_id]
        result[replacements[old_id]] = row
    return result


class ExecutionAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry, events, poses = neutral_fixture()
        cls.execution = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
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
            execution["schema"],
            "redred.mc_wtb_predictor_stage3.execution_input/v3",
        )
        self.assertEqual(
            execution["timing_authority"]["candidate_screen_preroll_rule"][
                "cycle_boundary"
            ],
            "last_warmup_occurrence_cycle<"
            "timestamp_to_cycle(query_start_ns,warmup_start_ns)"
            "<=first_query_occurrence_cycle",
        )
        self.assertEqual(
            execution["logical_ingress_profile"]["scope"],
            "MODEL_ONLY_LOGICAL_REPLAY_NO_RTL_OR_PPA_CLAIM",
        )
        self.assertEqual(
            execution["logical_ingress_profile"],
            STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.to_mapping(),
        )
        replay_authority = logical_replay_authority()
        self.assertEqual(
            execution["logical_cycle_replay_authority"], replay_authority
        )
        self.assertEqual(
            execution["logical_cycle_replay_authority_sha256"],
            replay_authority["authority_sha256"],
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

    def test_warmup_query_boundary_must_be_cycle_atomic(self):
        registry, event_streams, pose_streams = neutral_fixture()
        query_start = registry[0].query_start_ns_inclusive
        colliding = dict(event_streams)
        colliding["w0"] = (
            event(100, query_start - 1, query_start, 2),
            event(101, query_start, query_start, 2),
        )
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "boundary is not cycle atomic"
        ):
            build_stage3_execution_input(
                registry,
                colliding,
                pose_streams,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

        # Capture the exact artifact the pre-fix builder would have returned,
        # then prove that the public verifier independently rejects it.
        with mock.patch.object(
            authority_module, "verify_stage3_execution_input", return_value=ZERO_SHA
        ):
            pre_fix_artifact = build_stage3_execution_input(
                registry,
                colliding,
                pose_streams,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "boundary is not cycle atomic"
        ):
            verify_stage3_execution_input(pre_fix_artifact, repo_root=ROOT)

        delayed_query = dict(event_streams)
        delayed_query["w0"] = (
            event(100, query_start - 1, query_start, 2),
            event(101, query_start + 3, query_start, 2),
        )
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "boundary is not cycle atomic"
        ):
            build_stage3_execution_input(
                registry,
                delayed_query,
                pose_streams,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

        query_only = dict(event_streams)
        query_only["w0"] = (event(101, query_start, query_start, 2),)
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "event phase is empty"
        ):
            build_stage3_execution_input(
                registry,
                query_only,
                pose_streams,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

        adjacent = dict(event_streams)
        adjacent["w0"] = (
            event(100, query_start - 5, query_start, 2),
            event(101, query_start, query_start, 2),
        )
        accepted = build_stage3_execution_input(
            registry,
            adjacent,
            pose_streams,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        self.assertEqual(
            verify_stage3_execution_input(
                accepted,
                expected_aggregate_sha256=accepted["aggregate_sha256"],
                repo_root=ROOT,
            ),
            accepted["aggregate_sha256"],
        )

    def test_builder_locks_repository_runner_and_exact_stage3_profile(self):
        registry, events, poses = neutral_fixture()

        def substituted_runner(*args, **kwargs):
            return run_cycle_model(*args, **kwargs)

        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "repository runner"):
            build_stage3_execution_input(
                registry,
                events,
                poses,
                source_events_authority=source_authority(),
                cycle_runner=substituted_runner,
                repo_root=ROOT,
            )
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "locked profile"):
            build_stage3_execution_input(
                registry,
                events,
                poses,
                source_events_authority=source_authority(),
                cycle_profile=object(),
                repo_root=ROOT,
            )

        resealed = deepcopy(self.execution)
        resealed["logical_ingress_profile"]["profile_id"] = "SUBSTITUTED_PROFILE"
        resealed["logical_ingress_profile_sha256"] = canonical_sha256(
            resealed["logical_ingress_profile"]
        )
        reseal(resealed)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "locked Stage3 profile"):
            verify_stage3_execution_input(resealed, repo_root=ROOT)

    def test_execution_bytes_are_identical_under_label_mutation(self):
        registry, events, poses = neutral_fixture()
        before = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        mutated_labels = labels()
        mutated_labels["w0"]["axis"] = "Y"
        mutated_labels["w0"]["rank_sha256"] = THREE_SHA
        after = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        self.assertNotEqual(mutated_labels, labels())
        self.assertEqual(canonical_json_bytes(before), canonical_json_bytes(after))

    def test_replay_authority_object_and_companion_digest_fail_closed(self):
        companion = deepcopy(self.execution)
        companion["logical_cycle_replay_authority_sha256"] = ZERO_SHA
        reseal(companion)
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "authority digest differs"
        ):
            verify_stage3_execution_input(companion, repo_root=ROOT)

        substituted = deepcopy(self.execution)
        authority = substituted["logical_cycle_replay_authority"]
        authority["event_order_rule"] = "substituted"
        authority["authority_sha256"] = canonical_sha256({
            key: value
            for key, value in authority.items()
            if key != "authority_sha256"
        })
        substituted["logical_cycle_replay_authority_sha256"] = authority[
            "authority_sha256"
        ]
        reseal(substituted)
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "locked Stage3 authority"
        ):
            verify_stage3_execution_input(substituted, repo_root=ROOT)

        extra = deepcopy(self.execution)
        extra["logical_cycle_replay_authority"]["free_digest"] = ZERO_SHA
        extra["logical_cycle_replay_authority_sha256"] = extra[
            "logical_cycle_replay_authority"
        ]["authority_sha256"]
        reseal(extra)
        with self.assertRaisesRegex(
            Stage3ExecutionAuthorityError, "field schema differs"
        ):
            verify_stage3_execution_input(extra, repo_root=ROOT)

    def test_unique_decreasing_event_ids_preserve_source_order(self):
        registry, _, poses = neutral_fixture()
        decreasing = {
            "w0": (
                event(900, 20_000_000, 50_000_000, 1),
                event(100, 50_100_000, 50_000_000, 2),
            ),
            "w1": (
                event(900, 20_000_000, 60_000_000, 1),
                event(50, 60_100_000, 60_000_000, 3),
            ),
        }
        built = build_stage3_execution_input(
            registry,
            decreasing,
            poses,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        expected = ((900, 100), (900, 50))
        self.assertEqual(
            tuple(
                tuple(row["event_id"] for row in window["events"])
                for window in built["windows"]
            ),
            expected,
        )
        self.assertEqual(
            tuple(
                tuple(
                    row["event_id"]
                    for row in window["simulation"]["records"]
                )
                for window in built["score_free_current_cav_trace"]["windows"]
            ),
            expected,
        )
        self.assertEqual(
            verify_stage3_execution_input(built, repo_root=ROOT),
            built["aggregate_sha256"],
        )
        schema = json.loads((
            ROOT / "benchmarks/redred_mc_wtb_predictor_stage3"
            / "stage3_execution_input.schema.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(built)), []
        )

    def test_duplicate_ids_and_backwards_source_time_fail_closed(self):
        registry, events, poses = neutral_fixture()
        duplicate = dict(events)
        duplicate["w0"] = (
            event(900, 20_000_000, 50_000_000, 1),
            event(900, 50_100_000, 50_000_000, 2),
        )
        with self.assertRaisesRegex(
            CurrentCAVTraceError, "event IDs repeat within a window"
        ):
            build_stage3_execution_input(
                registry,
                duplicate,
                poses,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

        backwards = dict(events)
        backwards["w0"] = tuple(reversed(events["w0"]))
        with self.assertRaisesRegex(
            CurrentCAVTraceError, "event timestamps move backwards"
        ):
            build_stage3_execution_input(
                registry,
                backwards,
                poses,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

    def test_production_window_ids_and_actual_candidate_ids(self):
        registry, events, poses = production_id_fixture()
        execution = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        authority = build_stage3_label_authority(
            execution,
            production_id_labels(),
            selector_authority=selector_authority(),
            repo_root=ROOT,
        )
        expected_ids = [row.window_id for row in registry]
        self.assertEqual(
            [row["window_id"] for row in execution["neutral_registry"]],
            expected_ids,
        )
        schema_path = (
            ROOT / "benchmarks/redred_mc_wtb_predictor_stage3/"
            "stage3_scoring_join_receipt.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for candidate_id in (RG3_POLICY.candidate_id, PLL_CANDIDATE_ID):
            with self.subTest(candidate_id=candidate_id):
                output = candidate_output(execution)
                output["candidate_id"] = candidate_id
                reseal(output)
                receipt = build_stage3_scoring_join_receipt(
                    execution, output, authority, repo_root=ROOT
                )
                self.assertEqual(receipt["candidate_id"], candidate_id)
                self.assertEqual(list(validator.iter_errors(receipt)), [])

        invalid_output = candidate_output(execution)
        invalid_output["candidate_id"] = "candidate with spaces"
        reseal(invalid_output)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "candidate identifier"):
            build_stage3_scoring_join_receipt(
                execution, invalid_output, authority, repo_root=ROOT
            )

        bad_registry = list(registry)
        bad_registry[0] = Registry(
            "shapes_rotation/query_start_ns=50000001",
            registry[0].warmup_start_ns_inclusive,
            registry[0].query_start_ns_inclusive,
            registry[0].query_end_ns_exclusive,
        )
        bad_events = dict(events)
        bad_events[bad_registry[0].window_id] = bad_events.pop(registry[0].window_id)
        bad_poses = dict(poses)
        bad_poses[bad_registry[0].window_id] = bad_poses.pop(registry[0].window_id)
        with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "query timestamp"):
            build_stage3_execution_input(
                bad_registry,
                bad_events,
                bad_poses,
                source_events_authority=source_authority(),
                repo_root=ROOT,
            )

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

    def test_package_alias_forbidden_import_mutations_fail_closed(self):
        import hashlib

        for source_line in (
            "from benchmarks.redred_mc_wtb_predictor_stage3 import screen108\n",
            "from . import screen108\n",
        ):
            with self.subTest(source_line=source_line), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for relative in CONSUMER_DEPENDENCY_PATHS:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / relative, target)
                framework_path = root / "benchmarks/redred_mc_wtb_predictor_stage3/framework.py"
                framework_path.write_text(
                    source_line + framework_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                mutated = deepcopy(self.execution)
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

    def test_resealed_forbidden_candidate_fields_fail_closed(self):
        mutations = []
        top_level = deepcopy(self.output)
        top_level["selector_rank_sha256"] = ZERO_SHA
        reseal(top_level)
        mutations.append(top_level)

        nested_window = deepcopy(self.output)
        nested_window["windows"][0]["label_metadata"] = {"axis": "X"}
        reseal(nested_window)
        mutations.append(nested_window)

        nested_event = deepcopy(self.output)
        nested_event["windows"][0]["events"][0]["score"] = 1.0
        nested_event["windows"][0]["events_sha256"] = canonical_sha256(
            nested_event["windows"][0]["events"]
        )
        reseal(nested_event)
        mutations.append(nested_event)

        evaluator_nested = deepcopy(self.output)
        evaluator_nested["evidence"] = {"evaluator_version": "v1"}
        reseal(evaluator_nested)
        mutations.append(evaluator_nested)

        for mutated in mutations:
            with self.subTest(keys=sorted(mutated.keys())):
                with self.assertRaisesRegex(Stage3ExecutionAuthorityError, "forbidden field"):
                    build_stage3_scoring_join_receipt(
                        self.execution,
                        mutated,
                        self.label_authority,
                        repo_root=ROOT,
                    )

    def test_candidate_output_is_verified_before_label_authority_opens(self):
        original_candidate = authority_module._candidate_output_bindings
        original_labels = authority_module.verify_stage3_label_authority
        calls = []

        def candidate_spy(*args, **kwargs):
            result = original_candidate(*args, **kwargs)
            calls.append("output_verified")
            return result

        def label_spy(*args, **kwargs):
            calls.append("label_opened")
            return original_labels(*args, **kwargs)

        with mock.patch.object(
            authority_module, "_candidate_output_bindings", side_effect=candidate_spy
        ), mock.patch.object(
            authority_module, "verify_stage3_label_authority", side_effect=label_spy
        ):
            build_stage3_scoring_join_receipt(
                self.execution, self.output, self.label_authority, repo_root=ROOT
            )
        self.assertEqual(
            calls,
            ["output_verified", "label_opened", "output_verified", "label_opened"],
        )

        calls.clear()
        with mock.patch.object(
            authority_module, "_candidate_output_bindings", side_effect=candidate_spy
        ), mock.patch.object(
            authority_module, "verify_stage3_label_authority", side_effect=label_spy
        ):
            verify_stage3_scoring_join_receipt(
                self.join,
                self.execution,
                self.output,
                self.label_authority,
                repo_root=ROOT,
            )
        self.assertEqual(calls, ["output_verified", "label_opened"])

        unsealed = deepcopy(self.output)
        del unsealed["aggregate_sha256"]
        for operation in ("build", "verify"):
            calls.clear()
            with self.subTest(operation=operation), mock.patch.object(
                authority_module, "_candidate_output_bindings", side_effect=candidate_spy
            ), mock.patch.object(
                authority_module, "verify_stage3_label_authority", side_effect=label_spy
            ):
                with self.assertRaises(Stage3ExecutionAuthorityError):
                    if operation == "build":
                        build_stage3_scoring_join_receipt(
                            self.execution, unsealed, self.label_authority, repo_root=ROOT
                        )
                    else:
                        verify_stage3_scoring_join_receipt(
                            self.join,
                            self.execution,
                            unsealed,
                            self.label_authority,
                            repo_root=ROOT,
                        )
            self.assertEqual(calls, [])

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

            nested = deepcopy(artifact)
            if filename == "stage3_execution_input.schema.json":
                nested["windows"][0]["events"][0]["unexpected"] = True
            elif filename == "stage3_label_authority.schema.json":
                nested["labels"][0]["unexpected"] = True
            else:
                continue
            self.assertTrue(list(validator.iter_errors(nested)))

    def test_built_execution_round_trips_committed_schema_loader_and_dependencies(self):
        registry, events, poses = neutral_fixture()
        built = build_stage3_execution_input(
            registry,
            events,
            poses,
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        encoded = canonical_json_bytes(built)
        loaded = json.loads(encoded.decode("ascii"))
        schema_path = (
            ROOT / "benchmarks/redred_mc_wtb_predictor_stage3"
            / "stage3_execution_input.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(loaded)), [])
        self.assertEqual(
            verify_stage3_execution_input(
                loaded,
                expected_aggregate_sha256=built["aggregate_sha256"],
                repo_root=ROOT,
            ),
            built["aggregate_sha256"],
        )
        expected_paths = (
            "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
            "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
            "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
            "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
            "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
            "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
        )
        dependencies = loaded["consumer_dependency_manifest"]
        self.assertEqual(len(dependencies), 9)
        self.assertEqual(
            tuple(row["path"] for row in dependencies), expected_paths
        )
        self.assertEqual(
            loaded["logical_ingress_profile"]["schema"],
            "redred.mc_wtb_predictor_stage3.logical_ingress_profile/v1",
        )
        self.assertEqual(
            loaded["logical_cycle_replay_authority"],
            logical_replay_authority(),
        )
        self.assertEqual(
            loaded["logical_cycle_replay_authority_sha256"],
            loaded["logical_cycle_replay_authority"]["authority_sha256"],
        )
        schema_mutation = deepcopy(loaded)
        schema_mutation["logical_cycle_replay_authority"]["unexpected"] = True
        self.assertTrue(list(validator.iter_errors(schema_mutation)))

    def test_execution_v2_sources_parse_with_python38_grammar(self):
        paths = (
            "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
        )
        for relative in paths:
            with self.subTest(path=relative):
                ast.parse(
                    (ROOT / relative).read_text(encoding="utf-8"),
                    feature_version=(3, 8),
                )


if __name__ == "__main__":
    unittest.main()
