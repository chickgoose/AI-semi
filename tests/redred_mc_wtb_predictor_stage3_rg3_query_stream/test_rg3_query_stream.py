from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_query_stream
from benchmarks.redred_mc_wtb_predictor_stage3 import rg3_query_stream_core
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_output import (
    generate_locked_rg3_output,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_query_stream import (
    NATIVE_TRANSITION_HOLD,
    RG3_QUERY_STREAM_SCHEMA,
    RG3QueryStreamError,
    VERIFIED_INPUT_HOLD,
    generate_rg3_query_stream,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from tests.redred_mc_wtb_predictor_stage3_execution_authority.test_execution_authority import (
    source_authority,
)
from tests.redred_mc_wtb_predictor_stage3_rg3_output.test_rg3_output import (
    ADAPTER_SHA256,
    _fixture,
)


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "benchmarks" / "redred_mc_wtb_predictor_stage3"


def _execution(registry, events, poses):
    return build_stage3_execution_input(
        registry,
        events,
        poses,
        source_events_authority=source_authority(),
        repo_root=ROOT,
    )


def _decreasing_event_ids(events):
    result = {}
    next_id = 90_000
    for window_id, rows in events.items():
        converted = []
        for event in rows:
            event_id = next_id
            next_id -= 1
            digest = canonical_event_content_sha256(
                event_id,
                event.timestamp_ns,
                event.polarity,
                event.is_query,
                event.sensor_ray,
                event.causal_pose_source_index,
                event.transform_guard_valid,
            )
            converted.append(replace(
                event,
                event_id=event_id,
                event_content_sha256=digest,
            ))
        result[window_id] = tuple(converted)
    return result


def _assert_manifest(testcase, manifest):
    body = dict(manifest)
    supplied = body.pop("manifest_sha256")
    testcase.assertEqual(supplied, canonical_sha256(body))
    for row in manifest["files"]:
        testcase.assertEqual(
            row["sha256"],
            hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest(),
        )


class RG3QueryStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.events, cls.poses, _ = _fixture()
        cls.execution = _execution(cls.registry, cls.events, cls.poses)

    def test_query_rows_are_byte_exact_locked_batch_projection(self):
        streamed = generate_rg3_query_stream(self.execution, repo_root=ROOT)
        batch = generate_locked_rg3_output(
            self.registry,
            self.events,
            self.poses,
            ADAPTER_SHA256,
        )
        for stream_window, batch_window, execution_window in zip(
            streamed["windows"], batch["windows"], self.execution["windows"]
        ):
            query_ids = {
                event["event_id"]
                for event in execution_window["events"]
                if event["is_query"]
            }
            expected = [
                row for row in batch_window["events"]
                if row["event_id"] in query_ids
            ]
            self.assertEqual(
                canonical_json_bytes(stream_window["query_rows"]),
                canonical_json_bytes(expected),
            )

    def test_warmup_rows_are_neither_emitted_nor_candidate_state(self):
        real = rg3_query_stream_core.recover_rg3_cav
        with mock.patch.object(
            rg3_query_stream_core,
            "recover_rg3_cav",
            wraps=real,
        ) as recover:
            output = generate_rg3_query_stream(self.execution, repo_root=ROOT)
        attempted_queries = sum(
            record["disposition_reason"] == "causal_cav"
            for window in self.execution["score_free_current_cav_trace"]["windows"]
            for event, record in zip(
                window["input_events"], window["simulation"]["records"]
            )
            if event["is_query"]
        )
        # The coordinator deliberately replays twice.  No warmup occurrence
        # invokes RG3 candidate logic in either pass.
        self.assertEqual(recover.call_count, 2 * attempted_queries)
        self.assertEqual(output["schema"], RG3_QUERY_STREAM_SCHEMA)
        self.assertEqual(output["warmup_rows_emitted"], 0)
        self.assertEqual(output["retained_candidate_event_rows"], 0)
        self.assertLessEqual(output["maximum_retained_candidate_pose_count"], 3)
        for result, execution in zip(output["windows"], self.execution["windows"]):
            expected_query_ids = [
                event["event_id"] for event in execution["events"]
                if event["is_query"]
            ]
            warmup_ids = {
                event["event_id"] for event in execution["events"]
                if not event["is_query"]
            }
            actual_ids = [row["event_id"] for row in result["query_rows"]]
            self.assertEqual(actual_ids, expected_query_ids)
            self.assertTrue(warmup_ids.isdisjoint(actual_ids))
            self.assertEqual(result["warmup_rows_emitted"], 0)
            self.assertEqual(result["retained_candidate_event_rows"], 0)
            self.assertLessEqual(result["maximum_retained_candidate_pose_count"], 3)

    def test_decreasing_unique_event_ids_preserve_source_order(self):
        decreasing = _decreasing_event_ids(self.events)
        execution = _execution(self.registry, decreasing, self.poses)
        output = generate_rg3_query_stream(execution, repo_root=ROOT)
        expected = [
            event.event_id
            for window_id in (window.window_id for window in self.registry)
            for event in decreasing[window_id]
            if event.is_query
        ]
        actual = [
            row["event_id"]
            for window in output["windows"]
            for row in window["query_rows"]
        ]
        self.assertEqual(actual, expected)
        self.assertTrue(all(right < left for left, right in zip(actual, actual[1:])))

    def test_double_replay_is_deterministic_and_self_sealed(self):
        first = generate_rg3_query_stream(self.execution, repo_root=ROOT)
        second = generate_rg3_query_stream(self.execution, repo_root=ROOT)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["deterministic_replay_count"], 2)
        self.assertIs(first["deterministic_double_replay_verified"], True)
        replay = {
            "windows": first["windows"],
            "windows_sha256": first["windows_sha256"],
            "query_event_count": first["query_event_count"],
            "warmup_rows_emitted": first["warmup_rows_emitted"],
            "retained_candidate_event_rows": first["retained_candidate_event_rows"],
            "maximum_retained_candidate_pose_count": first[
                "maximum_retained_candidate_pose_count"
            ],
        }
        self.assertEqual(first["replay_sha256"], canonical_sha256(replay))
        body = dict(first)
        supplied = body.pop("aggregate_sha256")
        self.assertEqual(supplied, canonical_sha256(body))

    def test_internal_double_replay_detects_divergence(self):
        real = rg3_query_stream._run_verified_execution_snapshot
        first = real(self.execution)
        second = dict(first)
        second["query_event_count"] += 1
        with mock.patch.object(
            rg3_query_stream,
            "_run_verified_execution_snapshot",
            side_effect=(first, second),
        ):
            with self.assertRaisesRegex(
                RG3QueryStreamError, "deterministic double replay differs"
            ):
                generate_rg3_query_stream(self.execution, repo_root=ROOT)

    def test_tampered_v3_never_reaches_candidate_core(self):
        tampered = dict(self.execution)
        tampered["query_event_count"] += 1
        with mock.patch.object(
            rg3_query_stream,
            "_run_verified_execution_snapshot",
        ) as core:
            with self.assertRaises(RG3QueryStreamError):
                generate_rg3_query_stream(tampered, repo_root=ROOT)
        core.assert_not_called()

    def test_explicit_linear_holds_and_both_manifests_are_bound(self):
        output = generate_rg3_query_stream(self.execution, repo_root=ROOT)
        self.assertEqual(output["verified_input_complexity_hold"], VERIFIED_INPUT_HOLD)
        self.assertEqual(
            output["native_transition_complexity_hold"], NATIVE_TRANSITION_HOLD
        )
        for hold in (VERIFIED_INPUT_HOLD, NATIVE_TRANSITION_HOLD):
            self.assertEqual(hold["status"], "HOLD")
            self.assertEqual(hold["complexity"], "O(N)")
        core = output["candidate_safe_core_manifest"]
        coordinator = output["coordinator_manifest"]
        _assert_manifest(self, core)
        _assert_manifest(self, coordinator)
        self.assertEqual(
            output["candidate_safe_core_manifest_sha256"],
            core["manifest_sha256"],
        )
        self.assertEqual(
            output["coordinator_manifest_sha256"],
            coordinator["manifest_sha256"],
        )
        self.assertEqual(
            coordinator["candidate_safe_core_manifest_sha256"],
            core["manifest_sha256"],
        )
        paths = {row["path"] for row in core["files"]}
        self.assertIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream_core.py",
            paths,
        )
        self.assertNotIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
            paths,
        )

    def test_candidate_core_imports_no_authority_evaluator_selector_or_labels(self):
        core_path = MODULE_ROOT / "rg3_query_stream_core.py"
        coordinator_path = MODULE_ROOT / "rg3_query_stream.py"
        core_source = core_path.read_text(encoding="utf-8")
        core_tree = ast.parse(core_source, feature_version=(3, 8))
        imports = []
        for node in ast.walk(core_tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = ("execution_authority", "evaluator", "selector", "labels")
        self.assertTrue(all(
            all(piece not in imported for piece in forbidden)
            for imported in imports
        ))
        self.assertNotIn("execution_authority", core_source)

        coordinator_tree = ast.parse(
            coordinator_path.read_text(encoding="utf-8"),
            feature_version=(3, 8),
        )
        coordinator_imports = []
        for node in ast.walk(coordinator_tree):
            if isinstance(node, ast.Import):
                coordinator_imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                coordinator_imports.append(node.module or "")
        self.assertFalse(any(
            piece in imported
            for imported in coordinator_imports
            for piece in ("evaluator", "selector", "labels")
        ))
        self.assertEqual(
            rg3_query_stream.__all__,
            (
                "NATIVE_TRANSITION_HOLD",
                "RG3_QUERY_STREAM_SCHEMA",
                "RG3QueryStreamError",
                "VERIFIED_INPUT_HOLD",
                "generate_rg3_query_stream",
            ),
        )


if __name__ == "__main__":
    unittest.main()
