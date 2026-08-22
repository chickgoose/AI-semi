"""Candidate-safe bounded-state DSPB query streaming core.

The coordinator verifies and snapshots execution_input/v3 before calling the
private entrypoint in this module.  This module imports no execution authority,
evaluator, selector, label, or scoring implementation.

Native DSPB pose and event transitions are preserved.  Diagnostic native
receipt histories and seen-ID sets are cleared after each transition because
v3 has already authenticated exact-once source identities.  Algorithmic pose
history is hard-capped per reset window.  Warmup decisions contribute only one
rolling decision digest; no warmup rich row is emitted or retained.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryMode,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import (
    DSPBConfig,
    DSPBError,
    DSPBModel,
    EventRecord,
    SuppliedPose,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


MAX_WINDOW_POSE_OCCURRENCES = 256
MAX_EQUAL_TIME_CLUSTER_EVENTS = 8
ROUTE_CANDIDATE = "CANDIDATE"
ROUTE_CURRENT_CAV = "CURRENT_CAV"
ROUTE_FRESH_ZOH = "FRESH_ZOH"
ROUTE_SENSOR_FIXED = "SENSOR_FIXED"
RESET_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.window_reset/v1"
STATE_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_state/v1"
POSE_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_pose_chain/v1"


class DSPBQueryStreamCoreError(ValueError):
    """Verified execution evidence cannot drive bounded native DSPB."""


def _native_sha256(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise DSPBQueryStreamCoreError(
            "native DSPB receipt is not canonical JSON"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _expert_mapping(function: object) -> Mapping[str, object]:
    return {
        "expert_id": function.expert_id,
        "state_version": function.state_version,
        "anchor_pose_id": function.anchor_pose_id,
        "anchor_timestamp_ns": function.anchor_timestamp_ns,
        "anchor_commit_cycle": function.anchor_commit_cycle,
        "anchor_quaternion_xyzw": list(function.anchor_quaternion_xyzw),
        "source_pose_ids": list(function.source_pose_ids),
        "source_timestamps_ns": list(function.source_timestamps_ns),
        "source_commit_cycles": list(function.source_commit_cycles),
        "parent_state_version": function.parent_state_version,
        "rate_body_rad_s": list(function.rate_body_rad_s),
        "acceleration_body_rad_s2": list(function.acceleration_body_rad_s2),
        "previous_quaternion_xyzw": (
            None
            if function.previous_quaternion_xyzw is None
            else list(function.previous_quaternion_xyzw)
        ),
        "previous_interval_ns": function.previous_interval_ns,
        "valid": function.valid,
        "invalid_reason": function.invalid_reason,
    }


def _native_state_mapping(state: object) -> Mapping[str, object]:
    return {
        "state_version": state.state_version,
        "native_effective_cycle": state.effective_cycle,
        "expert_functions": [
            _expert_mapping(function) for function in state.expert_functions
        ],
        "credits": [credit.to_mapping() for credit in state.credits],
        "selected_expert_id": state.selected_expert_id,
        "lock_reason": state.lock_reason,
    }


def _state_receipt(
    window_id: str,
    reset_generation_sha256: str,
    state: object,
    effective_cycle: int,
    parent_state_sha256: Optional[str],
    transition_pose_id: Optional[int],
    dependency_pose_ids: Sequence[int],
) -> Mapping[str, object]:
    body = {
        "schema": STATE_RECEIPT_SCHEMA,
        "window_id": window_id,
        "reset_generation_sha256": reset_generation_sha256,
        "state_version": state.state_version,
        "effective_cycle": effective_cycle,
        "parent_state_sha256": parent_state_sha256,
        "transition_pose_id": transition_pose_id,
        "dependency_pose_ids": list(dependency_pose_ids),
        "native_state": _native_state_mapping(state),
    }
    return dict(body, state_sha256=canonical_sha256(body))


def _reset(
    registry: Mapping[str, object],
    excluded_pose_ids: Sequence[int],
    initial_state: object,
) -> Tuple[str, Mapping[str, object], Mapping[str, object]]:
    config = DSPBConfig()
    generation_body = {
        "window_id": registry["window_id"],
        "warmup_start_ns_inclusive": registry["warmup_start_ns_inclusive"],
        "query_start_ns_inclusive": registry["query_start_ns_inclusive"],
        "query_end_ns_exclusive": registry["query_end_ns_exclusive"],
        "candidate_id": config.candidate_id,
        "candidate_config_sha256": config.sha256,
        "reset_cycle": 0,
        "excluded_pre_reset_pose_ids": list(excluded_pose_ids),
    }
    generation_sha256 = canonical_sha256(generation_body)
    initial = _state_receipt(
        registry["window_id"],
        generation_sha256,
        initial_state,
        0,
        None,
        None,
        (),
    )
    reset_body = {
        "schema": RESET_RECEIPT_SCHEMA,
        "reset_generation_sha256": generation_sha256,
        "generation": generation_body,
        "previous_window_state_sha256": None,
        "initial_state_sha256": initial["state_sha256"],
    }
    reset_receipt = dict(
        reset_body,
        reset_receipt_sha256=canonical_sha256(reset_body),
    )
    return generation_sha256, initial, reset_receipt


def _pose_chain_receipt(
    pose: Mapping[str, object],
    native: object,
    prior_state: Mapping[str, object],
    next_state: Mapping[str, object],
    previous_receipt_sha256: str,
) -> Mapping[str, object]:
    native_mapping = native.to_mapping()
    native_body = dict(native_mapping)
    native_digest = native_body.pop("receipt_sha256", None)
    if native_digest != _native_sha256(native_body):
        raise DSPBQueryStreamCoreError("native DSPB pose receipt digest differs")
    if (
        native.candidate_id != DSPBConfig().candidate_id
        or native.config_sha256 != DSPBConfig().sha256
        or native.pose_id != pose["pose_id"]
        or native.measurement_timestamp_ns != pose["timestamp_ns"]
        or native.commit_cycle != pose["commit_cycle"]
        or native.prior_state_version != prior_state["state_version"]
        or native.next_state_version != next_state["state_version"]
        or native.next_effective_cycle != next_state["effective_cycle"]
        or next_state["parent_state_sha256"] != prior_state["state_sha256"]
    ):
        raise DSPBQueryStreamCoreError("native DSPB pose/state transition differs")
    body = {
        "schema": POSE_RECEIPT_SCHEMA,
        "pose_id": pose["pose_id"],
        "pose_content_sha256": pose["pose_sha256"],
        "prior_state_sha256": prior_state["state_sha256"],
        "next_state_sha256": next_state["state_sha256"],
        "previous_pose_receipt_sha256": previous_receipt_sha256,
        "native_pose_receipt": native_mapping,
    }
    return dict(body, pose_receipt_sha256=canonical_sha256(body))


def _baseline_quaternion(
    event: Mapping[str, object],
    baseline: Mapping[str, object],
    poses_by_id: Mapping[int, Mapping[str, object]],
) -> Optional[Tuple[float, float, float, float]]:
    if baseline["disposition_reason"] == "causal_cav":
        if baseline["disposition"] != "corrected_world_ray" or len(baseline["used_pose_ids"]) != 2:
            raise DSPBQueryStreamCoreError("baseline current-CAV receipt differs")
        samples = tuple(
            PoseSample(
                poses_by_id[pose_id]["timestamp_ns"],
                poses_by_id[pose_id]["commit_cycle"],
                tuple(poses_by_id[pose_id]["quaternion_xyzw"]),
            )
            for pose_id in baseline["used_pose_ids"]
        )
        try:
            recovery = recover_causal_cav(
                samples,
                event["timestamp_ns"],
                baseline["occurrence_cycle"],
            )
        except GeometryError as exc:
            raise DSPBQueryStreamCoreError(
                "cannot reconstruct exact current CAV"
            ) from exc
        if recovery.mode is not RecoveryMode.CAV or recovery.quaternion_xyzw is None:
            raise DSPBQueryStreamCoreError("baseline current-CAV geometry differs")
        return recovery.quaternion_xyzw
    if baseline["disposition_reason"] == "fresh_zoh_fallback":
        if baseline["disposition"] != "corrected_world_ray" or len(baseline["used_pose_ids"]) != 1:
            raise DSPBQueryStreamCoreError("baseline fresh-ZOH receipt differs")
        return tuple(poses_by_id[baseline["used_pose_ids"][0]]["quaternion_xyzw"])
    if baseline["disposition"] != "raw_bypass":
        raise DSPBQueryStreamCoreError("baseline route taxonomy differs")
    return None


def _unit_world_ray(
    quaternion_xyzw: Sequence[float],
    sensor_ray: Sequence[float],
) -> Tuple[float, float, float]:
    try:
        world = rotate_sensor_ray_to_world(quaternion_xyzw, sensor_ray)
    except GeometryError as exc:
        raise DSPBQueryStreamCoreError("DSPB world-ray projection failed") from exc
    norm = math.sqrt(math.fsum(component * component for component in world))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-12:
        raise DSPBQueryStreamCoreError("DSPB world ray is not normalized")
    return world


def _candidate_pose_ids(
    event: Mapping[str, object],
    baseline: Mapping[str, object],
    decision: object,
    poses_by_id: Mapping[int, Mapping[str, object]],
    state_dependency_pose_ids: Sequence[int],
) -> Tuple[int, ...]:
    identifiers = tuple(decision.used_pose_ids)
    if identifiers != tuple(sorted(set(identifiers))) or not identifiers:
        raise DSPBQueryStreamCoreError("DSPB candidate pose dependencies differ")
    if not (
        len(identifiers)
        == len(decision.used_pose_timestamps_ns)
        == len(decision.used_pose_commit_cycles)
    ):
        raise DSPBQueryStreamCoreError("DSPB candidate pose cardinality differs")
    for pose_id, timestamp, commit in zip(
        identifiers,
        decision.used_pose_timestamps_ns,
        decision.used_pose_commit_cycles,
    ):
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose["timestamp_ns"] != timestamp
            or pose["commit_cycle"] != commit
            or pose["commit_cycle"] >= baseline["occurrence_cycle"]
            or pose["timestamp_ns"] > event["timestamp_ns"]
            or not pose["value_valid"]
            or not pose["arithmetic_valid"]
            or pose_id not in state_dependency_pose_ids
        ):
            raise DSPBQueryStreamCoreError("DSPB candidate used unavailable pose")
    return identifiers


def _event_row(
    event: Mapping[str, object],
    baseline: Mapping[str, object],
    decision: object,
    poses_by_id: Mapping[int, Mapping[str, object]],
    state_receipt: Mapping[str, object],
    state_dependency_pose_ids: Sequence[int],
    pose_receipt_chain_sha256: str,
    prior_decision_sha256: Optional[str],
) -> Mapping[str, object]:
    decision_cycle = baseline["occurrence_cycle"]
    occurrence_cycle = decision_cycle - 1
    config = DSPBConfig()
    if (
        decision.candidate_id != config.candidate_id
        or decision.config_sha256 != config.sha256
        or decision.event_id != event["event_id"]
        or decision.occurrence_timestamp_ns != event["timestamp_ns"]
        or decision.occurrence_cycle != occurrence_cycle
        or decision.decision_cycle != decision_cycle
        or decision.state_version != state_receipt["state_version"]
        or list(state_dependency_pose_ids) != state_receipt["dependency_pose_ids"]
    ):
        raise DSPBQueryStreamCoreError("native DSPB event receipt differs")
    native_mapping = decision.to_mapping()
    native_body = dict(native_mapping)
    if native_body.pop("decision_sha256", None) != _native_sha256(native_body):
        raise DSPBQueryStreamCoreError("native DSPB decision digest differs")

    baseline_quaternion = _baseline_quaternion(event, baseline, poses_by_id)
    candidate_attempted = baseline["disposition_reason"] == "causal_cav"
    candidate_failure_reason = None
    geometry_expert_id = None
    fallback_reason = None
    if candidate_attempted and decision.candidate_used:
        if decision.output_quaternion_xyzw is None or decision.geometry_expert_id is None:
            raise DSPBQueryStreamCoreError("DSPB candidate geometry is missing")
        route = ROUTE_CANDIDATE
        used_pose_ids = _candidate_pose_ids(
            event,
            baseline,
            decision,
            poses_by_id,
            state_dependency_pose_ids,
        )
        output_quaternion = decision.output_quaternion_xyzw
        geometry_expert_id = decision.geometry_expert_id
    elif candidate_attempted:
        if decision.candidate_used or baseline_quaternion is None:
            raise DSPBQueryStreamCoreError("DSPB current-CAV fallback differs")
        if type(decision.fallback_reason) is not str or not decision.fallback_reason:
            raise DSPBQueryStreamCoreError("DSPB failure reason is missing")
        route = ROUTE_CURRENT_CAV
        used_pose_ids = tuple(baseline["used_pose_ids"])
        output_quaternion = baseline_quaternion
        candidate_failure_reason = decision.fallback_reason
        fallback_reason = decision.fallback_reason
    elif baseline["disposition_reason"] == "fresh_zoh_fallback":
        route = ROUTE_FRESH_ZOH
        used_pose_ids = tuple(baseline["used_pose_ids"])
        output_quaternion = baseline_quaternion
        fallback_reason = baseline["disposition_reason"]
    else:
        route = ROUTE_SENSOR_FIXED
        used_pose_ids = tuple(baseline["used_pose_ids"])
        output_quaternion = None
        fallback_reason = baseline["disposition_reason"]

    used_pose_evidence = []
    for pose_id in used_pose_ids:
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose["commit_cycle"] >= decision_cycle
            or pose["timestamp_ns"] > event["timestamp_ns"]
        ):
            raise DSPBQueryStreamCoreError("exact route used unavailable pose")
        used_pose_evidence.append({
            "pose_id": pose["pose_id"],
            "measurement_timestamp_ns": pose["timestamp_ns"],
            "commit_cycle": pose["commit_cycle"],
            "pose_content_sha256": pose["pose_sha256"],
            "value_valid": pose["value_valid"],
            "arithmetic_valid": pose["arithmetic_valid"],
        })
    world_ray = (
        None
        if output_quaternion is None
        else list(_unit_world_ray(output_quaternion, event["sensor_ray"]))
    )
    ray_body = {
        "event_content_sha256": event["event_content_sha256"],
        "route": route,
        "sensor_ray": list(event["sensor_ray"]),
        "output_quaternion_xyzw": (
            None if output_quaternion is None else list(output_quaternion)
        ),
        "world_ray": world_ray,
    }
    ray_receipt = dict(
        ray_body,
        ray_derivation_sha256=canonical_sha256(ray_body),
    )
    body = {
        "event_id": event["event_id"],
        "event_content_sha256": event["event_content_sha256"],
        "event_timestamp_ns": event["timestamp_ns"],
        "is_query": event["is_query"],
        "occurrence_cycle": occurrence_cycle,
        "decision_cycle": decision_cycle,
        "model_id": config.candidate_id,
        "geometry_expert_id": geometry_expert_id,
        "predictor_state_version": decision.state_version,
        "predictor_state_sha256": state_receipt["state_sha256"],
        "state_dependency_pose_ids": list(state_dependency_pose_ids),
        "pose_receipt_chain_sha256": pose_receipt_chain_sha256,
        "used_pose_ids": list(used_pose_ids),
        "used_pose_evidence": used_pose_evidence,
        "route": route,
        "route_reason": baseline["disposition_reason"],
        "candidate_attempted": candidate_attempted,
        "candidate_used": route == ROUTE_CANDIDATE,
        "candidate_failure_reason": candidate_failure_reason,
        "fallback_reason": fallback_reason,
        "output_quaternion_xyzw": (
            None if output_quaternion is None else list(output_quaternion)
        ),
        "world_ray": world_ray,
        "ray_derivation_receipt": ray_receipt,
        "native_decision_sha256": decision.decision_sha256,
        "prior_decision_sha256": prior_decision_sha256,
    }
    return dict(body, decision_sha256=canonical_sha256(body))


class _BoundedDSPB(object):
    """Native DSPB with bounded diagnostic retention for verified source."""

    __slots__ = ("maximum_native_pose_count", "model")

    def __init__(self) -> None:
        self.model = DSPBModel()
        self.maximum_native_pose_count = 0

    def predict_cluster(self, events: Sequence[EventRecord]) -> Sequence[object]:
        if len(events) > MAX_EQUAL_TIME_CLUSTER_EVENTS:
            raise DSPBQueryStreamCoreError("DSPB equal-time cluster exceeds eight events")
        try:
            decisions = self.model.predict_event_cluster(events)
        except DSPBError as exc:
            raise DSPBQueryStreamCoreError("DSPB native event transition failed") from exc
        # v3 independently authenticates exact-once event identity.  These
        # native collections are diagnostics, not algorithmic state.
        self.model._event_decisions = ()
        self.model._seen_event_ids.clear()
        return decisions

    def commit_pose(self, pose: SuppliedPose) -> object:
        try:
            receipt = self.model.commit_pose(pose)
        except DSPBError as exc:
            raise DSPBQueryStreamCoreError("DSPB native pose transition failed") from exc
        self.maximum_native_pose_count = max(
            self.maximum_native_pose_count,
            len(self.model._valid_poses),
        )
        if self.maximum_native_pose_count > MAX_WINDOW_POSE_OCCURRENCES:
            raise DSPBQueryStreamCoreError("DSPB native pose state exceeds 256")
        self.model._pose_receipts = ()
        self.model._seen_pose_ids.clear()
        return receipt

    def prune_state_receipts(
        self,
        states: Dict[int, Mapping[str, object]],
        dependencies: Dict[int, Tuple[int, ...]],
    ) -> None:
        retained = {self.model.published_state.state_version}
        if self.model.pending_state is not None:
            retained.add(self.model.pending_state.state_version)
        for version in tuple(states):
            if version not in retained:
                del states[version]
                del dependencies[version]


def _run_window(
    registry: Mapping[str, object],
    window: Mapping[str, object],
    trace_window: Mapping[str, object],
) -> Mapping[str, object]:
    events = window["events"]
    poses = window["poses"]
    records = trace_window["simulation"]["records"]
    if len(events) != len(records):
        raise DSPBQueryStreamCoreError("execution and trace cardinality differs")
    if len(poses) > MAX_WINDOW_POSE_OCCURRENCES:
        raise DSPBQueryStreamCoreError("DSPB window has more than 256 poses")
    active_poses = [pose for pose in poses if pose["commit_cycle"] >= 0]
    active_pose_cycles = [pose["commit_cycle"] for pose in active_poses]
    if len(set(active_pose_cycles)) != len(active_pose_cycles):
        raise DSPBQueryStreamCoreError(
            "DSPB post-reset pose commit cycles must be unique"
        )
    excluded_ids = [pose["pose_id"] for pose in poses if pose["commit_cycle"] < 0]
    poses_by_id = {pose["pose_id"]: pose for pose in poses}
    bounded = _BoundedDSPB()
    generation_sha, initial, reset = _reset(
        registry,
        excluded_ids,
        bounded.model.published_state,
    )
    states = {0: initial}
    dependencies = {0: ()}  # type: Dict[int, Tuple[int, ...]]
    pose_chain_sha = reset["reset_receipt_sha256"]
    prior_decision_sha = None
    query_rows = []
    warmup_count = 0
    maximum_cluster = 0
    event_index = 0
    pose_index = 0

    while event_index < len(events) or pose_index < len(active_poses):
        event_cycle = (
            records[event_index]["occurrence_cycle"]
            if event_index < len(events)
            else None
        )
        pose_cycle = (
            active_poses[pose_index]["commit_cycle"]
            if pose_index < len(active_poses)
            else None
        )
        if event_cycle is None:
            cycle = pose_cycle
        elif pose_cycle is None:
            cycle = event_cycle
        else:
            cycle = min(event_cycle, pose_cycle)

        # All event clusters on an edge are decided before its pose commit.
        while event_index < len(events) and records[event_index]["occurrence_cycle"] == cycle:
            timestamp = events[event_index]["timestamp_ns"]
            end = event_index + 1
            while (
                end < len(events)
                and records[end]["occurrence_cycle"] == cycle
                and events[end]["timestamp_ns"] == timestamp
            ):
                end += 1
            cluster_events = events[event_index:end]
            cluster_records = records[event_index:end]
            maximum_cluster = max(maximum_cluster, len(cluster_events))
            native_events = tuple(
                EventRecord(
                    event["event_id"],
                    event["timestamp_ns"],
                    cycle - 1,
                    cycle,
                )
                for event in cluster_events
            )
            decisions = bounded.predict_cluster(native_events)
            if len({decision.state_version for decision in decisions}) != 1:
                raise DSPBQueryStreamCoreError("equal-time cluster changed state")
            for event, baseline, decision in zip(
                cluster_events,
                cluster_records,
                decisions,
            ):
                state = states.get(decision.state_version)
                state_dependencies = dependencies.get(decision.state_version)
                if state is None or state_dependencies is None:
                    raise DSPBQueryStreamCoreError("DSPB decision state is unavailable")
                row = _event_row(
                    event,
                    baseline,
                    decision,
                    poses_by_id,
                    state,
                    state_dependencies,
                    pose_chain_sha,
                    prior_decision_sha,
                )
                prior_decision_sha = row["decision_sha256"]
                if event["is_query"]:
                    query_rows.append(row)
                else:
                    warmup_count += 1
            event_index = end
            bounded.prune_state_receipts(states, dependencies)

        if pose_index < len(active_poses) and active_poses[pose_index]["commit_cycle"] == cycle:
            pose = active_poses[pose_index]
            native = bounded.commit_pose(SuppliedPose(
                pose["pose_id"],
                pose["timestamp_ns"],
                pose["commit_cycle"],
                tuple(pose["quaternion_xyzw"]),
                pose["value_valid"],
                pose["arithmetic_valid"],
            ))
            prior_state = states.get(native.prior_state_version)
            pending = bounded.model.pending_state
            if prior_state is None or pending is None:
                raise DSPBQueryStreamCoreError("DSPB pose transition state is missing")
            prior_dependencies = dependencies[native.prior_state_version]
            next_dependencies = prior_dependencies + (pose["pose_id"],)
            next_state = _state_receipt(
                registry["window_id"],
                generation_sha,
                pending,
                native.next_effective_cycle,
                prior_state["state_sha256"],
                pose["pose_id"],
                next_dependencies,
            )
            states[pending.state_version] = next_state
            dependencies[pending.state_version] = next_dependencies
            chain = _pose_chain_receipt(
                pose,
                native,
                prior_state,
                next_state,
                pose_chain_sha,
            )
            pose_chain_sha = chain["pose_receipt_sha256"]
            pose_index += 1
            bounded.prune_state_receipts(states, dependencies)

    return {
        "window_id": window["window_id"],
        "query_rows": query_rows,
        "query_rows_sha256": canonical_sha256(query_rows),
        "warmup_event_count": warmup_count,
        "query_event_count": len(query_rows),
        "warmup_rows_emitted": 0,
        "retained_candidate_event_rows": 0,
        "maximum_retained_native_pose_count": bounded.maximum_native_pose_count,
        "maximum_equal_time_cluster_count": maximum_cluster,
        "retained_native_event_decisions": len(bounded.model.event_decisions),
        "retained_native_pose_receipts": len(bounded.model.pose_receipts),
        "retained_native_seen_event_ids": len(bounded.model._seen_event_ids),
        "retained_native_seen_pose_ids": len(bounded.model._seen_pose_ids),
    }


def _run_verified_execution_snapshot(
    execution: Mapping[str, object],
) -> Mapping[str, object]:
    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    registries = execution["neutral_registry"]
    windows = []
    for registry, window, trace_window in zip(
        registries,
        execution["windows"],
        trace_windows,
    ):
        if not (
            registry["window_id"]
            == window["window_id"]
            == trace_window["registry"]["window_id"]
        ):
            raise DSPBQueryStreamCoreError("DSPB window order differs")
        windows.append(_run_window(registry, window, trace_window))
    return {
        "windows": windows,
        "windows_sha256": canonical_sha256(windows),
        "query_event_count": sum(window["query_event_count"] for window in windows),
        "warmup_rows_emitted": 0,
        "retained_candidate_event_rows": 0,
        "maximum_retained_native_pose_count": max(
            window["maximum_retained_native_pose_count"] for window in windows
        ),
        "maximum_equal_time_cluster_count": max(
            window["maximum_equal_time_cluster_count"] for window in windows
        ),
    }


__all__ = ()
