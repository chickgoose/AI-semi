"""Score-free locked RG3 candidate-output generator for screen108.

The generator accepts only the neutral registry, neutral event/pose streams,
and the adapter aggregate digest.  It independently replays the frozen
CAUSAL_CAV cycle model to recover each event's occurrence-decision edge, then
invokes the frozen model-only RG3 policy.  It does not import the NEW108
adapter, evaluator, screen runner, selector, labels, losses, or filters.

Every input window starts exactly 50 ms before its query interval.  All state
is reconstructed inside that window, every neutral event produces exactly one
append-only decision, and no state is shared between windows.  A successful
RG3 receipt records all three pose IDs actually consumed.  Otherwise the
receipt requests the screen runner's exact frozen current-CAV fallback by
supplying ``world_ray=None`` and ``model_id=CURRENT_CAV``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from typing import Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    PoseSample,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.rg3 import (
    RG3_POLICY,
    recover_rg3_cav,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    PosePacket,
    PoseSource,
    RAW_INGRESS_LANES,
    run_cycle_model,
    timestamp_to_cycle,
)


CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_output/v1"
PREROLL_NS = 50_000_000
CURRENT_CAV_MODEL_ID = "CURRENT_CAV"
# The model-only policy ID uses path separators.  screen108/v1 identifiers are
# deliberately restricted to [A-Za-z0-9_.-], so the output namespace uses the
# one-to-one dot encoding and binds the original policy ID in RG3_CONFIG.
RG3_OUTPUT_CANDIDATE_ID = RG3_POLICY.candidate_id.replace("/", ".")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRY_FIELDS = frozenset((
    "window_id",
    "warmup_start_ns_inclusive",
    "query_start_ns_inclusive",
    "query_end_ns_exclusive",
))
_EVENT_FIELDS = frozenset((
    "event_id",
    "timestamp_ns",
    "polarity",
    "is_query",
    "sensor_ray",
    "causal_pose_source_index",
    "event_content_sha256",
    "transform_guard_valid",
))
_POSE_FIELDS = frozenset((
    "pose_id",
    "timestamp_ns",
    "commit_cycle",
    "quaternion_xyzw",
    "pose_sha256",
    "value_valid",
    "arithmetic_valid",
))


class RG3OutputError(ValueError):
    """A neutral input, causal replay, or output-sealing invariant failed."""


@dataclass(frozen=True)
class _Window:
    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "window_id": self.window_id,
            "warmup_start_ns_inclusive": self.warmup_start_ns_inclusive,
            "query_start_ns_inclusive": self.query_start_ns_inclusive,
            "query_end_ns_exclusive": self.query_end_ns_exclusive,
        }


@dataclass(frozen=True)
class _Event:
    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: Tuple[float, float, float]
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "event_id": self.event_id,
            "timestamp_ns": self.timestamp_ns,
            "polarity": self.polarity,
            "is_query": self.is_query,
            "sensor_ray": list(self.sensor_ray),
            "causal_pose_source_index": self.causal_pose_source_index,
            "event_content_sha256": self.event_content_sha256,
            "transform_guard_valid": self.transform_guard_valid,
        }


@dataclass(frozen=True)
class _Pose:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Tuple[float, float, float, float]
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool

    def to_mapping(self) -> Mapping[str, object]:
        return {
            "pose_id": self.pose_id,
            "timestamp_ns": self.timestamp_ns,
            "commit_cycle": self.commit_cycle,
            "quaternion_xyzw": list(self.quaternion_xyzw),
            "pose_sha256": self.pose_sha256,
            "value_valid": self.value_valid,
            "arithmetic_valid": self.arithmetic_valid,
        }


RG3_MODEL_PATH = Path(__file__).with_name("rg3.py")
RG3_MODEL_SHA256 = hashlib.sha256(RG3_MODEL_PATH.read_bytes()).hexdigest()
RG3_CONFIG = {
    "schema": "redred.mc_wtb_predictor_stage3.rg3_config/v1",
    "candidate_id": RG3_OUTPUT_CANDIDATE_ID,
    "model_policy_id": RG3_POLICY.candidate_id,
    "model_implementation_sha256": RG3_MODEL_SHA256,
    "maximum_pose_interval_ns": RG3_POLICY.maximum_pose_interval_ns,
    "near_pi_margin_rad": RG3_POLICY.near_pi_margin_rad,
    "maximum_rate_change_ratio": RG3_POLICY.maximum_rate_change_ratio,
    "minimum_direction_cosine": RG3_POLICY.minimum_direction_cosine,
    "maximum_acceleration_contribution_ratio": (
        RG3_POLICY.maximum_acceleration_contribution_ratio
    ),
    "decision_edge": "cycle_model_occurrence_cycle",
    "pose_visibility": "commit_cycle<decision_cycle_and_timestamp<=event_timestamp",
    "preroll_ns": PREROLL_NS,
    "fallback": "exact_current_cav",
}
RG3_CONFIG_BYTES = canonical_json_bytes(RG3_CONFIG)
RG3_CONFIG_SHA256 = hashlib.sha256(RG3_CONFIG_BYTES).hexdigest()
RG3_EXECUTABLE_PATH = Path(__file__)
RG3_EXECUTABLE_SHA256 = hashlib.sha256(RG3_EXECUTABLE_PATH.read_bytes()).hexdigest()


def _exact_object(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    try:
        body = vars(value)
    except TypeError as exc:
        raise RG3OutputError("%s must be an exact neutral dataclass" % where) from exc
    if frozenset(body) != fields:
        raise RG3OutputError("%s field schema differs" % where)
    return body


def _integer(value: object, where: str, nonnegative: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RG3OutputError("%s must be an integer" % where)
    if nonnegative and value < 0:
        raise RG3OutputError("%s must be nonnegative" % where)
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise RG3OutputError("%s must be lowercase SHA-256" % where)
    return value


def _unit_tuple(value: object, length: int, where: str) -> Tuple[float, ...]:
    if type(value) is not tuple or len(value) != length:  # type: ignore[arg-type]
        raise RG3OutputError("%s must be an immutable %d-tuple" % (where, length))
    converted = []
    for component in value:  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise RG3OutputError("%s components must be finite numbers" % where)
        number = float(component)
        if not math.isfinite(number):
            raise RG3OutputError("%s components must be finite numbers" % where)
        converted.append(number)
    norm = math.sqrt(math.fsum(number * number for number in converted))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        raise RG3OutputError("%s must have unit norm" % where)
    return tuple(converted)


def _event_digest(event: _Event) -> str:
    return canonical_sha256({
        "event_id": event.event_id,
        "timestamp_ns": event.timestamp_ns,
        "polarity": event.polarity,
        "is_query": event.is_query,
        "sensor_ray": list(event.sensor_ray),
        "causal_pose_source_index": event.causal_pose_source_index,
        "transform_guard_valid": event.transform_guard_valid,
    })


def _pose_digest(pose: _Pose) -> str:
    return canonical_sha256({
        "pose_id": pose.pose_id,
        "timestamp_ns": pose.timestamp_ns,
        "quaternion_xyzw": list(pose.quaternion_xyzw),
    })


def _snapshot_window(value: object) -> _Window:
    body = _exact_object(value, _REGISTRY_FIELDS, "neutral registry window")
    window_id = body["window_id"]
    if type(window_id) is not str or not window_id:
        raise RG3OutputError("window_id must be nonempty text")
    start = _integer(body["warmup_start_ns_inclusive"], "warmup start")
    query = _integer(body["query_start_ns_inclusive"], "query start")
    end = _integer(body["query_end_ns_exclusive"], "query end")
    if query - start != PREROLL_NS or query >= end:
        raise RG3OutputError("window must begin at the exact 50 ms pre-roll")
    return _Window(window_id, start, query, end)


def _snapshot_event(value: object) -> _Event:
    body = _exact_object(value, _EVENT_FIELDS, "neutral event")
    polarity = body["polarity"]
    if isinstance(polarity, bool) or polarity not in (0, 1):
        raise RG3OutputError("event polarity must be integer zero or one")
    if type(body["is_query"]) is not bool or type(body["transform_guard_valid"]) is not bool:
        raise RG3OutputError("event flags must be exact bools")
    event = _Event(
        _integer(body["event_id"], "event ID"),
        _integer(body["timestamp_ns"], "event timestamp"),
        polarity,  # type: ignore[arg-type]
        body["is_query"],  # type: ignore[arg-type]
        _unit_tuple(body["sensor_ray"], 3, "sensor ray"),  # type: ignore[arg-type]
        _integer(body["causal_pose_source_index"], "causal pose source index"),
        _sha256(body["event_content_sha256"], "event content digest"),
        body["transform_guard_valid"],  # type: ignore[arg-type]
    )
    if event.event_content_sha256 != _event_digest(event):
        raise RG3OutputError("event content digest differs")
    return event


def _snapshot_pose(value: object) -> _Pose:
    body = _exact_object(value, _POSE_FIELDS, "neutral pose")
    if type(body["value_valid"]) is not bool or type(body["arithmetic_valid"]) is not bool:
        raise RG3OutputError("pose validity flags must be exact bools")
    pose = _Pose(
        _integer(body["pose_id"], "pose ID"),
        _integer(body["timestamp_ns"], "pose timestamp"),
        _integer(body["commit_cycle"], "pose commit cycle", nonnegative=False),
        _unit_tuple(body["quaternion_xyzw"], 4, "pose quaternion"),  # type: ignore[arg-type]
        _sha256(body["pose_sha256"], "pose content digest"),
        body["value_valid"],  # type: ignore[arg-type]
        body["arithmetic_valid"],  # type: ignore[arg-type]
    )
    if pose.pose_sha256 != _pose_digest(pose):
        raise RG3OutputError("pose content digest differs")
    return pose


def _snapshot_inputs(
    registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
) -> Tuple[
    Tuple[_Window, ...],
    Mapping[str, Tuple[_Event, ...]],
    Mapping[str, Tuple[_Pose, ...]],
]:
    if type(registry) is not tuple or not registry:
        raise RG3OutputError("neutral registry must be a nonempty tuple")
    windows = tuple(_snapshot_window(value) for value in registry)
    identifiers = tuple(window.window_id for window in windows)
    if len(set(identifiers)) != len(identifiers):
        raise RG3OutputError("neutral registry window IDs are duplicated")
    for left, right in zip(windows, windows[1:]):
        if left.query_end_ns_exclusive > right.warmup_start_ns_inclusive:
            raise RG3OutputError("neutral registry windows overlap or move backwards")
    try:
        event_source = dict(event_streams)
        pose_source = dict(pose_streams)
    except (TypeError, ValueError) as exc:
        raise RG3OutputError("neutral streams must be mappings") from exc
    if set(event_source) != set(identifiers) or set(pose_source) != set(identifiers):
        raise RG3OutputError("neutral stream window identities differ from registry")

    events = {}  # type: Dict[str, Tuple[_Event, ...]]
    poses = {}  # type: Dict[str, Tuple[_Pose, ...]]
    for window in windows:
        event_rows = event_source[window.window_id]
        pose_rows = pose_source[window.window_id]
        if type(event_rows) is not tuple or not event_rows:
            raise RG3OutputError("neutral event stream must be a nonempty tuple")
        if type(pose_rows) is not tuple or not pose_rows:
            raise RG3OutputError("neutral pose stream must be a nonempty tuple")
        event_values = tuple(_snapshot_event(value) for value in event_rows)
        pose_values = tuple(_snapshot_pose(value) for value in pose_rows)
        if any(
            right.event_id <= left.event_id
            for left, right in zip(event_values, event_values[1:])
        ):
            raise RG3OutputError("event IDs must be strictly increasing")
        if any(
            right.timestamp_ns < left.timestamp_ns
            for left, right in zip(event_values, event_values[1:])
        ):
            raise RG3OutputError("event timestamps must be nondecreasing")
        if any(right.pose_id < left.pose_id for left, right in zip(pose_values, pose_values[1:])):
            raise RG3OutputError("pose IDs must be increasing")
        if any(
            right.timestamp_ns <= left.timestamp_ns
            for left, right in zip(pose_values, pose_values[1:])
        ):
            raise RG3OutputError("pose timestamps must be strictly increasing")
        for event in event_values:
            if not (
                window.warmup_start_ns_inclusive
                <= event.timestamp_ns
                < window.query_end_ns_exclusive
            ):
                raise RG3OutputError("event lies outside its neutral window")
            expected_query = window.query_start_ns_inclusive <= event.timestamp_ns
            if event.is_query != expected_query:
                raise RG3OutputError("event query flag differs from neutral bounds")
        if not any(event.is_query for event in event_values):
            raise RG3OutputError("neutral window has no query events")
        counts = {}  # type: Dict[int, int]
        for event in event_values:
            cycle = timestamp_to_cycle(event.timestamp_ns, window.warmup_start_ns_inclusive)
            counts[cycle] = counts.get(cycle, 0) + 1
        if max(counts.values()) > RAW_INGRESS_LANES:
            raise RG3OutputError("more than six events share one occurrence cycle")
        events[window.window_id] = event_values
        poses[window.window_id] = pose_values
    return windows, events, poses


def _neutral_input_sha256(
    windows: Sequence[_Window],
    events: Mapping[str, Sequence[_Event]],
    poses: Mapping[str, Sequence[_Pose]],
) -> str:
    return canonical_sha256({
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": [window.to_mapping() for window in windows],
        "windows": [
            {
                "window_id": window.window_id,
                "events": [event.to_mapping() for event in events[window.window_id]],
                "poses": [pose.to_mapping() for pose in poses[window.window_id]],
            }
            for window in windows
        ],
    })


def _seal(
    windows: Sequence[Mapping[str, object]],
    adapter_digest: str,
    neutral_digest: str,
) -> Mapping[str, object]:
    sealed_windows = []
    for supplied in windows:
        event_rows = supplied["events"]
        events = []
        for event in event_rows:  # type: ignore[union-attr]
            body = dict(event)
            events.append(dict(body, decision_sha256=canonical_sha256(body)))
        sealed_windows.append({
            "window_id": supplied["window_id"],
            "events": events,
            "events_sha256": canonical_sha256(events),
        })
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": RG3_OUTPUT_CANDIDATE_ID,
        "adapter_aggregate_sha256": adapter_digest,
        "neutral_input_sha256": neutral_digest,
        "candidate_executable_sha256": RG3_EXECUTABLE_SHA256,
        "candidate_config_sha256": RG3_CONFIG_SHA256,
        "windows": sealed_windows,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def generate_locked_rg3_output(
    neutral_registry: Sequence[object],
    event_streams: Mapping[str, Sequence[object]],
    pose_streams: Mapping[str, Sequence[object]],
    adapter_aggregate_sha256: str,
) -> Mapping[str, object]:
    """Replay every neutral event and return a sealed screen108-v1 receipt.

    The wrapper-facing arguments deliberately exclude the adapter bundle so
    selector labels and selector/source metadata are not reachable.  Callers
    pass only the already authenticated adapter aggregate digest alongside the
    neutral projection.
    """

    adapter_digest = _sha256(adapter_aggregate_sha256, "adapter aggregate digest")
    windows, events_by_window, poses_by_window = _snapshot_inputs(
        neutral_registry, event_streams, pose_streams
    )
    output_windows = []
    for window in windows:
        event_values = events_by_window[window.window_id]
        pose_values = poses_by_window[window.window_id]
        pose_packets = tuple(PosePacket(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            PoseSource.DATASET,
            pose.pose_sha256,
            pose.value_valid,
            pose.arithmetic_valid,
        ) for pose in pose_values)
        simulation = run_cycle_model(
            window_id=window.window_id,
            window_start_ns=window.warmup_start_ns_inclusive,
            arm=Arm.CAUSAL_CAV,
            events=tuple(Event(
                event.event_id,
                event.timestamp_ns,
                event.transform_guard_valid,
                event.causal_pose_source_index,
            ) for event in event_values),
            poses=pose_packets,
        )
        if simulation.synthetic_test_mode or not simulation.all_event_pose_indices_verified:
            raise RG3OutputError("cycle replay did not authenticate every event pose index")
        if len(simulation.records) != len(event_values):
            raise RG3OutputError("cycle replay changed event cardinality")

        rows = []
        for event, record in zip(event_values, simulation.records):
            edge = record.occurrence_cycle
            visible = tuple(
                pose for pose in pose_values
                if pose.commit_cycle < edge and pose.timestamp_ns <= event.timestamp_ns
            )
            if tuple(pose.pose_id for pose in visible[-2:]) != record.occurrence_pose_ids:
                raise RG3OutputError("cycle occurrence snapshot differs from strict visibility")
            state_version = len(visible)
            candidate_used = False
            fallback_reason = "baseline_%s" % record.disposition_reason
            used_pose_ids = list(record.used_pose_ids)
            world_ray = None
            model_id = CURRENT_CAV_MODEL_ID

            # The cycle-model disposition is the authoritative current-CAV
            # validity gate, including transform and pose-validity guards.
            if record.disposition_reason == "causal_cav":
                latest_three = visible[-3:]
                if len(latest_three) == 3 and not all(
                    pose.value_valid and pose.arithmetic_valid for pose in latest_three
                ):
                    fallback_reason = "invalid_rg3_pose_history"
                else:
                    decision = recover_rg3_cav(
                        tuple(PoseSample(
                            pose.timestamp_ns,
                            pose.commit_cycle,
                            pose.quaternion_xyzw,
                        ) for pose in latest_three),
                        event.timestamp_ns,
                        edge,
                    )
                    if decision.candidate_used and decision.quaternion_xyzw is not None:
                        expected_timestamps = tuple(pose.timestamp_ns for pose in latest_three)
                        expected_commits = tuple(pose.commit_cycle for pose in latest_three)
                        if (
                            decision.used_measurement_timestamps_ns != expected_timestamps
                            or decision.used_commit_cycles != expected_commits
                        ):
                            raise RG3OutputError("RG3 used-pose provenance differs")
                        # Honest exact provenance: all three consumed pose IDs.
                        used_pose_ids = [pose.pose_id for pose in latest_three]
                        candidate_used = True
                        fallback_reason = None
                        model_id = RG3_OUTPUT_CANDIDATE_ID
                        world_ray = list(rotate_sensor_ray_to_world(
                            decision.quaternion_xyzw, event.sensor_ray
                        ))
                    else:
                        fallback_reason = decision.reason

            rows.append({
                "event_id": event.event_id,
                "event_content_sha256": event.event_content_sha256,
                "decision_cycle": edge,
                "model_id": model_id,
                "predictor_state_version": state_version,
                "used_pose_ids": used_pose_ids,
                "candidate_used": candidate_used,
                "fallback_reason": fallback_reason,
                "world_ray": world_ray,
            })
        output_windows.append({"window_id": window.window_id, "events": rows})

    return _seal(
        output_windows,
        adapter_digest,
        _neutral_input_sha256(windows, events_by_window, poses_by_window),
    )


__all__ = (
    "CANDIDATE_OUTPUT_SCHEMA",
    "CURRENT_CAV_MODEL_ID",
    "PREROLL_NS",
    "RG3_CONFIG",
    "RG3_CONFIG_BYTES",
    "RG3_CONFIG_SHA256",
    "RG3_EXECUTABLE_PATH",
    "RG3_EXECUTABLE_SHA256",
    "RG3_MODEL_PATH",
    "RG3_MODEL_SHA256",
    "RG3_OUTPUT_CANDIDATE_ID",
    "RG3OutputError",
    "generate_locked_rg3_output",
)
