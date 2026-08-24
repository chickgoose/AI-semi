"""Exact functional assay joining official UZH events to native observations.

The core accepts already validated in-memory objects.  It performs no file,
path, receipt, scorer, or label I/O.  Original UZH timestamps drive the CAV
geometry path; native occurrence and retirement cycles remain an observational
2 ns transport sidecar and can never replace a geometry timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

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

from .cav_adapter import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
)
from .functional_source import (
    FunctionalSourceBundle,
    NativeEventIdentity,
)
from .native_outcome_bundle import NativeOutcome
from .transport_time import (
    DualTimeEvent,
    TRANSPORT_TIME_SEMANTICS,
    TransportTimeValidationError,
    build_dual_time_event,
    validate_dual_time_event,
)
from .world_grid import (
    COORDINATE_CONVENTION,
    WorldGridCoordinate,
    WorldGridError,
    quantize_world_ray,
)


EXPECTED_EVENT_COUNT = 8_503
EXPECTED_POSE_COUNT = 11_883
EXPECTED_CAUSAL_CAV_COUNT = 8_420
EXPECTED_ZOH_COUNT = 0
EXPECTED_BYPASS_COUNT = 83
GRID_WIDTH = 512
GRID_HEIGHT = 256
NATIVE_CLOCK_PERIOD_PS = 2_000
LATENCY_SIDECAR_ONLY = "LATENCY_SIDECAR_ONLY"
SIDECAR_ORDER = ("retire_cycle", "event_id")

RAW_CAV_VIEW = "RAW-CAV"
AER_OCC_CAV_VIEW = "AER-OCC-CAV"
AER_RET_CAV_VIEW = "AER-RET-CAV"
VIEW_ORDER = (RAW_CAV_VIEW, AER_OCC_CAV_VIEW, AER_RET_CAV_VIEW)
WORLD_FRAME = "WORLD"
SENSOR_FIXED_FRAME = "SENSOR_FIXED"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GEOMETRY_FIELDS = frozenset((
    "event_id", "event_timestamp_ns", "cav_occurrence_cycle",
    "recovery_mode", "coordinate_frame", "ray_xyz", "used_pose_ids",
    "world_grid",
))
_RETIRE_FIELDS = frozenset((
    "event_id", "source_index", "native_occurrence_cycle", "dual_time",
))
_VIEW_FIELDS = frozenset((
    "view_name", "geometry", "geometry_sha256", "transport_sidecar",
))
_STATISTICS_FIELDS = frozenset((
    "event_count", "pose_count", "exact_join_count", "decision_count",
    "mode_counts", "frame_counts", "latency_histogram", "grid_width",
    "grid_height", "grid_quantized_count", "grid_unique_count",
    "grid_x_min", "grid_x_max", "grid_y_min", "grid_y_max",
    "grid_index_min", "grid_index_max", "join_identity_sha256",
    "geometry_sha256", "retire_sidecar_sha256", "grid_sha256",
    "view_geometry_sha256", "transport_time_semantics",
    "coordinate_convention",
))
_RESULT_FIELDS = frozenset(("views", "statistics"))


class FunctionalAssayError(ValueError):
    """The exact functional population or a derived result is inconsistent."""


def _fail(message: str) -> None:
    raise FunctionalAssayError(message)


def _exact_fields(
    value: object, expected_type: type, expected_fields: frozenset, where: str
) -> None:
    if type(value) is not expected_type:
        _fail("%s must have its exact functional-assay type" % where)
    try:
        fields = frozenset(vars(value))
    except TypeError as error:
        raise FunctionalAssayError("%s has no field mapping" % where) from error
    if fields != expected_fields:
        _fail("%s field schema differs" % where)


def _nonnegative_int(value: object, where: str) -> int:
    if type(value) is not int or value < 0:
        _fail("%s must be a non-negative integer" % where)
    return value  # type: ignore[return-value]


def _positive_int(value: object, where: str) -> int:
    result = _nonnegative_int(value, where)
    if result == 0:
        _fail("%s must be positive" % where)
    return result


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail("%s must be a lowercase full SHA-256" % where)
    return value  # type: ignore[return-value]


def _canonical_sha256(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as error:
        raise FunctionalAssayError("assay result is not canonicalizable") from error
    return hashlib.sha256(payload).hexdigest()


def _finite_unit_ray(value: object, where: str) -> Tuple[float, float, float]:
    if type(value) is not tuple or len(value) != 3:  # type: ignore[arg-type]
        _fail("%s must be an exact three-component tuple" % where)
    result = []
    for index, component in enumerate(value):  # type: ignore[union-attr]
        if type(component) is not float or not math.isfinite(component):
            _fail("%s[%d] must be an exact finite float" % (where, index))
        result.append(component)
    norm = math.sqrt(math.fsum(component * component for component in result))
    if not math.isfinite(norm) or abs(norm - 1.0) > 1.0e-9:
        _fail("%s must be normalized" % where)
    return tuple(result)  # type: ignore[return-value]


def _exact_counter(
    value: object, expected_keys: Tuple[object, ...], where: str
) -> Tuple[Tuple[object, int], ...]:
    if type(value) is not tuple:
        _fail("%s keys/order differ" % where)
    if len(value) != len(expected_keys):  # type: ignore[arg-type]
        _fail("%s keys/order differ" % where)
    result = []
    for index, pair in enumerate(value):  # type: ignore[union-attr]
        if type(pair) is not tuple or len(pair) != 2:
            _fail("%s rows must be exact key/count tuples" % where)
        key, count = pair
        expected_key = expected_keys[index]
        if type(key) is not type(expected_key) or key != expected_key:
            _fail("%s keys/order differ" % where)
        result.append((key, _nonnegative_int(count, "%s count" % where)))
    return tuple(result)


@dataclass(frozen=True)
class FunctionalGeometryRecord:
    """One geometry-only result; it contains no native transport time."""

    event_id: int
    event_timestamp_ns: int
    cav_occurrence_cycle: int
    recovery_mode: RecoveryMode
    coordinate_frame: str
    ray_xyz: Tuple[float, float, float]
    used_pose_ids: Tuple[int, ...]
    world_grid: Optional[WorldGridCoordinate]

    def __post_init__(self) -> None:
        _exact_fields(self, FunctionalGeometryRecord, _GEOMETRY_FIELDS, "geometry")
        _nonnegative_int(self.event_id, "geometry event_id")
        _nonnegative_int(self.event_timestamp_ns, "geometry event_timestamp_ns")
        _nonnegative_int(self.cav_occurrence_cycle, "CAV occurrence cycle")
        if type(self.recovery_mode) is not RecoveryMode:
            _fail("geometry recovery_mode must be exact RecoveryMode")
        if type(self.coordinate_frame) is not str:
            _fail("geometry coordinate_frame must be exact str")
        ray = _finite_unit_ray(self.ray_xyz, "geometry ray")
        object.__setattr__(self, "ray_xyz", ray)
        if type(self.used_pose_ids) is not tuple or any(
            type(value) is not int or value < 0 for value in self.used_pose_ids
        ):
            _fail("used_pose_ids must be non-negative integer tuple")
        if len(set(self.used_pose_ids)) != len(self.used_pose_ids):
            _fail("used_pose_ids repeat")
        if self.recovery_mode in (RecoveryMode.CAV, RecoveryMode.ZOH):
            if self.coordinate_frame != WORLD_FRAME:
                _fail("corrected geometry must use WORLD frame")
            if type(self.world_grid) is not WorldGridCoordinate:
                _fail("WORLD geometry must have an exact grid coordinate")
            try:
                checked_grid = WorldGridCoordinate(**vars(self.world_grid))
                expected_grid = quantize_world_ray(
                    self.ray_xyz, GRID_WIDTH, GRID_HEIGHT
                )
            except (TypeError, WorldGridError) as error:
                raise FunctionalAssayError(
                    "WORLD geometry grid cannot be exactly revalidated"
                ) from error
            if type(checked_grid.coordinate_convention) is not str:
                _fail("WORLD grid coordinate convention must be exact str")
            if self.world_grid != expected_grid:
                _fail("WORLD grid differs from quantized geometry ray")
        elif self.recovery_mode is RecoveryMode.BYPASS:
            if self.coordinate_frame != SENSOR_FIXED_FRAME:
                _fail("bypass geometry must use SENSOR_FIXED frame")
            if self.world_grid is not None:
                _fail("SENSOR_FIXED bypass must be excluded from the world grid")
        else:
            _fail("unknown recovery mode")


@dataclass(frozen=True)
class FunctionalRetireObservation:
    """Public latency-only observation, without sealed native retire geometry.

    ``NativeOutcome`` exposes event/source/occurrence/retire/latency only.
    Native retire lane, row, and column remain in the separate sealed evidence;
    this core neither reads nor reconstructs them.
    """

    event_id: int
    source_index: int
    native_occurrence_cycle: int
    dual_time: DualTimeEvent

    def __post_init__(self) -> None:
        _exact_fields(self, FunctionalRetireObservation, _RETIRE_FIELDS, "sidecar")
        _nonnegative_int(self.event_id, "sidecar event_id")
        source = _nonnegative_int(self.source_index, "sidecar source_index")
        if source > 15:
            _fail("sidecar source_index exceeds 15")
        occurrence = _nonnegative_int(
            self.native_occurrence_cycle, "sidecar native occurrence cycle"
        )
        if type(self.dual_time) is not DualTimeEvent:
            _fail("sidecar dual_time must be exact DualTimeEvent")
        try:
            validate_dual_time_event(self.dual_time)
        except TransportTimeValidationError as error:
            raise FunctionalAssayError(
                "sidecar DualTimeEvent validation failed"
            ) from error
        if type(self.dual_time.semantics_label) is not str:
            _fail("sidecar transport semantics must be exact str")
        if self.dual_time.occurrence_cycle != occurrence:
            _fail("sidecar native occurrence differs from DualTimeEvent")
        if self.dual_time.clock_period_ps != NATIVE_CLOCK_PERIOD_PS:
            _fail("sidecar transport clock differs from 2 ns")
        if self.dual_time.semantics_label != TRANSPORT_TIME_SEMANTICS:
            _fail("sidecar transport semantics differ")

    @property
    def event_timestamp_ns(self) -> int:
        return self.dual_time.event_timestamp_ns

    @property
    def retire_cycle(self) -> int:
        return self.dual_time.retire_cycle

    @property
    def latency_cycles(self) -> int:
        return self.dual_time.latency_cycles

    @property
    def latency_ns(self) -> int:
        return self.dual_time.latency_ns

    @property
    def latency_injected_timestamp_ns(self) -> int:
        return self.dual_time.latency_injected_timestamp_ns


@dataclass(frozen=True)
class FunctionalAssayView:
    """One named view over the shared geometry, optionally with retire sidecar."""

    view_name: str
    geometry: Tuple[FunctionalGeometryRecord, ...]
    geometry_sha256: str
    transport_sidecar: Tuple[FunctionalRetireObservation, ...] = ()

    def __post_init__(self) -> None:
        _exact_fields(self, FunctionalAssayView, _VIEW_FIELDS, "view")
        if type(self.view_name) is not str:
            _fail("view_name must be exact str")
        if self.view_name not in VIEW_ORDER:
            _fail("unknown functional assay view")
        if type(self.geometry) is not tuple or not self.geometry or any(
            type(row) is not FunctionalGeometryRecord for row in self.geometry
        ):
            _fail("view geometry must be a non-empty exact record tuple")
        _sha256(self.geometry_sha256, "view geometry digest")
        if type(self.transport_sidecar) is not tuple or any(
            type(row) is not FunctionalRetireObservation
            for row in self.transport_sidecar
        ):
            _fail("view transport sidecar must be an exact tuple")
        if self.view_name == AER_RET_CAV_VIEW:
            if not self.transport_sidecar:
                _fail("AER-RET-CAV must retain its observational sidecar")
        elif self.transport_sidecar:
            _fail("only AER-RET-CAV may carry a transport sidecar")

    @property
    def sidecar_semantics(self) -> Optional[str]:
        """Identify the intentionally limited public transport projection."""

        if self.view_name == AER_RET_CAV_VIEW:
            return LATENCY_SIDECAR_ONLY
        return None

    @property
    def latency_sidecar(self) -> Tuple[FunctionalRetireObservation, ...]:
        """Return the public latency sidecar without implying native geometry."""

        return self.transport_sidecar


@dataclass(frozen=True)
class FunctionalAssayStatistics:
    """Canonical counts, ranges, histograms, and population digests."""

    event_count: int
    pose_count: int
    exact_join_count: int
    decision_count: int
    mode_counts: Tuple[Tuple[str, int], ...]
    frame_counts: Tuple[Tuple[str, int], ...]
    latency_histogram: Tuple[Tuple[int, int], ...]
    grid_width: int
    grid_height: int
    grid_quantized_count: int
    grid_unique_count: int
    grid_x_min: int
    grid_x_max: int
    grid_y_min: int
    grid_y_max: int
    grid_index_min: int
    grid_index_max: int
    join_identity_sha256: str
    geometry_sha256: str
    retire_sidecar_sha256: str
    grid_sha256: str
    view_geometry_sha256: Tuple[Tuple[str, str], ...]
    transport_time_semantics: str
    coordinate_convention: str

    def __post_init__(self) -> None:
        _exact_fields(
            self, FunctionalAssayStatistics, _STATISTICS_FIELDS, "statistics"
        )
        counts = (
            self.event_count,
            self.pose_count,
            self.exact_join_count,
            self.decision_count,
            self.grid_width,
            self.grid_height,
            self.grid_quantized_count,
            self.grid_unique_count,
            self.grid_x_min,
            self.grid_x_max,
            self.grid_y_min,
            self.grid_y_max,
            self.grid_index_min,
            self.grid_index_max,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            _fail("statistics counts must be non-negative integers")
        if (
            self.event_count != EXPECTED_EVENT_COUNT
            or self.exact_join_count != EXPECTED_EVENT_COUNT
            or self.decision_count != EXPECTED_EVENT_COUNT
        ):
            _fail("statistics event/join/decision count differs from official")
        if self.pose_count != EXPECTED_POSE_COUNT:
            _fail("statistics pose count differs from official")
        modes = _exact_counter(
            self.mode_counts,
            (
                RecoveryMode.CAV.value,
                RecoveryMode.ZOH.value,
                RecoveryMode.BYPASS.value,
            ),
            "mode_counts",
        )
        frames = _exact_counter(
            self.frame_counts, (WORLD_FRAME, SENSOR_FIXED_FRAME), "frame_counts"
        )
        if modes != (
            (RecoveryMode.CAV.value, EXPECTED_CAUSAL_CAV_COUNT),
            (RecoveryMode.ZOH.value, EXPECTED_ZOH_COUNT),
            (RecoveryMode.BYPASS.value, EXPECTED_BYPASS_COUNT),
        ):
            _fail("statistics mode counts differ from official")
        if frames != (
            (WORLD_FRAME, EXPECTED_CAUSAL_CAV_COUNT + EXPECTED_ZOH_COUNT),
            (SENSOR_FIXED_FRAME, EXPECTED_BYPASS_COUNT),
        ):
            _fail("statistics frame counts differ from official")
        if sum(value for _, value in modes) != self.decision_count:
            _fail("mode counts do not sum to decision_count")
        if sum(value for _, value in frames) != self.decision_count:
            _fail("frame counts do not sum to decision_count")
        if type(self.latency_histogram) is not tuple or not self.latency_histogram:
            _fail("latency histogram must be non-empty")
        prior = -1
        for pair in self.latency_histogram:
            if type(pair) is not tuple or len(pair) != 2:
                _fail("latency histogram rows must be exact tuples")
            latency, count = pair
            latency = _nonnegative_int(latency, "latency histogram key")
            _positive_int(count, "latency histogram count")
            if latency <= prior:
                _fail("latency histogram keys must be strictly increasing")
            prior = latency
        if sum(value for _, value in self.latency_histogram) != self.event_count:
            _fail("latency histogram does not cover the event population")
        if (
            type(self.grid_width) is not int
            or type(self.grid_height) is not int
            or self.grid_width != GRID_WIDTH
            or self.grid_height != GRID_HEIGHT
        ):
            _fail("statistics grid dimensions differ from 512x256")
        if self.grid_quantized_count != dict(frames)[WORLD_FRAME]:
            _fail("only WORLD records may be grid quantized")
        if not 0 < self.grid_unique_count <= self.grid_quantized_count:
            _fail("grid unique count is outside the quantized population")
        if not (
            0 <= self.grid_x_min <= self.grid_x_max < self.grid_width
            and 0 <= self.grid_y_min <= self.grid_y_max < self.grid_height
            and 0 <= self.grid_index_min <= self.grid_index_max
            < self.grid_width * self.grid_height
        ):
            _fail("grid range lies outside 512x256")
        for name in (
            "join_identity_sha256", "geometry_sha256",
            "retire_sidecar_sha256", "grid_sha256",
        ):
            _sha256(getattr(self, name), name)
        if type(self.view_geometry_sha256) is not tuple or len(
            self.view_geometry_sha256
        ) != len(VIEW_ORDER):
            _fail("view geometry digest names/order differ")
        for index, pair in enumerate(self.view_geometry_sha256):
            if type(pair) is not tuple or len(pair) != 2:
                _fail("view geometry digest rows must be exact tuples")
            name, digest = pair
            if type(name) is not str or name != VIEW_ORDER[index]:
                _fail("view geometry digest names/order differ")
            if _sha256(digest, "view geometry digest") != self.geometry_sha256:
                _fail("three view geometry digests differ")
        if (
            type(self.transport_time_semantics) is not str
            or self.transport_time_semantics != TRANSPORT_TIME_SEMANTICS
        ):
            _fail("statistics transport semantics differ")
        if (
            type(self.coordinate_convention) is not str
            or self.coordinate_convention != COORDINATE_CONVENTION
        ):
            _fail("statistics coordinate convention differs")


@dataclass(frozen=True)
class FunctionalAssayResult:
    """Structurally consistent three-view result over one geometry population.

    Construction checks internal consistency only.  Bind a result to source
    inputs with :func:`validate_functional_assay_result`.
    """

    views: Tuple[FunctionalAssayView, ...]
    statistics: FunctionalAssayStatistics

    def __post_init__(self) -> None:
        _exact_fields(self, FunctionalAssayResult, _RESULT_FIELDS, "result")
        if type(self.views) is not tuple or tuple(
            view.view_name for view in self.views
        ) != VIEW_ORDER or any(
            type(view) is not FunctionalAssayView for view in self.views
        ):
            _fail("result views differ from the exact three-view order")
        if type(self.statistics) is not FunctionalAssayStatistics:
            _fail("result statistics must have exact type")
        for view in self.views:
            FunctionalAssayView(**vars(view))
        FunctionalAssayStatistics(**vars(self.statistics))
        geometry = self.views[0].geometry
        digest = self.views[0].geometry_sha256
        if any(view.geometry is not geometry for view in self.views):
            _fail("three views must share one geometry tuple")
        if any(view.geometry_sha256 != digest for view in self.views):
            _fail("three view geometry digests differ")
        if digest != self.statistics.geometry_sha256:
            _fail("view and statistics geometry digests differ")
        if len(geometry) != self.statistics.decision_count:
            _fail("geometry cardinality differs from decision_count")
        sidecar = self.views[2].transport_sidecar
        if len(sidecar) != self.statistics.exact_join_count:
            _fail("retire sidecar cardinality differs from exact join")
        for row in geometry:
            FunctionalGeometryRecord(**vars(row))
        for row in sidecar:
            FunctionalRetireObservation(**vars(row))

        expected_ids = set(range(EXPECTED_EVENT_COUNT))
        geometry_ids = tuple(row.event_id for row in geometry)
        sidecar_ids = tuple(row.event_id for row in sidecar)
        if len(set(geometry_ids)) != len(geometry_ids) or set(
            geometry_ids
        ) != expected_ids:
            _fail("result geometry event IDs are not exactly contiguous")
        if len(set(sidecar_ids)) != len(sidecar_ids) or set(
            sidecar_ids
        ) != expected_ids:
            _fail("result sidecar event IDs are not exactly contiguous")
        native_slots = tuple(
            (row.native_occurrence_cycle, row.source_index) for row in sidecar
        )
        if len(set(native_slots)) != len(native_slots):
            _fail("result sidecar native occurrence/source slots repeat")
        if any(
            (right.event_timestamp_ns, right.event_id)
            <= (left.event_timestamp_ns, left.event_id)
            for left, right in zip(geometry, geometry[1:])
        ):
            _fail("result geometry is not strict timestamp-then-event-ID order")
        if tuple((row.retire_cycle, row.event_id) for row in sidecar) != tuple(
            sorted((row.retire_cycle, row.event_id) for row in sidecar)
        ):
            _fail("result sidecar differs from SIDECAR_ORDER")
        geometry_by_id = dict((row.event_id, row) for row in geometry)
        if any(
            row.event_timestamp_ns
            != geometry_by_id[row.event_id].event_timestamp_ns
            for row in sidecar
        ):
            _fail("result sidecar event timestamp differs from geometry")

        derived = _derive_row_statistics(geometry, sidecar)
        statistics = self.statistics
        comparisons = (
            (statistics.event_count, len(geometry), "event_count"),
            (statistics.exact_join_count, len(sidecar), "exact_join_count"),
            (statistics.decision_count, len(geometry), "decision_count"),
            (statistics.mode_counts, derived["mode_counts"], "mode_counts"),
            (statistics.frame_counts, derived["frame_counts"], "frame_counts"),
            (
                statistics.latency_histogram,
                derived["latency_histogram"],
                "latency_histogram",
            ),
            (
                statistics.grid_quantized_count,
                derived["grid_quantized_count"],
                "grid_quantized_count",
            ),
            (
                statistics.grid_unique_count,
                derived["grid_unique_count"],
                "grid_unique_count",
            ),
            (statistics.grid_x_min, derived["grid_x_min"], "grid_x_min"),
            (statistics.grid_x_max, derived["grid_x_max"], "grid_x_max"),
            (statistics.grid_y_min, derived["grid_y_min"], "grid_y_min"),
            (statistics.grid_y_max, derived["grid_y_max"], "grid_y_max"),
            (
                statistics.grid_index_min,
                derived["grid_index_min"],
                "grid_index_min",
            ),
            (
                statistics.grid_index_max,
                derived["grid_index_max"],
                "grid_index_max",
            ),
            (
                statistics.join_identity_sha256,
                derived["join_identity_sha256"],
                "join_identity_sha256",
            ),
            (
                statistics.geometry_sha256,
                derived["geometry_sha256"],
                "geometry_sha256",
            ),
            (
                statistics.retire_sidecar_sha256,
                derived["retire_sidecar_sha256"],
                "retire_sidecar_sha256",
            ),
            (statistics.grid_sha256, derived["grid_sha256"], "grid_sha256"),
        )
        for actual, expected, name in comparisons:
            if actual != expected:
                _fail("result statistics %s differs from actual rows" % name)
        expected_view_digests = tuple(
            (name, derived["geometry_sha256"]) for name in VIEW_ORDER
        )
        if statistics.view_geometry_sha256 != expected_view_digests:
            _fail("result view geometry digest statistics differ from actual rows")
        if any(
            view.geometry_sha256 != derived["geometry_sha256"]
            for view in self.views
        ):
            _fail("view geometry digest differs from actual rows")

    def view(self, name: str) -> FunctionalAssayView:
        if type(name) is not str:
            _fail("view name must be exact text")
        for view in self.views:
            if view.view_name == name:
                return view
        _fail("unknown functional assay view: %s" % name)
        raise AssertionError("unreachable")

    @property
    def geometry(self) -> Tuple[FunctionalGeometryRecord, ...]:
        return self.views[0].geometry

    @property
    def retire_sidecar(self) -> Tuple[FunctionalRetireObservation, ...]:
        return self.views[2].transport_sidecar

    @property
    def latency_sidecar(self) -> Tuple[FunctionalRetireObservation, ...]:
        """Return the explicitly latency-only AER retirement observations."""

        return self.views[2].transport_sidecar


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


def _validate_source(source: object) -> FunctionalSourceBundle:
    if type(source) is not FunctionalSourceBundle:
        _fail("source must be an exact FunctionalSourceBundle")
    expected_fields = frozenset((
        "registry", "events", "poses", "native_identities",
        "required_pose_start_id", "required_pose_end_id",
        "required_pose_pre_roll_ns", "causal_cav_eligible_count",
        "fresh_zoh_fallback_count", "stale_pose_count",
    ))
    if frozenset(vars(source)) != expected_fields:
        _fail("FunctionalSourceBundle field schema differs")
    if type(source.registry) is not NeutralRegistryWindow:
        _fail("source registry type differs")
    NeutralRegistryWindow(**vars(source.registry))
    if type(source.events) is not tuple or len(source.events) != EXPECTED_EVENT_COUNT:
        _fail(
            "source event population must contain exactly %d rows"
            % EXPECTED_EVENT_COUNT
        )
    if type(source.native_identities) is not tuple or len(
        source.native_identities
    ) != EXPECTED_EVENT_COUNT:
        _fail(
            "native identity population must contain exactly %d rows"
            % EXPECTED_EVENT_COUNT
        )
    if type(source.poses) is not tuple or len(source.poses) != EXPECTED_POSE_COUNT:
        _fail(
            "source pose population must contain exactly %d rows"
            % EXPECTED_POSE_COUNT
        )
    if (
        source.causal_cav_eligible_count != EXPECTED_CAUSAL_CAV_COUNT
        or source.fresh_zoh_fallback_count != EXPECTED_ZOH_COUNT
        or source.stale_pose_count != EXPECTED_BYPASS_COUNT
    ):
        _fail("source disposition authority differs from the official population")
    if sum((
        source.causal_cav_eligible_count,
        source.fresh_zoh_fallback_count,
        source.stale_pose_count,
    )) != EXPECTED_EVENT_COUNT:
        _fail("source disposition authority does not partition the population")

    previous_event = None
    event_ids = set()
    for event in source.events:
        if type(event) is not NeutralEventInput:
            _fail("source events must have exact NeutralEventInput type")
        NeutralEventInput(**vars(event))
        key = (event.timestamp_ns, event.event_id)
        if previous_event is not None and key <= previous_event:
            _fail("source events are not strict timestamp-then-event-ID order")
        previous_event = key
        if event.event_id in event_ids:
            _fail("source event IDs repeat")
        event_ids.add(event.event_id)
        window = source.registry
        if not (
            window.warmup_start_ns_inclusive
            <= event.timestamp_ns
            < window.query_end_ns_exclusive
        ):
            _fail("source event lies outside its registry window")
        if event.is_query != (event.timestamp_ns >= window.query_start_ns_inclusive):
            _fail("source event query flag differs from registry")

    previous_pose_timestamp = -1
    previous_pose_commit = None
    for expected_id, pose in enumerate(source.poses):
        if type(pose) is not NeutralPoseInput:
            _fail("source poses must have exact NeutralPoseInput type")
        NeutralPoseInput(**vars(pose))
        if pose.pose_id != expected_id:
            _fail("source pose IDs are not contiguous")
        if pose.timestamp_ns <= previous_pose_timestamp:
            _fail("source pose timestamps are not strictly increasing")
        if (
            previous_pose_commit is not None
            and pose.commit_cycle < previous_pose_commit
        ):
            _fail("source pose commit cycles move backwards")
        if not pose.value_valid or not pose.arithmetic_valid:
            _fail("official functional source contains an invalid pose")
        previous_pose_timestamp = pose.timestamp_ns
        previous_pose_commit = pose.commit_cycle

    expected_ids = set(range(EXPECTED_EVENT_COUNT))
    if event_ids != expected_ids:
        _fail("source event ID set is not exactly contiguous")
    return source


def _exact_join(
    source_value: object, outcomes_value: object
) -> Tuple[
    FunctionalSourceBundle,
    Tuple[Tuple[NeutralEventInput, NativeEventIdentity, NativeOutcome], ...],
]:
    """Validate the complete identity join before any geometry call."""

    source = _validate_source(source_value)
    if type(outcomes_value) is not tuple or len(outcomes_value) != EXPECTED_EVENT_COUNT:
        _fail("native outcomes must be the exact %d-row tuple" % EXPECTED_EVENT_COUNT)
    outcomes = outcomes_value
    if any(type(row) is not NativeOutcome for row in outcomes):
        _fail("native outcomes contain a non-NativeOutcome row")
    if tuple(row.event_id for row in outcomes) != tuple(range(EXPECTED_EVENT_COUNT)):
        _fail("native outcome IDs/order are not exactly contiguous")

    outcome_by_id = {}  # type: Dict[int, NativeOutcome]
    for row in outcomes:
        # This exact public schema intentionally excludes sealed RTL retire
        # lane/row/column.  Do not infer those coordinates in this assay.
        if frozenset(vars(row)) != frozenset((
            "event_id", "source", "occurrence_cycle", "retire_cycle", "latency"
        )):
            _fail("NativeOutcome field schema differs")
        for name in (
            "event_id", "source", "occurrence_cycle", "retire_cycle", "latency"
        ):
            _nonnegative_int(getattr(row, name), "native outcome %s" % name)
        if row.source > 15:
            _fail("native outcome source exceeds 15")
        if row.retire_cycle <= row.occurrence_cycle:
            _fail("native outcome retirement must strictly follow occurrence")
        if row.latency != row.retire_cycle - row.occurrence_cycle:
            _fail("native outcome latency differs from retire-occurrence")
        outcome_by_id[row.event_id] = row

    if any(type(row) is not NativeEventIdentity for row in source.native_identities):
        _fail("source native identities contain an invalid type")
    joined = []
    slots = set()
    for event, identity in zip(source.events, source.native_identities):
        NativeEventIdentity(**vars(identity))
        if event.event_id != identity.event_id:
            _fail("source event/native identity alignment differs")
        outcome = outcome_by_id.get(event.event_id)
        if outcome is None:
            _fail("source event lacks a native outcome")
        if (
            identity.source_index != outcome.source_index
            or identity.native_occurrence_cycle != outcome.occurrence_cycle
        ):
            _fail("event_id/source/native occurrence exact join differs")
        slot = (identity.native_occurrence_cycle, identity.source_index)
        if slot in slots:
            _fail("native occurrence/source slot repeats")
        slots.add(slot)
        joined.append((event, identity, outcome))
    if len(joined) != EXPECTED_EVENT_COUNT:
        _fail("exact join cardinality differs")
    return source, tuple(joined)


def _run_geometry(
    source: FunctionalSourceBundle,
) -> Tuple[FunctionalGeometryRecord, ...]:
    """Run geometry from the original event timestamps and source poses only."""

    replay_events = tuple(_ReplayEvent(
        event.event_id,
        event.timestamp_ns,
        event.transform_guard_valid,
        event.causal_pose_source_index,
    ) for event in source.events)
    replay_poses = tuple(_ReplayPose(
        pose.pose_id,
        pose.timestamp_ns,
        pose.commit_cycle,
        tuple(pose.quaternion_xyzw),
        pose.pose_sha256,
        pose.value_valid,
        pose.arithmetic_valid,
    ) for pose in source.poses)
    simulation = run_stage3_logical_cycle_model(
        window_id=source.registry.window_id,
        window_start_ns=source.registry.warmup_start_ns_inclusive,
        arm="causal_cav",
        events=replay_events,
        poses=replay_poses,
    )
    decisions = tuple(simulation.records)
    if tuple(row.event_id for row in decisions) != tuple(
        event.event_id for event in source.events
    ):
        _fail("current-CAV replay changed event population/order")
    poses_by_id = dict((pose.pose_id, pose) for pose in source.poses)
    result = []
    for event, decision in zip(source.events, decisions):
        if decision.event_timestamp_ns != event.timestamp_ns:
            _fail("current-CAV replay changed the original event timestamp")
        if type(decision.occurrence_cycle) is not int or decision.occurrence_cycle < 0:
            _fail("current-CAV occurrence cycle is invalid")
        used_ids = tuple(decision.used_pose_ids)
        if not used_ids and decision.disposition_reason != "no_occurrence_pose":
            _fail("current-CAV decision unexpectedly lacks a pose")
        if len(set(used_ids)) != len(used_ids) or any(
            type(pose_id) is not int or pose_id not in poses_by_id
            for pose_id in used_ids
        ):
            _fail("current-CAV decision references an invalid pose")
        samples = tuple(PoseSample(
            poses_by_id[pose_id].timestamp_ns,
            poses_by_id[pose_id].commit_cycle,
            poses_by_id[pose_id].quaternion_xyzw,
        ) for pose_id in used_ids)
        recovered = recover_causal_cav(
            samples, event.timestamp_ns, decision.occurrence_cycle
        )
        expected = {
            ("corrected_world_ray", "causal_cav"): RecoveryMode.CAV,
            ("corrected_world_ray", "fresh_zoh_fallback"): RecoveryMode.ZOH,
            ("raw_bypass", "stale_pose"): RecoveryMode.BYPASS,
            ("raw_bypass", "no_occurrence_pose"): RecoveryMode.BYPASS,
        }.get((decision.disposition, decision.disposition_reason))
        if expected is None or recovered.mode is not expected:
            _fail("public recovery disagrees with the current-CAV decision")
        if tuple(recovered.used_measurement_timestamps_ns) != tuple(
            poses_by_id[pose_id].timestamp_ns for pose_id in used_ids
        ):
            _fail("public recovery used a different pose population")
        if recovered.mode in (RecoveryMode.CAV, RecoveryMode.ZOH):
            if recovered.quaternion_xyzw is None:
                _fail("corrected public recovery lacks a quaternion")
            ray = rotate_sensor_ray_to_world(
                recovered.quaternion_xyzw, event.sensor_ray
            )
            frame = WORLD_FRAME
            grid = quantize_world_ray(ray, GRID_WIDTH, GRID_HEIGHT)
        else:
            if recovered.quaternion_xyzw is not None:
                _fail("bypass public recovery unexpectedly returned a quaternion")
            ray = tuple(event.sensor_ray)
            frame = SENSOR_FIXED_FRAME
            grid = None
        result.append(FunctionalGeometryRecord(
            event_id=event.event_id,
            event_timestamp_ns=event.timestamp_ns,
            cav_occurrence_cycle=decision.occurrence_cycle,
            recovery_mode=recovered.mode,
            coordinate_frame=frame,
            ray_xyz=ray,
            used_pose_ids=used_ids,
            world_grid=grid,
        ))
    return tuple(result)


def _geometry_mapping(row: FunctionalGeometryRecord) -> Mapping[str, object]:
    grid = row.world_grid
    return {
        "event_id": row.event_id,
        "event_timestamp_ns": row.event_timestamp_ns,
        "cav_occurrence_cycle": row.cav_occurrence_cycle,
        "recovery_mode": row.recovery_mode.value,
        "coordinate_frame": row.coordinate_frame,
        "ray_xyz_binary64": [value.hex() for value in row.ray_xyz],
        "used_pose_ids": list(row.used_pose_ids),
        "world_grid": None if grid is None else {
            "x": grid.x,
            "y": grid.y,
            "index": grid.index,
            "width": grid.width,
            "height": grid.height,
            "azimuth_binary64": grid.azimuth_rad.hex(),
            "elevation_binary64": grid.elevation_rad.hex(),
            "coordinate_convention": grid.coordinate_convention,
        },
    }


def _sidecar_mapping(row: FunctionalRetireObservation) -> Mapping[str, object]:
    dual = row.dual_time
    return {
        "event_id": row.event_id,
        "source_index": row.source_index,
        "native_occurrence_cycle": row.native_occurrence_cycle,
        "event_timestamp_ns": dual.event_timestamp_ns,
        "retire_cycle": dual.retire_cycle,
        "clock_period_ps": dual.clock_period_ps,
        "latency_cycles": dual.latency_cycles,
        "latency_ns": dual.latency_ns,
        "latency_injected_timestamp_ns": dual.latency_injected_timestamp_ns,
        "semantics_label": dual.semantics_label,
    }


def _derive_row_statistics(
    geometry: Tuple[FunctionalGeometryRecord, ...],
    sidecar: Tuple[FunctionalRetireObservation, ...],
) -> Mapping[str, object]:
    """Recompute every row-derived statistic and digest from first principles."""

    mode_counts = tuple(
        (mode.value, sum(row.recovery_mode is mode for row in geometry))
        for mode in (RecoveryMode.CAV, RecoveryMode.ZOH, RecoveryMode.BYPASS)
    )
    frame_counts = (
        (
            WORLD_FRAME,
            sum(row.coordinate_frame == WORLD_FRAME for row in geometry),
        ),
        (
            SENSOR_FIXED_FRAME,
            sum(row.coordinate_frame == SENSOR_FIXED_FRAME for row in geometry),
        ),
    )
    latency_counts = {}  # type: Dict[int, int]
    for row in sidecar:
        latency_counts[row.latency_cycles] = latency_counts.get(
            row.latency_cycles, 0
        ) + 1
    latency_histogram = tuple(sorted(latency_counts.items()))
    grids = tuple(
        row.world_grid for row in geometry if row.world_grid is not None
    )
    if not grids:
        _fail("official functional population has no WORLD grid records")
    grid_rows = tuple({
        "event_id": row.event_id,
        "x": row.world_grid.x,  # type: ignore[union-attr]
        "y": row.world_grid.y,  # type: ignore[union-attr]
        "index": row.world_grid.index,  # type: ignore[union-attr]
    } for row in geometry if row.world_grid is not None)
    geometry_digest = _canonical_sha256([
        _geometry_mapping(row) for row in geometry
    ])
    join_digest = _canonical_sha256([{
        "event_id": row.event_id,
        "source_index": row.source_index,
        "native_occurrence_cycle": row.native_occurrence_cycle,
    } for row in sorted(sidecar, key=lambda value: value.event_id)])
    sidecar_digest = _canonical_sha256([
        _sidecar_mapping(row) for row in sidecar
    ])
    grid_digest = _canonical_sha256(list(grid_rows))
    return {
        "mode_counts": mode_counts,
        "frame_counts": frame_counts,
        "latency_histogram": latency_histogram,
        "grid_quantized_count": len(grids),
        "grid_unique_count": len(set(grid.index for grid in grids)),
        "grid_x_min": min(grid.x for grid in grids),
        "grid_x_max": max(grid.x for grid in grids),
        "grid_y_min": min(grid.y for grid in grids),
        "grid_y_max": max(grid.y for grid in grids),
        "grid_index_min": min(grid.index for grid in grids),
        "grid_index_max": max(grid.index for grid in grids),
        "join_identity_sha256": join_digest,
        "geometry_sha256": geometry_digest,
        "retire_sidecar_sha256": sidecar_digest,
        "grid_sha256": grid_digest,
    }


def _build_sidecar(
    joined: Tuple[
        Tuple[NeutralEventInput, NativeEventIdentity, NativeOutcome], ...
    ],
) -> Tuple[FunctionalRetireObservation, ...]:
    """Build the public latency-only sidecar in ``SIDECAR_ORDER``.

    The parser's sealed evidence retains native lane/row/column separately;
    the public ``NativeOutcome`` does not expose them, so they are neither
    copied nor reconstructed here.
    """

    rows = []
    for event, identity, outcome in joined:
        dual = build_dual_time_event(
            event.timestamp_ns,
            outcome.occurrence_cycle,
            outcome.retire_cycle,
            NATIVE_CLOCK_PERIOD_PS,
        )
        if dual.latency_cycles != outcome.latency_cycles:
            _fail("DualTimeEvent latency differs from native outcome")
        rows.append(FunctionalRetireObservation(
            event_id=event.event_id,
            source_index=identity.source_index,
            native_occurrence_cycle=identity.native_occurrence_cycle,
            dual_time=dual,
        ))
    return tuple(sorted(
        rows,
        key=lambda row: (row.retire_cycle, row.event_id),
    ))


def _build_statistics(
    source: FunctionalSourceBundle,
    joined: Tuple[
        Tuple[NeutralEventInput, NativeEventIdentity, NativeOutcome], ...
    ],
    geometry: Tuple[FunctionalGeometryRecord, ...],
    sidecar: Tuple[FunctionalRetireObservation, ...],
) -> FunctionalAssayStatistics:
    derived = _derive_row_statistics(geometry, sidecar)
    mode_counts = derived["mode_counts"]
    frame_counts = derived["frame_counts"]
    if mode_counts != (
        (RecoveryMode.CAV.value, source.causal_cav_eligible_count),
        (RecoveryMode.ZOH.value, source.fresh_zoh_fallback_count),
        (RecoveryMode.BYPASS.value, source.stale_pose_count),
    ):
        _fail("current-CAV modes differ from source disposition authority")
    if dict(frame_counts) != {
        WORLD_FRAME: source.causal_cav_eligible_count
        + source.fresh_zoh_fallback_count,
        SENSOR_FIXED_FRAME: source.stale_pose_count,
    }:
        _fail("current-CAV frames differ from source disposition authority")

    geometry_digest = derived["geometry_sha256"]
    return FunctionalAssayStatistics(
        event_count=len(source.events),
        pose_count=len(source.poses),
        exact_join_count=len(joined),
        decision_count=len(geometry),
        mode_counts=mode_counts,
        frame_counts=frame_counts,
        latency_histogram=derived["latency_histogram"],
        grid_width=GRID_WIDTH,
        grid_height=GRID_HEIGHT,
        grid_quantized_count=derived["grid_quantized_count"],
        grid_unique_count=derived["grid_unique_count"],
        grid_x_min=derived["grid_x_min"],
        grid_x_max=derived["grid_x_max"],
        grid_y_min=derived["grid_y_min"],
        grid_y_max=derived["grid_y_max"],
        grid_index_min=derived["grid_index_min"],
        grid_index_max=derived["grid_index_max"],
        join_identity_sha256=derived["join_identity_sha256"],
        geometry_sha256=geometry_digest,
        retire_sidecar_sha256=derived["retire_sidecar_sha256"],
        grid_sha256=derived["grid_sha256"],
        view_geometry_sha256=tuple(
            (name, geometry_digest) for name in VIEW_ORDER
        ),
        transport_time_semantics=TRANSPORT_TIME_SEMANTICS,
        coordinate_convention=COORDINATE_CONVENTION,
    )


def run_functional_assay(
    source: FunctionalSourceBundle,
    native_outcomes: Sequence[NativeOutcome],
) -> FunctionalAssayResult:
    """Run the exact 8,503-event, 512x256, 2 ns observational assay.

    ``source`` is produced by ``build_official_uzh_functional_source`` and
    ``native_outcomes`` by ``load_abaa094_native_outcomes``.  Loading stays
    outside this core.  The exact identity join completes before the geometry
    function is entered, and the geometry function receives only ``source``.
    """

    try:
        checked_source, joined = _exact_join(source, native_outcomes)
        geometry = _run_geometry(checked_source)
        sidecar = _build_sidecar(joined)
        statistics = _build_statistics(
            checked_source, joined, geometry, sidecar
        )
        views = (
            FunctionalAssayView(
                RAW_CAV_VIEW, geometry, statistics.geometry_sha256
            ),
            FunctionalAssayView(
                AER_OCC_CAV_VIEW, geometry, statistics.geometry_sha256
            ),
            FunctionalAssayView(
                AER_RET_CAV_VIEW,
                geometry,
                statistics.geometry_sha256,
                sidecar,
            ),
        )
        return FunctionalAssayResult(views, statistics)
    except FunctionalAssayError:
        raise
    except (
        GeometryError,
        LogicalCycleReplayError,
        TransportTimeValidationError,
        WorldGridError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ) as error:
        raise FunctionalAssayError(
            "functional assay failed: %s" % error
        ) from error


def _canonical_result_sha256(result: FunctionalAssayResult) -> str:
    """Bind exact binary64 rows, metadata, and statistics for replay compare."""

    return _canonical_sha256({
        "view_names_and_geometry_sha256": [
            [view.view_name, view.geometry_sha256] for view in result.views
        ],
        "geometry": [_geometry_mapping(row) for row in result.geometry],
        "latency_sidecar": [
            _sidecar_mapping(row) for row in result.latency_sidecar
        ],
        "statistics": dict(vars(result.statistics)),
    })


def validate_functional_assay_result(
    result: FunctionalAssayResult,
    source: FunctionalSourceBundle,
    native_outcomes: Sequence[NativeOutcome],
) -> FunctionalAssayResult:
    """Approve a structural result only if exact replay reproduces every row.

    Dataclass construction cannot establish input provenance.  This validator
    re-runs the public assay from ``source`` and ``native_outcomes``, then
    compares both full dataclass equality and a canonical representation that
    distinguishes binary64 signed zero.
    """

    if type(result) is not FunctionalAssayResult:
        _fail("result must be an exact FunctionalAssayResult")
    FunctionalAssayResult(**vars(result))
    expected = run_functional_assay(source, native_outcomes)
    if (
        result != expected
        or _canonical_result_sha256(result)
        != _canonical_result_sha256(expected)
    ):
        _fail("functional assay result differs from exact input replay")
    return result


__all__ = (
    "AER_OCC_CAV_VIEW",
    "AER_RET_CAV_VIEW",
    "EXPECTED_BYPASS_COUNT",
    "EXPECTED_CAUSAL_CAV_COUNT",
    "EXPECTED_EVENT_COUNT",
    "EXPECTED_POSE_COUNT",
    "EXPECTED_ZOH_COUNT",
    "FunctionalAssayError",
    "FunctionalAssayResult",
    "FunctionalAssayStatistics",
    "FunctionalAssayView",
    "FunctionalGeometryRecord",
    "FunctionalRetireObservation",
    "GRID_HEIGHT",
    "GRID_WIDTH",
    "LATENCY_SIDECAR_ONLY",
    "NATIVE_CLOCK_PERIOD_PS",
    "RAW_CAV_VIEW",
    "SENSOR_FIXED_FRAME",
    "SIDECAR_ORDER",
    "VIEW_ORDER",
    "WORLD_FRAME",
    "run_functional_assay",
    "validate_functional_assay_result",
)
