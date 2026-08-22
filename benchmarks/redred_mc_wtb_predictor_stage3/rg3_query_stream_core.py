"""Candidate-safe RG3 query-row streaming core.

This module deliberately has no execution-authority, evaluator, selector, or
label dependency.  Its only caller is the coordinator in ``rg3_query_stream``;
the coordinator snapshots and verifies execution_input/v3 before invoking the
private function below.

Warmup occurrences advance only bounded pose visibility state.  They never
produce an output row and no event object is retained by candidate state.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Mapping, Sequence

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import (
    RG3_POLICY,
    recover_rg3_cav,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256


CURRENT_CAV_MODEL_ID = "CURRENT_CAV"
ROUTE_CANDIDATE = "CANDIDATE"
ROUTE_CURRENT_CAV = "CURRENT_CAV"
ROUTE_FRESH_ZOH = "FRESH_ZOH"
ROUTE_SENSOR_FIXED = "SENSOR_FIXED"


class RG3QueryStreamCoreError(ValueError):
    """Verified execution evidence is internally inconsistent for RG3."""


class _WindowState(object):
    """Bounded candidate state; it intentionally has no event-row storage."""

    __slots__ = (
        "baseline_poses",
        "candidate_poses",
        "maximum_candidate_pose_count",
        "pose_cursor",
        "visible_candidate_pose_count",
    )

    def __init__(self) -> None:
        self.baseline_poses = deque(maxlen=2)  # type: Deque[Mapping[str, object]]
        self.candidate_poses = deque(maxlen=3)  # type: Deque[Mapping[str, object]]
        self.maximum_candidate_pose_count = 0
        self.pose_cursor = 0
        self.visible_candidate_pose_count = 0

    def advance(
        self,
        poses: Sequence[Mapping[str, object]],
        event_timestamp_ns: int,
        decision_cycle: int,
    ) -> None:
        while self.pose_cursor < len(poses):
            pose = poses[self.pose_cursor]
            if not (
                pose["commit_cycle"] < decision_cycle
                and pose["timestamp_ns"] <= event_timestamp_ns
            ):
                break
            self.pose_cursor += 1
            self.baseline_poses.append(pose)
            if pose["commit_cycle"] >= 0:
                self.candidate_poses.append(pose)
                self.visible_candidate_pose_count += 1
                self.maximum_candidate_pose_count = max(
                    self.maximum_candidate_pose_count,
                    len(self.candidate_poses),
                )


def _query_row(
    event: Mapping[str, object],
    record: Mapping[str, object],
    state: _WindowState,
) -> Mapping[str, object]:
    edge = record["occurrence_cycle"]
    baseline_ids = tuple(pose["pose_id"] for pose in state.baseline_poses)
    if baseline_ids != tuple(record["occurrence_pose_ids"]):
        raise RG3QueryStreamCoreError(
            "current-CAV occurrence snapshot differs from streaming visibility"
        )

    candidate_attempted = False
    candidate_used = False
    fallback_reason = record["disposition_reason"]
    used_pose_ids = []
    world_ray = None
    model_id = CURRENT_CAV_MODEL_ID
    route = ROUTE_SENSOR_FIXED

    if record["disposition_reason"] == "causal_cav":
        if record["disposition"] != "corrected_world_ray" or len(record["used_pose_ids"]) != 2:
            raise RG3QueryStreamCoreError("current-CAV causal taxonomy differs")
        candidate_attempted = True
        route = ROUTE_CURRENT_CAV
        used_pose_ids = list(record["used_pose_ids"])
        latest_three = tuple(state.candidate_poses)
        if len(latest_three) == 3 and not all(
            pose["value_valid"] and pose["arithmetic_valid"]
            for pose in latest_three
        ):
            fallback_reason = "invalid_rg3_pose_history"
        else:
            decision = recover_rg3_cav(
                tuple(
                    PoseSample(
                        pose["timestamp_ns"],
                        pose["commit_cycle"],
                        tuple(pose["quaternion_xyzw"]),
                    )
                    for pose in latest_three
                ),
                event["timestamp_ns"],
                edge,
            )
            if decision.candidate_used and decision.quaternion_xyzw is not None:
                expected_timestamps = tuple(
                    pose["timestamp_ns"] for pose in latest_three
                )
                expected_commits = tuple(
                    pose["commit_cycle"] for pose in latest_three
                )
                if (
                    decision.used_measurement_timestamps_ns != expected_timestamps
                    or decision.used_commit_cycles != expected_commits
                ):
                    raise RG3QueryStreamCoreError("RG3 pose provenance differs")
                used_pose_ids = [pose["pose_id"] for pose in latest_three]
                candidate_used = True
                fallback_reason = None
                model_id = RG3_POLICY.candidate_id
                route = ROUTE_CANDIDATE
                world_ray = list(
                    rotate_sensor_ray_to_world(
                        decision.quaternion_xyzw,
                        tuple(event["sensor_ray"]),
                    )
                )
            else:
                fallback_reason = decision.reason
    elif record["disposition_reason"] == "fresh_zoh_fallback":
        if record["disposition"] != "corrected_world_ray" or len(record["used_pose_ids"]) != 1:
            raise RG3QueryStreamCoreError("current-CAV fresh-ZOH taxonomy differs")
        route = ROUTE_FRESH_ZOH
        used_pose_ids = list(record["used_pose_ids"])
    elif record["disposition_reason"] in (
        "no_occurrence_pose",
        "invalid_pose",
        "stale_pose",
    ):
        if record["disposition"] != "raw_bypass":
            raise RG3QueryStreamCoreError("current-CAV sensor-fixed taxonomy differs")
        route = ROUTE_SENSOR_FIXED
        used_pose_ids = list(record["used_pose_ids"])
    else:
        raise RG3QueryStreamCoreError("current-CAV fallback taxonomy differs")

    body = {
        "event_id": event["event_id"],
        "event_content_sha256": event["event_content_sha256"],
        "occurrence_cycle": edge - 1,
        "decision_cycle": edge,
        "model_id": model_id,
        "predictor_state_version": state.visible_candidate_pose_count,
        "used_pose_ids": used_pose_ids,
        "candidate_attempted": candidate_attempted,
        "candidate_used": candidate_used,
        "route": route,
        "fallback_reason": fallback_reason,
        "world_ray": world_ray,
    }
    return dict(body, decision_sha256=canonical_sha256(body))


def _run_verified_execution_snapshot(
    execution: Mapping[str, object],
) -> Mapping[str, object]:
    """Consume a coordinator-verified immutable snapshot.

    This is private by construction and accepts neither a verification flag
    nor a caller-supplied authority token.  Public callers must enter through
    ``generate_rg3_query_stream`` in the coordinator module.
    """

    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    result_windows = []
    maximum_candidate_pose_count = 0
    query_count = 0
    for window, trace_window in zip(execution["windows"], trace_windows):
        if window["window_id"] != trace_window["registry"]["window_id"]:
            raise RG3QueryStreamCoreError("execution and trace window order differs")
        events = window["events"]
        poses = window["poses"]
        records = trace_window["simulation"]["records"]
        if len(events) != len(records):
            raise RG3QueryStreamCoreError("execution and trace cardinality differs")
        state = _WindowState()
        query_rows = []
        warmup_count = 0
        for event, record in zip(events, records):
            state.advance(
                poses,
                event["timestamp_ns"],
                record["occurrence_cycle"],
            )
            if not event["is_query"]:
                warmup_count += 1
                continue
            query_rows.append(_query_row(event, record, state))
        maximum_candidate_pose_count = max(
            maximum_candidate_pose_count,
            state.maximum_candidate_pose_count,
        )
        query_count += len(query_rows)
        result_windows.append({
            "window_id": window["window_id"],
            "query_rows": query_rows,
            "query_rows_sha256": canonical_sha256(query_rows),
            "warmup_event_count": warmup_count,
            "query_event_count": len(query_rows),
            "warmup_rows_emitted": 0,
            "retained_candidate_event_rows": 0,
            "maximum_retained_candidate_pose_count": (
                state.maximum_candidate_pose_count
            ),
        })
    return {
        "windows": result_windows,
        "windows_sha256": canonical_sha256(result_windows),
        "query_event_count": query_count,
        "warmup_rows_emitted": 0,
        "retained_candidate_event_rows": 0,
        "maximum_retained_candidate_pose_count": maximum_candidate_pose_count,
    }


__all__ = ()
