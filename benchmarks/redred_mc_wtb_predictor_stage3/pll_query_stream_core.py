"""Candidate-safe bounded-state PLL query streaming core.

The coordinator verifies and snapshots execution_input/v3.  This private core
then retains only one effective state, one pending state, the latest two valid
poses needed by the exact fallback, and rolling state-dependency evidence.
Warmup events never produce rows.  At every edge all events observe the same
pre-edge state; pose transitions are published only after those events and are
effective on the following edge.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
import hashlib
from typing import Deque, Mapping, Optional

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import (
    ForecastState,
    SO3PLLConfig,
    SO3PLLError,
    SO3PLLModel,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


LOCKED_PLL_CONFIG = SO3PLLConfig()
PLL_STREAM_CANDIDATE_ID = LOCKED_PLL_CONFIG.candidate_id
ROUTE_CANDIDATE = "CANDIDATE"
ROUTE_CURRENT_CAV = "CURRENT_CAV"
ROUTE_FRESH_ZOH = "FRESH_ZOH"
ROUTE_SENSOR_FIXED = "SENSOR_FIXED"

_STATE_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_stream_state/v1"
_TRANSITION_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_stream_transition/v1"
_CONFIG_SCHEMA = "redred.mc_wtb_predictor_stage3.so3_pll_config/v2"


class PLLQueryStreamCoreError(ValueError):
    """Verified execution evidence is inconsistent with bounded PLL replay."""


def _config_mapping() -> Mapping[str, object]:
    return {
        "schema": _CONFIG_SCHEMA,
        "candidate_id": PLL_STREAM_CANDIDATE_ID,
        "pll": asdict(LOCKED_PLL_CONFIG),
        "pre_roll_ns": 50_000_000,
        "reset": "new_native_model_at_each_warmup_start",
        "event_edge": "occurrence_equals_decision_minus_one",
        "same_edge_priority": "events_before_pose_commits",
        "candidate_gate": "exact_causal_cav_only",
        "fallback_routes": [
            ROUTE_CURRENT_CAV,
            ROUTE_FRESH_ZOH,
            ROUTE_SENSOR_FIXED,
        ],
    }


PLL_STREAM_CONFIG_BYTES = canonical_json_bytes(_config_mapping())
PLL_STREAM_CONFIG_SHA256 = hashlib.sha256(PLL_STREAM_CONFIG_BYTES).hexdigest()


@dataclass(frozen=True)
class _PublishedState:
    native: ForecastState
    state: Mapping[str, object]
    state_sha256: str
    dependency_pose_count: int
    dependency_pose_chain_sha256: str


class _BoundedPLL(object):
    """Native PLL semantics with a constant-size retained-state projection."""

    __slots__ = (
        "dependency_pose_chain_sha256",
        "dependency_pose_count",
        "effective",
        "latest_pose_rows",
        "maximum_pose_count",
        "maximum_state_count",
        "model",
        "next_state_version",
        "pending",
    )

    def __init__(self) -> None:
        self.model = SO3PLLModel(LOCKED_PLL_CONFIG)
        self.effective = None  # type: Optional[_PublishedState]
        self.pending = None  # type: Optional[_PublishedState]
        self.latest_pose_rows = deque(maxlen=2)  # type: Deque[Mapping[str, object]]
        self.dependency_pose_count = 0
        self.dependency_pose_chain_sha256 = canonical_sha256([])
        self.next_state_version = 0
        self.maximum_pose_count = 0
        self.maximum_state_count = 0

    def _sync_native_storage(self) -> None:
        states = []
        if self.effective is not None:
            states.append(self.effective.native)
        if self.pending is not None:
            states.append(self.pending.native)
        self.model._states = states  # type: ignore[attr-defined]
        self.model._receipts = []  # type: ignore[attr-defined]
        self.maximum_state_count = max(self.maximum_state_count, len(states))

    def promote(self, cycle: int) -> None:
        if self.pending is not None and self.pending.native.effective_cycle <= cycle:
            self.effective = self.pending
            self.pending = None
        self._sync_native_storage()

    def pointer(self) -> Mapping[str, object]:
        if self.effective is None:
            return {
                "predictor_state_version": 0,
                "predictor_state_is_reset": True,
                "state_sha256": canonical_sha256({"schema": _STATE_SCHEMA, "reset": True}),
                "state_effective_cycle": 0,
                "state_dependency_pose_count": 0,
                "state_dependency_pose_chain_sha256": canonical_sha256([]),
                "state_anchor_pose_id": None,
            }
        return {
            "predictor_state_version": self.effective.native.state_version,
            "predictor_state_is_reset": False,
            "state_sha256": self.effective.state_sha256,
            "state_effective_cycle": self.effective.native.effective_cycle,
            "state_dependency_pose_count": self.effective.dependency_pose_count,
            "state_dependency_pose_chain_sha256": (
                self.effective.dependency_pose_chain_sha256
            ),
            "state_anchor_pose_id": self.effective.native.anchor_pose_id,
        }

    def boundary(self) -> Mapping[str, object]:
        body = {
            "effective_state": None if self.effective is None else self.effective.state,
            "effective_state_sha256": (
                None if self.effective is None else self.effective.state_sha256
            ),
            "pending_state": None if self.pending is None else self.pending.state,
            "pending_state_sha256": (
                None if self.pending is None else self.pending.state_sha256
            ),
            "latest_fallback_pose_ids": [
                pose["pose_id"] for pose in self.latest_pose_rows
            ],
            "dependency_pose_count": self.dependency_pose_count,
            "dependency_pose_chain_sha256": self.dependency_pose_chain_sha256,
        }
        return dict(body, boundary_sha256=canonical_sha256(body))

    def predict(self, timestamp_ns: int, cycle: int):
        if self.pending is not None:
            raise PLLQueryStreamCoreError("pending PLL state was not edge-promoted")
        self._sync_native_storage()
        try:
            return self.model.predict(timestamp_ns, cycle)
        except SO3PLLError as exc:
            raise PLLQueryStreamCoreError("native PLL query prediction failed") from exc

    def commit(self, pose: Mapping[str, object]) -> Mapping[str, object]:
        if self.pending is not None:
            raise PLLQueryStreamCoreError("multiple PLL publications share one edge")
        self._sync_native_storage()
        source = self.effective
        parent_dependency_pose_count = self.dependency_pose_count
        parent_dependency_chain_sha256 = self.dependency_pose_chain_sha256
        try:
            receipt = self.model.commit_pose(
                pose["pose_id"],
                pose["timestamp_ns"],
                pose["commit_cycle"],
                tuple(pose["quaternion_xyzw"]),
                valid=bool(pose["value_valid"] and pose["arithmetic_valid"]),
            )
        except SO3PLLError as exc:
            raise PLLQueryStreamCoreError("native PLL pose transition failed") from exc

        published = None  # type: Optional[_PublishedState]
        normalized_receipt = receipt
        if receipt.accepted:
            native = self.model.current_state
            if native is None or receipt.published_state_version is None:
                raise PLLQueryStreamCoreError("accepted PLL transition has no state")
            native = replace(native, state_version=self.next_state_version)
            normalized_receipt = replace(
                receipt,
                source_state_version=(
                    None if source is None else source.native.state_version
                ),
                published_state_version=self.next_state_version,
            )
            self.next_state_version += 1
            dependency_body = {
                "parent_sha256": self.dependency_pose_chain_sha256,
                "pose_id": pose["pose_id"],
                "pose_sha256": pose["pose_sha256"],
                "state_version": native.state_version,
            }
            self.dependency_pose_chain_sha256 = canonical_sha256(dependency_body)
            self.dependency_pose_count += 1
            state_body = {
                "schema": _STATE_SCHEMA,
                "candidate_id": PLL_STREAM_CANDIDATE_ID,
                "configuration_sha256": PLL_STREAM_CONFIG_SHA256,
                "parent_state_sha256": (
                    None if source is None else source.state_sha256
                ),
                "native_state": asdict(native),
                "dependency_pose_count": self.dependency_pose_count,
                "dependency_pose_chain_sha256": self.dependency_pose_chain_sha256,
            }
            state_sha = canonical_sha256(state_body)
            published = _PublishedState(
                native,
                dict(state_body, state_sha256=state_sha),
                state_sha,
                self.dependency_pose_count,
                self.dependency_pose_chain_sha256,
            )
            self.pending = published
            self.latest_pose_rows.append(pose)
            self.maximum_pose_count = max(
                self.maximum_pose_count, len(self.latest_pose_rows)
            )

        # The native object may momentarily have appended a state/receipt/pose;
        # restore the constant-size projection immediately after extracting it.
        self.model._poses = self.model._poses[-2:]  # type: ignore[attr-defined]
        self.model._pose_ids = self.model._pose_ids[-2:]  # type: ignore[attr-defined]
        self.model._receipts = []  # type: ignore[attr-defined]
        self._sync_native_storage()
        transition = {
            "schema": _TRANSITION_SCHEMA,
            "pose_id": pose["pose_id"],
            "pose_sha256": pose["pose_sha256"],
            "commit_cycle": pose["commit_cycle"],
            "accepted": normalized_receipt.accepted,
            "source_state_version": normalized_receipt.source_state_version,
            "published_state_version": normalized_receipt.published_state_version,
            "effective_cycle": normalized_receipt.effective_cycle,
            "publication_cycle": normalized_receipt.effective_cycle,
            "transition_reason": (
                normalized_receipt.fault_reason or normalized_receipt.update_kind
            ),
            "published_state": None if published is None else published.state,
            "published_state_sha256": (
                None if published is None else published.state_sha256
            ),
            "parent_dependency_pose_count": parent_dependency_pose_count,
            "parent_dependency_chain_endpoint_sha256": (
                parent_dependency_chain_sha256
            ),
            "dependency_pose_count": self.dependency_pose_count,
            "dependency_chain_endpoint_sha256": (
                self.dependency_pose_chain_sha256
            ),
            "native_receipt_sha256": canonical_sha256(asdict(normalized_receipt)),
        }
        return dict(transition, transition_sha256=canonical_sha256(transition))


def _baseline_route(record: Mapping[str, object]) -> str:
    disposition = record["disposition"]
    reason = record["disposition_reason"]
    if disposition == "corrected_world_ray" and reason == "causal_cav":
        return ROUTE_CURRENT_CAV
    if disposition == "corrected_world_ray" and reason == "fresh_zoh_fallback":
        return ROUTE_FRESH_ZOH
    if disposition == "raw_bypass" and reason in (
        "no_occurrence_pose",
        "invalid_pose",
        "stale_pose",
        "fifo_full_forced_bypass",
    ):
        return ROUTE_SENSOR_FIXED
    raise PLLQueryStreamCoreError("current-CAV route taxonomy differs")


def _query_row(
    event: Mapping[str, object],
    record: Mapping[str, object],
    runtime: _BoundedPLL,
) -> Mapping[str, object]:
    cycle = record["occurrence_cycle"]
    if (
        record["event_id"] != event["event_id"]
        or record["event_timestamp_ns"] != event["timestamp_ns"]
    ):
        raise PLLQueryStreamCoreError("current-CAV event identity differs")
    route = _baseline_route(record)
    attempted = route == ROUTE_CURRENT_CAV
    native = runtime.predict(event["timestamp_ns"], cycle) if attempted else None
    pointer = runtime.pointer()
    candidate_used = False
    candidate_failure_reason = None
    candidate_quaternion = None
    world_ray = None
    baseline_fallback_used_pose_ids = list(record["used_pose_ids"])
    candidate_direct_anchor_pose_id = None
    fallback_reason = record["disposition_reason"]
    model_id = "CURRENT_CAV"
    if native is not None and native.candidate_used:
        if native.quaternion_xyzw is None or native.anchor_pose_id is None:
            raise PLLQueryStreamCoreError("native PLL candidate lacks geometry")
        if pointer["state_anchor_pose_id"] != native.anchor_pose_id:
            raise PLLQueryStreamCoreError("native PLL anchor differs from effective state")
        if native.state_version != pointer["predictor_state_version"]:
            raise PLLQueryStreamCoreError("native PLL version differs from effective state")
        try:
            world = rotate_sensor_ray_to_world(
                native.quaternion_xyzw, tuple(event["sensor_ray"])
            )
        except GeometryError as exc:
            raise PLLQueryStreamCoreError("PLL world-ray projection failed") from exc
        candidate_used = True
        route = ROUTE_CANDIDATE
        candidate_quaternion = list(native.quaternion_xyzw)
        world_ray = list(world)
        candidate_direct_anchor_pose_id = native.anchor_pose_id
        fallback_reason = None
        model_id = PLL_STREAM_CANDIDATE_ID
    elif native is not None:
        candidate_failure_reason = native.reason.split(":", 1)[0]

    body = {
        "event_id": event["event_id"],
        "event_content_sha256": event["event_content_sha256"],
        "occurrence_cycle": cycle - 1,
        "decision_cycle": cycle,
        "model_id": model_id,
        "configuration_sha256": PLL_STREAM_CONFIG_SHA256,
        **pointer,
        # A bounded state cannot honestly reproduce pll_output's recursively
        # expanded state_dependency_pose_ids array.  Keep the exact baseline
        # fallback citation separate and represent candidate provenance by its
        # direct anchor plus the dependency-chain endpoint/count.
        "baseline_fallback_used_pose_ids": baseline_fallback_used_pose_ids,
        "candidate_direct_anchor_pose_id": candidate_direct_anchor_pose_id,
        "candidate_dependency_pose_count": pointer[
            "state_dependency_pose_count"
        ],
        "candidate_dependency_chain_endpoint_sha256": pointer[
            "state_dependency_pose_chain_sha256"
        ],
        "candidate_attempted": attempted,
        "candidate_used": candidate_used,
        "candidate_failure_reason": candidate_failure_reason,
        "route": route,
        "fallback_reason": fallback_reason,
        "candidate_quaternion_xyzw": candidate_quaternion,
        "world_ray": world_ray,
    }
    return dict(body, decision_sha256=canonical_sha256(body))


def _run_verified_execution_snapshot(
    execution: Mapping[str, object],
) -> Mapping[str, object]:
    """Replay a coordinator-verified snapshot with bounded candidate state."""

    trace_windows = execution["score_free_current_cav_trace"]["windows"]
    result_windows = []
    total_queries = 0
    total_query_transitions = 0
    maximum_pose_count = 0
    maximum_state_count = 0
    for window, trace_window in zip(execution["windows"], trace_windows):
        if window["window_id"] != trace_window["registry"]["window_id"]:
            raise PLLQueryStreamCoreError("execution and trace window order differs")
        events = window["events"]
        records = trace_window["simulation"]["records"]
        poses = window["poses"]
        if len(events) != len(records) or not events:
            raise PLLQueryStreamCoreError("execution and trace cardinality differs")
        active_pose_cycles = [
            pose["commit_cycle"] for pose in poses if pose["commit_cycle"] >= 0
        ]
        if len(set(active_pose_cycles)) != len(active_pose_cycles):
            raise PLLQueryStreamCoreError(
                "PLL post-reset pose commit cycles must be unique"
            )
        runtime = _BoundedPLL()
        query_rows = []
        query_transitions = []
        query_boundary = None
        warmup_count = 0
        event_index = 0
        pose_index = 0
        while pose_index < len(poses) and poses[pose_index]["commit_cycle"] < 0:
            pose_index += 1
        last_event_cycle = records[-1]["occurrence_cycle"]
        while event_index < len(events) or (
            pose_index < len(poses)
            and poses[pose_index]["commit_cycle"] <= last_event_cycle
        ):
            event_cycle = (
                records[event_index]["occurrence_cycle"]
                if event_index < len(events) else None
            )
            pose_cycle = (
                poses[pose_index]["commit_cycle"]
                if pose_index < len(poses)
                and poses[pose_index]["commit_cycle"] <= last_event_cycle
                else None
            )
            cycle = min(value for value in (event_cycle, pose_cycle) if value is not None)
            runtime.promote(cycle)
            # Exact same-edge rule: consume every event from the immutable
            # pre-edge snapshot before publishing any pose transition.
            while event_index < len(events) and records[event_index]["occurrence_cycle"] == cycle:
                event = events[event_index]
                record = records[event_index]
                if event["is_query"]:
                    if query_boundary is None:
                        query_boundary = runtime.boundary()
                    query_rows.append(_query_row(event, record, runtime))
                else:
                    warmup_count += 1
                    if record["event_id"] != event["event_id"]:
                        raise PLLQueryStreamCoreError("warmup event identity differs")
                event_index += 1
            while (
                pose_index < len(poses)
                and poses[pose_index]["commit_cycle"] == cycle
            ):
                transition = runtime.commit(poses[pose_index])
                if query_boundary is not None:
                    query_transitions.append(transition)
                pose_index += 1
        if query_boundary is None or not query_rows:
            raise PLLQueryStreamCoreError("query phase is empty")
        total_queries += len(query_rows)
        total_query_transitions += len(query_transitions)
        maximum_pose_count = max(maximum_pose_count, runtime.maximum_pose_count)
        maximum_state_count = max(maximum_state_count, runtime.maximum_state_count)
        result_windows.append({
            "window_id": window["window_id"],
            "first_query_state_boundary": query_boundary,
            "query_rows": query_rows,
            "query_rows_sha256": canonical_sha256(query_rows),
            "query_transitions": query_transitions,
            "query_transitions_sha256": canonical_sha256(query_transitions),
            "warmup_event_count": warmup_count,
            "query_event_count": len(query_rows),
            "query_transition_count": len(query_transitions),
            "warmup_rows_emitted": 0,
            "retained_candidate_event_rows": 0,
            "maximum_retained_fallback_pose_count": runtime.maximum_pose_count,
            "maximum_retained_effective_pending_state_count": runtime.maximum_state_count,
        })
    return {
        "windows": result_windows,
        "windows_sha256": canonical_sha256(result_windows),
        "query_event_count": total_queries,
        "query_transition_count": total_query_transitions,
        "warmup_rows_emitted": 0,
        "retained_candidate_event_rows": 0,
        "maximum_retained_fallback_pose_count": maximum_pose_count,
        "maximum_retained_effective_pending_state_count": maximum_state_count,
    }


__all__ = ()
