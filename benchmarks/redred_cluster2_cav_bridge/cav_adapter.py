"""Score-free Cluster2 bridge adapter for the current CAV baseline.

The three delivered-event views share one source-ordinal, occurrence-time
``NeutralEventInput`` population.  AER retirement observations never enter the
geometry clock: they are retained only in the explicitly observational
transport sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    PoseSample,
    RecoveryMode,
    recover_causal_cav,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cycle_replay import (
    LogicalCycleReplayError,
    run_stage3_logical_cycle_model,
)

from .contract import (
    OBSERVATIONAL_JOIN_LABEL,
    SOURCE_EVENT_SCHEMA,
    BridgeBundle,
    BridgeValidationError,
    canonical_event_content_sha256,
    canonical_json_bytes,
)
from .transport_time import (
    TRANSPORT_TIME_SEMANTICS,
    TransportTimeValidationError,
    build_dual_time_event,
)


CAV_VIEW_ORDER = ("RAW4X4_MATCHED", "AER_OCC", "AER_RET")
OBSERVATIONAL_SIDECAR_ASSISTED = "OBSERVATIONAL_SIDECAR_ASSISTED"
SENSOR_FIXED_FRAME = "SENSOR_FIXED"
WORLD_FRAME = "WORLD"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

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
    "retire_row", "retire_col", "physical_retire_timestamp_ns",
    "latency_injected_timestamp_ns", "latency_cycles", "latency_ns",
    "transport_time_semantics", "window_id", "is_query", "polarity",
    "sensor_ray", "causal_pose_source_index", "transform_guard_valid",
    "event_content_sha256",
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


def _fail(message: str) -> None:
    raise CAVAdapterError(message)


def _exact_dataclass(
    value: object, expected_type: type, expected_fields: frozenset, where: str
) -> None:
    if type(value) is not expected_type:
        _fail("%s must have its exact adapter-local type" % where)
    try:
        actual_fields = frozenset(vars(value))
    except TypeError as error:
        raise CAVAdapterError("%s has no dataclass field mapping" % where) from error
    if actual_fields != expected_fields:
        _fail("%s dataclass field set differs" % where)


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        _fail("%s must be a non-negative integer" % where)
    return value  # type: ignore[return-value]


def _signed_int(value: object, where: str) -> int:
    if type(value) is not int:
        _fail("%s must be an integer" % where)
    return value  # type: ignore[return-value]


def _nonempty_text(value: object, where: str) -> str:
    if type(value) is not str or not value:
        _fail("%s must be non-empty text" % where)
    return value  # type: ignore[return-value]


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("%s must be lowercase SHA-256" % where)
    return value  # type: ignore[return-value]


def _finite_tuple(value: object, length: int, where: str) -> Tuple[float, ...]:
    if type(value) not in (tuple, list) or len(value) != length:  # type: ignore[arg-type]
        _fail("%s must contain exactly %d components" % (where, length))
    result = []
    for index, component in enumerate(value):  # type: ignore[union-attr]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            _fail("%s[%d] must be finite" % (where, index))
        converted = float(component)
        if not math.isfinite(converted):
            _fail("%s[%d] must be finite" % (where, index))
        result.append(converted)
    return tuple(result)


def _unit_tuple(
    value: object, length: int, where: str
) -> Tuple[float, ...]:
    result = _finite_tuple(value, length, where)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        _fail("%s must be normalized" % where)
    return result


def _pose_content_sha256(
    pose_id: int, timestamp_ns: int, quaternion_xyzw: Sequence[float]
) -> str:
    return hashlib.sha256(canonical_json_bytes({
        "pose_id": pose_id,
        "timestamp_ns": timestamp_ns,
        "quaternion_xyzw": list(quaternion_xyzw),
    })).hexdigest()


@dataclass(frozen=True)
class NeutralRegistryWindow:
    """Evaluator-free exact registry input for one CAV window."""

    window_id: str
    warmup_start_ns_inclusive: int
    query_start_ns_inclusive: int
    query_end_ns_exclusive: int

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralRegistryWindow, _REGISTRY_FIELDS, "registry")
        object.__setattr__(self, "window_id", _nonempty_text(self.window_id, "window_id"))
        for field in (
            "warmup_start_ns_inclusive",
            "query_start_ns_inclusive",
            "query_end_ns_exclusive",
        ):
            object.__setattr__(self, field, _nonnegative_int(getattr(self, field), field))
        if not (
            self.warmup_start_ns_inclusive
            < self.query_start_ns_inclusive
            < self.query_end_ns_exclusive
        ):
            _fail("neutral registry bounds are not increasing")


@dataclass(frozen=True)
class NeutralEventInput:
    """Evaluator-free exact event input with hash-bound geometry."""

    event_id: int
    timestamp_ns: int
    polarity: int
    is_query: bool
    sensor_ray: Tuple[float, float, float]
    causal_pose_source_index: int
    event_content_sha256: str
    transform_guard_valid: bool = True

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralEventInput, frozenset((
            "event_id", "timestamp_ns", "polarity", "is_query", "sensor_ray",
            "causal_pose_source_index", "event_content_sha256",
            "transform_guard_valid",
        )), "event")
        object.__setattr__(self, "event_id", _nonnegative_int(self.event_id, "event_id"))
        object.__setattr__(self, "timestamp_ns", _nonnegative_int(self.timestamp_ns, "event timestamp"))
        if type(self.polarity) is not int or self.polarity not in (0, 1):
            _fail("event polarity must be integer zero or one")
        if type(self.is_query) is not bool:
            _fail("event is_query must be bool")
        object.__setattr__(self, "sensor_ray", _unit_tuple(self.sensor_ray, 3, "sensor ray"))
        object.__setattr__(
            self, "causal_pose_source_index",
            _nonnegative_int(self.causal_pose_source_index, "causal pose source index"),
        )
        if type(self.transform_guard_valid) is not bool:
            _fail("transform_guard_valid must be bool")
        supplied = _sha256(self.event_content_sha256, "event content digest")
        expected = canonical_event_content_sha256(
            self.event_id, self.timestamp_ns, self.polarity, self.is_query,
            self.sensor_ray, self.causal_pose_source_index,
            self.transform_guard_valid,
        )
        if supplied != expected:
            _fail("event content digest differs")


@dataclass(frozen=True)
class NeutralPoseInput:
    """Evaluator-free exact pose packet for logical CAV replay."""

    pose_id: int
    timestamp_ns: int
    commit_cycle: int
    quaternion_xyzw: Tuple[float, float, float, float]
    pose_sha256: str
    value_valid: bool = True
    arithmetic_valid: bool = True

    def __post_init__(self) -> None:
        _exact_dataclass(self, NeutralPoseInput, _POSE_FIELDS, "pose")
        object.__setattr__(self, "pose_id", _nonnegative_int(self.pose_id, "pose_id"))
        object.__setattr__(self, "timestamp_ns", _nonnegative_int(self.timestamp_ns, "pose timestamp"))
        object.__setattr__(self, "commit_cycle", _signed_int(self.commit_cycle, "commit cycle"))
        object.__setattr__(
            self, "quaternion_xyzw",
            _unit_tuple(self.quaternion_xyzw, 4, "pose quaternion"),
        )
        supplied = _sha256(self.pose_sha256, "pose digest")
        if type(self.value_valid) is not bool or type(self.arithmetic_valid) is not bool:
            _fail("pose validity flags must be bool")
        if supplied != _pose_content_sha256(
            self.pose_id, self.timestamp_ns, self.quaternion_xyzw
        ):
            _fail("pose content digest differs")


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
    event_timestamp_ns: int
    latency_ns: int
    latency_injected_timestamp_ns: int
    semantics_label: str
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


def _exact_fields(
    row: object, fields: frozenset, where: str
) -> Mapping[str, object]:
    if not isinstance(row, Mapping) or frozenset(row) != fields:
        _fail("%s field schema differs" % where)
    return row  # type: ignore[return-value]


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


def _bounded_index(value: object, maximum: int, where: str) -> int:
    number = _nonnegative_int(value, where)
    if number > maximum:
        _fail("%s must be in [0, %d]" % (where, maximum))
    return number


def _occurrence_cycle(timestamp_ns: int, zero_ns: int, period_ps: int) -> int:
    if timestamp_ns < zero_ns:
        _fail("source timestamp precedes AER cycle zero")
    delta_ps = (timestamp_ns - zero_ns) * 1000
    return (delta_ps + period_ps - 1) // period_ps


def _manifest_timebase(bundle: BridgeBundle) -> Tuple[int, int]:
    manifest = bundle.manifest
    if not isinstance(manifest, Mapping):
        _fail("BridgeBundle manifest must remain a mapping")
    period_ps = _nonnegative_int(
        manifest.get("aer_clock_period_ps"), "manifest aer_clock_period_ps"
    )
    zero_ns = _nonnegative_int(
        manifest.get("aer_cycle_zero_timestamp_ns"),
        "manifest aer_cycle_zero_timestamp_ns",
    )
    if period_ps == 0 or period_ps % 1000:
        _fail("manifest AER clock must be a positive whole nanosecond")
    return zero_ns, period_ps


def _validate_raw_all(
    rows: Tuple[Mapping[str, object], ...], zero_ns: int, period_ps: int
) -> None:
    identifiers = set()
    occurrence_slots = set()
    previous_timestamp = -1
    for expected_ordinal, row in enumerate(rows):
        if row["schema"] != SOURCE_EVENT_SCHEMA:
            _fail("RAW4X4_ALL source schema differs")
        event_id = _nonnegative_int(row["event_id"], "RAW4X4_ALL event_id")
        if event_id in identifiers:
            _fail("RAW4X4_ALL event IDs are duplicated")
        identifiers.add(event_id)
        ordinal = _nonnegative_int(row["ordinal"], "RAW4X4_ALL ordinal")
        if ordinal != expected_ordinal:
            _fail("RAW4X4_ALL ordinals must be contiguous")
        source_index = _bounded_index(
            row["source_index"], 15, "RAW4X4_ALL source_index"
        )
        timestamp = _nonnegative_int(
            row["timestamp_ns"], "RAW4X4_ALL timestamp_ns"
        )
        if timestamp < previous_timestamp:
            _fail("RAW4X4_ALL timestamps must be nondecreasing")
        previous_timestamp = timestamp
        NeutralEventInput(
            row["event_id"], row["timestamp_ns"], row["polarity"],
            row["is_query"], tuple(row["sensor_ray"]),  # type: ignore[arg-type]
            row["causal_pose_source_index"], row["event_content_sha256"],
            row["transform_guard_valid"],
        )
        _nonempty_text(row["window_id"], "RAW4X4_ALL window_id")
        cycle = _occurrence_cycle(timestamp, zero_ns, period_ps)
        slot = (source_index, cycle)
        if slot in occurrence_slots:
            _fail("RAW4X4_ALL has a duplicate native occurrence slot")
        occurrence_slots.add(slot)


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
    zero_ns: int,
    period_ps: int,
) -> Tuple[
    Tuple[ProjectedNeutralEvent, ...],
    Tuple[RetireTransportObservation, ...],
]:
    if frozenset(projected) != frozenset((
        "RAW4X4_ALL", "RAW4X4_MATCHED", "AER_OCC", "AER_RET"
    )):
        _fail("BridgeBundle projection view set differs")
    all_rows = tuple(
        _exact_fields(row, _RAW_FIELDS, "RAW4X4_ALL row")
        for row in projected["RAW4X4_ALL"]
    )
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
    _validate_raw_all(all_rows, zero_ns, period_ps)

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
    dual_time_by_id = {}
    native_rows_by_cycle_lane = {}
    native_slots = set()
    for row in ret_rows:
        event_id = _nonnegative_int(row["event_id"], "retire event_id")
        source_index = _bounded_index(
            row["source_index"], 15, "retire source_index"
        )
        occurrence_cycle = _nonnegative_int(
            row["occurrence_cycle"], "retire occurrence_cycle"
        )
        retire_cycle = _nonnegative_int(row["retire_cycle"], "retire_cycle")
        lane = _bounded_index(row["retire_native_lane"], 1, "retire_native_lane")
        retire_row = _bounded_index(row["retire_row"], 3, "retire_row")
        retire_col = _bounded_index(row["retire_col"], 3, "retire_col")
        if source_index != retire_row * 4 + retire_col:
            _fail("retire native coordinate differs from source_index")
        if (lane == 0 and retire_row not in (0, 1, 2)) or (
            lane == 1 and retire_row not in (0, 2, 3)
        ):
            _fail("retire native lane cannot emit the source row")
        bitmap_key = (retire_cycle, lane)
        prior_row = native_rows_by_cycle_lane.get(bitmap_key)
        if prior_row is not None and prior_row != retire_row:
            _fail("one native lane-cycle contains more than one row")
        other_row = native_rows_by_cycle_lane.get((retire_cycle, 1 - lane))
        if other_row == retire_row:
            _fail("two native lanes select the same row in one cycle")
        native_rows_by_cycle_lane[bitmap_key] = retire_row
        native_slot = (retire_cycle, lane, retire_col)
        if native_slot in native_slots:
            _fail("two events occupy one native cycle-lane-column slot")
        native_slots.add(native_slot)
        event_timestamp = _nonnegative_int(
            row["occurrence_timestamp_ns"], "occurrence_timestamp_ns"
        )
        physical_timestamp = _nonnegative_int(
            row["physical_retire_timestamp_ns"],
            "contract physical_retire_timestamp_ns",
        )
        expected_physical = zero_ns + retire_cycle * (period_ps // 1000)
        if physical_timestamp != expected_physical:
            _fail("contract physical retire timestamp differs from manifest")
        dual_time = build_dual_time_event(
            event_timestamp, occurrence_cycle, retire_cycle, period_ps
        )
        supplied_latency_cycles = _nonnegative_int(
            row["latency_cycles"], "contract latency_cycles"
        )
        supplied_latency_ns = _nonnegative_int(
            row["latency_ns"], "contract latency_ns"
        )
        supplied_injected_timestamp = _nonnegative_int(
            row["latency_injected_timestamp_ns"],
            "contract latency_injected_timestamp_ns",
        )
        if row["transport_time_semantics"] != dual_time.semantics_label:
            _fail("contract transport-time semantics label differs")
        if (
            supplied_latency_cycles != dual_time.latency_cycles
            or supplied_latency_ns != dual_time.latency_ns
            or supplied_injected_timestamp
            != dual_time.latency_injected_timestamp_ns
        ):
            _fail("contract latency-injected transport time differs")
        dual_time_by_id[event_id] = dual_time

    expected_retire_order = tuple(sorted(
        ret_rows,
        key=lambda row: (
            row["retire_cycle"], row["retire_native_lane"],
            row["retire_col"], row["event_id"],
        ),
    ))
    if ret_rows != expected_retire_order:
        _fail("AER_RET rows do not retain canonical retirement order")

    source_ordinals = {}  # type: Dict[int, int]
    common = []  # type: List[ProjectedNeutralEvent]
    previous_ordinal = -1
    last_retire_by_source = {}  # type: Dict[int, int]

    for row in raw_rows:
        if row["schema"] != SOURCE_EVENT_SCHEMA:
            _fail("RAW4X4_MATCHED source schema differs")
        event_id = _nonnegative_int(row["event_id"], "event_id")
        ordinal = _nonnegative_int(row["ordinal"], "source ordinal")
        if ordinal <= previous_ordinal:
            _fail("delivered source ordinals must be strictly increasing")
        previous_ordinal = ordinal
        if ordinal >= len(all_rows) or row != all_rows[ordinal]:
            _fail("RAW4X4_MATCHED row differs from RAW4X4_ALL source ordinal")
        source_index = _bounded_index(row["source_index"], 15, "source index")
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
        timestamp = _nonnegative_int(row["timestamp_ns"], "event timestamp_ns")
        if cycle != _occurrence_cycle(timestamp, zero_ns, period_ps):
            _fail("AER occurrence cycle differs from manifest ceil mapping")
        if retired["occurrence_cycle"] != cycle:
            _fail("AER_RET occurrence cycle differs from AER_OCC")
        retire_cycle = _nonnegative_int(retired["retire_cycle"], "retire_cycle")
        previous_retire = last_retire_by_source.get(source_index)
        if previous_retire is not None and retire_cycle <= previous_retire:
            _fail("per-source FIFO retire order differs")
        last_retire_by_source[source_index] = retire_cycle
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

    sidecar = []  # type: List[RetireTransportObservation]
    for retire_ordinal, row in enumerate(ret_rows):
        event_id = _nonnegative_int(row["event_id"], "retire event_id")
        dual_time = dual_time_by_id[event_id]
        sidecar.append(RetireTransportObservation(
            event_id=event_id,
            source_ordinal=source_ordinals[event_id],
            retire_ordinal=retire_ordinal,
            occurrence_cycle=dual_time.occurrence_cycle,
            retire_cycle=dual_time.retire_cycle,
            latency_cycles=dual_time.latency_cycles,
            event_timestamp_ns=dual_time.event_timestamp_ns,
            latency_ns=dual_time.latency_ns,
            latency_injected_timestamp_ns=(
                dual_time.latency_injected_timestamp_ns
            ),
            semantics_label=dual_time.semantics_label,
            retire_native_lane=row["retire_native_lane"],  # type: ignore[arg-type]
            retire_row=row["retire_row"],  # type: ignore[arg-type]
            retire_col=row["retire_col"],  # type: ignore[arg-type]
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
        zero_ns, period_ps = _manifest_timebase(bundle)
        population, sidecar = _neutral_population(
            projected, zero_ns, period_ps
        )
        rays = _geometry(registry_rows, population, checked_poses)
    except CAVAdapterError:
        raise
    except (
        BridgeValidationError,
        LogicalCycleReplayError,
        GeometryError,
        TransportTimeValidationError,
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
                TRANSPORT_TIME_SEMANTICS if is_retired else None
            ),
            transport_sidecar=sidecar if is_retired else (),
        ))
    return BridgeCAVProjection(tuple(views))


__all__ = (
    "CAV_VIEW_ORDER",
    "OBSERVATIONAL_SIDECAR_ASSISTED",
    "TRANSPORT_TIME_SEMANTICS",
    "SENSOR_FIXED_FRAME",
    "WORLD_FRAME",
    "CAVAdapterError",
    "NeutralRegistryWindow",
    "NeutralEventInput",
    "NeutralPoseInput",
    "ProjectedNeutralEvent",
    "CAVRayProjection",
    "RetireTransportObservation",
    "CAVViewProjection",
    "BridgeCAVProjection",
    "project_bridge_bundle_to_cav",
)
