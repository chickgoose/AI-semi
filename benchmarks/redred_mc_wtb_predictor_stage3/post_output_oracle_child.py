"""Fixed post-output batch-oracle worker for Stage-3 query streams.

The worker is started only after the two candidate workers have terminated
with byte-identical output.  It receives no labels, selector rows, scores,
paths, callables, configuration, or timeout.  Candidate-specific batch
producers are imported only after the canonical request, execution input,
refreeze contract, current-CAV trace, and candidate envelope are authenticated.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Dict, Mapping, Sequence, Tuple


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from benchmarks.redred_mc_wtb_predictor_stage3.current_cav_trace import (  # noqa: E402
    CurrentCAVTrace,
    canonical_event_content_sha256,
    load_current_cav_trace,
)
from benchmarks.redred_mc_wtb_predictor_stage3.execution_authority import (  # noqa: E402
    EXECUTION_INPUT_SCHEMA,
    verify_stage3_execution_input,
)
from benchmarks.redred_mc_wtb_predictor_stage3.refreeze_v4 import (  # noqa: E402
    DSPB_CANDIDATE_ID,
    PLL_CANDIDATE_ID,
    RG3_CANDIDATE_ID,
    verify_refreeze_v4_contract,
)
from benchmarks.redred_mc_wtb_stage4_contract import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)


POST_OUTPUT_REQUEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.post_output_oracle_request/v1"
)
POST_OUTPUT_RECEIPT_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.post_output_oracle_receipt/v1"
)
_ADAPTER_SENTINEL_SHA256 = "0" * 64

_REQUEST_FIELDS = frozenset((
    "schema", "candidate_id", "execution_input",
    "execution_input_aggregate_sha256", "refreeze_contract",
    "refreeze_contract_sha256", "candidate_output",
    "candidate_output_sha256", "candidate_output_aggregate_sha256",
    "candidate_child_response_sha256", "expected_runtime_manifest",
    "expected_runtime_manifest_sha256", "request_sha256",
))

_RG3_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "candidate_safe_core_manifest",
    "candidate_safe_core_manifest_sha256", "coordinator_manifest",
    "coordinator_manifest_sha256", "verified_input_complexity_hold",
    "native_transition_complexity_hold", "output_authority_hold",
    "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_candidate_pose_count",
    "aggregate_sha256",
))
_DSPB_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "candidate_config",
    "candidate_config_sha256", "bounded_state_profile",
    "candidate_safe_core_manifest", "candidate_safe_core_manifest_sha256",
    "coordinator_manifest", "coordinator_manifest_sha256",
    "verified_input_complexity_hold", "output_authority_hold",
    "input_domain_hold", "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_native_pose_count",
    "maximum_equal_time_cluster_count", "aggregate_sha256",
))
_PLL_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "status", "execution_input_schema",
    "execution_input_aggregate_sha256", "neutral_input_sha256",
    "ordered_query_event_ids_sha256", "configuration_sha256",
    "candidate_safe_core_manifest", "candidate_safe_core_manifest_sha256",
    "coordinator_manifest", "coordinator_manifest_sha256",
    "verified_input_complexity_hold", "input_domain_hold",
    "native_transition_complexity_hold", "batch_provenance_equivalence_hold",
    "output_authority_hold", "candidate_provenance_representation",
    "query_path_sha256", "deterministic_replay_count",
    "deterministic_double_replay_verified", "replay_sha256", "windows",
    "windows_sha256", "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows",
    "maximum_retained_effective_pending_state_count",
    "maximum_retained_fallback_pose_count", "query_transition_count",
    "aggregate_sha256",
))

_RG3_WINDOW_FIELDS = frozenset((
    "window_id", "query_rows", "query_rows_sha256", "warmup_event_count",
    "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_candidate_pose_count",
))
_DSPB_WINDOW_FIELDS = frozenset((
    "window_id", "query_rows", "query_rows_sha256", "warmup_event_count",
    "query_event_count", "warmup_rows_emitted",
    "retained_candidate_event_rows", "maximum_retained_native_pose_count",
    "maximum_equal_time_cluster_count", "retained_native_event_decisions",
    "retained_native_pose_receipts", "retained_native_seen_event_ids",
    "retained_native_seen_pose_ids",
))
_PLL_WINDOW_FIELDS = frozenset((
    "window_id", "first_query_state_boundary", "query_rows",
    "query_rows_sha256", "query_transitions", "query_transitions_sha256",
    "warmup_event_count", "query_event_count", "query_transition_count",
    "warmup_rows_emitted", "retained_candidate_event_rows",
    "maximum_retained_fallback_pose_count",
    "maximum_retained_effective_pending_state_count",
))

_RG3_ROW_FIELDS = frozenset((
    "event_id", "event_content_sha256", "occurrence_cycle",
    "decision_cycle", "model_id", "predictor_state_version",
    "used_pose_ids", "candidate_attempted", "candidate_used", "route",
    "fallback_reason", "world_ray", "decision_sha256",
))
_DSPB_ROW_FIELDS = frozenset((
    "event_id", "event_content_sha256", "event_timestamp_ns", "is_query",
    "occurrence_cycle", "decision_cycle", "model_id", "geometry_expert_id",
    "predictor_state_version", "predictor_state_sha256",
    "state_dependency_pose_ids", "pose_receipt_chain_sha256", "used_pose_ids",
    "used_pose_evidence", "route", "route_reason", "candidate_attempted",
    "candidate_used", "candidate_failure_reason", "fallback_reason",
    "output_quaternion_xyzw", "world_ray", "ray_derivation_receipt",
    "native_decision_sha256", "prior_decision_sha256", "decision_sha256",
))
_DSPB_RAY_FIELDS = frozenset((
    "event_content_sha256", "route", "sensor_ray",
    "output_quaternion_xyzw", "world_ray", "ray_derivation_sha256",
))
_DSPB_POSE_EVIDENCE_FIELDS = frozenset((
    "pose_id", "measurement_timestamp_ns", "commit_cycle",
    "pose_content_sha256", "value_valid", "arithmetic_valid",
))
_PLL_ROW_FIELDS = frozenset((
    "event_id", "event_content_sha256", "occurrence_cycle", "decision_cycle",
    "model_id", "configuration_sha256", "predictor_state_version",
    "predictor_state_is_reset", "state_sha256", "state_effective_cycle",
    "state_dependency_pose_count", "state_dependency_pose_chain_sha256",
    "state_anchor_pose_id", "baseline_fallback_used_pose_ids",
    "candidate_direct_anchor_pose_id", "candidate_dependency_pose_count",
    "candidate_dependency_chain_endpoint_sha256", "candidate_attempted",
    "candidate_used", "candidate_failure_reason", "route", "fallback_reason",
    "candidate_quaternion_xyzw", "world_ray", "decision_sha256",
))
_PLL_STATE_FIELDS = frozenset((
    "schema", "candidate_id", "configuration_sha256", "parent_state_sha256",
    "native_state", "dependency_pose_count",
    "dependency_pose_chain_sha256", "state_sha256",
))
_PLL_NATIVE_STATE_FIELDS = frozenset((
    "state_version", "effective_cycle", "source_commit_cycle",
    "anchor_pose_id", "anchor_measurement_timestamp_ns",
    "anchor_quaternion_xyzw", "angular_velocity_body_rad_s",
    "integral_correction_body_rad_s", "previous_interval_ns", "lock_streak",
    "locked", "previous_residual_body_rad", "status",
))
_PLL_BOUNDARY_FIELDS = frozenset((
    "effective_state", "effective_state_sha256", "pending_state",
    "pending_state_sha256", "latest_fallback_pose_ids",
    "dependency_pose_count", "dependency_pose_chain_sha256", "boundary_sha256",
))
_PLL_TRANSITION_FIELDS = frozenset((
    "schema", "pose_id", "pose_sha256", "commit_cycle", "accepted",
    "source_state_version", "published_state_version", "effective_cycle",
    "publication_cycle", "transition_reason", "published_state",
    "published_state_sha256", "parent_dependency_pose_count",
    "parent_dependency_chain_endpoint_sha256", "dependency_pose_count",
    "dependency_chain_endpoint_sha256", "native_receipt_sha256",
    "transition_sha256",
))

_OUTPUT_SCHEMAS = {
    RG3_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.rg3_query_stream/v1",
    DSPB_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.dspb_query_stream/v1",
    PLL_CANDIDATE_ID: "redred.mc_wtb_predictor_stage3.pll_query_stream/v1",
}
_TOP_FIELDS = {
    RG3_CANDIDATE_ID: _RG3_TOP_FIELDS,
    DSPB_CANDIDATE_ID: _DSPB_TOP_FIELDS,
    PLL_CANDIDATE_ID: _PLL_TOP_FIELDS,
}

EXTERNAL_ORACLE_RELEASE_HOLD = {
    "status": "HOLD",
    "authority_go": False,
    "reason": "the post-output oracle worker has no externally signed release authority",
}
RESOURCE_PPA_HOLD = {
    "status": "HOLD",
    "resource_go": False,
    "ppa_go": False,
    "reason": "batch-oracle replay has no bounded-resource, RTL, or PPA authority",
}
FILESYSTEM_PUBLICATION_HOLD = {
    "status": "HOLD",
    "publication_allowed": False,
    "reason": "candidate and batch outputs remain in memory and no publication API exists",
}
_PLL_PROVENANCE_HOLD = {
    "status": "HOLD",
    "reason": (
        "bounded output records a dependency-chain endpoint and count, not "
        "pll_output's fully expanded state_dependency_pose_ids; the schemas "
        "are intentionally not byte-equivalent"
    ),
}
_OUTPUT_AUTHORITY_HOLDS = {
    RG3_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": "this development slice has no externally pinned candidate authority or independent closed-schema replay verifier",
    },
    DSPB_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": "this development slice has no externally pinned candidate authority or independent closed-schema replay verifier",
    },
    PLL_CANDIDATE_ID: {
        "status": "HOLD",
        "reason": "development slice has no externally pinned candidate authority or independent closed-schema replay verifier",
    },
}
_RG3_VERIFIED_INPUT_HOLD = {
    "status": "HOLD", "complexity": "O(N)",
    "reason": "execution_input/v3 is fully verified before streaming; verification traverses all sealed events, poses, and current-CAV records",
}
_RG3_NATIVE_TRANSITION_HOLD = {
    "status": "HOLD", "complexity": "O(N)",
    "reason": "the slice consumes the fully verified native current-CAV trace and does not claim an independently streaming native-transition authority",
}
_DSPB_VERIFIED_INPUT_HOLD = dict(_RG3_VERIFIED_INPUT_HOLD)
_PLL_VERIFIED_INPUT_HOLD = {
    "status": "HOLD", "complexity": "O(N)",
    "reason": "execution_input/v3 is fully verified before bounded PLL replay",
}
_PLL_NATIVE_TRANSITION_HOLD = {
    "status": "HOLD", "complexity": "O(query rows + query transitions)",
    "reason": "warmup transitions are reduced to a verified first-query boundary; only query decisions and subsequent transitions are emitted",
}
_DSPB_BOUNDED_STATE_PROFILE = {
    "maximum_window_pose_occurrences": 256,
    "maximum_equal_time_cluster_events": 8,
    "native_event_decision_history": 0,
    "native_pose_receipt_history": 0,
    "native_seen_event_ids": 0,
    "native_seen_pose_ids": 0,
}


class PostOutputOracleError(ValueError):
    """The fixed oracle rejected its request, batch replay, or candidate output."""


def _snapshot(value: object, where: str) -> Mapping[str, object]:
    try:
        result = json.loads(canonical_json_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PostOutputOracleError("%s is not canonical JSON" % where) from exc
    if not isinstance(result, dict):
        raise PostOutputOracleError("%s must be an object" % where)
    return result


def _exact(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise PostOutputOracleError("%s fields are not exact" % where)
    return value


def _sealed(mapping: Mapping[str, object], field: str, where: str) -> None:
    supplied = mapping.get(field)
    body = {key: value for key, value in mapping.items() if key != field}
    if supplied != canonical_sha256(body):
        raise PostOutputOracleError("%s seal differs" % where)


def runtime_manifest() -> Mapping[str, object]:
    executable = Path(sys.executable).resolve()
    worker = Path(__file__).resolve()
    return {
        "schema": "redred.mc_wtb_predictor_stage3.post_output_oracle_runtime/v1",
        "python_executable_path": str(executable),
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_executable_size_bytes": executable.stat().st_size,
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "python_cache_tag": sys.implementation.cache_tag,
        "isolated": bool(sys.flags.isolated),
        "no_site": bool(sys.flags.no_site),
        "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
        "repository_root": str(_REPOSITORY_ROOT),
        "worker_path": str(worker),
        "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
    }


def _verify_common_envelope(
    output: Mapping[str, object], execution: Mapping[str, object],
    contract: Mapping[str, object], candidate_id: str,
) -> None:
    if frozenset(output) != _TOP_FIELDS[candidate_id]:
        raise PostOutputOracleError("candidate top fields are not exact")
    candidate = contract["candidate_manifest"]
    if (
        output.get("schema") != _OUTPUT_SCHEMAS[candidate_id]
        or output.get("schema") != candidate["candidate_schema"]
        or output.get("candidate_id") != candidate_id
        or output.get("status") != "DEVELOPMENT_HOLD"
        or output.get("execution_input_schema") != EXECUTION_INPUT_SCHEMA
        or output.get("execution_input_aggregate_sha256") != execution["aggregate_sha256"]
        or output.get("neutral_input_sha256") != execution["neutral_input_sha256"]
        or output.get("ordered_query_event_ids_sha256") != execution["ordered_query_event_ids_sha256"]
        or output.get("candidate_safe_core_manifest") != candidate["candidate_safe_core_manifest"]
        or output.get("candidate_safe_core_manifest_sha256") != candidate["candidate_safe_core_manifest_sha256"]
        or output.get("coordinator_manifest") != candidate["coordinator_manifest"]
        or output.get("coordinator_manifest_sha256") != candidate["coordinator_manifest_sha256"]
        or output.get("output_authority_hold") != _OUTPUT_AUTHORITY_HOLDS[candidate_id]
        or output.get("deterministic_replay_count") != 2
        or output.get("deterministic_double_replay_verified") is not True
        or output.get("warmup_rows_emitted") != 0
        or output.get("retained_candidate_event_rows") != 0
        or output.get("query_event_count") != execution["query_event_count"]
    ):
        raise PostOutputOracleError("candidate common binding differs")
    if candidate_id == DSPB_CANDIDATE_ID:
        if (
            output["candidate_config"] != candidate["config_manifest"]["configuration"]
            or output["candidate_config_sha256"] != candidate["config_manifest"]["candidate_native_config_sha256"]
            or output["input_domain_hold"] != candidate["candidate_domain_hold"]
            or output["verified_input_complexity_hold"]
            != _DSPB_VERIFIED_INPUT_HOLD
            or output["bounded_state_profile"] != _DSPB_BOUNDED_STATE_PROFILE
        ):
            raise PostOutputOracleError("DSPB fixed configuration or domain differs")
    elif candidate_id == PLL_CANDIDATE_ID:
        if (
            output["configuration_sha256"] != candidate["config_manifest"]["candidate_native_config_sha256"]
            or output["input_domain_hold"] != candidate["candidate_domain_hold"]
            or output["batch_provenance_equivalence_hold"] != _PLL_PROVENANCE_HOLD
            or output["candidate_provenance_representation"] != "direct_anchor_plus_dependency_chain_endpoint_and_count"
            or output["verified_input_complexity_hold"]
            != _PLL_VERIFIED_INPUT_HOLD
            or output["native_transition_complexity_hold"]
            != _PLL_NATIVE_TRANSITION_HOLD
        ):
            raise PostOutputOracleError("PLL fixed configuration, provenance, or domain differs")
    else:
        if (
            output["native_transition_complexity_hold"]
            != _RG3_NATIVE_TRANSITION_HOLD
            or output["native_transition_complexity_hold"]
            != candidate["candidate_domain_hold"]
            or output["verified_input_complexity_hold"]
            != _RG3_VERIFIED_INPUT_HOLD
        ):
            raise PostOutputOracleError("RG3 domain HOLD differs")
    _sealed(output, "aggregate_sha256", "candidate aggregate")


def _query_path(execution: Mapping[str, object], pll: bool) -> Mapping[str, object]:
    windows = []
    total = 0
    transitions_total = 0
    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    for window, trace_window in zip(execution["windows"], trace_windows):
        records = trace_window["simulation"]["records"]
        rows = []
        first_query_cycle = None
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
        total += len(rows)
        if pll:
            if first_query_cycle is None:
                raise PostOutputOracleError("PLL query path is empty")
            last_event_cycle = records[-1]["occurrence_cycle"]
            transitions = [{
                "pose_id": pose["pose_id"], "pose_sha256": pose["pose_sha256"],
                "commit_cycle": pose["commit_cycle"],
            } for pose in window["poses"] if (
                first_query_cycle <= pose["commit_cycle"] <= last_event_cycle
            )]
            transitions_total += len(transitions)
            windows.append({
                "window_id": window["window_id"], "rows": rows,
                "transitions": transitions,
            })
        else:
            windows.append({
                "window_id": window["window_id"], "query_count": len(rows),
                "query_path_sha256": canonical_sha256(rows), "rows": rows,
            })
    result = {"windows": windows, "query_count": total,
              "query_path_sha256": canonical_sha256(windows)}
    if pll:
        result["query_transition_count"] = transitions_total
    return result


def _verify_rows_and_windows(
    output: Mapping[str, object], execution: Mapping[str, object],
    candidate_id: str,
) -> None:
    windows = output.get("windows")
    if not isinstance(windows, list) or len(windows) != len(execution["windows"]):
        raise PostOutputOracleError("candidate windows differ")
    window_fields = {
        RG3_CANDIDATE_ID: _RG3_WINDOW_FIELDS,
        DSPB_CANDIDATE_ID: _DSPB_WINDOW_FIELDS,
        PLL_CANDIDATE_ID: _PLL_WINDOW_FIELDS,
    }[candidate_id]
    row_fields = {
        RG3_CANDIDATE_ID: _RG3_ROW_FIELDS,
        DSPB_CANDIDATE_ID: _DSPB_ROW_FIELDS,
        PLL_CANDIDATE_ID: _PLL_ROW_FIELDS,
    }[candidate_id]
    total = 0
    for actual, source in zip(windows, execution["windows"]):
        actual = _exact(actual, window_fields, "candidate window")
        if actual["window_id"] != source["window_id"]:
            raise PostOutputOracleError("candidate window order differs")
        rows = actual["query_rows"]
        expected_events = [event for event in source["events"] if event["is_query"]]
        if not isinstance(rows, list) or len(rows) != len(expected_events):
            raise PostOutputOracleError("candidate query cardinality differs")
        if (
            actual["query_event_count"] != len(rows)
            or actual["warmup_event_count"] != len(source["events"]) - len(rows)
            or actual["warmup_rows_emitted"] != 0
            or actual["retained_candidate_event_rows"] != 0
            or actual["query_rows_sha256"] != canonical_sha256(rows)
        ):
            raise PostOutputOracleError("candidate window count or row seal differs")
        for row, event in zip(rows, expected_events):
            row = _exact(row, row_fields, "candidate query row")
            if (
                row["event_id"] != event["event_id"]
                or row["event_content_sha256"] != event["event_content_sha256"]
            ):
                raise PostOutputOracleError("candidate query identity differs")
            _sealed(row, "decision_sha256", "candidate decision")
            if candidate_id == DSPB_CANDIDATE_ID:
                ray = _exact(row["ray_derivation_receipt"], _DSPB_RAY_FIELDS, "DSPB ray receipt")
                _sealed(ray, "ray_derivation_sha256", "DSPB ray derivation")
                evidence = row["used_pose_evidence"]
                if not isinstance(evidence, list):
                    raise PostOutputOracleError("DSPB used-pose evidence differs")
                for pose in evidence:
                    _exact(pose, _DSPB_POSE_EVIDENCE_FIELDS, "DSPB used pose")
        if candidate_id == PLL_CANDIDATE_ID:
            boundary = _exact(actual["first_query_state_boundary"], _PLL_BOUNDARY_FIELDS, "PLL boundary")
            _sealed(boundary, "boundary_sha256", "PLL boundary")
            for name in ("effective", "pending"):
                state = boundary[name + "_state"]
                digest = boundary[name + "_state_sha256"]
                if state is None:
                    if digest is not None:
                        raise PostOutputOracleError("PLL boundary null state digest differs")
                else:
                    _verify_pll_state(state)
                    if digest != state["state_sha256"]:
                        raise PostOutputOracleError("PLL boundary state digest differs")
            transitions = actual["query_transitions"]
            if not isinstance(transitions, list):
                raise PostOutputOracleError("PLL transitions differ")
            if (
                actual["query_transition_count"] != len(transitions)
                or actual["query_transitions_sha256"] != canonical_sha256(transitions)
            ):
                raise PostOutputOracleError("PLL transition count or seal differs")
            for transition in transitions:
                transition = _exact(transition, _PLL_TRANSITION_FIELDS, "PLL transition")
                state = transition["published_state"]
                if state is None:
                    if transition["published_state_sha256"] is not None:
                        raise PostOutputOracleError("PLL null published-state digest differs")
                else:
                    _verify_pll_state(state)
                    if transition["published_state_sha256"] != state["state_sha256"]:
                        raise PostOutputOracleError("PLL published-state digest differs")
                _sealed(transition, "transition_sha256", "PLL transition")
        total += len(rows)
    if output["windows_sha256"] != canonical_sha256(windows):
        raise PostOutputOracleError("candidate windows seal differs")
    if output["query_event_count"] != total:
        raise PostOutputOracleError("candidate total query count differs")
    path = _query_path(execution, candidate_id == PLL_CANDIDATE_ID)
    if output["query_path_sha256"] != path["query_path_sha256"]:
        raise PostOutputOracleError("candidate query path differs")
    if candidate_id == PLL_CANDIDATE_ID:
        if output["query_transition_count"] != path["query_transition_count"]:
            raise PostOutputOracleError("PLL query transition path differs")
        for actual_window, expected_window in zip(output["windows"], path["windows"]):
            actual_transitions = [{
                "pose_id": transition["pose_id"],
                "pose_sha256": transition["pose_sha256"],
                "commit_cycle": transition["commit_cycle"],
            } for transition in actual_window["query_transitions"]]
            if actual_transitions != expected_window["transitions"]:
                raise PostOutputOracleError("PLL query transition identity differs")
    core_keys = {
        RG3_CANDIDATE_ID: (
            "windows", "windows_sha256", "query_event_count",
            "warmup_rows_emitted", "retained_candidate_event_rows",
            "maximum_retained_candidate_pose_count",
        ),
        DSPB_CANDIDATE_ID: (
            "windows", "windows_sha256", "query_event_count",
            "warmup_rows_emitted", "retained_candidate_event_rows",
            "maximum_retained_native_pose_count", "maximum_equal_time_cluster_count",
        ),
        PLL_CANDIDATE_ID: (
            "windows", "windows_sha256", "query_event_count",
            "query_transition_count", "warmup_rows_emitted",
            "retained_candidate_event_rows",
            "maximum_retained_fallback_pose_count",
            "maximum_retained_effective_pending_state_count",
        ),
    }[candidate_id]
    core = {key: output[key] for key in core_keys}
    if output["replay_sha256"] != canonical_sha256(core):
        raise PostOutputOracleError("candidate replay seal differs")


def _verify_pll_state(value: object) -> None:
    state = _exact(value, _PLL_STATE_FIELDS, "PLL stream state")
    _exact(state["native_state"], _PLL_NATIVE_STATE_FIELDS, "PLL native state")
    _sealed(state, "state_sha256", "PLL stream state")


def _neutral_objects(trace: CurrentCAVTrace):
    # Imported only inside the post-output phase.  The batch DSPB producer
    # requires these exact neutral dataclass types.
    from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
        NeutralEventInput, NeutralPoseInput, NeutralRegistryWindow,
    )
    registry = []
    events = {}
    poses = {}
    for window in trace.windows:
        row = window.registry
        neutral_window = NeutralRegistryWindow(
            row.window_id, row.warmup_start_ns_inclusive,
            row.query_start_ns_inclusive, row.query_end_ns_exclusive,
        )
        registry.append(neutral_window)
        events[row.window_id] = tuple(NeutralEventInput(
            event.event_id, event.timestamp_ns, event.polarity, event.is_query,
            event.sensor_ray, event.causal_pose_source_index,
            event.event_content_sha256, event.transform_guard_valid,
        ) for event in window.input_events)
        poses[row.window_id] = tuple(NeutralPoseInput(
            pose.pose_id, pose.timestamp_ns, pose.commit_cycle,
            pose.quaternion_xyzw, pose.pose_sha256,
            pose.value_valid, pose.arithmetic_valid,
        ) for pose in window.input_poses)
    return tuple(registry), events, poses


def _batch_projection(
    output: Mapping[str, object], execution: Mapping[str, object],
    trace: CurrentCAVTrace, candidate_id: str,
) -> Tuple[str, str, str]:
    """Return batch schema, aggregate SHA, and ordered query projection SHA."""

    expected_windows = []
    if candidate_id == RG3_CANDIDATE_ID:
        from benchmarks.redred_mc_wtb_predictor_stage3.rg3_output import (
            CANDIDATE_OUTPUT_SCHEMA, generate_locked_rg3_output,
        )
        registry = tuple(window.registry for window in trace.windows)
        # The locked batch wrapper predates execution_input/v3 and requires
        # increasing numeric IDs.  Run it over a private source-order ordinal
        # transport, then restore every ID-derived public field and decision
        # seal before comparison.  Candidate semantics never observe the
        # ordinal outside this oracle process.
        events = {}
        ordinal = 0
        for window in trace.windows:
            renamed = []
            for event in window.input_events:
                digest = canonical_event_content_sha256(
                    ordinal, event.timestamp_ns, event.polarity,
                    event.is_query, event.sensor_ray,
                    event.causal_pose_source_index,
                    event.transform_guard_valid,
                )
                renamed.append(SimpleNamespace(
                    event_id=ordinal,
                    timestamp_ns=event.timestamp_ns,
                    polarity=event.polarity,
                    is_query=event.is_query,
                    sensor_ray=event.sensor_ray,
                    causal_pose_source_index=event.causal_pose_source_index,
                    event_content_sha256=digest,
                    transform_guard_valid=event.transform_guard_valid,
                ))
                ordinal += 1
            events[window.registry.window_id] = tuple(renamed)
        poses = {window.registry.window_id: tuple(window.input_poses)
                 for window in trace.windows}
        batch = generate_locked_rg3_output(registry, events, poses, _ADAPTER_SENTINEL_SHA256)
        for batch_window, source_window, actual_window in zip(
            batch["windows"], execution["windows"], output["windows"]
        ):
            expected = []
            for row, event in zip(batch_window["events"], source_window["events"]):
                if not event["is_query"]:
                    continue
                restored = dict(row)
                restored["event_id"] = event["event_id"]
                restored["event_content_sha256"] = event["event_content_sha256"]
                restored["decision_sha256"] = canonical_sha256({
                    key: value for key, value in restored.items()
                    if key != "decision_sha256"
                })
                expected.append(restored)
            if canonical_json_bytes(expected) != canonical_json_bytes(actual_window["query_rows"]):
                raise PostOutputOracleError("RG3 ordered batch query projection differs")
            expected_windows.append({"window_id": source_window["window_id"], "query_rows": expected})
        return CANDIDATE_OUTPUT_SCHEMA, batch["aggregate_sha256"], canonical_sha256(expected_windows)

    registry, events, poses = _neutral_objects(trace)
    if candidate_id == DSPB_CANDIDATE_ID:
        from benchmarks.redred_mc_wtb_predictor_stage3.dspb_output import (
            CANDIDATE_OUTPUT_SCHEMA, generate_dspb_candidate_output,
            verify_dspb_candidate_output,
        )
        batch = generate_dspb_candidate_output(
            registry, events, poses, _ADAPTER_SENTINEL_SHA256
        )
        verify_dspb_candidate_output(
            batch, registry, events, poses, _ADAPTER_SENTINEL_SHA256
        )
        for batch_window, source_window, actual_window in zip(
            batch["windows"], execution["windows"], output["windows"]
        ):
            expected = [row for row, event in zip(batch_window["events"], source_window["events"])
                        if event["is_query"]]
            if canonical_json_bytes(expected) != canonical_json_bytes(actual_window["query_rows"]):
                raise PostOutputOracleError("DSPB ordered batch query projection differs")
            expected_windows.append({"window_id": source_window["window_id"], "query_rows": expected})
        return CANDIDATE_OUTPUT_SCHEMA, batch["aggregate_sha256"], canonical_sha256(expected_windows)

    from benchmarks.redred_mc_wtb_predictor_stage3.pll_output import (
        CANDIDATE_OUTPUT_SCHEMA, generate_locked_pll_output,
        verify_locked_pll_output,
    )
    bundle = SimpleNamespace(
        neutral_registry=registry, event_streams=events, pose_streams=poses,
        provenance_seal={"aggregate_sha256": _ADAPTER_SENTINEL_SHA256},
    )
    baseline_windows = []
    for neutral_window, trace_window in zip(registry, trace.windows):
        baseline_windows.append(SimpleNamespace(
            registry=neutral_window,
            input_events=events[neutral_window.window_id],
            input_poses=poses[neutral_window.window_id],
            simulation=SimpleNamespace(records=trace_window.simulation.records),
        ))
    baseline = SimpleNamespace(
        windows=tuple(baseline_windows),
        neutral_input_sha256=trace.neutral_input_sha256,
    )
    batch = generate_locked_pll_output(bundle, baseline)
    verify_locked_pll_output(batch, bundle, baseline)
    semantic_fields = (
        "event_id", "event_content_sha256", "occurrence_cycle",
        "decision_cycle", "model_id", "configuration_sha256",
        "predictor_state_version", "predictor_state_is_reset",
        "state_effective_cycle",
        "candidate_attempted", "candidate_used", "candidate_failure_reason",
        "route", "fallback_reason", "candidate_quaternion_xyzw", "world_ray",
    )
    for batch_window, source_window, trace_window, actual_window in zip(
        batch["windows"], execution["windows"], trace.windows, output["windows"]
    ):
        selected = [(row, decision) for row, event, decision in zip(
            batch_window["events"], source_window["events"],
            trace_window.simulation.records,
        ) if event["is_query"]]
        if len(selected) != len(actual_window["query_rows"]):
            raise PostOutputOracleError("PLL ordered batch query count differs")
        compact = []
        for (batch_row, decision), actual in zip(selected, actual_window["query_rows"]):
            for field in semantic_fields:
                if actual[field] != batch_row[field]:
                    raise PostOutputOracleError("PLL batch semantic field %s differs" % field)
            dependencies = batch_row["state_dependency_pose_ids"]
            anchor = dependencies[-1] if dependencies else None
            if (
                actual["baseline_fallback_used_pose_ids"] != list(decision.used_pose_ids)
                or actual["state_dependency_pose_count"] != len(dependencies)
                or actual["candidate_dependency_pose_count"] != len(dependencies)
                or actual["state_anchor_pose_id"] != anchor
                or actual["candidate_direct_anchor_pose_id"]
                != (anchor if actual["candidate_used"] else None)
            ):
                raise PostOutputOracleError("PLL batch dependency count or direct anchor differs")
            compact.append({field: actual[field] for field in semantic_fields})
        expected_windows.append({"window_id": source_window["window_id"], "query_rows": compact})
    return CANDIDATE_OUTPUT_SCHEMA, batch["aggregate_sha256"], canonical_sha256(expected_windows)


def _parse_request(raw: bytes, argv_candidate_id: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise PostOutputOracleError("oracle request is not JSON") from exc
    if not isinstance(value, dict) or frozenset(value) != _REQUEST_FIELDS:
        raise PostOutputOracleError("oracle request fields are not exact")
    if canonical_json_bytes(value) != raw:
        raise PostOutputOracleError("oracle request is not canonical")
    body = {key: item for key, item in value.items() if key != "request_sha256"}
    if value["request_sha256"] != canonical_sha256(body):
        raise PostOutputOracleError("oracle request seal differs")
    if value["schema"] != POST_OUTPUT_REQUEST_SCHEMA or value["candidate_id"] != argv_candidate_id:
        raise PostOutputOracleError("oracle request identity differs")
    return value


def run_request(raw: bytes, argv_candidate_id: str) -> bytes:
    if (
        not sys.flags.isolated or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or Path.cwd().resolve() != _REPOSITORY_ROOT
    ):
        raise PostOutputOracleError("oracle runtime flags or root differ")
    request = _parse_request(raw, argv_candidate_id)
    runtime = runtime_manifest()
    if (
        runtime != request["expected_runtime_manifest"]
        or canonical_sha256(runtime) != request["expected_runtime_manifest_sha256"]
    ):
        raise PostOutputOracleError("oracle runtime manifest differs")
    execution = _snapshot(request["execution_input"], "execution input")
    contract = _snapshot(request["refreeze_contract"], "refreeze contract")
    output = _snapshot(request["candidate_output"], "candidate output")
    execution_digest = verify_stage3_execution_input(
        execution,
        expected_aggregate_sha256=request["execution_input_aggregate_sha256"],
        repo_root=_REPOSITORY_ROOT,
    )
    contract_digest = verify_refreeze_v4_contract(
        contract, execution, argv_candidate_id
    )
    if (
        execution_digest != request["execution_input_aggregate_sha256"]
        or contract_digest != request["refreeze_contract_sha256"]
        or canonical_sha256(output) != request["candidate_output_sha256"]
        or output.get("aggregate_sha256") != request["candidate_output_aggregate_sha256"]
    ):
        raise PostOutputOracleError("oracle request cross-binding differs")
    trace = load_current_cav_trace(execution["score_free_current_cav_trace"])
    if trace.aggregate_sha256 != execution["score_free_current_cav_trace_sha256"]:
        raise PostOutputOracleError("loaded current-CAV trace binding differs")
    _verify_common_envelope(output, execution, contract, argv_candidate_id)
    _verify_rows_and_windows(output, execution, argv_candidate_id)
    batch_schema, batch_sha, projection_sha = _batch_projection(
        output, execution, trace, argv_candidate_id
    )
    actual_projection = canonical_sha256([{
        "window_id": window["window_id"], "query_rows": window["query_rows"]
    } for window in output["windows"]])
    # PLL's batch projection intentionally contains only published semantic
    # fields, so its hash is distinct from the rich candidate projection.
    mode = (
        "exact_ordered_query_row_projection"
        if argv_candidate_id != PLL_CANDIDATE_ID
        else "published_semantics_plus_dependency_count_and_direct_anchor"
    )
    provenance = (
        {"status": "PASS", "mode": "exact_ordered_query_row_projection"}
        if argv_candidate_id != PLL_CANDIDATE_ID
        else dict(_PLL_PROVENANCE_HOLD)
    )
    body = {
        "schema": POST_OUTPUT_RECEIPT_SCHEMA,
        "status": "DEVELOPMENT_HOLD",
        "authority_go": False,
        "candidate_id": argv_candidate_id,
        "candidate_output_schema": output["schema"],
        "execution_input_aggregate_sha256": execution_digest,
        "refreeze_contract_sha256": contract_digest,
        "candidate_output_sha256": request["candidate_output_sha256"],
        "candidate_output_aggregate_sha256": output["aggregate_sha256"],
        "candidate_child_response_sha256": request["candidate_child_response_sha256"],
        "runtime_manifest": runtime,
        "runtime_manifest_sha256": canonical_sha256(runtime),
        "verification_mode": mode,
        "batch_output_schema": batch_schema,
        "batch_output_aggregate_sha256": batch_sha,
        "batch_query_projection_sha256": projection_sha,
        "candidate_query_projection_sha256": actual_projection,
        "window_count": len(output["windows"]),
        "query_event_count": output["query_event_count"],
        "all_nested_self_seals_verified": True,
        "query_projection_verified": True,
        "provenance_verification": provenance,
        "output_authority_hold": output["output_authority_hold"],
        "external_oracle_release_hold": dict(EXTERNAL_ORACLE_RELEASE_HOLD),
        "resource_ppa_hold": dict(RESOURCE_PPA_HOLD),
        "filesystem_publication_hold": dict(FILESYSTEM_PUBLICATION_HOLD),
    }
    return canonical_json_bytes(dict(body, aggregate_sha256=canonical_sha256(body)))


def main() -> int:
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in (
            RG3_CANDIDATE_ID, DSPB_CANDIDATE_ID, PLL_CANDIDATE_ID
        ):
            return 64
        result = run_request(sys.stdin.buffer.read(), sys.argv[1])
        sys.stdout.buffer.write(result)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:
        return 70


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "EXTERNAL_ORACLE_RELEASE_HOLD", "FILESYSTEM_PUBLICATION_HOLD",
    "POST_OUTPUT_RECEIPT_SCHEMA", "POST_OUTPUT_REQUEST_SCHEMA",
    "PostOutputOracleError", "RESOURCE_PPA_HOLD",
)
