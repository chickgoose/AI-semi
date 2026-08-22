"""Fixed-root execution_input/v3 coordinator for bounded DSPB streaming."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_predictor_stage3.dspb_query_stream_core import (
    DSPBQueryStreamCoreError,
    MAX_EQUAL_TIME_CLUSTER_EVENTS,
    MAX_WINDOW_POSE_OCCURRENCES,
    _run_verified_execution_snapshot,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


DSPB_QUERY_STREAM_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_CORE_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream_core.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/__init__.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
)
_COORDINATOR_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)

VERIFIED_INPUT_HOLD = {
    "status": "HOLD",
    "complexity": "O(N)",
    "reason": (
        "execution_input/v3 is fully verified before streaming; verification "
        "traverses all sealed events, poses, and current-CAV records"
    ),
}
OUTPUT_AUTHORITY_HOLD = {
    "status": "HOLD",
    "reason": (
        "this development slice has no externally pinned candidate authority "
        "or independent closed-schema replay verifier"
    ),
}
INPUT_DOMAIN_HOLD = {
    "status": "HOLD",
    "maximum_window_pose_occurrences": MAX_WINDOW_POSE_OCCURRENCES,
    "maximum_equal_time_cluster_events": MAX_EQUAL_TIME_CLUSTER_EVENTS,
    "valid_v3_inputs_beyond_fixed_caps": "fail_closed",
    "caps_are_execution_input_v3_guarantees": False,
    "reason": (
        "the 256-pose and 8-event-cluster caps are development policy, not "
        "execution_input/v3 guarantees; otherwise valid v3 inputs beyond "
        "either fixed cap fail closed"
    ),
}


class DSPBQueryStreamError(ValueError):
    """The DSPB coordinator rejected input, replay, or executable binding."""


def _query_path(execution: Mapping[str, object]) -> Mapping[str, object]:
    windows = []
    total = 0
    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    for window, trace_window in zip(execution["windows"], trace_windows):
        rows = []
        for event, record in zip(
            window["events"],
            trace_window["simulation"]["records"],
        ):
            if event["is_query"]:
                rows.append({
                    "event_id": event["event_id"],
                    "event_content_sha256": event["event_content_sha256"],
                    "occurrence_cycle": record["occurrence_cycle"] - 1,
                    "decision_cycle": record["occurrence_cycle"],
                })
        total += len(rows)
        windows.append({
            "window_id": window["window_id"],
            "query_count": len(rows),
            "query_path_sha256": canonical_sha256(rows),
            "rows": rows,
        })
    return {
        "windows": windows,
        "query_count": total,
        "query_path_sha256": canonical_sha256(windows),
    }


def _verify_query_path(
    result: Mapping[str, object],
    expected: Mapping[str, object],
) -> None:
    if len(result["windows"]) != len(expected["windows"]):
        raise DSPBQueryStreamError("DSPB query window cardinality differs")
    if result["query_event_count"] != expected["query_count"]:
        raise DSPBQueryStreamError("DSPB query event cardinality differs")
    for actual_window, expected_window in zip(
        result["windows"], expected["windows"]
    ):
        if actual_window["window_id"] != expected_window["window_id"]:
            raise DSPBQueryStreamError("DSPB query window order differs")
        actual = [{
            "event_id": row["event_id"],
            "event_content_sha256": row["event_content_sha256"],
            "occurrence_cycle": row["occurrence_cycle"],
            "decision_cycle": row["decision_cycle"],
        } for row in actual_window["query_rows"]]
        if actual != expected_window["rows"]:
            raise DSPBQueryStreamError("DSPB query identity/order/cycle path differs")


def _manifest(
    schema: str,
    entrypoint: str,
    paths: Sequence[str],
    *,
    core_manifest_sha256: Optional[str] = None,
) -> Mapping[str, object]:
    files = []
    for relative in sorted(paths):
        path = (_REPOSITORY_ROOT / relative).resolve()
        try:
            path.relative_to(_REPOSITORY_ROOT)
        except ValueError as exc:  # pragma: no cover
            raise DSPBQueryStreamError("dependency escapes repository") from exc
        files.append({
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    body = {
        "schema": schema,
        "candidate_id": DSPBConfig().candidate_id,
        "entrypoint": entrypoint,
        "files": files,
    }
    if core_manifest_sha256 is not None:
        body["candidate_safe_core_manifest_sha256"] = core_manifest_sha256
    return dict(body, manifest_sha256=canonical_sha256(body))


def _manifests() -> Mapping[str, Mapping[str, object]]:
    core = _manifest(
        "redred.mc_wtb_predictor_stage3.dspb_query_stream_core_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream_core.py",
        _CORE_DEPENDENCY_PATHS,
    )
    coordinator = _manifest(
        "redred.mc_wtb_predictor_stage3.dspb_query_stream_coordinator_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/dspb_query_stream.py",
        _COORDINATOR_DEPENDENCY_PATHS,
        core_manifest_sha256=core["manifest_sha256"],
    )
    return {"candidate_safe_core": core, "coordinator": coordinator}


def generate_dspb_query_stream(execution_input: object) -> Mapping[str, object]:
    """Verify v3, replay bounded native DSPB twice, and emit query rows only."""

    try:
        snapshot_bytes = canonical_json_bytes(execution_input)
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        execution_digest = verify_stage3_execution_input(
            snapshot,
            expected_aggregate_sha256=snapshot.get("aggregate_sha256"),
            repo_root=_REPOSITORY_ROOT,
        )
        expected_query_path = _query_path(snapshot)
        manifests_before = _manifests()
        first = _run_verified_execution_snapshot(snapshot)
        second = _run_verified_execution_snapshot(snapshot)
        manifests_after = _manifests()
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise DSPBQueryStreamError("DSPB query stream failed: %s" % exc) from exc
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise DSPBQueryStreamError("DSPB deterministic double replay differs")
    _verify_query_path(first, expected_query_path)
    if manifests_before != manifests_after:
        raise DSPBQueryStreamError("DSPB executable dependencies changed during replay")

    config = DSPBConfig()
    replay_sha = canonical_sha256(first)
    body = {
        "schema": DSPB_QUERY_STREAM_SCHEMA,
        "candidate_id": config.candidate_id,
        "status": "DEVELOPMENT_HOLD",
        "execution_input_schema": EXECUTION_INPUT_SCHEMA,
        "execution_input_aggregate_sha256": execution_digest,
        "neutral_input_sha256": snapshot["neutral_input_sha256"],
        "ordered_query_event_ids_sha256": snapshot[
            "ordered_query_event_ids_sha256"
        ],
        "candidate_config": config.to_mapping(),
        "candidate_config_sha256": config.sha256,
        "bounded_state_profile": {
            "maximum_window_pose_occurrences": MAX_WINDOW_POSE_OCCURRENCES,
            "maximum_equal_time_cluster_events": MAX_EQUAL_TIME_CLUSTER_EVENTS,
            "native_event_decision_history": 0,
            "native_pose_receipt_history": 0,
            "native_seen_event_ids": 0,
            "native_seen_pose_ids": 0,
        },
        "candidate_safe_core_manifest": manifests_before["candidate_safe_core"],
        "candidate_safe_core_manifest_sha256": manifests_before[
            "candidate_safe_core"
        ]["manifest_sha256"],
        "coordinator_manifest": manifests_before["coordinator"],
        "coordinator_manifest_sha256": manifests_before["coordinator"][
            "manifest_sha256"
        ],
        "verified_input_complexity_hold": dict(VERIFIED_INPUT_HOLD),
        "output_authority_hold": dict(OUTPUT_AUTHORITY_HOLD),
        "input_domain_hold": dict(INPUT_DOMAIN_HOLD),
        "query_path_sha256": expected_query_path["query_path_sha256"],
        "deterministic_replay_count": 2,
        "deterministic_double_replay_verified": True,
        "replay_sha256": replay_sha,
        "windows": first["windows"],
        "windows_sha256": first["windows_sha256"],
        "query_event_count": first["query_event_count"],
        "warmup_rows_emitted": first["warmup_rows_emitted"],
        "retained_candidate_event_rows": first["retained_candidate_event_rows"],
        "maximum_retained_native_pose_count": first[
            "maximum_retained_native_pose_count"
        ],
        "maximum_equal_time_cluster_count": first[
            "maximum_equal_time_cluster_count"
        ],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


__all__ = (
    "DSPB_QUERY_STREAM_SCHEMA",
    "DSPBQueryStreamError",
    "INPUT_DOMAIN_HOLD",
    "OUTPUT_AUTHORITY_HOLD",
    "VERIFIED_INPUT_HOLD",
    "generate_dspb_query_stream",
)
