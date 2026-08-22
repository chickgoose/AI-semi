from __future__ import annotations

import ast
import copy
from dataclasses import replace
import hashlib
import inspect
import math
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import pll_query_stream
from benchmarks.redred_mc_wtb_predictor_stage3 import pll_query_stream_core
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
    generate_locked_pll_output,
    locked_config_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_query_stream import (
    BATCH_PROVENANCE_EQUIVALENCE_HOLD,
    INPUT_DOMAIN_HOLD,
    NATIVE_TRANSITION_HOLD,
    OUTPUT_AUTHORITY_HOLD,
    PLL_QUERY_STREAM_SCHEMA,
    PLLQueryStreamError,
    VERIFIED_INPUT_HOLD,
    generate_pll_query_stream,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_query_stream_core import (
    PLL_STREAM_CONFIG_SHA256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import pose_timestamp_to_cycle
from tests.redred_mc_wtb_predictor_stage3_execution_authority.test_execution_authority import (
    source_authority,
)
from tests.redred_mc_wtb_predictor_stage3_pll_output.test_pll_output import (
    _candidate_fixture,
    _cav_unlocked_fixture,
    _fallback_fixture,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "benchmarks" / "redred_mc_wtb_predictor_stage3"


def _execution(bundle):
    return build_stage3_execution_input(
        bundle.neutral_registry,
        bundle.event_streams,
        bundle.pose_streams,
        source_events_authority=source_authority(),
        repo_root=ROOT,
    )


def _assert_manifest(testcase, manifest):
    body = dict(manifest)
    supplied = body.pop("manifest_sha256")
    testcase.assertEqual(supplied, canonical_sha256(body))
    for row in manifest["files"]:
        testcase.assertEqual(
            row["sha256"],
            hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(),
        )


def _clustered_execution():
    bundle, _ = _candidate_fixture()
    window_id = bundle.neutral_registry[0].window_id
    rows = list(bundle.event_streams[window_id])
    template = rows[1]
    event_id = 99
    digest = canonical_event_content_sha256(
        event_id,
        template.timestamp_ns,
        template.polarity,
        template.is_query,
        template.sensor_ray,
        template.causal_pose_source_index,
        template.transform_guard_valid,
    )
    rows.insert(2, replace(template, event_id=event_id, event_content_sha256=digest))
    bundle.event_streams = {window_id: tuple(rows)}
    return _execution(bundle)


def _long_warmup_execution():
    start = 0
    query = 50_000_000
    window_id = "pll-long-warmup"
    registry = (NeutralRegistryWindow(window_id, start, query, query + 1_000),)
    poses = []
    for pose_id in range(49):
        timestamp = (pose_id + 1) * 1_000_000
        angle = 0.001 * pose_id
        quaternion = (0.0, 0.0, math.sin(angle / 2.0), math.cos(angle / 2.0))
        poses.append(NeutralPoseInput(
            pose_id,
            timestamp,
            pose_timestamp_to_cycle(timestamp, start),
            quaternion,
            canonical_pose_value_sha256(pose_id, timestamp, quaternion),
            True,
            True,
        ))
    ray = (1.0, 0.0, 0.0)
    events = []
    for event_id, timestamp, is_query in (
        (1, 49_500_000, False),
        (2, query, True),
    ):
        digest = canonical_event_content_sha256(
            event_id, timestamp, 0, is_query, ray, 48, True
        )
        events.append(NeutralEventInput(
            event_id, timestamp, 0, is_query, ray, 48, digest, True
        ))
    return build_stage3_execution_input(
        registry,
        {window_id: tuple(events)},
        {window_id: tuple(poses)},
        source_events_authority=source_authority(),
        repo_root=ROOT,
    )


class PLLQueryStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle, cls.baseline = _candidate_fixture()
        cls.execution = _execution(cls.bundle)

    def test_query_semantics_match_batch_without_claiming_provenance_byte_equivalence(self):
        streamed = generate_pll_query_stream(self.execution)
        batch = generate_locked_pll_output(self.bundle, self.baseline)
        self.assertEqual(streamed["schema"], PLL_QUERY_STREAM_SCHEMA)
        query_ids = [
            event["event_id"]
            for event in self.execution["windows"][0]["events"]
            if event["is_query"]
        ]
        batch_rows = [
            row for row in batch["windows"][0]["events"]
            if row["event_id"] in query_ids
        ]
        stream_rows = streamed["windows"][0]["query_rows"]
        for actual, expected in zip(stream_rows, batch_rows):
            for field in (
                "event_id",
                "event_content_sha256",
                "occurrence_cycle",
                "decision_cycle",
                "model_id",
                "predictor_state_version",
                "candidate_attempted",
                "candidate_used",
                "candidate_failure_reason",
                "route",
                "fallback_reason",
                "candidate_quaternion_xyzw",
                "world_ray",
            ):
                self.assertEqual(actual[field], expected[field])
            self.assertNotIn("used_pose_ids", actual)
            self.assertEqual(
                actual["candidate_dependency_pose_count"],
                len(expected["state_dependency_pose_ids"]),
            )
            self.assertEqual(
                actual["candidate_direct_anchor_pose_id"],
                expected["state_dependency_pose_ids"][-1],
            )
        self.assertEqual(
            streamed["batch_provenance_equivalence_hold"],
            BATCH_PROVENANCE_EQUIVALENCE_HOLD,
        )
        self.assertEqual(
            streamed["candidate_provenance_representation"],
            "direct_anchor_plus_dependency_chain_endpoint_and_count",
        )

    def test_same_edge_cluster_observes_pre_transition_state_then_future_sees_publication(self):
        output = generate_pll_query_stream(_clustered_execution())
        window = output["windows"][0]
        same_edge = window["query_rows"][:2]
        future = window["query_rows"][2]
        self.assertEqual(same_edge[0]["decision_cycle"], same_edge[1]["decision_cycle"])
        self.assertEqual(
            [(row["predictor_state_version"], row["candidate_direct_anchor_pose_id"])
             for row in same_edge],
            [(2, 2), (2, 2)],
        )
        transition = window["query_transitions"][0]
        boundary = window["first_query_state_boundary"]
        self.assertEqual(transition["pose_id"], 3)
        self.assertEqual(transition["commit_cycle"], same_edge[0]["decision_cycle"])
        self.assertEqual(
            transition["effective_cycle"], transition["commit_cycle"] + 1
        )
        self.assertEqual(
            transition["parent_dependency_chain_endpoint_sha256"],
            boundary["dependency_pose_chain_sha256"],
        )
        self.assertEqual(
            transition["dependency_chain_endpoint_sha256"],
            canonical_sha256({
                "parent_sha256": transition[
                    "parent_dependency_chain_endpoint_sha256"
                ],
                "pose_id": transition["pose_id"],
                "pose_sha256": transition["pose_sha256"],
                "state_version": transition["published_state_version"],
            }),
        )
        self.assertEqual(future["predictor_state_version"], 3)
        self.assertEqual(future["candidate_direct_anchor_pose_id"], 3)
        self.assertIsNone(boundary["pending_state"])
        self.assertEqual(
            boundary["effective_state"]["native_state"]["anchor_pose_id"], 2
        )

    def test_candidate_failure_and_baseline_fallback_taxonomy_match_batch(self):
        for fixture in (_cav_unlocked_fixture, _fallback_fixture):
            with self.subTest(fixture=fixture.__name__):
                bundle, baseline = fixture()
                streamed = generate_pll_query_stream(_execution(bundle))
                batch = generate_locked_pll_output(bundle, baseline)
                query_ids = {
                    event.event_id
                    for event in bundle.event_streams[
                        bundle.neutral_registry[0].window_id
                    ]
                    if event.is_query
                }
                expected = [
                    row for row in batch["windows"][0]["events"]
                    if row["event_id"] in query_ids
                ]
                actual = streamed["windows"][0]["query_rows"]
                for observed, wanted in zip(actual, expected):
                    for field in (
                        "event_id",
                        "candidate_attempted",
                        "candidate_used",
                        "candidate_failure_reason",
                        "route",
                        "fallback_reason",
                        "world_ray",
                    ):
                        self.assertEqual(observed[field], wanted[field])
                    self.assertEqual(
                        observed["baseline_fallback_used_pose_ids"],
                        wanted["used_pose_ids"],
                    )

    def test_warmup_rows_zero_and_only_query_attempts_call_predict(self):
        calls = []
        real = pll_query_stream_core.SO3PLLModel.predict

        def counted(model, timestamp_ns, cycle):
            calls.append((timestamp_ns, cycle))
            return real(model, timestamp_ns, cycle)

        with mock.patch.object(
            pll_query_stream_core.SO3PLLModel, "predict", new=counted
        ):
            output = generate_pll_query_stream(self.execution)
        attempted_queries = sum(
            record["disposition_reason"] == "causal_cav"
            for window in self.execution["score_free_current_cav_trace"]["windows"]
            for event, record in zip(
                window["input_events"], window["simulation"]["records"]
            )
            if event["is_query"]
        )
        self.assertEqual(len(calls), 2 * attempted_queries)
        self.assertEqual(output["warmup_rows_emitted"], 0)
        self.assertEqual(output["retained_candidate_event_rows"], 0)
        self.assertEqual(
            [row["event_id"] for row in output["windows"][0]["query_rows"]],
            [1, 2],
        )

    def test_many_warmup_poses_keep_functional_state_constant_size(self):
        output = generate_pll_query_stream(_long_warmup_execution())
        self.assertEqual(output["query_event_count"], 1)
        self.assertLessEqual(output["maximum_retained_fallback_pose_count"], 2)
        self.assertLessEqual(
            output["maximum_retained_effective_pending_state_count"], 2
        )
        row = output["windows"][0]["query_rows"][0]
        self.assertEqual(row["candidate_dependency_pose_count"], 49)
        self.assertNotIn("state_dependency_pose_ids", row)

    def test_v3_same_cycle_poses_fail_closed_in_narrow_pll_domain(self):
        start = 0
        query = 50_000_000
        window_id = "pll-same-pose-cycle"
        registry = (NeutralRegistryWindow(
            window_id, start, query, query + 500_000
        ),)
        pose_rows = []
        for pose_id, timestamp, angle in (
            (0, query - 1_000, 0.1),
            (1, query - 999, 0.2),
        ):
            quaternion = (
                0.0,
                0.0,
                math.sin(angle / 2.0),
                math.cos(angle / 2.0),
            )
            pose_rows.append(NeutralPoseInput(
                pose_id,
                timestamp,
                pose_timestamp_to_cycle(timestamp, start),
                quaternion,
                canonical_pose_value_sha256(pose_id, timestamp, quaternion),
                True,
                True,
            ))
        self.assertEqual(pose_rows[0].commit_cycle, pose_rows[1].commit_cycle)
        ray = (1.0, 0.0, 0.0)
        event_rows = []
        for event_id, timestamp, is_query in (
            (2, query - 500, False),
            (1, query, True),
        ):
            digest = canonical_event_content_sha256(
                event_id, timestamp, 0, is_query, ray, 1, True
            )
            event_rows.append(NeutralEventInput(
                event_id, timestamp, 0, is_query, ray, 1, digest, True
            ))
        execution = build_stage3_execution_input(
            registry,
            {window_id: tuple(event_rows)},
            {window_id: tuple(pose_rows)},
            source_events_authority=source_authority(),
            repo_root=ROOT,
        )
        with self.assertRaisesRegex(
            PLLQueryStreamError, "post-reset pose commit cycles must be unique"
        ):
            generate_pll_query_stream(execution)

    def test_verified_v3_query_and_transition_paths_reject_forged_core(self):
        real = pll_query_stream._run_verified_execution_snapshot(self.execution)
        mutations = []
        changed = copy.deepcopy(real)
        changed["windows"][0]["query_rows"].pop()
        changed["windows"][0]["query_event_count"] -= 1
        changed["query_event_count"] -= 1
        mutations.append((changed, "query event cardinality"))
        changed = copy.deepcopy(real)
        changed["windows"][0]["query_transitions"].clear()
        changed["windows"][0]["query_transition_count"] = 0
        changed["query_transition_count"] = 0
        mutations.append((changed, "query transition cardinality"))
        for forged, message in mutations:
            with self.subTest(message=message), mock.patch.object(
                pll_query_stream,
                "_run_verified_execution_snapshot",
                return_value=forged,
            ):
                with self.assertRaisesRegex(PLLQueryStreamError, message):
                    generate_pll_query_stream(self.execution)

    def test_tampered_v3_never_reaches_core(self):
        tampered = dict(self.execution)
        tampered["query_event_count"] += 1
        with mock.patch.object(
            pll_query_stream, "_run_verified_execution_snapshot"
        ) as core:
            with self.assertRaises(PLLQueryStreamError):
                generate_pll_query_stream(tampered)
        core.assert_not_called()

    def test_fixed_root_holds_config_and_dependency_manifests(self):
        output = generate_pll_query_stream(self.execution)
        self.assertNotIn(
            "repo_root", inspect.signature(generate_pll_query_stream).parameters
        )
        self.assertEqual(output["status"], "DEVELOPMENT_HOLD")
        self.assertEqual(output["input_domain_hold"], INPUT_DOMAIN_HOLD)
        self.assertIs(
            INPUT_DOMAIN_HOLD[
                "unique_pose_commit_cycles_are_execution_input_v3_guaranteed"
            ],
            False,
        )
        self.assertEqual(output["verified_input_complexity_hold"], VERIFIED_INPUT_HOLD)
        self.assertEqual(output["native_transition_complexity_hold"], NATIVE_TRANSITION_HOLD)
        self.assertEqual(output["output_authority_hold"], OUTPUT_AUTHORITY_HOLD)
        self.assertEqual(PLL_STREAM_CONFIG_SHA256, locked_config_sha256())
        _assert_manifest(self, output["candidate_safe_core_manifest"])
        _assert_manifest(self, output["coordinator_manifest"])
        self.assertEqual(
            output["coordinator_manifest"]["candidate_safe_core_manifest_sha256"],
            output["candidate_safe_core_manifest_sha256"],
        )

    def test_core_and_coordinator_import_boundaries_are_score_free(self):
        forbidden = ("evaluator", "selector", "label", "scorer", "scoring")
        for name in ("pll_query_stream_core.py", "pll_query_stream.py"):
            source = (MODULE_ROOT / name).read_text(encoding="utf-8")
            tree = ast.parse(source, feature_version=(3, 8))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertFalse(any(
                piece in imported.lower()
                for imported in imports
                for piece in forbidden
            ))
        core_source = (MODULE_ROOT / "pll_query_stream_core.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("execution_authority", core_source)
        self.assertEqual(pll_query_stream_core.__all__, ())


if __name__ == "__main__":
    unittest.main()
