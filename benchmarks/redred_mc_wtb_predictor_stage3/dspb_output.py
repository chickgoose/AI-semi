"""Locked, score-blind DSPB output generation for the consumed 108 screen.

The generator accepts only the neutral registry, neutral event/pose streams,
and the already authenticated adapter aggregate digest.  It never accepts
selector sidecars, quality values, outcome data, or routing policy.  Each
window creates a fresh :class:`DSPBModel` at the start of its exact 50 ms
pre-roll and replays score-free source records in cycle order.

On a shared cycle every event cluster is decided before the pose commit, so a
same-edge pose cannot affect that event.  Equal-timestamp members are passed
to one atomic DSPB call.  Candidate geometry is emitted only where frozen
current CAV is valid; every other row delegates to the exact baseline without
supplying replacement geometry.  The returned envelope is the exact
``screen108.candidate_output/v1`` schema and is sealed by the locked screen
sealer.
"""

from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Dict, List, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import dspb as dspb_module
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import (
    DSPBConfig,
    DSPBError,
    DSPBModel,
    EventRecord,
    SuppliedPose,
)
from benchmarks.redred_mc_wtb_predictor_stage3.screen108 import (
    CANDIDATE_OUTPUT_SCHEMA,
    seal_candidate_output,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    Arm,
    Event,
    PosePacket,
    PoseSource,
    run_cycle_model,
)


PREROLL_NS = 50_000_000
CURRENT_CAV_MODEL_ID = "CURRENT_CAV"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRY_FIELDS = tuple(field.name for field in fields(NeutralRegistryWindow))
_EVENT_FIELDS = tuple(field.name for field in fields(NeutralEventInput))
_POSE_FIELDS = tuple(field.name for field in fields(NeutralPoseInput))
_EventCluster = Tuple[
    Tuple[int, ...], Tuple[NeutralEventInput, ...], Tuple[object, ...]
]


class DSPBOutputError(ValueError):
    """Neutral input, replay, or output sealing failed closed."""


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise DSPBOutputError("%s must be lowercase SHA-256" % where)
    return value


def _exact_dataclass(
    value: object, expected_type: type, expected_fields: Tuple[str, ...], where: str
) -> None:
    if type(value) is not expected_type:
        raise DSPBOutputError("%s must have exact %s type" % (
            where, expected_type.__name__
        ))
    if tuple(vars(value)) != expected_fields:
        raise DSPBOutputError("%s dataclass field schema differs" % where)


def _checked_registry(value: object) -> NeutralRegistryWindow:
    _exact_dataclass(value, NeutralRegistryWindow, _REGISTRY_FIELDS, "registry")
    row = value  # type: ignore[assignment]
    try:
        rebuilt = NeutralRegistryWindow(
            row.window_id,
            row.warmup_start_ns_inclusive,
            row.query_start_ns_inclusive,
            row.query_end_ns_exclusive,
        )
    except (TypeError, ValueError) as exc:
        raise DSPBOutputError("registry validation failed") from exc
    if rebuilt != row:
        raise DSPBOutputError("registry differs from validated reconstruction")
    if (
        rebuilt.query_start_ns_inclusive
        - rebuilt.warmup_start_ns_inclusive
        != PREROLL_NS
    ):
        raise DSPBOutputError("DSPB window must retain the exact 50 ms pre-roll")
    return rebuilt


def _checked_event(value: object) -> NeutralEventInput:
    _exact_dataclass(value, NeutralEventInput, _EVENT_FIELDS, "event")
    row = value  # type: ignore[assignment]
    expected_digest = canonical_event_content_sha256(
        row.event_id,
        row.timestamp_ns,
        row.polarity,
        row.is_query,
        row.sensor_ray,
        row.causal_pose_source_index,
        row.transform_guard_valid,
    )
    if row.event_content_sha256 != expected_digest:
        raise DSPBOutputError("event content digest differs")
    try:
        rebuilt = NeutralEventInput(
            row.event_id,
            row.timestamp_ns,
            row.polarity,
            row.is_query,
            row.sensor_ray,
            row.causal_pose_source_index,
            row.event_content_sha256,
            row.transform_guard_valid,
        )
    except (TypeError, ValueError) as exc:
        raise DSPBOutputError("event validation failed") from exc
    if rebuilt != row:
        raise DSPBOutputError("event differs from validated reconstruction")
    return rebuilt


def _checked_pose(value: object) -> NeutralPoseInput:
    _exact_dataclass(value, NeutralPoseInput, _POSE_FIELDS, "pose")
    row = value  # type: ignore[assignment]
    expected_digest = canonical_pose_value_sha256(
        row.pose_id, row.timestamp_ns, row.quaternion_xyzw
    )
    if row.pose_sha256 != expected_digest:
        raise DSPBOutputError("pose content digest differs")
    try:
        rebuilt = NeutralPoseInput(
            row.pose_id,
            row.timestamp_ns,
            row.commit_cycle,
            row.quaternion_xyzw,
            row.pose_sha256,
            row.value_valid,
            row.arithmetic_valid,
        )
    except (TypeError, ValueError) as exc:
        raise DSPBOutputError("pose validation failed") from exc
    if rebuilt != row:
        raise DSPBOutputError("pose differs from validated reconstruction")
    return rebuilt


def locked_dspb_config_bytes() -> bytes:
    """Return the exact config bytes whose digest the DSPB model publishes."""

    config = DSPBConfig()
    payload = json.dumps(
        config.to_mapping(),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    if hashlib.sha256(payload).hexdigest() != config.sha256:
        raise DSPBOutputError("DSPB config serialization differs from model authority")
    return payload


def locked_dspb_executable_sha256() -> str:
    """Bind the actual imported DSPB implementation bytes."""

    path = Path(dspb_module.__file__).resolve()
    if path.name != "dspb.py":
        raise DSPBOutputError("DSPB executable authority is not the source module")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DSPBOutputError("cannot read DSPB executable authority") from exc
    return hashlib.sha256(payload).hexdigest()


def locked_dspb_config_sha256() -> str:
    return hashlib.sha256(locked_dspb_config_bytes()).hexdigest()


def _neutral_input_sha256(
    registries: Sequence[NeutralRegistryWindow],
    event_streams: Mapping[str, Sequence[NeutralEventInput]],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
) -> str:
    return canonical_sha256({
        "schema": "redred.mc_wtb.current_cav_neutral_inputs/v1",
        "registry": [registry.to_mapping() for registry in registries],
        "windows": [
            {
                "window_id": registry.window_id,
                "events": [
                    event.to_content_mapping()
                    for event in event_streams[registry.window_id]
                ],
                "poses": [
                    pose.to_content_mapping()
                    for pose in pose_streams[registry.window_id]
                ],
            }
            for registry in registries
        ],
    })


def _validate_inputs(
    registry: Sequence[NeutralRegistryWindow],
    event_streams: Mapping[str, Sequence[NeutralEventInput]],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
) -> Tuple[
    Tuple[NeutralRegistryWindow, ...],
    Mapping[str, Tuple[NeutralEventInput, ...]],
    Mapping[str, Tuple[NeutralPoseInput, ...]],
]:
    if isinstance(registry, (str, bytes)) or not isinstance(registry, Sequence):
        raise DSPBOutputError("neutral registry must be a sequence")
    registries = tuple(_checked_registry(row) for row in registry)
    if not registries:
        raise DSPBOutputError("neutral registry must not be empty")
    identifiers = tuple(row.window_id for row in registries)
    if len(set(identifiers)) != len(identifiers):
        raise DSPBOutputError("neutral registry window IDs repeat")
    expected = set(identifiers)
    if not isinstance(event_streams, Mapping) or set(event_streams) != expected:
        raise DSPBOutputError("event stream window IDs differ from registry")
    if not isinstance(pose_streams, Mapping) or set(pose_streams) != expected:
        raise DSPBOutputError("pose stream window IDs differ from registry")

    checked_events = {}  # type: Dict[str, Tuple[NeutralEventInput, ...]]
    checked_poses = {}  # type: Dict[str, Tuple[NeutralPoseInput, ...]]
    for window in registries:
        supplied_events = event_streams[window.window_id]
        supplied_poses = pose_streams[window.window_id]
        if (
            isinstance(supplied_events, (str, bytes))
            or not isinstance(supplied_events, Sequence)
            or not supplied_events
        ):
            raise DSPBOutputError("window event stream must be nonempty")
        if (
            isinstance(supplied_poses, (str, bytes))
            or not isinstance(supplied_poses, Sequence)
            or not supplied_poses
        ):
            raise DSPBOutputError("window pose stream must be nonempty")
        events = tuple(_checked_event(row) for row in supplied_events)
        poses = tuple(_checked_pose(row) for row in supplied_poses)
        if any(
            left.timestamp_ns > right.timestamp_ns
            for left, right in zip(events, events[1:])
        ):
            raise DSPBOutputError("event timestamps must be nondecreasing")
        if len({event.event_id for event in events}) != len(events):
            raise DSPBOutputError("event IDs must be exact-once within a window")
        if any(
            not (
                window.warmup_start_ns_inclusive
                <= event.timestamp_ns
                < window.query_end_ns_exclusive
            )
            for event in events
        ):
            raise DSPBOutputError("event lies outside its neutral window")
        if any(
            event.is_query
            != (event.timestamp_ns >= window.query_start_ns_inclusive)
            for event in events
        ):
            raise DSPBOutputError("event query membership differs from neutral bounds")
        if not any(event.is_query for event in events):
            raise DSPBOutputError("window contains no query event")
        if any(
            left.pose_id >= right.pose_id
            for left, right in zip(poses, poses[1:])
        ):
            raise DSPBOutputError("pose IDs must be strictly increasing")
        if any(
            left.timestamp_ns >= right.timestamp_ns
            for left, right in zip(poses, poses[1:])
        ):
            raise DSPBOutputError("pose timestamps must be strictly increasing")
        active_cycles = [pose.commit_cycle for pose in poses if pose.commit_cycle >= 0]
        if len(set(active_cycles)) != len(active_cycles):
            raise DSPBOutputError("post-reset pose commit cycles must be unique")
        checked_events[window.window_id] = events
        checked_poses[window.window_id] = poses
    return registries, checked_events, checked_poses


def _score_free_baseline_records(
    registry: NeutralRegistryWindow,
    events: Sequence[NeutralEventInput],
    poses: Sequence[NeutralPoseInput],
) -> Tuple[object, ...]:
    try:
        simulation = run_cycle_model(
            window_id=registry.window_id,
            window_start_ns=registry.warmup_start_ns_inclusive,
            arm=Arm.CAUSAL_CAV,
            events=tuple(Event(
                event.event_id,
                event.timestamp_ns,
                transform_guard_valid=event.transform_guard_valid,
                causal_pose_index=event.causal_pose_source_index,
            ) for event in events),
            poses=tuple(PosePacket(
                pose.pose_id,
                pose.timestamp_ns,
                pose.commit_cycle,
                PoseSource.DATASET,
                pose.pose_sha256,
                pose.value_valid,
                pose.arithmetic_valid,
            ) for pose in poses),
        )
    except (TypeError, ValueError) as exc:
        raise DSPBOutputError("score-free current-CAV replay failed") from exc
    if simulation.synthetic_test_mode or not simulation.all_event_pose_indices_verified:
        raise DSPBOutputError("score-free replay did not verify every event pose index")
    records = tuple(simulation.records)
    if tuple(record.event_id for record in records) != tuple(
        event.event_id for event in events
    ):
        raise DSPBOutputError("score-free replay changed event order or identity")
    return records


def _unit_world_ray(
    quaternion_xyzw: Sequence[float], sensor_ray: Sequence[float]
) -> Tuple[float, float, float]:
    try:
        world = rotate_sensor_ray_to_world(quaternion_xyzw, sensor_ray)
    except GeometryError as exc:
        raise DSPBOutputError("DSPB world-ray projection failed") from exc
    norm = math.sqrt(math.fsum(component * component for component in world))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-12:
        raise DSPBOutputError("DSPB world ray is not normalized")
    return world


def _fallback_row(
    event: NeutralEventInput,
    baseline: object,
    state_version: int,
    reason: str,
) -> Mapping[str, object]:
    fallback_reason = "%s:CURRENT_CAV:%s" % (
        reason,
        baseline.disposition_reason,
    )
    return {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "decision_cycle": baseline.occurrence_cycle,
        "model_id": CURRENT_CAV_MODEL_ID,
        "predictor_state_version": state_version,
        "used_pose_ids": sorted(set(baseline.used_pose_ids)),
        "candidate_used": False,
        "fallback_reason": fallback_reason,
        "world_ray": None,
    }


def _candidate_row(
    event: NeutralEventInput,
    baseline: object,
    decision: object,
) -> Mapping[str, object]:
    if decision.output_quaternion_xyzw is None or decision.geometry_expert_id is None:
        raise DSPBOutputError("DSPB candidate decision lacks geometry")
    occurrence_ids = set(baseline.occurrence_pose_ids)
    direct_pose_ids = sorted(set(decision.used_pose_ids).intersection(occurrence_ids))
    if not direct_pose_ids:
        raise DSPBOutputError("DSPB candidate has no screen-visible source pose")
    return {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "decision_cycle": baseline.occurrence_cycle,
        "model_id": decision.geometry_expert_id,
        "predictor_state_version": decision.state_version,
        "used_pose_ids": direct_pose_ids,
        "candidate_used": True,
        "fallback_reason": None,
        "world_ray": list(_unit_world_ray(
            decision.output_quaternion_xyzw, event.sensor_ray
        )),
    }


def _event_groups(
    events: Sequence[NeutralEventInput], records: Sequence[object]
) -> Mapping[int, Tuple[_EventCluster, ...]]:
    groups = {}  # type: Dict[int, List[_EventCluster]]
    index = 0
    while index < len(events):
        timestamp = events[index].timestamp_ns
        cycle = records[index].occurrence_cycle
        end = index + 1
        while (
            end < len(events)
            and events[end].timestamp_ns == timestamp
            and records[end].occurrence_cycle == cycle
        ):
            end += 1
        indices = tuple(range(index, end))
        groups.setdefault(cycle, []).append((
            indices, tuple(events[index:end]), tuple(records[index:end])
        ))
        index = end
    return {cycle: tuple(value) for cycle, value in groups.items()}


def _replay_window(
    registry: NeutralRegistryWindow,
    events: Sequence[NeutralEventInput],
    poses: Sequence[NeutralPoseInput],
) -> Mapping[str, object]:
    baseline_records = _score_free_baseline_records(registry, events, poses)
    event_groups = _event_groups(events, baseline_records)
    active_poses = tuple(pose for pose in poses if pose.commit_cycle >= 0)
    pose_by_cycle = {pose.commit_cycle: pose for pose in active_poses}
    cycles = sorted(set(event_groups).union(pose_by_cycle))
    model = DSPBModel()
    output_rows = [None for _ in events]  # type: List[object]

    for cycle in cycles:
        # Event decisions deliberately precede a pose commit on the same edge.
        for indices, cluster, baselines in event_groups.get(cycle, ()):
            dspb_events = tuple(EventRecord(
                event.event_id,
                event.timestamp_ns,
                cycle - 1,
                cycle,
            ) for event in cluster)
            try:
                decisions = model.predict_event_cluster(dspb_events)
            except DSPBError as exc:
                raise DSPBOutputError("DSPB event replay failed") from exc
            if len({decision.state_version for decision in decisions}) != 1:
                raise DSPBOutputError("equal-time cluster changed DSPB state version")
            for index, event, baseline, decision in zip(
                indices, cluster, baselines, decisions
            ):
                if decision.decision_cycle != baseline.occurrence_cycle:
                    raise DSPBOutputError("DSPB decision edge differs from locked occurrence")
                if decision.candidate_used and baseline.disposition == "corrected_world_ray":
                    row = _candidate_row(event, baseline, decision)
                else:
                    reason = (
                        "CURRENT_CAV_NOT_VALID_FOR_CANDIDATE"
                        if decision.candidate_used
                        else decision.fallback_reason
                    )
                    if type(reason) is not str or not reason:
                        raise DSPBOutputError("DSPB fallback reason is missing")
                    row = _fallback_row(
                        event, baseline, decision.state_version, reason
                    )
                output_rows[index] = row
        pose = pose_by_cycle.get(cycle)
        if pose is not None:
            try:
                model.commit_pose(SuppliedPose(
                    pose.pose_id,
                    pose.timestamp_ns,
                    pose.commit_cycle,
                    pose.quaternion_xyzw,
                    pose.value_valid,
                    pose.arithmetic_valid,
                ))
            except DSPBError as exc:
                raise DSPBOutputError("DSPB pose replay failed") from exc

    if any(row is None for row in output_rows):
        raise DSPBOutputError("DSPB replay did not conserve every event")
    return {"window_id": registry.window_id, "events": output_rows}


def generate_dspb_candidate_output(
    registry: Sequence[NeutralRegistryWindow],
    event_streams: Mapping[str, Sequence[NeutralEventInput]],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
    adapter_aggregate_sha256: str,
) -> Mapping[str, object]:
    """Replay locked neutral windows and return one sealed DSPB output envelope."""

    adapter_digest = _sha256(adapter_aggregate_sha256, "adapter aggregate digest")
    registries, events, poses = _validate_inputs(
        registry, event_streams, pose_streams
    )
    neutral_digest = _neutral_input_sha256(registries, events, poses)
    windows = [
        _replay_window(row, events[row.window_id], poses[row.window_id])
        for row in registries
    ]
    output = seal_candidate_output(
        DSPBConfig().candidate_id,
        adapter_digest,
        neutral_digest,
        locked_dspb_executable_sha256(),
        locked_dspb_config_sha256(),
        windows,
    )
    if output.get("schema") != CANDIDATE_OUTPUT_SCHEMA:
        raise DSPBOutputError("screen108 candidate output schema differs")
    return output


def verify_dspb_candidate_output(
    value: object,
    registry: Sequence[NeutralRegistryWindow],
    event_streams: Mapping[str, Sequence[NeutralEventInput]],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
    adapter_aggregate_sha256: str,
) -> str:
    """Reproduce all decisions and return the authenticated aggregate digest."""

    expected = generate_dspb_candidate_output(
        registry, event_streams, pose_streams, adapter_aggregate_sha256
    )
    if value != expected:
        raise DSPBOutputError("DSPB candidate output differs from locked replay")
    digest = expected.get("aggregate_sha256")
    return _sha256(digest, "DSPB candidate output aggregate")


__all__ = (
    "CURRENT_CAV_MODEL_ID",
    "DSPBOutputError",
    "PREROLL_NS",
    "generate_dspb_candidate_output",
    "locked_dspb_config_bytes",
    "locked_dspb_config_sha256",
    "locked_dspb_executable_sha256",
    "verify_dspb_candidate_output",
)
