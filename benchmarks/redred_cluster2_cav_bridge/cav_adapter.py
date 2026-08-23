"""Score-free Cluster2 bridge adapter for the current CAV baseline.

The three delivered-event views share one source-ordinal, occurrence-time
``NeutralEventInput`` population.  AER retirement observations never enter the
geometry clock: they are retained only in the explicitly observational
transport sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryMode,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    CurrentCAVEvaluationError,
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    LogicalCycleReplayError,
    run_stage3_logical_cycle_model,
)

from .contract import (
    OBSERVATIONAL_JOIN_LABEL,
    BridgeBundle,
    BridgeValidationError,
)


CAV_VIEW_ORDER = ("RAW4X4_MATCHED", "AER_OCC", "AER_RET")
OBSERVATIONAL_SIDECAR_ASSISTED = "OBSERVATIONAL_SIDECAR_ASSISTED"
TRANSPORT_LATENCY_SEPARATE = "TRANSPORT_LATENCY_SEPARATE"
SENSOR_FIXED_FRAME = "SENSOR_FIXED"
WORLD_FRAME = "WORLD"

_RAW_FIELDS = frozenset((
    "schema", "event_id", "ordinal", "timestamp_ns", "source_index",
    "polarity", "window_id", "is_query", "sensor_ray",
    "causal_pose_source_index", "transform_guard_valid",
    "event_content_sha256",
))
_AER_OCC_FIELDS = frozenset((
    "projection_semantics", "event_id", "source_index", "occurrence_cycle",
    "timestamp_ns", "window_id", "is_query", "polarity", "sensor_ray",
    "causal_pose_source_index", "transform_guard_valid",
    "event_content_sha256",
))
_AER_RET_FIELDS = frozenset((
    "projection_semantics", "event_id", "source_index", "occurrence_cycle",
    "occurrence_timestamp_ns", "retire_cycle", "retire_native_lane",
    "retire_row", "retire_col", "derived_retire_timestamp_ns", "window_id",
    "is_query", "polarity", "sensor_ray", "causal_pose_source_index",
    "transform_guard_valid", "event_content_sha256",
))
_REGISTRY_FIELDS = frozenset((
    "window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive",
    "query_end_ns_exclusive",
))
_POSE_FIELDS = frozenset((
    "pose_id", "timestamp_ns", "commit_cycle", "quaternion_xyzw",
    "pose_sha256", "value_valid", "arithmetic_valid",
))


class CAVAdapterError(ValueError):
    """The bridge views cannot be bound to one score-free CAV input."""


@dataclass(frozen=True)
class ProjectedNeutralEvent:
    """One common neutral input with its original Cluster2 source identity."""

    source_ordinal: int
    source_index: int
    window_id: str
    neutral_input: NeutralEventInput


@dataclass(frozen=True)
class CAVRayProjection:
    """Score-free current-CAV geometry result for one delivered event."""

    event_id: int
    source_ordinal: int
    cav_occurrence_cycle: int
    recovery_mode: RecoveryMode
    coordinate_frame: str
    ray_xyz: Tuple[float, float, float]
    used_pose_ids: Tuple[int, ...]


@dataclass(frozen=True)
class RetireTransportObservation:
    """AER retirement evidence kept outside the CAV geometry input."""

    event_id: int
    source_ordinal: int
    retire_ordinal: int
    occurrence_cycle: int
    retire_cycle: int
    latency_cycles: int
    occurrence_timestamp_ns: int
    derived_retire_timestamp_ns: int
    latency_ns: int
    retire_native_lane: int
    retire_row: int
    retire_col: int


@dataclass(frozen=True)
class CAVViewProjection:
    """One bridge view projected onto the common current-CAV population."""

    view_name: str
    events: Tuple[ProjectedNeutralEvent, ...]
    rays: Tuple[CAVRayProjection, ...]
    input_coordinate_frame: str
    measurement_class: Optional[str]
    latency_semantics: Optional[str]
    transport_sidecar: Tuple[RetireTransportObservation, ...]


@dataclass(frozen=True)
class BridgeCAVProjection:
    """The three views, all sharing the same neutral event tuple."""

    views: Tuple[CAVViewProjection, ...]

    def view(self, name: str) -> CAVViewProjection:
        if type(name) is not str:
            raise CAVAdapterError("view name must be exact text")
        for value in self.views:
            if value.view_name == name:
                return value
        raise CAVAdapterError("unknown CAV bridge view: %s" % name)


@dataclass(frozen=True)
class _ReplayEvent:
    event_id: int
    timestamp_ns: int
    transform_guard_valid: bool
    causal_pose_index: int


@dataclass(frozen=True)
class _ReplayPose:
    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Tuple[float, float, float, float]
    pose_sha256: str
    value_valid: bool
    arithmetic_valid: bool
    source: str = "dataset"


def _fail(message: str) -> None:
    raise CAVAdapterError(message)


def _exact_fields(
    row: object, fields: frozenset, where: str
) -> Mapping[str, object]:
    if not isinstance(row, Mapping) or frozenset(row) != fields:
        _fail("%s field schema differs" % where)
    return row  # type: ignore[return-value]


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        _fail("%s must be a non-negative integer" % where)
    return value  # type: ignore[return-value]


def _event_identity(row: Mapping[str, object], timestamp_field: str) -> Tuple[object, ...]:
    return (
        row["event_id"],
        row["source_index"],
        row[timestamp_field],
        row["window_id"],
        row["is_query"],
        row["polarity"],
        tuple(row["sensor_ray"]),  # type: ignore[arg-type]
        row["causal_pose_source_index"],
        row["transform_guard_valid"],
        row["event_content_sha256"],
    )


def _validated_registry(
    registry: Sequence[NeutralRegistryWindow],
) -> Tuple[NeutralRegistryWindow, ...]:
    if isinstance(registry, (str, bytes)) or not isinstance(registry, Sequence):
        _fail("registry must be a non-empty ordered sequence")
    rows = tuple(registry)
    if not rows:
        _fail("registry must be a non-empty ordered sequence")
    identifiers = []  # type: List[str]
    for index, row in enumerate(rows):
        if type(row) is not NeutralRegistryWindow:
            _fail("registry row %d must have the exact public CAV type" % index)
        if frozenset(vars(row)) != _REGISTRY_FIELDS:
            _fail("registry row %d field set differs" % index)
        # Reconstruct to re-run exact scalar and range validation.
        NeutralRegistryWindow(
            row.window_id,
            row.warmup_start_ns_inclusive,
            row.query_start_ns_inclusive,
            row.query_end_ns_exclusive,
        )
        identifiers.append(row.window_id)
    if len(set(identifiers)) != len(identifiers):
        _fail("registry window IDs are duplicated")
    for left, right in zip(rows, rows[1:]):
        if left.query_end_ns_exclusive > right.warmup_start_ns_inclusive:
            _fail("registry windows overlap or move backwards")
    return rows


def _validated_poses(
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
    registry_ids: Tuple[str, ...],
) -> Dict[str, Tuple[NeutralPoseInput, ...]]:
    if not isinstance(pose_streams, Mapping):
        _fail("pose_streams must be a window mapping")
    if set(pose_streams) != set(registry_ids):
        _fail("pose stream window IDs differ from the neutral registry")
    result = {}  # type: Dict[str, Tuple[NeutralPoseInput, ...]]
    for window_id in registry_ids:
        supplied = pose_streams[window_id]
        if isinstance(supplied, (str, bytes)) or not isinstance(supplied, Sequence):
            _fail("pose stream %s must be an ordered sequence" % window_id)
        rows = tuple(supplied)
        if not rows:
            _fail("pose stream %s must not be empty" % window_id)
        for index, row in enumerate(rows):
            if type(row) is not NeutralPoseInput:
                _fail("pose %s[%d] must have the exact public CAV type" % (
                    window_id, index,
                ))
            if frozenset(vars(row)) != _POSE_FIELDS:
                _fail("pose %s[%d] field set differs" % (window_id, index))
            NeutralPoseInput(
                row.pose_id,
                row.timestamp_ns,
                row.commit_cycle,
                tuple(row.quaternion_xyzw),
                row.pose_sha256,
                row.value_valid,
                row.arithmetic_valid,
            )
        result[window_id] = rows
    return result


def _neutral_population(
    projected: Mapping[str, Sequence[Mapping[str, object]]],
) -> Tuple[
    Tuple[ProjectedNeutralEvent, ...],
    Tuple[RetireTransportObservation, ...],
]:
    if frozenset(projected) != frozenset((
        "RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"
    )):
        _fail("BridgeBundle projection view set differs")
    raw_rows = tuple(
        _exact_fields(row, _RAW_FIELDS, "RAW4X4_MATCHED row")
        for row in projected["RAW4X4_MATCHED"]
    )
    occ_rows = tuple(
        _exact_fields(row, _AER_OCC_FIELDS, "AER_OCC row")
        for row in projected["AER_OCC"]
    )
    ret_rows = tuple(
        _exact_fields(row, _AER_RET_FIELDS, "AER_RET row")
        for row in projected["AER_RET"]
    )
    if not raw_rows:
        _fail("delivered CAV population must not be empty")

    raw_ids = tuple(row["event_id"] for row in raw_rows)
    occ_ids = tuple(row["event_id"] for row in occ_rows)
    ret_ids = tuple(row["event_id"] for row in ret_rows)
    if len(set(raw_ids)) != len(raw_ids):
        _fail("RAW4X4_MATCHED event IDs are duplicated")
    if raw_ids != occ_ids:
        _fail("RAW4X4_MATCHED and AER_OCC population/order differs")
    if len(ret_ids) != len(set(ret_ids)) or set(ret_ids) != set(raw_ids):
        _fail("AER_RET population differs from RAW4X4_MATCHED")

    occ_by_id = dict((row["event_id"], row) for row in occ_rows)
    ret_by_id = dict((row["event_id"], row) for row in ret_rows)
    source_ordinals = {}  # type: Dict[int, int]
    common = []  # type: List[ProjectedNeutralEvent]
    previous_ordinal = -1

    for row in raw_rows:
        event_id = _nonnegative_int(row["event_id"], "event_id")
        ordinal = _nonnegative_int(row["ordinal"], "source ordinal")
        if ordinal <= previous_ordinal:
            _fail("delivered source ordinals must be strictly increasing")
        previous_ordinal = ordinal
        source_index = _nonnegative_int(row["source_index"], "source index")
        occurrence = occ_by_id[event_id]
        retired = ret_by_id[event_id]
        if occurrence["projection_semantics"] != OBSERVATIONAL_JOIN_LABEL:
            _fail("AER_OCC projection semantics differs")
        if retired["projection_semantics"] != OBSERVATIONAL_JOIN_LABEL:
            _fail("AER_RET projection semantics differs")
        identity = _event_identity(row, "timestamp_ns")
        if identity != _event_identity(occurrence, "timestamp_ns"):
            _fail("AER_OCC event identity/geometry differs from RAW4X4_MATCHED")
        if identity != _event_identity(retired, "occurrence_timestamp_ns"):
            _fail("AER_RET event identity/geometry differs from RAW4X4_MATCHED")
        cycle = _nonnegative_int(
            occurrence["occurrence_cycle"], "AER occurrence cycle"
        )
        if retired["occurrence_cycle"] != cycle:
            _fail("AER_RET occurrence cycle differs from AER_OCC")
        neutral = NeutralEventInput(
            row["event_id"],
            row["timestamp_ns"],
            row["polarity"],
            row["is_query"],
            tuple(row["sensor_ray"]),  # type: ignore[arg-type]
            row["causal_pose_source_index"],
            row["event_content_sha256"],
            row["transform_guard_valid"],
        )
        window_id = row["window_id"]
        if type(window_id) is not str:
            _fail("window_id must be exact text")
        common.append(ProjectedNeutralEvent(
            ordinal, source_index, window_id, neutral
        ))
        source_ordinals[event_id] = ordinal

    expected_retire_order = tuple(sorted(
        ret_rows,
        key=lambda row: (
            row["retire_cycle"], row["retire_native_lane"],
            row["retire_col"], row["event_id"],
        ),
    ))
    if ret_rows != expected_retire_order:
        _fail("AER_RET rows do not retain canonical retirement order")
    sidecar = []  # type: List[RetireTransportObservation]
    for retire_ordinal, row in enumerate(ret_rows):
        event_id = _nonnegative_int(row["event_id"], "retire event_id")
        occurrence_cycle = _nonnegative_int(
            row["occurrence_cycle"], "retire occurrence_cycle"
        )
        retire_cycle = _nonnegative_int(row["retire_cycle"], "retire_cycle")
        occurrence_timestamp = _nonnegative_int(
            row["occurrence_timestamp_ns"], "occurrence_timestamp_ns"
        )
        retire_timestamp = _nonnegative_int(
            row["derived_retire_timestamp_ns"], "derived_retire_timestamp_ns"
        )
        if retire_cycle < occurrence_cycle or retire_timestamp < occurrence_timestamp:
            _fail("retire latency must be non-negative")
        sidecar.append(RetireTransportObservation(
            event_id=event_id,
            source_ordinal=source_ordinals[event_id],
            retire_ordinal=retire_ordinal,
            occurrence_cycle=occurrence_cycle,
            retire_cycle=retire_cycle,
            latency_cycles=retire_cycle - occurrence_cycle,
            occurrence_timestamp_ns=occurrence_timestamp,
            derived_retire_timestamp_ns=retire_timestamp,
            latency_ns=retire_timestamp - occurrence_timestamp,
            retire_native_lane=_nonnegative_int(
                row["retire_native_lane"], "retire_native_lane"
            ),
            retire_row=_nonnegative_int(row["retire_row"], "retire_row"),
            retire_col=_nonnegative_int(row["retire_col"], "retire_col"),
        ))
    return tuple(common), tuple(sidecar)


def _geometry(
    registry: Tuple[NeutralRegistryWindow, ...],
    population: Tuple[ProjectedNeutralEvent, ...],
    pose_streams: Mapping[str, Tuple[NeutralPoseInput, ...]],
) -> Tuple[CAVRayProjection, ...]:
    registry_by_id = dict((row.window_id, row) for row in registry)
    population_ids = set(row.window_id for row in population)
    if population_ids != set(registry_by_id):
        _fail("delivered event window IDs differ from the neutral registry")

    output_by_id = {}  # type: Dict[int, CAVRayProjection]
    for window in registry:
        entries = tuple(row for row in population if row.window_id == window.window_id)
        events = tuple(row.neutral_input for row in entries)
        if not events or not any(event.is_query for event in events):
            _fail("every delivered CAV window must contain a query event")
        for event in events:
            if not (
                window.warmup_start_ns_inclusive
                <= event.timestamp_ns
                < window.query_end_ns_exclusive
            ):
                _fail("event lies outside its neutral registry window")
            if event.is_query != (
                window.query_start_ns_inclusive <= event.timestamp_ns
            ):
                _fail("event query flag differs from neutral registry bounds")

        poses = pose_streams[window.window_id]
        replay_poses = tuple(_ReplayPose(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            tuple(pose.quaternion_xyzw),
            pose.pose_sha256,
            pose.value_valid,
            pose.arithmetic_valid,
        ) for pose in poses)
        replay_events = tuple(_ReplayEvent(
            event.event_id,
            event.timestamp_ns,
            event.transform_guard_valid,
            event.causal_pose_source_index,
        ) for event in events)
        simulation = run_stage3_logical_cycle_model(
            window_id=window.window_id,
            window_start_ns=window.warmup_start_ns_inclusive,
            arm="causal_cav",
            events=replay_events,
            poses=replay_poses,
        )
        decisions = tuple(simulation.records)
        if tuple(row.event_id for row in decisions) != tuple(
            event.event_id for event in events
        ):
            _fail("current-CAV replay changed event population/order")
        poses_by_id = dict((pose.pose_id, pose) for pose in poses)
        for projected, event, decision in zip(entries, events, decisions):
            used_ids = tuple(decision.used_pose_ids)
            if decision.disposition == "corrected_world_ray":
                if not used_ids or any(pose_id not in poses_by_id for pose_id in used_ids):
                    _fail("current-CAV decision references an unknown pose")
                samples = tuple(PoseSample(
                    poses_by_id[pose_id].timestamp_ns,
                    poses_by_id[pose_id].commit_cycle,
                    poses_by_id[pose_id].quaternion_xyzw,
                ) for pose_id in used_ids)
                recovered = recover_causal_cav(
                    samples, event.timestamp_ns, decision.occurrence_cycle
                )
                if recovered.mode not in (RecoveryMode.CAV, RecoveryMode.ZOH):
                    _fail("public pose recovery disagrees with current-CAV replay")
                expected_mode = (
                    RecoveryMode.CAV
                    if decision.disposition_reason == "causal_cav"
                    else RecoveryMode.ZOH
                )
                if recovered.mode is not expected_mode or recovered.quaternion_xyzw is None:
                    _fail("public pose recovery mode differs from current-CAV replay")
                ray = rotate_sensor_ray_to_world(
                    recovered.quaternion_xyzw, event.sensor_ray
                )
                frame = WORLD_FRAME
                mode = recovered.mode
            elif decision.disposition == "raw_bypass":
                ray = tuple(event.sensor_ray)
                frame = SENSOR_FIXED_FRAME
                mode = RecoveryMode.BYPASS
            else:
                _fail("current-CAV disposition is unknown")
            output_by_id[event.event_id] = CAVRayProjection(
                event_id=event.event_id,
                source_ordinal=projected.source_ordinal,
                cav_occurrence_cycle=decision.occurrence_cycle,
                recovery_mode=mode,
                coordinate_frame=frame,
                ray_xyz=ray,
                used_pose_ids=used_ids,
            )
    return tuple(output_by_id[row.neutral_input.event_id] for row in population)


def project_bridge_bundle_to_cav(
    bundle: BridgeBundle,
    registry: Sequence[NeutralRegistryWindow],
    pose_streams: Mapping[str, Sequence[NeutralPoseInput]],
) -> BridgeCAVProjection:
    """Project all delivered bridge views through one score-free CAV path.

    The function intentionally does not call the current-CAV evaluator,
    scorer, reference bank, selector, or any label authority.  It runs only
    the closed logical cycle replay and public pose-recovery geometry.
    """

    if type(bundle) is not BridgeBundle:
        _fail("bundle must have the exact BridgeBundle type")
    try:
        registry_rows = _validated_registry(registry)
        registry_ids = tuple(row.window_id for row in registry_rows)
        checked_poses = _validated_poses(pose_streams, registry_ids)
        projected = bundle.project()
        population, sidecar = _neutral_population(projected)
        rays = _geometry(registry_rows, population, checked_poses)
    except CAVAdapterError:
        raise
    except (
        BridgeValidationError,
        CurrentCAVEvaluationError,
        LogicalCycleReplayError,
        GeometryError,
        TypeError,
        ValueError,
        OverflowError,
    ) as error:
        raise CAVAdapterError("CAV bridge adaptation failed: %s" % error) from error

    views = []  # type: List[CAVViewProjection]
    for name in CAV_VIEW_ORDER:
        is_retired = name == "AER_RET"
        views.append(CAVViewProjection(
            view_name=name,
            events=population,
            rays=rays,
            input_coordinate_frame=SENSOR_FIXED_FRAME,
            measurement_class=(
                OBSERVATIONAL_SIDECAR_ASSISTED if is_retired else None
            ),
            latency_semantics=(
                TRANSPORT_LATENCY_SEPARATE if is_retired else None
            ),
            transport_sidecar=sidecar if is_retired else (),
        ))
    return BridgeCAVProjection(tuple(views))


__all__ = (
    "CAV_VIEW_ORDER",
    "OBSERVATIONAL_SIDECAR_ASSISTED",
    "TRANSPORT_LATENCY_SEPARATE",
    "SENSOR_FIXED_FRAME",
    "WORLD_FRAME",
    "CAVAdapterError",
    "ProjectedNeutralEvent",
    "CAVRayProjection",
    "RetireTransportObservation",
    "CAVViewProjection",
    "BridgeCAVProjection",
    "project_bridge_bundle_to_cav",
)
