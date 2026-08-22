"""Score-free projection of the locked 108-window cohort into neutral CAV inputs.

Axis/sign/motion labels remain in a sidecar and are never passed to the neutral
evaluator API.  This adapter validates the pinned UZH source, re-reads the exact
selected event lines, constructs strictly pre-edge dataset-pose references, and
runs only the frozen CAUSAL_CAV cycle model as a preflight.  It does not import
or compute any loss or outcome statistic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
from typing import Dict, Iterator, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_assay.source import (
    EventSample,
    PoseSample,
    SourcePins,
    ValidatedSources,
    canonicalize_quaternion,
    iter_event_samples,
    parse_calibration_bytes,
    sensor_ray,
    validate_sources,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    RAW_INGRESS_LANES,
    Arm,
    Event,
    PosePacket,
    PoseSource,
    pose_timestamp_to_cycle,
    run_cycle_model,
    timestamp_to_cycle,
)

from .evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
    load_neutral_registry,
)
from .selector import EXPECTED_WINDOW_COUNT, select_full_source


_SEAL_SCHEMA = "redred.mc_wtb_so3_axis_audit.new108_adapter_seal/v2"
_SEAL_FIELDS = frozenset((
    "schema",
    "source_member_sha256",
    "source_events_size_bytes",
    "source_events_line_count",
    "selector_registry_sha256",
    "selector_implementation_sha256",
    "projection_implementation_sha256",
    "neutral_registry_sha256",
    "selector_labels_sidecar_sha256",
    "window_count",
    "selected_event_count",
    "selected_pose_packet_count",
    "windows",
    "aggregate_sha256",
))
_SOURCE_MEMBER_FIELDS = frozenset(("events", "poses", "calibration"))
_WINDOW_SEAL_FIELDS = frozenset((
    "window_id",
    "neutral_bounds_sha256",
    "selected_raw_event_lines_sha256",
    "event_inputs_sha256",
    "pose_inputs_sha256",
    "neutral_inputs_sha256",
    "causal_cav_preflight_sha256",
))


class New108AdapterError(ValueError):
    """A locked-source, projection, provenance, or preflight invariant failed."""


@dataclass(frozen=True)
class New108AdapterBundle:
    selector_registry: Mapping[str, object]
    neutral_registry: Tuple[NeutralRegistryWindow, ...]
    event_streams: Mapping[str, Tuple[NeutralEventInput, ...]]
    pose_streams: Mapping[str, Tuple[NeutralPoseInput, ...]]
    selector_labels: Mapping[str, Mapping[str, object]]
    provenance_seal: Mapping[str, object]


class _CapturedLineStream:
    def __init__(self, stream: object) -> None:
        self._stream = stream
        self.current = b""
        self.count = 0

    def __iter__(self) -> Iterator[bytes]:
        for raw in self._stream:  # type: ignore[union-attr]
            self.current = raw
            self.count += 1
            yield raw


def _source_member_sha256(pins: SourcePins) -> Mapping[str, str]:
    return {
        "events": pins.events_sha256,
        "poses": pins.groundtruth_sha256,
        "calibration": pins.calibration_sha256,
    }


def _authenticate_registry_snapshot(
    registry: Mapping[str, object], sources: ValidatedSources
) -> None:
    if not isinstance(registry, Mapping):
        raise New108AdapterError("selector registry is not an object")
    unsigned = dict(registry)
    supplied = unsigned.pop("registry_sha256", None)
    if supplied != canonical_sha256(unsigned):
        raise New108AdapterError("selector registry content hash differs")
    bindings = registry.get("bindings")
    if not isinstance(bindings, Mapping):
        raise New108AdapterError("selector source bindings are missing")
    members = bindings.get("source_member_sha256")
    if not isinstance(members, Mapping) or frozenset(members) != _SOURCE_MEMBER_FIELDS:
        raise New108AdapterError("selector source member schema differs")
    if dict(members) != _source_member_sha256(sources.pins):
        raise New108AdapterError("selector source member hashes differ")


def _timestamp_ns(text: str, where: str) -> int:
    try:
        value = Decimal(text) * Decimal(1_000_000_000)
    except InvalidOperation as exc:
        raise New108AdapterError("%s timestamp is invalid" % where) from exc
    integral = value.to_integral_value()
    if value != integral or integral < 0:
        raise New108AdapterError("%s timestamp is not a nonnegative nanosecond" % where)
    return int(integral)


def _load_pinned_pose_samples(sources: ValidatedSources) -> Tuple[PoseSample, ...]:
    """Parse one immutable, hash-checked snapshot of groundtruth.txt."""

    try:
        payload = sources.groundtruth_path.read_bytes()
    except OSError as exc:
        raise New108AdapterError("cannot read pinned groundtruth source") from exc
    if hashlib.sha256(payload).hexdigest() != sources.pins.groundtruth_sha256:
        raise New108AdapterError("consumed groundtruth source hash differs")
    try:
        lines = payload.decode("ascii").splitlines(keepends=True)
    except UnicodeError as exc:
        raise New108AdapterError("cannot parse pinned groundtruth source") from exc
    poses = []
    previous_timestamp = None
    try:
        for line_number, line in enumerate(lines, 1):
            body = line[:-1] if line.endswith("\n") else line
            fields = body.split(" ")
            if len(fields) != 8 or any(field == "" for field in fields):
                raise New108AdapterError(
                    "groundtruth line %d is not canonical" % line_number
                )
            timestamp_ns = _timestamp_ns(
                fields[0], "groundtruth line %d" % line_number
            )
            numeric = tuple(float(field) for field in fields[1:])
            if not all(math.isfinite(value) for value in numeric):
                raise New108AdapterError(
                    "groundtruth line %d is non-finite" % line_number
                )
            if previous_timestamp is not None and timestamp_ns <= previous_timestamp:
                raise New108AdapterError("pose timestamps must be strictly increasing")
            previous_timestamp = timestamp_ns
            poses.append(PoseSample(
                line_number - 1,
                timestamp_ns,
                canonicalize_quaternion(
                    (numeric[3], numeric[4], numeric[5], numeric[6])
                ),
            ))
    except New108AdapterError:
        raise
    except (ValueError, OverflowError) as exc:
        raise New108AdapterError("cannot parse pinned groundtruth source") from exc
    if not poses:
        raise New108AdapterError("groundtruth source is empty")
    return tuple(poses)


def _validate_calibration_snapshot(sources: ValidatedSources) -> None:
    digest = hashlib.sha256(sources.calibration_bytes).hexdigest()
    if (
        digest != sources.pins.calibration_sha256
        or sources.calibration_sha256 != sources.pins.calibration_sha256
    ):
        raise New108AdapterError("consumed calibration source hash differs")


def _window_id(row: Mapping[str, object]) -> str:
    value = row.get("candidate_id")
    if type(value) is not str or not value:
        raise New108AdapterError("selector candidate_id is invalid")
    return value


def _selector_windows(registry: Mapping[str, object]) -> Tuple[Mapping[str, object], ...]:
    rows = registry.get("windows")
    if not isinstance(rows, list) or not rows:
        raise New108AdapterError("selector registry has no windows")
    if any(not isinstance(row, Mapping) for row in rows):
        raise New108AdapterError("selector registry window is not an object")
    result = tuple(rows)  # type: ignore[arg-type]
    identifiers = tuple(_window_id(row) for row in result)
    if len(set(identifiers)) != len(identifiers):
        raise New108AdapterError("selector window IDs repeat")
    return result


def _neutral_rows(rows: Sequence[Mapping[str, object]]) -> Tuple[NeutralRegistryWindow, ...]:
    return load_neutral_registry(tuple({
        "window_id": _window_id(row),
        "warmup_start_ns_inclusive": row["warmup_start_ns"],
        "query_start_ns_inclusive": row["query_start_ns"],
        "query_end_ns_exclusive": row["query_end_ns_exclusive"],
    } for row in rows))


def _labels(rows: Sequence[Mapping[str, object]]) -> Mapping[str, Mapping[str, object]]:
    return {
        _window_id(row): {
            "axis": row["axis"],
            "sign": row["sign"],
            "motion_bin": row["motion_bin"],
            "rotation_vector_rad": row["rotation_vector_rad"],
            "purity": row["purity"],
            "motion_proxy": row["motion_proxy"],
            "rank_sha256": row["rank_sha256"],
        }
        for row in rows
    }


def _pose_inputs(
    rows: Sequence[Mapping[str, object]], poses: Sequence[object]
) -> Mapping[str, Tuple[NeutralPoseInput, ...]]:
    output: Dict[str, Tuple[NeutralPoseInput, ...]] = {}
    for row in rows:
        window_id = _window_id(row)
        start = int(row["warmup_start_ns"])
        indices = row.get("dataset_pose_support_indices")
        if not isinstance(indices, list) or not indices:
            raise New108AdapterError("dataset pose support is empty")
        if indices != sorted(indices) or len(indices) != len(set(indices)):
            raise New108AdapterError("dataset pose support is not unique and ordered")
        values = []
        for index in indices:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(poses):
                raise New108AdapterError("dataset pose support index is invalid")
            pose = poses[index]
            if pose.pose_id != index:  # type: ignore[attr-defined]
                raise New108AdapterError("dataset pose source index differs")
            quaternion = pose.quaternion_xyzw  # type: ignore[attr-defined]
            digest = canonical_pose_value_sha256(
                pose.pose_id, pose.timestamp_ns, quaternion  # type: ignore[attr-defined]
            )
            values.append(NeutralPoseInput(
                pose.pose_id,  # type: ignore[attr-defined]
                pose.timestamp_ns,  # type: ignore[attr-defined]
                pose_timestamp_to_cycle(pose.timestamp_ns, start),  # type: ignore[attr-defined]
                quaternion,
                digest,
            ))
        output[window_id] = tuple(values)
    return output


def _selected_event_owners(
    rows: Sequence[Mapping[str, object]],
) -> Tuple[Mapping[int, Tuple[str, bool]], Mapping[str, str]]:
    owners: Dict[int, Tuple[str, bool]] = {}
    expected_raw: Dict[str, str] = {}
    for row in rows:
        window_id = _window_id(row)
        expected_raw[window_id] = str(row["selected_raw_event_lines_sha256"])
        for field, query in (("warmup_event_ids", False), ("query_event_ids", True)):
            values = row.get(field)
            if not isinstance(values, list) or not values:
                raise New108AdapterError("selected event IDs are empty")
            for event_id in values:
                if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 0:
                    raise New108AdapterError("selected event ID is invalid")
                if event_id in owners:
                    raise New108AdapterError("selected event ID is shared")
                owners[event_id] = (window_id, query)
    return owners, expected_raw


def _causal_pose_index(
    event: EventSample,
    window_start_ns: int,
    poses: Sequence[NeutralPoseInput],
) -> int:
    occurrence = timestamp_to_cycle(event.timestamp_ns, window_start_ns)
    visible = tuple(
        pose for pose in poses
        if pose.commit_cycle < occurrence and pose.timestamp_ns <= event.timestamp_ns
    )
    if not visible:
        raise New108AdapterError("event has no strictly pre-edge dataset pose")
    return visible[-1].pose_id


def _read_selected_events(
    sources: ValidatedSources,
    rows: Sequence[Mapping[str, object]],
    neutral: Sequence[NeutralRegistryWindow],
    poses: Mapping[str, Tuple[NeutralPoseInput, ...]],
) -> Tuple[Mapping[str, Tuple[NeutralEventInput, ...]], Mapping[str, str]]:
    owners, expected_raw = _selected_event_owners(rows)
    bounds = {window.window_id: window for window in neutral}
    calibration = parse_calibration_bytes(sources.calibration_bytes)
    events: Dict[str, list] = {window.window_id: [] for window in neutral}
    digests = {window.window_id: hashlib.sha256() for window in neutral}
    full_digest = hashlib.sha256()
    full_size = 0
    seen = set()
    with sources.events_path.open("rb") as source_stream:
        captured = _CapturedLineStream(source_stream)
        for event in iter_event_samples(captured):  # type: ignore[arg-type]
            full_digest.update(captured.current)
            full_size += len(captured.current)
            owner = owners.get(event.event_id)
            if owner is None:
                continue
            window_id, is_query = owner
            window = bounds[window_id]
            if not (
                window.warmup_start_ns_inclusive <= event.timestamp_ns
                < window.query_end_ns_exclusive
            ):
                raise New108AdapterError("selected event lies outside window bounds")
            if is_query != (event.timestamp_ns >= window.query_start_ns_inclusive):
                raise New108AdapterError("selected event phase differs from bounds")
            causal_index = _causal_pose_index(
                event, window.warmup_start_ns_inclusive, poses[window_id]
            )
            ray = sensor_ray(event, calibration)
            content_hash = canonical_event_content_sha256(
                event.event_id, event.timestamp_ns, event.polarity, is_query, ray,
                causal_index,
            )
            events[window_id].append(NeutralEventInput(
                event.event_id, event.timestamp_ns, event.polarity, is_query, ray,
                causal_index, content_hash,
            ))
            digests[window_id].update(captured.current)
            seen.add(event.event_id)
    if captured.count != sources.pins.events_line_count:
        raise New108AdapterError("events source line count differs")
    if full_size != sources.pins.events_size_bytes:
        raise New108AdapterError("consumed events source size differs")
    if full_digest.hexdigest() != sources.pins.events_sha256:
        raise New108AdapterError("consumed events source hash differs")
    if seen != set(owners):
        raise New108AdapterError("not every selected event ID was re-read")
    actual_raw = {window_id: digest.hexdigest() for window_id, digest in digests.items()}
    if actual_raw != expected_raw:
        raise New108AdapterError("selected raw event lines differ from selector seal")
    return {key: tuple(value) for key, value in events.items()}, actual_raw


def _preflight(
    neutral: Sequence[NeutralRegistryWindow],
    events: Mapping[str, Tuple[NeutralEventInput, ...]],
    poses: Mapping[str, Tuple[NeutralPoseInput, ...]],
) -> Mapping[str, str]:
    evidence = {}
    for window in neutral:
        event_values = tuple(NeutralEventInput(
            row.event_id, row.timestamp_ns, row.polarity, row.is_query,
            row.sensor_ray, row.causal_pose_source_index,
            row.event_content_sha256, row.transform_guard_valid,
        ) for row in events[window.window_id])
        pose_values = tuple(NeutralPoseInput(
            row.pose_id, row.timestamp_ns, row.commit_cycle,
            row.quaternion_xyzw, row.pose_sha256, row.value_valid,
            row.arithmetic_valid,
        ) for row in poses[window.window_id])
        if not event_values or not pose_values:
            raise New108AdapterError("neutral event and pose streams must not be empty")
        if not any(event.is_query for event in event_values):
            raise New108AdapterError("neutral window has no query events")
        counts: Dict[int, int] = {}
        for event in event_values:
            cycle = timestamp_to_cycle(event.timestamp_ns, window.warmup_start_ns_inclusive)
            counts[cycle] = counts.get(cycle, 0) + 1
        if counts and max(counts.values()) > RAW_INGRESS_LANES:
            raise New108AdapterError("more than six selected events share an occurrence cycle")
        simulation = run_cycle_model(
            window_id=window.window_id,
            window_start_ns=window.warmup_start_ns_inclusive,
            arm=Arm.CAUSAL_CAV,
            events=tuple(Event(
                event.event_id, event.timestamp_ns, event.transform_guard_valid,
                event.causal_pose_source_index,
            ) for event in event_values),
            poses=tuple(PosePacket(
                pose.pose_id, pose.timestamp_ns, pose.commit_cycle,
                PoseSource.DATASET, pose.pose_sha256, pose.value_valid,
                pose.arithmetic_valid,
            ) for pose in pose_values),
        )
        if not simulation.all_event_pose_indices_verified or simulation.synthetic_test_mode:
            raise New108AdapterError("CAUSAL_CAV preflight did not verify pose indices")
        evidence[window.window_id] = canonical_sha256({
            "arm": simulation.arm.value,
            "decision_records_sha256": simulation.decision_records_sha256,
            "cycle_receipts_sha256": simulation.cycle_receipts_sha256,
            "pose_ring_accounting_sha256": simulation.pose_ring_accounting_sha256,
            "input_event_count": len(event_values),
            "input_pose_count": len(pose_values),
            "all_event_pose_indices_verified": simulation.all_event_pose_indices_verified,
            "synthetic_test_mode": simulation.synthetic_test_mode,
        })
    return evidence


def _projection_seal(
    registry: Mapping[str, object],
    sources: ValidatedSources,
    neutral: Sequence[NeutralRegistryWindow],
    events: Mapping[str, Tuple[NeutralEventInput, ...]],
    poses: Mapping[str, Tuple[NeutralPoseInput, ...]],
    labels: Mapping[str, Mapping[str, object]],
    raw_hashes: Mapping[str, str],
    preflight: Mapping[str, str],
) -> Mapping[str, object]:
    per_window = []
    for window in neutral:
        window_id = window.window_id
        event_mapping = [value.to_content_mapping() for value in events[window_id]]
        pose_mapping = [value.to_content_mapping() for value in poses[window_id]]
        per_window.append({
            "window_id": window_id,
            "neutral_bounds_sha256": canonical_sha256(window.to_mapping()),
            "selected_raw_event_lines_sha256": raw_hashes[window_id],
            "event_inputs_sha256": canonical_sha256(event_mapping),
            "pose_inputs_sha256": canonical_sha256(pose_mapping),
            "neutral_inputs_sha256": canonical_sha256({
                "registry": window.to_mapping(), "events": event_mapping,
                "poses": pose_mapping,
            }),
            "causal_cav_preflight_sha256": preflight[window_id],
        })
    body = {
        "schema": _SEAL_SCHEMA,
        "source_member_sha256": _source_member_sha256(sources.pins),
        "source_events_size_bytes": sources.pins.events_size_bytes,
        "source_events_line_count": sources.pins.events_line_count,
        "selector_registry_sha256": registry["registry_sha256"],
        "selector_implementation_sha256": registry["bindings"]["selector_py_sha256"],  # type: ignore[index]
        "projection_implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "neutral_registry_sha256": canonical_sha256([row.to_mapping() for row in neutral]),
        "selector_labels_sidecar_sha256": canonical_sha256(labels),
        "window_count": len(neutral),
        "selected_event_count": sum(len(values) for values in events.values()),
        "selected_pose_packet_count": sum(len(values) for values in poses.values()),
        "windows": per_window,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _project(
    registry: Mapping[str, object], sources: ValidatedSources
) -> New108AdapterBundle:
    _authenticate_registry_snapshot(registry, sources)
    _validate_calibration_snapshot(sources)
    rows = _selector_windows(registry)
    neutral = _neutral_rows(rows)
    labels = _labels(rows)
    source_poses = _load_pinned_pose_samples(sources)
    poses = _pose_inputs(rows, source_poses)
    events, raw_hashes = _read_selected_events(sources, rows, neutral, poses)
    preflight = _preflight(neutral, events, poses)
    seal = _projection_seal(
        registry, sources, neutral, events, poses, labels, raw_hashes, preflight
    )
    return New108AdapterBundle(registry, neutral, events, poses, labels, seal)


def build_locked_new108_adapter(dataset_directory: Path) -> New108AdapterBundle:
    """Build neutral inputs from the committed public 108-window registry."""

    root = Path(dataset_directory)
    registry = select_full_source(root)
    if registry.get("window_count") != EXPECTED_WINDOW_COUNT:
        raise New108AdapterError("production selector did not return 108 windows")
    sources = validate_sources(root)
    bundle = _project(registry, sources)
    verify_new108_adapter(bundle, root)
    return bundle


def _validate_seal_schema(seal: object) -> Mapping[str, object]:
    if not isinstance(seal, Mapping) or frozenset(seal) != _SEAL_FIELDS:
        raise New108AdapterError("adapter provenance field schema differs")
    if seal.get("schema") != _SEAL_SCHEMA:
        raise New108AdapterError("adapter provenance schema differs")
    source_members = seal.get("source_member_sha256")
    if (
        not isinstance(source_members, Mapping)
        or frozenset(source_members) != _SOURCE_MEMBER_FIELDS
    ):
        raise New108AdapterError("adapter source member schema differs")
    sealed_windows = seal.get("windows")
    if not isinstance(sealed_windows, list):
        raise New108AdapterError("adapter per-window provenance differs")
    for row in sealed_windows:
        if not isinstance(row, Mapping) or frozenset(row) != _WINDOW_SEAL_FIELDS:
            raise New108AdapterError("adapter per-window field schema differs")
    unsigned = dict(seal)
    supplied_aggregate = unsigned.pop("aggregate_sha256")
    if supplied_aggregate != canonical_sha256(unsigned):
        raise New108AdapterError("adapter aggregate provenance hash differs")
    return seal


def _verify_against_pinned_source(
    bundle: New108AdapterBundle,
    expected_registry: Mapping[str, object],
    sources: ValidatedSources,
) -> str:
    """Reconstruct a projection from independently supplied registry and sources."""

    if type(bundle) is not New108AdapterBundle:
        raise New108AdapterError("adapter bundle type differs")
    _authenticate_registry_snapshot(expected_registry, sources)
    if bundle.selector_registry != expected_registry:
        raise New108AdapterError("selector registry differs from authenticated authority")
    seal = _validate_seal_schema(bundle.provenance_seal)
    reconstructed = _project(expected_registry, sources)
    if bundle.neutral_registry != reconstructed.neutral_registry:
        raise New108AdapterError("neutral bounds projection differs")
    if bundle.selector_labels != reconstructed.selector_labels:
        raise New108AdapterError("selector label sidecar differs")
    if bundle.event_streams != reconstructed.event_streams:
        raise New108AdapterError("projected event inputs differ from pinned source")
    if bundle.pose_streams != reconstructed.pose_streams:
        raise New108AdapterError("projected pose inputs differ from pinned source")
    if dict(seal) != dict(reconstructed.provenance_seal):
        raise New108AdapterError("adapter provenance differs from reconstructed source")
    return str(seal["aggregate_sha256"])


def verify_new108_adapter(
    bundle: New108AdapterBundle, dataset_directory: Path
) -> str:
    """Authenticate authority and reconstruct every input from pinned sources."""

    root = Path(dataset_directory)
    registry = select_full_source(root)
    if registry.get("window_count") != EXPECTED_WINDOW_COUNT:
        raise New108AdapterError("production selector did not return 108 windows")
    sources = validate_sources(root)
    return _verify_against_pinned_source(bundle, registry, sources)


__all__ = [
    "New108AdapterBundle", "New108AdapterError", "build_locked_new108_adapter",
    "verify_new108_adapter",
]
