"""Verified execution_input/v3 coordinator for query-only RG3 streaming."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import RG3_POLICY
from benchmarks.redred_mc_wtb_predictor_stage3.rg3_query_stream_core import (
    RG3QueryStreamCoreError,
    _run_verified_execution_snapshot,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


RG3_QUERY_STREAM_SCHEMA = "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_CORE_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/rg3.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream_core.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
)
_COORDINATOR_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream.py",
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
NATIVE_TRANSITION_HOLD = {
    "status": "HOLD",
    "complexity": "O(N)",
    "reason": (
        "the slice consumes the fully verified native current-CAV trace and "
        "does not claim an independently streaming native-transition authority"
    ),
}


class RG3QueryStreamError(ValueError):
    """The coordinator rejected input, replay, or executable binding."""


def _query_path(execution: Mapping[str, object]) -> Mapping[str, object]:
    """Derive the exact query identity/order/cycle path from verified v3."""

    windows = []
    total = 0
    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    for window, trace_window in zip(execution["windows"], trace_windows):
        rows = []
        for event, record in zip(
            window["events"], trace_window["simulation"]["records"]
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


def _verify_core_query_path(
    result: Mapping[str, object],
    expected: Mapping[str, object],
) -> None:
    if len(result["windows"]) != len(expected["windows"]):
        raise RG3QueryStreamError("RG3 query window cardinality differs")
    if result["query_event_count"] != expected["query_count"]:
        raise RG3QueryStreamError("RG3 query event cardinality differs")
    for actual_window, expected_window in zip(
        result["windows"], expected["windows"]
    ):
        if actual_window["window_id"] != expected_window["window_id"]:
            raise RG3QueryStreamError("RG3 query window order differs")
        actual_rows = [{
            "event_id": row["event_id"],
            "event_content_sha256": row["event_content_sha256"],
            "occurrence_cycle": row["occurrence_cycle"],
            "decision_cycle": row["decision_cycle"],
        } for row in actual_window["query_rows"]]
        if actual_rows != expected_window["rows"]:
            raise RG3QueryStreamError("RG3 query identity/order/cycle path differs")


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
        except ValueError as exc:  # pragma: no cover - constants are local
            raise RG3QueryStreamError("dependency escapes repository") from exc
        files.append({
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    body = {
        "schema": schema,
        "candidate_id": RG3_POLICY.candidate_id,
        "entrypoint": entrypoint,
        "files": files,
    }
    if core_manifest_sha256 is not None:
        body["candidate_safe_core_manifest_sha256"] = core_manifest_sha256
    return dict(body, manifest_sha256=canonical_sha256(body))


def _manifests() -> Mapping[str, Mapping[str, object]]:
    core = _manifest(
        "redred.mc_wtb_predictor_stage3.rg3_query_stream_core_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream_core.py",
        _CORE_DEPENDENCY_PATHS,
    )
    coordinator = _manifest(
        "redred.mc_wtb_predictor_stage3.rg3_query_stream_coordinator_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/rg3_query_stream.py",
        _COORDINATOR_DEPENDENCY_PATHS,
        core_manifest_sha256=core["manifest_sha256"],
    )
    return {"candidate_safe_core": core, "coordinator": coordinator}


def generate_rg3_query_stream(
    execution_input: object,
) -> Mapping[str, object]:
    """Verify v3 once, replay the private core twice, and seal query rows.

    There is intentionally no alternate entrypoint accepting a boolean,
    digest, or token asserting prior verification.
    """

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
    except (AttributeError, KeyError, TypeError, ValueError, RG3QueryStreamCoreError) as exc:
        raise RG3QueryStreamError("RG3 query stream failed: %s" % exc) from exc
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise RG3QueryStreamError("RG3 deterministic double replay differs")
    _verify_core_query_path(first, expected_query_path)
    if manifests_before != manifests_after:
        raise RG3QueryStreamError("RG3 executable dependencies changed during replay")

    replay_sha256 = canonical_sha256(first)
    body = {
        "schema": RG3_QUERY_STREAM_SCHEMA,
        "candidate_id": RG3_POLICY.candidate_id,
        "status": "DEVELOPMENT_HOLD",
        "execution_input_schema": EXECUTION_INPUT_SCHEMA,
        "execution_input_aggregate_sha256": execution_digest,
        "neutral_input_sha256": snapshot["neutral_input_sha256"],
        "ordered_query_event_ids_sha256": snapshot[
            "ordered_query_event_ids_sha256"
        ],
        "candidate_safe_core_manifest": manifests_before["candidate_safe_core"],
        "candidate_safe_core_manifest_sha256": manifests_before[
            "candidate_safe_core"
        ]["manifest_sha256"],
        "coordinator_manifest": manifests_before["coordinator"],
        "coordinator_manifest_sha256": manifests_before["coordinator"][
            "manifest_sha256"
        ],
        "verified_input_complexity_hold": dict(VERIFIED_INPUT_HOLD),
        "native_transition_complexity_hold": dict(NATIVE_TRANSITION_HOLD),
        "output_authority_hold": dict(OUTPUT_AUTHORITY_HOLD),
        "query_path_sha256": expected_query_path["query_path_sha256"],
        "deterministic_replay_count": 2,
        "deterministic_double_replay_verified": True,
        "replay_sha256": replay_sha256,
        "windows": first["windows"],
        "windows_sha256": first["windows_sha256"],
        "query_event_count": first["query_event_count"],
        "warmup_rows_emitted": first["warmup_rows_emitted"],
        "retained_candidate_event_rows": first[
            "retained_candidate_event_rows"
        ],
        "maximum_retained_candidate_pose_count": first[
            "maximum_retained_candidate_pose_count"
        ],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


__all__ = (
    "NATIVE_TRANSITION_HOLD",
    "OUTPUT_AUTHORITY_HOLD",
    "RG3_QUERY_STREAM_SCHEMA",
    "RG3QueryStreamError",
    "VERIFIED_INPUT_HOLD",
    "generate_rg3_query_stream",
)
