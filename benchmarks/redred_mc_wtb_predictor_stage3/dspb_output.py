"""Dependency-closed, score-blind DSPB execution and receipt generation.

The generator accepts only the neutral registry, neutral event/pose streams,
and the already authenticated adapter aggregate digest.  It never accepts
selector sidecars, quality values, outcome data, or routing policy.  Each
window creates a fresh :class:`DSPBModel` at the start of its exact 50 ms
pre-roll and replays score-free source records in cycle order.

On a shared cycle every event cluster is decided before the pose commit, so a
same-edge pose cannot affect that event.  Equal-timestamp members are passed
to one atomic DSPB call.  Candidate geometry is emitted only where frozen
current CAV is exactly valid.  Every row records distinct occurrence and
decision edges, attempt and route semantics, and direct plus state-transitive
pose dependencies.  Window reset, native pose-feedback, state, event, and
dependency-manifest receipts form reproducible hash chains.
"""

from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

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
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE,
    run_stage3_logical_cycle_model,
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
)


PREROLL_NS = 50_000_000
CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_output/v2"
EXECUTABLE_MANIFEST_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.executable_manifest/v1"
)
RESET_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.window_reset/v1"
STATE_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_state/v1"
POSE_RECEIPT_SCHEMA = "redred.mc_wtb_predictor_stage3.dspb_pose_chain/v1"
ROUTE_CANDIDATE = "CANDIDATE"
ROUTE_CURRENT_CAV = "CURRENT_CAV"
ROUTE_FRESH_ZOH = "FRESH_ZOH"
ROUTE_SENSOR_FIXED = "SENSOR_FIXED"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REGISTRY_FIELDS = tuple(field.name for field in fields(NeutralRegistryWindow))
_EVENT_FIELDS = tuple(field.name for field in fields(NeutralEventInput))
_POSE_FIELDS = tuple(field.name for field in fields(NeutralPoseInput))
_EventCluster = Tuple[
    Tuple[int, ...], Tuple[NeutralEventInput, ...], Tuple[object, ...]
]
_EXECUTABLE_DEPENDENCIES = (
    ("producer", "benchmarks/redred_mc_wtb_predictor_stage3/dspb_output.py"),
    ("candidate_package", "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py"),
    ("candidate_framework", "benchmarks/redred_mc_wtb_predictor_stage3/framework.py"),
    ("logical_cycle_replay", "benchmarks/redred_mc_wtb_predictor_stage3/logical_cycle_replay.py"),
    ("candidate_model", "benchmarks/redred_mc_wtb_predictor_stage3/dspb.py"),
    ("pose_recovery_api", "benchmarks/redred_mc_wtb_pose_recovery/__init__.py"),
    ("pose_recovery_implementation", "benchmarks/redred_mc_wtb_pose_recovery/geometry.py"),
    ("canonical_api", "benchmarks/redred_mc_wtb_stage4_contract/__init__.py"),
    ("canonical_implementation", "benchmarks/redred_mc_wtb_stage4_contract/contract.py"),
    ("canonical_receipts", "benchmarks/redred_mc_wtb_stage4_contract/receipt.py"),
    ("cycle_model_api", "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py"),
    ("cycle_model_implementation", "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py"),
    ("neutral_package", "benchmarks/redred_mc_wtb_so3_axis_audit/__init__.py"),
    ("neutral_package_dependency", "benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py"),
    ("neutral_projection", "benchmarks/redred_mc_wtb_so3_axis_audit/evaluator.py"),
    ("reference_api", "benchmarks/redred_mc_wtb_causal_reference/__init__.py"),
    ("reference_implementation", "benchmarks/redred_mc_wtb_causal_reference/reference.py"),
    ("reference_routing", "benchmarks/redred_mc_wtb_causal_reference/routing.py"),
    ("routing_dependency_api", "benchmarks/redred_mc_wtb_motion_qualification/__init__.py"),
    ("routing_dependency", "benchmarks/redred_mc_wtb_motion_qualification/controller.py"),
)


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


def locked_dspb_executable_manifest() -> Mapping[str, object]:
    """Return the complete in-repository source closure used by this producer."""

    root = Path(__file__).resolve().parents[2]
    files = []
    for role, relative in _EXECUTABLE_DEPENDENCIES:
        path = (root / relative).resolve()
        try:
            if path.relative_to(root) != Path(relative) or not path.is_file():
                raise DSPBOutputError("DSPB dependency path escaped the repository")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise DSPBOutputError("cannot read DSPB executable dependency") from exc
        files.append({"role": role, "path": relative, "sha256": digest})
    body = {
        "schema": EXECUTABLE_MANIFEST_SCHEMA,
        "candidate_id": DSPBConfig().candidate_id,
        "entrypoint": _EXECUTABLE_DEPENDENCIES[0][1],
        "files": files,
    }
    return dict(body, manifest_sha256=canonical_sha256(body))


def locked_dspb_executable_sha256() -> str:
    """Bind the producer and every in-repository execution dependency."""

    digest = locked_dspb_executable_manifest().get("manifest_sha256")
    return _sha256(digest, "DSPB executable manifest digest")


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
        simulation = run_stage3_logical_cycle_model(
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
    if (
        simulation.raw_ingress_lanes
        != STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.raw_ingress_lanes
        or simulation.ingress_staging_entries
        != STAGE3_LOGICAL_REPLAY_INGRESS_PROFILE.ingress_staging_entries
    ):
        raise DSPBOutputError("cycle model used the wrong ingress profile")
    records = tuple(simulation.records)
    if tuple(record.event_id for record in records) != tuple(
        event.event_id for event in events
    ):
        raise DSPBOutputError("score-free replay changed event order or identity")
    return records


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
        raise DSPBOutputError("native DSPB receipt is not canonical JSON") from exc
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


def _reset_generation(
    registry: NeutralRegistryWindow, excluded_pose_ids: Sequence[int]
) -> Tuple[str, Mapping[str, object]]:
    generation_body = {
        "window_id": registry.window_id,
        "warmup_start_ns_inclusive": registry.warmup_start_ns_inclusive,
        "query_start_ns_inclusive": registry.query_start_ns_inclusive,
        "query_end_ns_exclusive": registry.query_end_ns_exclusive,
        "candidate_id": DSPBConfig().candidate_id,
        "candidate_config_sha256": locked_dspb_config_sha256(),
        "reset_cycle": 0,
        "excluded_pre_reset_pose_ids": list(excluded_pose_ids),
    }
    generation_sha256 = canonical_sha256(generation_body)
    return generation_sha256, generation_body


def _reset_receipt(
    generation_sha256: str,
    generation_body: Mapping[str, object],
    initial_state_sha256: str,
) -> Mapping[str, object]:
    body = {
        "schema": RESET_RECEIPT_SCHEMA,
        "reset_generation_sha256": generation_sha256,
        "generation": generation_body,
        "previous_window_state_sha256": None,
        "initial_state_sha256": initial_state_sha256,
    }
    return dict(body, reset_receipt_sha256=canonical_sha256(body))


def _pose_chain_receipt(
    pose: NeutralPoseInput,
    native: object,
    prior_state: Mapping[str, object],
    next_state: Mapping[str, object],
    previous_receipt_sha256: str,
) -> Mapping[str, object]:
    native_mapping = native.to_mapping()
    native_body = dict(native_mapping)
    native_digest = native_body.pop("receipt_sha256", None)
    if native_digest != _native_sha256(native_body):
        raise DSPBOutputError("native DSPB pose receipt digest differs")
    if (
        native.candidate_id != DSPBConfig().candidate_id
        or native.config_sha256 != locked_dspb_config_sha256()
        or native.pose_id != pose.pose_id
        or native.measurement_timestamp_ns != pose.timestamp_ns
        or native.commit_cycle != pose.commit_cycle
        or native.prior_state_version != prior_state["state_version"]
        or native.next_state_version != next_state["state_version"]
        or native.next_effective_cycle != next_state["effective_cycle"]
        or next_state["parent_state_sha256"] != prior_state["state_sha256"]
    ):
        raise DSPBOutputError("native DSPB pose/state transition differs")
    body = {
        "schema": POSE_RECEIPT_SCHEMA,
        "pose_id": pose.pose_id,
        "pose_content_sha256": pose.pose_sha256,
        "prior_state_sha256": prior_state["state_sha256"],
        "next_state_sha256": next_state["state_sha256"],
        "previous_pose_receipt_sha256": previous_receipt_sha256,
        "native_pose_receipt": native_mapping,
    }
    return dict(body, pose_receipt_sha256=canonical_sha256(body))


def _exact_baseline_quaternion(
    event: NeutralEventInput,
    baseline: object,
    poses_by_id: Mapping[int, NeutralPoseInput],
) -> Optional[Tuple[float, float, float, float]]:
    if baseline.disposition_reason == "causal_cav":
        if baseline.disposition != "corrected_world_ray" or len(baseline.used_pose_ids) != 2:
            raise DSPBOutputError("baseline exact current CAV receipt differs")
        samples = tuple(PoseSample(
            poses_by_id[pose_id].timestamp_ns,
            poses_by_id[pose_id].commit_cycle,
            poses_by_id[pose_id].quaternion_xyzw,
        ) for pose_id in baseline.used_pose_ids)
        try:
            recovery = recover_causal_cav(
                samples, event.timestamp_ns, baseline.occurrence_cycle
            )
        except GeometryError as exc:
            raise DSPBOutputError("cannot reconstruct exact current CAV") from exc
        if recovery.mode is not RecoveryMode.CAV or recovery.quaternion_xyzw is None:
            raise DSPBOutputError("baseline current CAV reconstruction differs")
        return recovery.quaternion_xyzw
    if baseline.disposition_reason == "fresh_zoh_fallback":
        if baseline.disposition != "corrected_world_ray" or len(baseline.used_pose_ids) != 1:
            raise DSPBOutputError("baseline fresh-ZOH receipt differs")
        return poses_by_id[baseline.used_pose_ids[0]].quaternion_xyzw
    if baseline.disposition != "raw_bypass":
        raise DSPBOutputError("baseline route is not exact CAV/ZOH/sensor-fixed")
    return None


def _candidate_pose_ids(
    event: NeutralEventInput,
    baseline: object,
    decision: object,
    poses_by_id: Mapping[int, NeutralPoseInput],
    state_dependency_pose_ids: Sequence[int],
) -> Tuple[int, ...]:
    identifiers = tuple(decision.used_pose_ids)
    if identifiers != tuple(sorted(set(identifiers))) or not identifiers:
        raise DSPBOutputError("DSPB candidate pose dependencies differ")
    if not (
        len(identifiers)
        == len(decision.used_pose_timestamps_ns)
        == len(decision.used_pose_commit_cycles)
    ):
        raise DSPBOutputError("DSPB candidate pose receipt cardinality differs")
    for pose_id, timestamp, commit in zip(
        identifiers,
        decision.used_pose_timestamps_ns,
        decision.used_pose_commit_cycles,
    ):
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.timestamp_ns != timestamp
            or pose.commit_cycle != commit
            or pose.commit_cycle >= baseline.occurrence_cycle
            or pose.timestamp_ns > event.timestamp_ns
            or not pose.value_valid
            or not pose.arithmetic_valid
            or pose_id not in state_dependency_pose_ids
        ):
            raise DSPBOutputError("DSPB candidate used an unavailable pose")
    return identifiers


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


def _event_row(
    event: NeutralEventInput,
    baseline: object,
    decision: object,
    poses_by_id: Mapping[int, NeutralPoseInput],
    state_receipt: Mapping[str, object],
    state_dependency_pose_ids: Sequence[int],
    pose_receipt_chain_sha256: str,
    prior_decision_sha256: Optional[str],
) -> Mapping[str, object]:
    decision_cycle = baseline.occurrence_cycle
    occurrence_cycle = decision_cycle - 1
    if (
        decision.candidate_id != DSPBConfig().candidate_id
        or decision.config_sha256 != locked_dspb_config_sha256()
        or decision.event_id != event.event_id
        or decision.occurrence_timestamp_ns != event.timestamp_ns
        or decision.occurrence_cycle != occurrence_cycle
        or decision.decision_cycle != decision_cycle
        or decision.state_version != state_receipt["state_version"]
        or list(state_dependency_pose_ids)
        != state_receipt["dependency_pose_ids"]
    ):
        raise DSPBOutputError("native DSPB event receipt differs")
    native_mapping = decision.to_mapping()
    native_body = dict(native_mapping)
    native_digest = native_body.pop("decision_sha256", None)
    if native_digest != _native_sha256(native_body):
        raise DSPBOutputError("native DSPB decision digest differs")

    baseline_quaternion = _exact_baseline_quaternion(
        event, baseline, poses_by_id
    )
    candidate_attempted = baseline.disposition_reason == "causal_cav"
    candidate_failure_reason = None  # type: Optional[str]
    geometry_expert_id = None  # type: Optional[str]
    fallback_reason = None  # type: Optional[str]

    if candidate_attempted and decision.candidate_used:
        if decision.output_quaternion_xyzw is None or decision.geometry_expert_id is None:
            raise DSPBOutputError("DSPB candidate decision lacks geometry")
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
        if (
            decision.candidate_used
            or baseline_quaternion is None
        ):
            raise DSPBOutputError("DSPB exact current-CAV fallback differs")
        if type(decision.fallback_reason) is not str or not decision.fallback_reason:
            raise DSPBOutputError("DSPB candidate failure reason is missing")
        route = ROUTE_CURRENT_CAV
        used_pose_ids = tuple(baseline.used_pose_ids)
        output_quaternion = baseline_quaternion
        candidate_failure_reason = decision.fallback_reason
        fallback_reason = decision.fallback_reason
    elif baseline.disposition_reason == "fresh_zoh_fallback":
        route = ROUTE_FRESH_ZOH
        used_pose_ids = tuple(baseline.used_pose_ids)
        output_quaternion = baseline_quaternion
        fallback_reason = baseline.disposition_reason
    else:
        route = ROUTE_SENSOR_FIXED
        used_pose_ids = tuple(baseline.used_pose_ids)
        output_quaternion = None
        fallback_reason = baseline.disposition_reason

    used_pose_evidence = []
    for pose_id in used_pose_ids:
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.commit_cycle >= decision_cycle
            or pose.timestamp_ns > event.timestamp_ns
        ):
            raise DSPBOutputError("exact route used an unavailable pose")
        used_pose_evidence.append({
            "pose_id": pose.pose_id,
            "measurement_timestamp_ns": pose.timestamp_ns,
            "commit_cycle": pose.commit_cycle,
            "pose_content_sha256": pose.pose_sha256,
            "value_valid": pose.value_valid,
            "arithmetic_valid": pose.arithmetic_valid,
        })

    if output_quaternion is None:
        world_ray = None
    else:
        world_ray = list(_unit_world_ray(output_quaternion, event.sensor_ray))
    ray_body = {
        "event_content_sha256": event.event_content_sha256,
        "route": route,
        "sensor_ray": list(event.sensor_ray),
        "output_quaternion_xyzw": (
            None if output_quaternion is None else list(output_quaternion)
        ),
        "world_ray": world_ray,
    }
    ray_receipt = dict(
        ray_body, ray_derivation_sha256=canonical_sha256(ray_body)
    )
    body = {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "event_timestamp_ns": event.timestamp_ns,
        "is_query": event.is_query,
        "occurrence_cycle": occurrence_cycle,
        "decision_cycle": decision_cycle,
        "model_id": DSPBConfig().candidate_id,
        "geometry_expert_id": geometry_expert_id,
        "predictor_state_version": decision.state_version,
        "predictor_state_sha256": state_receipt["state_sha256"],
        "state_dependency_pose_ids": list(state_dependency_pose_ids),
        "pose_receipt_chain_sha256": pose_receipt_chain_sha256,
        "used_pose_ids": list(used_pose_ids),
        "used_pose_evidence": used_pose_evidence,
        "route": route,
        "route_reason": baseline.disposition_reason,
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
    excluded_poses = tuple(pose for pose in poses if pose.commit_cycle < 0)
    pose_by_cycle = {pose.commit_cycle: pose for pose in active_poses}
    cycles = sorted(set(event_groups).union(pose_by_cycle))
    model = DSPBModel()
    output_rows = [None for _ in events]  # type: List[object]
    poses_by_id = {pose.pose_id: pose for pose in poses}

    reset_generation_sha256, reset_generation_body = _reset_generation(
        registry, tuple(pose.pose_id for pose in excluded_poses)
    )
    initial_state = _state_receipt(
        registry.window_id,
        reset_generation_sha256,
        model.published_state,
        0,
        None,
        None,
        (),
    )
    reset = _reset_receipt(
        reset_generation_sha256,
        reset_generation_body,
        initial_state["state_sha256"],  # type: ignore[arg-type]
    )
    state_receipts = [initial_state]
    states_by_version = {0: initial_state}
    state_dependencies = {0: ()}  # type: Dict[int, Tuple[int, ...]]
    pose_receipts = []
    pose_chain_sha256 = reset["reset_receipt_sha256"]
    prior_decision_sha256 = None  # type: Optional[str]

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
                state = states_by_version.get(decision.state_version)
                dependencies = state_dependencies.get(decision.state_version)
                if state is None or dependencies is None:
                    raise DSPBOutputError("DSPB decision references an unknown state")
                row = _event_row(
                    event,
                    baseline,
                    decision,
                    poses_by_id,
                    state,
                    dependencies,
                    pose_chain_sha256,  # type: ignore[arg-type]
                    prior_decision_sha256,
                )
                output_rows[index] = row
                prior_decision_sha256 = row["decision_sha256"]  # type: ignore[assignment]
        pose = pose_by_cycle.get(cycle)
        if pose is not None:
            try:
                native_receipt = model.commit_pose(SuppliedPose(
                    pose.pose_id,
                    pose.timestamp_ns,
                    pose.commit_cycle,
                    pose.quaternion_xyzw,
                    pose.value_valid,
                    pose.arithmetic_valid,
                ))
            except DSPBError as exc:
                raise DSPBOutputError("DSPB pose replay failed") from exc
            prior_state = states_by_version.get(native_receipt.prior_state_version)
            pending = model.pending_state
            if prior_state is None or pending is None:
                raise DSPBOutputError("DSPB pose transition state is missing")
            prior_dependencies = state_dependencies[native_receipt.prior_state_version]
            next_dependencies = prior_dependencies + (pose.pose_id,)
            next_state = _state_receipt(
                registry.window_id,
                reset_generation_sha256,
                pending,
                native_receipt.next_effective_cycle,
                prior_state["state_sha256"],  # type: ignore[arg-type]
                pose.pose_id,
                next_dependencies,
            )
            if pending.state_version in states_by_version:
                raise DSPBOutputError("DSPB state version repeated")
            states_by_version[pending.state_version] = next_state
            state_dependencies[pending.state_version] = next_dependencies
            state_receipts.append(next_state)
            chained = _pose_chain_receipt(
                pose,
                native_receipt,
                prior_state,
                next_state,
                pose_chain_sha256,  # type: ignore[arg-type]
            )
            pose_receipts.append(chained)
            pose_chain_sha256 = chained["pose_receipt_sha256"]

    if any(row is None for row in output_rows):
        raise DSPBOutputError("DSPB replay did not conserve every event")
    events_sha256 = canonical_sha256(output_rows)
    states_sha256 = canonical_sha256(state_receipts)
    poses_sha256 = canonical_sha256(pose_receipts)
    body = {
        "window_id": registry.window_id,
        "reset_receipt": reset,
        "state_receipts": state_receipts,
        "state_receipts_sha256": states_sha256,
        "pose_receipts": pose_receipts,
        "pose_receipts_sha256": poses_sha256,
        "events": output_rows,
        "events_sha256": events_sha256,
    }
    return dict(body, window_sha256=canonical_sha256(body))


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
    manifest = locked_dspb_executable_manifest()
    neutral_digest = _neutral_input_sha256(registries, events, poses)
    windows = [
        _replay_window(row, events[row.window_id], poses[row.window_id])
        for row in registries
    ]
    if manifest != locked_dspb_executable_manifest():
        raise DSPBOutputError("DSPB executable dependencies changed during replay")
    config = DSPBConfig()
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": config.candidate_id,
        "adapter_aggregate_sha256": adapter_digest,
        "neutral_input_sha256": neutral_digest,
        "candidate_executable_sha256": manifest["manifest_sha256"],
        "candidate_executable_manifest": manifest,
        "candidate_config_sha256": locked_dspb_config_sha256(),
        "candidate_config": config.to_mapping(),
        "windows": windows,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


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
    "CANDIDATE_OUTPUT_SCHEMA",
    "DSPBOutputError",
    "EXECUTABLE_MANIFEST_SCHEMA",
    "POSE_RECEIPT_SCHEMA",
    "PREROLL_NS",
    "RESET_RECEIPT_SCHEMA",
    "ROUTE_CANDIDATE",
    "ROUTE_CURRENT_CAV",
    "ROUTE_FRESH_ZOH",
    "ROUTE_SENSOR_FIXED",
    "STATE_RECEIPT_SCHEMA",
    "generate_dspb_candidate_output",
    "locked_dspb_config_bytes",
    "locked_dspb_config_sha256",
    "locked_dspb_executable_manifest",
    "locked_dspb_executable_sha256",
    "verify_dspb_candidate_output",
)
