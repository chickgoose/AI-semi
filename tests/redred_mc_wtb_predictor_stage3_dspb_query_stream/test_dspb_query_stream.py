from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from benchmarks.redred_mc_wtb_predictor_stage3 import dspb_query_stream
from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (
    canonical_event_content_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_output import (
    ROUTE_CANDIDATE,
    ROUTE_CURRENT_CAV,
    ROUTE_FRESH_ZOH,
    ROUTE_SENSOR_FIXED,
    generate_dspb_candidate_output,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_query_stream import (
    DSPB_QUERY_STREAM_SCHEMA,
    DSPBQueryStreamError,
    INPUT_DOMAIN_HOLD,
    OUTPUT_AUTHORITY_HOLD,
    VERIFIED_INPUT_HOLD,
    generate_dspb_query_stream,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    build_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralRegistryWindow,
)
from tests.redred_mc_wtb_predictor_stage3_dspb_output.test_dspb_output import (
    ADAPTER_SHA256,
    _event,
    _fallback_fixture,
    _motion_fixture,
    _pose,
)
from tests.redred_mc_wtb_predictor_stage3_execution_authority.test_execution_authority import (
    source_authority,
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


def _decreasing_ids(events):
    result = {}
    next_id = 80_000
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


def _route_fixture(route):
    start = 0
    query = 50_000_000
    window_id = "stream-%s" % route.lower()
    registry = (
        NeutralRegistryWindow(window_id, start, query, query + 500_000),
    )
    if route == ROUTE_FRESH_ZOH:
        poses = (_pose(0, query - 500_000, start, 0.1),)
    elif route == ROUTE_SENSOR_FIXED:
        poses = (_pose(0, start, start, 0.1),)
    else:
        raise AssertionError("unknown route")
    events = (
        _event(10, query - 100_000, query, 0.01, 0),
        _event(9, query, query, 0.02, 0),
    )
    return registry, {window_id: events}, {window_id: poses}


def _batch_query_rows(registry, events, poses, execution):
    batch = generate_dspb_candidate_output(
        registry,
        events,
        poses,
        ADAPTER_SHA256,
    )
    result = []
    for batch_window, execution_window in zip(batch["windows"], execution["windows"]):
        query_ids = {
            event["event_id"]
            for event in execution_window["events"]
            if event["is_query"]
        }
        result.append([
            row for row in batch_window["events"]
            if row["event_id"] in query_ids
        ])
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


class DSPBQueryStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.events, cls.poses = _motion_fixture(1)
        cls.execution = _execution(cls.registry, cls.events, cls.poses)

    def test_query_rows_are_exact_dspb_v2_projection(self):
        streamed = generate_dspb_query_stream(self.execution)
        expected = _batch_query_rows(
            self.registry,
            self.events,
            self.poses,
            self.execution,
        )
        for window, rows in zip(streamed["windows"], expected):
            self.assertEqual(
                canonical_json_bytes(window["query_rows"]),
                canonical_json_bytes(rows),
            )
        # This includes the last warmup decision in the first query row's
        # rolling prior_decision_sha256 without retaining the warmup row.
        self.assertIsNotNone(streamed["windows"][0]["query_rows"][0][
            "prior_decision_sha256"
        ])

    def test_native_candidate_and_all_fallback_routes_are_preserved(self):
        fixtures = [
            _motion_fixture(1),
            _fallback_fixture(),
            _route_fixture(ROUTE_FRESH_ZOH),
            _route_fixture(ROUTE_SENSOR_FIXED),
        ]
        expected_routes = (
            ROUTE_CANDIDATE,
            ROUTE_CURRENT_CAV,
            ROUTE_FRESH_ZOH,
            ROUTE_SENSOR_FIXED,
        )
        for fixture, expected_route in zip(fixtures, expected_routes):
            registry, events, poses = fixture
            execution = _execution(registry, events, poses)
            streamed = generate_dspb_query_stream(execution)
            expected = _batch_query_rows(registry, events, poses, execution)
            rows = streamed["windows"][0]["query_rows"]
            self.assertEqual(canonical_json_bytes(rows), canonical_json_bytes(expected[0]))
            self.assertEqual(rows[0]["route"], expected_route)
            self.assertEqual(rows[0]["model_id"], DSPBConfig().candidate_id)

    def test_warmup_rows_and_native_diagnostic_histories_are_zero(self):
        output = generate_dspb_query_stream(self.execution)
        self.assertEqual(output["warmup_rows_emitted"], 0)
        self.assertEqual(output["retained_candidate_event_rows"], 0)
        self.assertLessEqual(output["maximum_retained_native_pose_count"], 256)
        for result, execution in zip(output["windows"], self.execution["windows"]):
            query_ids = [
                event["event_id"] for event in execution["events"]
                if event["is_query"]
            ]
            self.assertEqual(
                [row["event_id"] for row in result["query_rows"]],
                query_ids,
            )
            self.assertEqual(result["warmup_rows_emitted"], 0)
            self.assertEqual(result["retained_candidate_event_rows"], 0)
            self.assertEqual(result["retained_native_event_decisions"], 0)
            self.assertEqual(result["retained_native_pose_receipts"], 0)
            self.assertEqual(result["retained_native_seen_event_ids"], 0)
            self.assertEqual(result["retained_native_seen_pose_ids"], 0)

    def test_decreasing_unique_ids_preserve_exact_query_order(self):
        events = _decreasing_ids(self.events)
        execution = _execution(self.registry, events, self.poses)
        streamed = generate_dspb_query_stream(execution)
        expected = _batch_query_rows(self.registry, events, self.poses, execution)
        rows = streamed["windows"][0]["query_rows"]
        self.assertEqual(canonical_json_bytes(rows), canonical_json_bytes(expected[0]))
        identifiers = [row["event_id"] for row in rows]
        self.assertTrue(all(
            right < left for left, right in zip(identifiers, identifiers[1:])
        ))

    def test_pose_cap_fails_closed_before_native_replay(self):
        start = 0
        query = 50_000_000
        window_id = "pose-cap"
        registry = (
            NeutralRegistryWindow(window_id, start, query, query + 500_000),
        )
        poses = tuple(
            _pose(index, index * 100_000, start, 0.0001 * index)
            for index in range(257)
        )
        events = (
            _event(2, query - 100_000, query, 0.0, 256),
            _event(1, query, query, 0.0, 256),
        )
        execution = _execution(
            registry,
            {window_id: events},
            {window_id: poses},
        )
        self.assertEqual(execution["schema"], "redred.mc_wtb_predictor_stage3.execution_input/v3")
        self.assertEqual(execution["windows"][0]["pose_input_count"], 257)
        with self.assertRaisesRegex(DSPBQueryStreamError, "more than 256 poses"):
            generate_dspb_query_stream(execution)

    def test_verified_query_path_and_double_replay_fail_closed(self):
        real = dspb_query_stream._run_verified_execution_snapshot
        first = real(self.execution)
        changed = {key: value for key, value in first.items()}
        changed["windows"] = [dict(window) for window in first["windows"]]
        changed["windows"][0]["query_rows"] = [
            dict(row) for row in first["windows"][0]["query_rows"]
        ]
        changed["windows"][0]["query_rows"][0]["event_id"] += 1
        with mock.patch.object(
            dspb_query_stream,
            "_run_verified_execution_snapshot",
            side_effect=(first, changed),
        ):
            with self.assertRaisesRegex(DSPBQueryStreamError, "double replay"):
                generate_dspb_query_stream(self.execution)

        with mock.patch.object(
            dspb_query_stream,
            "_run_verified_execution_snapshot",
            return_value=changed,
        ):
            with self.assertRaisesRegex(DSPBQueryStreamError, "query identity"):
                generate_dspb_query_stream(self.execution)

    def test_development_hold_fixed_root_manifests_and_seals(self):
        output = generate_dspb_query_stream(self.execution)
        self.assertEqual(output["schema"], DSPB_QUERY_STREAM_SCHEMA)
        self.assertEqual(output["status"], "DEVELOPMENT_HOLD")
        self.assertEqual(output["verified_input_complexity_hold"], VERIFIED_INPUT_HOLD)
        self.assertEqual(output["output_authority_hold"], OUTPUT_AUTHORITY_HOLD)
        self.assertEqual(output["input_domain_hold"], INPUT_DOMAIN_HOLD)
        self.assertEqual(INPUT_DOMAIN_HOLD["status"], "HOLD")
        self.assertEqual(
            INPUT_DOMAIN_HOLD["valid_v3_inputs_beyond_fixed_caps"],
            "fail_closed",
        )
        self.assertIs(
            INPUT_DOMAIN_HOLD["caps_are_execution_input_v3_guarantees"],
            False,
        )
        self.assertEqual(output["deterministic_replay_count"], 2)
        self.assertIs(output["deterministic_double_replay_verified"], True)
        self.assertEqual(
            set(inspect.signature(generate_dspb_query_stream).parameters),
            {"execution_input"},
        )
        core = output["candidate_safe_core_manifest"]
        coordinator = output["coordinator_manifest"]
        _assert_manifest(self, core)
        _assert_manifest(self, coordinator)
        core_paths = {row["path"] for row in core["files"]}
        self.assertTrue({
            "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
            "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
            "benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py",
        }.issubset(core_paths))
        self.assertNotIn(
            "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
            core_paths,
        )
        self.assertEqual(
            coordinator["candidate_safe_core_manifest_sha256"],
            core["manifest_sha256"],
        )
        body = dict(output)
        supplied = body.pop("aggregate_sha256")
        self.assertEqual(supplied, canonical_sha256(body))

    def test_no_evaluator_label_or_public_verification_escape_and_python38(self):
        for name in ("dspb_query_stream.py", "dspb_query_stream_core.py"):
            path = MODULE_ROOT / name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name, feature_version=(3, 8))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            self.assertFalse(any(
                piece in imported
                for imported in imports
                for piece in ("evaluator", "selector", "labels", "scoring")
            ))
        core_source = (MODULE_ROOT / "dspb_query_stream_core.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("execution_authority", core_source)
        self.assertEqual(
            dspb_query_stream.__all__,
            (
                "DSPB_QUERY_STREAM_SCHEMA",
                "DSPBQueryStreamError",
                "INPUT_DOMAIN_HOLD",
                "OUTPUT_AUTHORITY_HOLD",
                "VERIFIED_INPUT_HOLD",
                "generate_dspb_query_stream",
            ),
        )
        script = """
import json
import sys
import benchmarks.redred_mc_wtb_predictor_stage3.dspb_query_stream
print(json.dumps(sorted(
    name for name in sys.modules
    if 'evaluator' in name or 'selector' in name or 'labels' in name
)))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])

        core_tree = ast.parse(
            (MODULE_ROOT / "dspb_query_stream_core.py").read_text(
                encoding="utf-8"
            ),
            feature_version=(3, 8),
        )
        events_assignments = [
            node
            for node in ast.walk(core_tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "events"
                for target in node.targets
            )
        ]
        self.assertEqual(len(events_assignments), 1)


if __name__ == "__main__":
    unittest.main()
