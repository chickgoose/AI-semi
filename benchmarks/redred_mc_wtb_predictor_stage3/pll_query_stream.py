"""Fixed-authority coordinator for bounded PLL query-only streaming."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.pll_query_stream_core import (
    PLLQueryStreamCoreError,
    PLL_STREAM_CANDIDATE_ID,
    PLL_STREAM_CONFIG_SHA256,
    _run_verified_execution_snapshot,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


PLL_QUERY_STREAM_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_query_stream/v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_CORE_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream_core.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
)
_COORDINATOR_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/current_cav_trace.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/execution_authority.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)

VERIFIED_INPUT_HOLD = {
    "status": "HOLD",
    "complexity": "O(N)",
    "reason": "execution_input/v3 is fully verified before bounded PLL replay",
}
OUTPUT_AUTHORITY_HOLD = {
    "status": "HOLD",
    "reason": (
        "development slice has no externally pinned candidate authority or "
        "independent closed-schema replay verifier"
    ),
}
INPUT_DOMAIN_HOLD = {
    "status": "HOLD",
    "post_reset_pose_commit_cycles": "strictly_unique",
    "unique_pose_commit_cycles_are_execution_input_v3_guaranteed": False,
    "reason": (
        "execution_input/v3 permits multiple poses on one post-reset commit "
        "cycle, while this native PLL development slice requires one pose "
        "publication per edge and fails closed outside that narrower domain"
    ),
}
NATIVE_TRANSITION_HOLD = {
    "status": "HOLD",
    "complexity": "O(query rows + query transitions)",
    "reason": (
        "warmup transitions are reduced to a verified first-query boundary; "
        "only query decisions and subsequent transitions are emitted"
    ),
}
BATCH_PROVENANCE_EQUIVALENCE_HOLD = {
    "status": "HOLD",
    "reason": (
        "bounded output records a dependency-chain endpoint and count, not "
        "pll_output's fully expanded state_dependency_pose_ids; the schemas "
        "are intentionally not byte-equivalent"
    ),
}


class PLLQueryStreamError(ValueError):
    """The coordinator rejected PLL input, replay, or executable binding."""


def _query_path(execution: Mapping[str, object]) -> Mapping[str, object]:
    windows = []
    total_queries = 0
    total_transitions = 0
    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    for window, trace_window in zip(execution["windows"], trace_windows):
        records = trace_window["simulation"]["records"]
        rows = []
        first_query_cycle = None
        last_event_cycle = records[-1]["occurrence_cycle"]
        for event, record in zip(window["events"], records):
            if event["is_query"]:
                if first_query_cycle is None:
                    first_query_cycle = record["occurrence_cycle"]
                rows.append({
                    "event_id": event["event_id"],
                    "event_content_sha256": event["event_content_sha256"],
                    "occurrence_cycle": record["occurrence_cycle"] - 1,
                    "decision_cycle": record["occurrence_cycle"],
                })
        if first_query_cycle is None:
            raise PLLQueryStreamError("verified query path is empty")
        transitions = [{
            "pose_id": pose["pose_id"],
            "pose_sha256": pose["pose_sha256"],
            "commit_cycle": pose["commit_cycle"],
        } for pose in window["poses"] if (
            first_query_cycle <= pose["commit_cycle"] <= last_event_cycle
        )]
        total_queries += len(rows)
        total_transitions += len(transitions)
        windows.append({
            "window_id": window["window_id"],
            "rows": rows,
            "transitions": transitions,
        })
    return {
        "windows": windows,
        "query_count": total_queries,
        "query_transition_count": total_transitions,
        "query_path_sha256": canonical_sha256(windows),
    }


def _verify_core_paths(
    result: Mapping[str, object], expected: Mapping[str, object]
) -> None:
    if len(result["windows"]) != len(expected["windows"]):
        raise PLLQueryStreamError("PLL query window cardinality differs")
    if result["query_event_count"] != expected["query_count"]:
        raise PLLQueryStreamError("PLL query event cardinality differs")
    if result["query_transition_count"] != expected["query_transition_count"]:
        raise PLLQueryStreamError("PLL query transition cardinality differs")
    for actual, wanted in zip(result["windows"], expected["windows"]):
        if actual["window_id"] != wanted["window_id"]:
            raise PLLQueryStreamError("PLL query window order differs")
        rows = [{
            "event_id": row["event_id"],
            "event_content_sha256": row["event_content_sha256"],
            "occurrence_cycle": row["occurrence_cycle"],
            "decision_cycle": row["decision_cycle"],
        } for row in actual["query_rows"]]
        if rows != wanted["rows"]:
            raise PLLQueryStreamError("PLL query identity/order/cycle path differs")
        transitions = [{
            "pose_id": row["pose_id"],
            "pose_sha256": row["pose_sha256"],
            "commit_cycle": row["commit_cycle"],
        } for row in actual["query_transitions"]]
        if transitions != wanted["transitions"]:
            raise PLLQueryStreamError("PLL query transition path differs")


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
        except ValueError as exc:
            raise PLLQueryStreamError("dependency escapes repository") from exc
        files.append({
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    body = {
        "schema": schema,
        "candidate_id": PLL_STREAM_CANDIDATE_ID,
        "entrypoint": entrypoint,
        "files": files,
    }
    if core_manifest_sha256 is not None:
        body["candidate_safe_core_manifest_sha256"] = core_manifest_sha256
    return dict(body, manifest_sha256=canonical_sha256(body))


def _manifests() -> Mapping[str, Mapping[str, object]]:
    core = _manifest(
        "redred.mc_wtb_predictor_stage3.pll_query_stream_core_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream_core.py",
        _CORE_DEPENDENCY_PATHS,
    )
    coordinator = _manifest(
        "redred.mc_wtb_predictor_stage3.pll_query_stream_coordinator_manifest/v1",
        "benchmarks/redred_mc_wtb_predictor_stage3/pll_query_stream.py",
        _COORDINATOR_DEPENDENCY_PATHS,
        core_manifest_sha256=core["manifest_sha256"],
    )
    return {"candidate_safe_core": core, "coordinator": coordinator}


def generate_pll_query_stream(execution_input: object) -> Mapping[str, object]:
    """Verify v3 at the fixed root and emit query-only bounded PLL evidence."""

    try:
        snapshot_bytes = canonical_json_bytes(execution_input)
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
        execution_digest = verify_stage3_execution_input(
            snapshot,
            expected_aggregate_sha256=snapshot.get("aggregate_sha256"),
            repo_root=_REPOSITORY_ROOT,
        )
        expected_paths = _query_path(snapshot)
        manifests_before = _manifests()
        first = _run_verified_execution_snapshot(snapshot)
        second = _run_verified_execution_snapshot(snapshot)
        manifests_after = _manifests()
    except (AttributeError, KeyError, TypeError, ValueError, PLLQueryStreamCoreError) as exc:
        raise PLLQueryStreamError("PLL query stream failed: %s" % exc) from exc
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise PLLQueryStreamError("PLL deterministic double replay differs")
    _verify_core_paths(first, expected_paths)
    if manifests_before != manifests_after:
        raise PLLQueryStreamError("PLL executable dependencies changed during replay")
    replay_sha = canonical_sha256(first)
    body = {
        "schema": PLL_QUERY_STREAM_SCHEMA,
        "candidate_id": PLL_STREAM_CANDIDATE_ID,
        "status": "DEVELOPMENT_HOLD",
        "execution_input_schema": EXECUTION_INPUT_SCHEMA,
        "execution_input_aggregate_sha256": execution_digest,
        "neutral_input_sha256": snapshot["neutral_input_sha256"],
        "ordered_query_event_ids_sha256": snapshot["ordered_query_event_ids_sha256"],
        "configuration_sha256": PLL_STREAM_CONFIG_SHA256,
        "candidate_safe_core_manifest": manifests_before["candidate_safe_core"],
        "candidate_safe_core_manifest_sha256": manifests_before[
            "candidate_safe_core"
        ]["manifest_sha256"],
        "coordinator_manifest": manifests_before["coordinator"],
        "coordinator_manifest_sha256": manifests_before["coordinator"][
            "manifest_sha256"
        ],
        "verified_input_complexity_hold": dict(VERIFIED_INPUT_HOLD),
        "input_domain_hold": dict(INPUT_DOMAIN_HOLD),
        "native_transition_complexity_hold": dict(NATIVE_TRANSITION_HOLD),
        "batch_provenance_equivalence_hold": dict(
            BATCH_PROVENANCE_EQUIVALENCE_HOLD
        ),
        "output_authority_hold": dict(OUTPUT_AUTHORITY_HOLD),
        "candidate_provenance_representation": (
            "direct_anchor_plus_dependency_chain_endpoint_and_count"
        ),
        "query_path_sha256": expected_paths["query_path_sha256"],
        "deterministic_replay_count": 2,
        "deterministic_double_replay_verified": True,
        "replay_sha256": replay_sha,
        **first,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


__all__ = (
    "BATCH_PROVENANCE_EQUIVALENCE_HOLD",
    "INPUT_DOMAIN_HOLD",
    "NATIVE_TRANSITION_HOLD",
    "OUTPUT_AUTHORITY_HOLD",
    "PLL_QUERY_STREAM_SCHEMA",
    "PLLQueryStreamError",
    "VERIFIED_INPUT_HOLD",
    "generate_pll_query_stream",
)
