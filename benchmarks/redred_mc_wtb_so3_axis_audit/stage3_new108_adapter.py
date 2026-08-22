"""Locked Stage3 50 ms replay projection for the frozen NEW108 query cohort.

This is deliberately separate from :mod:`new108_adapter`: the historical 1 ms
adapter and evaluator remain unchanged for baseline reproducibility.  Selector
labels stay in the bundle sidecar and are never consulted while projecting the
neutral event or pose streams.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_stage4_assay.source import (
    EventSample,
    PoseSample,
    ValidatedSources,
    iter_event_samples,
    parse_calibration_bytes,
    sensor_ray,
    validate_sources,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256
from benchmarks.redred_mc_wtb_stage4_cyclemodel import (
    pose_timestamp_to_cycle,
    timestamp_to_cycle,
)

from .evaluator import (
    NeutralEventInput,
    NeutralPoseInput,
    NeutralRegistryWindow,
    canonical_event_content_sha256,
    canonical_pose_value_sha256,
)
from .new108_adapter import (
    New108AdapterBundle,
    New108AdapterError,
    _CapturedLineStream,
    _authenticate_registry_snapshot,
    _labels,
    _load_pinned_pose_samples,
    _preflight,
    _selector_windows,
    _source_member_sha256,
    _validate_calibration_snapshot,
    _window_id,
)
from .selector import EXPECTED_WINDOW_COUNT, select_full_source


STAGE3_PREROLL_NS = 50_000_000
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLAN_PATH = _REPO_ROOT / "benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json"
_FREEZE_PATH = _REPO_ROOT / "benchmarks/redred_mc_wtb_predictor_stage12/checkpoint_a_freeze_receipt.json"
_PLAN_RELATIVE = "benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json"
_PLAN_SHA256 = "654582131fe0d44ea047268163e928d53fd7120493292eda957b8c3180e14a6e"
_FREEZE_SHA256 = "f66ad07f5596a408045199f5d78aa4c7c02d6aeff302d9565f1474ea00f63dd8"
_PREROLL_RULE = {
    "duration_ns": STAGE3_PREROLL_NS,
    "interval": "QUERY_START_MINUS_DURATION_INCLUSIVE_TO_QUERY_START_EXCLUSIVE",
    "state_initialization": "RESET_AT_PREROLL_START",
    "scoring": "PREROLL_EVENTS_AND_POSES_ARE_NEVER_SCORED",
    "insufficient_support": "ONLY_COMMON_SOURCE_OR_SCORER_SUPPORT_CAN_INVALIDATE_A_WINDOW_CANDIDATE_SPECIFIC_HISTORY_SHORTAGE_MUST_FALL_BACK_WITH_Q_UNCHANGED",
    "outcome_dependent_extension": "FORBIDDEN",
}

# Exact repository Python sources loaded by a clean ``python -S`` import of the
# Stage3 adapter.  Interpreter and standard-library code are outside this seal.
_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_causal_reference/__init__.py",
    "benchmarks/redred_mc_wtb_causal_reference/development.py",
    "benchmarks/redred_mc_wtb_causal_reference/reference.py",
    "benchmarks/redred_mc_wtb_causal_reference/routing.py",
    "benchmarks/redred_mc_wtb_motion_qualification/__init__.py",
    "benchmarks/redred_mc_wtb_motion_qualification/controller.py",
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/__init__.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/analyzer.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/evaluator.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/new108_adapter.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/selector.py",
    "benchmarks/redred_mc_wtb_so3_axis_audit/stage3_new108_adapter.py",
    "benchmarks/redred_mc_wtb_stage4_assay/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_assay/generator.py",
    "benchmarks/redred_mc_wtb_stage4_assay/source.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_cyclemodel/model.py",
)

_SEAL_SCHEMA = "redred.mc_wtb_so3_axis_audit.stage3_new108_adapter_seal/v1"
_SEAL_FIELDS = frozenset((
    "schema", "source_lock_sha256", "source_member_sha256",
    "source_events_size_bytes", "source_events_line_count",
    "stage12_freeze_receipt_sha256", "stage12_source_split_plan_sha256",
    "candidate_screen_preroll_rule_sha256", "pre_roll_ns",
    "selector_registry_sha256", "selector_implementation_sha256",
    "projection_dependency_manifest", "projection_dependency_aggregate_sha256",
    "neutral_registry_sha256", "selector_labels_sidecar_sha256",
    "window_count", "source_window_event_count", "unique_source_event_count",
    "warmup_event_count", "query_event_count", "selected_pose_packet_count",
    "windows", "aggregate_sha256",
))
_WINDOW_FIELDS = frozenset((
    "window_id", "selector_window_sha256", "neutral_bounds_sha256",
    "selector_selected_raw_event_lines_sha256",
    "raw_source_event_lines_sha256", "ordered_source_event_ids_sha256",
    "ordered_warmup_event_ids_sha256", "ordered_query_event_ids_sha256",
    "source_event_count", "warmup_event_count", "query_event_count",
    "event_inputs_sha256", "ordered_pose_ids_sha256",
    "negative_commit_pose_ids_sha256", "pose_input_count",
    "pose_inputs_sha256", "neutral_inputs_sha256",
    "causal_cav_preflight_sha256",
))
_SOURCE_FIELDS = frozenset(("events", "poses", "calibration"))
_DEPENDENCY_FIELDS = frozenset(("path", "sha256"))


class Stage3New108AdapterError(ValueError):
    """A Stage3 timing, source, causality, or provenance invariant failed."""


@dataclass(frozen=True)
class _Stage12Authority:
    freeze_sha256: str
    plan_sha256: str
    rule_sha256: str


def _strict_object(pairs: Sequence[Tuple[str, object]]) -> Mapping[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise Stage3New108AdapterError("Stage12 authority contains duplicate keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise Stage3New108AdapterError("Stage12 authority contains non-finite JSON")


def _read_json(path: Path, where: str) -> Tuple[bytes, Mapping[str, object]]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except Stage3New108AdapterError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise Stage3New108AdapterError("cannot read %s" % where) from exc
    if not isinstance(value, Mapping):
        raise Stage3New108AdapterError("%s is not an object" % where)
    return payload, value


def _stage12_authority() -> _Stage12Authority:
    plan_bytes, plan = _read_json(_PLAN_PATH, "Stage12 source plan")
    freeze_bytes, freeze = _read_json(_FREEZE_PATH, "Stage12 freeze receipt")
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    freeze_sha = hashlib.sha256(freeze_bytes).hexdigest()
    if plan_sha != _PLAN_SHA256 or freeze_sha != _FREEZE_SHA256:
        raise Stage3New108AdapterError("Stage12 frozen authority digest differs")
    if plan.get("candidate_screen_preroll_rule") != _PREROLL_RULE:
        raise Stage3New108AdapterError("Stage12 pre-roll rule differs")
    artifacts = freeze.get("artifacts")
    matches = [] if not isinstance(artifacts, list) else [
        row for row in artifacts
        if isinstance(row, Mapping) and row.get("path") == _PLAN_RELATIVE
    ]
    if (
        freeze.get("status") != "FROZEN_STAGE1_STAGE2_CHECKPOINT_A"
        or len(matches) != 1 or matches[0].get("sha256") != plan_sha
    ):
        raise Stage3New108AdapterError("Stage12 source plan is not freeze-bound")
    return _Stage12Authority(freeze_sha, plan_sha, canonical_sha256(_PREROLL_RULE))


def _dependency_manifest() -> Tuple[Mapping[str, str], ...]:
    rows = []
    for relative in _DEPENDENCY_PATHS:
        try:
            digest = hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest()
        except OSError as exc:
            raise Stage3New108AdapterError("cannot read projection dependency") from exc
        rows.append({"path": relative, "sha256": digest})
    return tuple(rows)


def _neutral_registry(
    rows: Sequence[Mapping[str, object]],
) -> Tuple[NeutralRegistryWindow, ...]:
    windows = []
    for row in rows:
        query = row.get("query_start_ns")
        end = row.get("query_end_ns_exclusive")
        if (
            isinstance(query, bool) or not isinstance(query, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or query < STAGE3_PREROLL_NS
        ):
            raise Stage3New108AdapterError("selector query cannot support 50 ms pre-roll")
        windows.append(NeutralRegistryWindow(
            _window_id(row), query - STAGE3_PREROLL_NS, query, end,
        ))
    identifiers = tuple(window.window_id for window in windows)
    if len(set(identifiers)) != len(identifiers):
        raise Stage3New108AdapterError("Stage3 window IDs repeat")
    for left, right in zip(windows, windows[1:]):
        if (
            left.warmup_start_ns_inclusive >= right.warmup_start_ns_inclusive
            or left.query_start_ns_inclusive >= right.query_start_ns_inclusive
            or left.query_end_ns_exclusive > right.query_start_ns_inclusive
        ):
            raise Stage3New108AdapterError(
                "Stage3 query intervals overlap or move backwards"
            )
    return tuple(windows)


def _pose_inputs(
    windows: Sequence[NeutralRegistryWindow], poses: Sequence[PoseSample],
) -> Mapping[str, Tuple[NeutralPoseInput, ...]]:
    timestamps = tuple(pose.timestamp_ns for pose in poses)
    result: Dict[str, Tuple[NeutralPoseInput, ...]] = {}
    for window in windows:
        first = bisect_left(timestamps, window.warmup_start_ns_inclusive)
        end = bisect_left(timestamps, window.query_end_ns_exclusive)
        if first < 2 or end <= first:
            raise Stage3New108AdapterError("50 ms window lacks common pose support")
        values = []
        for index in range(first - 2, end):
            pose = poses[index]
            if pose.pose_id != index:
                raise Stage3New108AdapterError("dataset pose source index differs")
            digest = canonical_pose_value_sha256(
                pose.pose_id, pose.timestamp_ns, pose.quaternion_xyzw,
            )
            values.append(NeutralPoseInput(
                pose.pose_id, pose.timestamp_ns,
                pose_timestamp_to_cycle(
                    pose.timestamp_ns, window.warmup_start_ns_inclusive
                ),
                pose.quaternion_xyzw, digest,
            ))
        result[window.window_id] = tuple(values)
    return result


def _selector_evidence(
    rows: Sequence[Mapping[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    result = {}
    global_query_ids = set()
    for row in rows:
        window_id = _window_id(row)
        checked = {}
        for field in ("warmup_event_ids", "query_event_ids"):
            values = row.get(field)
            if not isinstance(values, list) or not values:
                raise Stage3New108AdapterError("selector event IDs are empty")
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                   for value in values):
                raise Stage3New108AdapterError("selector event ID is invalid")
            if len(set(values)) != len(values):
                raise Stage3New108AdapterError("selector event IDs repeat within a window")
            checked[field] = tuple(values)
        for event_id in checked["query_event_ids"]:
            if event_id in global_query_ids:
                raise Stage3New108AdapterError("selector query event ID repeats")
            global_query_ids.add(event_id)
        raw_sha = row.get("selected_raw_event_lines_sha256")
        if type(raw_sha) is not str or len(raw_sha) != 64:
            raise Stage3New108AdapterError("selector raw event seal is invalid")
        result[window_id] = dict(
            checked, selected_raw_event_lines_sha256=raw_sha,
        )
    return result


def _source_lock_sha256(registry: Mapping[str, object]) -> str:
    bindings = registry.get("bindings")
    value = bindings.get("source_lock_sha256") if isinstance(bindings, Mapping) else None
    if (
        type(value) is not str or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise Stage3New108AdapterError("selector source-lock authority is invalid")
    return value


def _causal_pose_id(
    event: EventSample, window: NeutralRegistryWindow,
    poses: Sequence[NeutralPoseInput],
) -> int:
    decision_edge = timestamp_to_cycle(
        event.timestamp_ns, window.warmup_start_ns_inclusive
    )
    visible = [
        pose for pose in poses
        if pose.commit_cycle < decision_edge
        and pose.timestamp_ns <= event.timestamp_ns
    ]
    if not visible:
        raise Stage3New108AdapterError("event has no strictly pre-edge dataset pose")
    return visible[-1].pose_id


def _read_events(
    sources: ValidatedSources,
    rows: Sequence[Mapping[str, object]],
    windows: Sequence[NeutralRegistryWindow],
    poses: Mapping[str, Tuple[NeutralPoseInput, ...]],
) -> Tuple[Mapping[str, Tuple[NeutralEventInput, ...]], Mapping[str, Mapping[str, object]]]:
    expected = _selector_evidence(rows)
    row_by_id = {_window_id(row): row for row in rows}
    calibration = parse_calibration_bytes(sources.calibration_bytes)
    event_streams: Dict[str, list] = {window.window_id: [] for window in windows}
    all_ids: Dict[str, list] = {window.window_id: [] for window in windows}
    warmup_ids: Dict[str, list] = {window.window_id: [] for window in windows}
    query_ids: Dict[str, list] = {window.window_id: [] for window in windows}
    raw_digests = {window.window_id: hashlib.sha256() for window in windows}
    selector_digests = {window.window_id: hashlib.sha256() for window in windows}
    full_digest = hashlib.sha256()
    full_size = 0
    active = []
    next_window = 0
    with sources.events_path.open("rb") as source_stream:
        captured = _CapturedLineStream(source_stream)
        for event in iter_event_samples(captured):  # type: ignore[arg-type]
            full_digest.update(captured.current)
            full_size += len(captured.current)
            while (
                next_window < len(windows)
                and windows[next_window].warmup_start_ns_inclusive <= event.timestamp_ns
            ):
                active.append(windows[next_window])
                next_window += 1
            active = [
                window for window in active
                if event.timestamp_ns < window.query_end_ns_exclusive
            ]
            for window in active:
                window_id = window.window_id
                is_query = event.timestamp_ns >= window.query_start_ns_inclusive
                pose_id = _causal_pose_id(event, window, poses[window_id])
                ray = sensor_ray(event, calibration)
                content_sha = canonical_event_content_sha256(
                    event.event_id, event.timestamp_ns, event.polarity,
                    is_query, ray, pose_id,
                )
                event_streams[window_id].append(NeutralEventInput(
                    event.event_id, event.timestamp_ns, event.polarity,
                    is_query, ray, pose_id, content_sha,
                ))
                all_ids[window_id].append(event.event_id)
                (query_ids if is_query else warmup_ids)[window_id].append(event.event_id)
                raw_digests[window_id].update(captured.current)
                legacy_start = row_by_id[window_id].get("warmup_start_ns")
                if (
                    isinstance(legacy_start, int)
                    and legacy_start <= event.timestamp_ns < window.query_end_ns_exclusive
                ):
                    selector_digests[window_id].update(captured.current)
    if captured.count != sources.pins.events_line_count:
        raise Stage3New108AdapterError("events source line count differs")
    if full_size != sources.pins.events_size_bytes:
        raise Stage3New108AdapterError("consumed events source size differs")
    if full_digest.hexdigest() != sources.pins.events_sha256:
        raise Stage3New108AdapterError("consumed events source hash differs")

    evidence = {}
    observed_global_query_ids = []
    for window in windows:
        window_id = window.window_id
        authority = expected[window_id]
        if tuple(query_ids[window_id]) != authority["query_event_ids"]:
            raise Stage3New108AdapterError(
                "50 ms query event IDs differ from selector authority"
            )
        legacy_warmup_ids = tuple(
            event.event_id for event in event_streams[window_id]
            if int(row_by_id[window_id]["warmup_start_ns"])
            <= event.timestamp_ns < window.query_start_ns_inclusive
        )
        if legacy_warmup_ids != authority["warmup_event_ids"]:
            raise Stage3New108AdapterError("legacy selector warmup evidence differs")
        if selector_digests[window_id].hexdigest() != authority["selected_raw_event_lines_sha256"]:
            raise Stage3New108AdapterError("selector raw event evidence differs")
        if not warmup_ids[window_id] or not query_ids[window_id]:
            raise Stage3New108AdapterError("Stage3 window event phase is empty")
        observed_global_query_ids.extend(query_ids[window_id])
        evidence[window_id] = {
            "raw_source_event_lines_sha256": raw_digests[window_id].hexdigest(),
            "ordered_source_event_ids_sha256": canonical_sha256(all_ids[window_id]),
            "ordered_warmup_event_ids_sha256": canonical_sha256(warmup_ids[window_id]),
            "ordered_query_event_ids_sha256": canonical_sha256(query_ids[window_id]),
            "source_event_count": len(all_ids[window_id]),
            "warmup_event_count": len(warmup_ids[window_id]),
            "query_event_count": len(query_ids[window_id]),
        }
    if len(set(observed_global_query_ids)) != len(observed_global_query_ids):
        raise Stage3New108AdapterError("Stage3 query event IDs are not exact-once")
    return {key: tuple(value) for key, value in event_streams.items()}, evidence


def _seal(
    registry: Mapping[str, object], sources: ValidatedSources,
    rows: Sequence[Mapping[str, object]], windows: Sequence[NeutralRegistryWindow],
    events: Mapping[str, Tuple[NeutralEventInput, ...]],
    poses: Mapping[str, Tuple[NeutralPoseInput, ...]],
    labels: Mapping[str, Mapping[str, object]],
    evidence: Mapping[str, Mapping[str, object]],
    preflight: Mapping[str, str], authority: _Stage12Authority,
    dependencies: Sequence[Mapping[str, str]],
) -> Mapping[str, object]:
    row_by_id = {_window_id(row): row for row in rows}
    window_seals = []
    for window in windows:
        window_id = window.window_id
        event_mapping = [value.to_content_mapping() for value in events[window_id]]
        pose_mapping = [value.to_content_mapping() for value in poses[window_id]]
        pose_ids = [value.pose_id for value in poses[window_id]]
        negative_ids = [
            value.pose_id for value in poses[window_id] if value.commit_cycle < 0
        ]
        window_seals.append({
            "window_id": window_id,
            "selector_window_sha256": canonical_sha256(row_by_id[window_id]),
            "neutral_bounds_sha256": canonical_sha256(window.to_mapping()),
            "selector_selected_raw_event_lines_sha256":
                row_by_id[window_id]["selected_raw_event_lines_sha256"],
            **evidence[window_id],
            "event_inputs_sha256": canonical_sha256(event_mapping),
            "ordered_pose_ids_sha256": canonical_sha256(pose_ids),
            "negative_commit_pose_ids_sha256": canonical_sha256(negative_ids),
            "pose_input_count": len(pose_mapping),
            "pose_inputs_sha256": canonical_sha256(pose_mapping),
            "neutral_inputs_sha256": canonical_sha256({
                "registry": window.to_mapping(), "events": event_mapping,
                "poses": pose_mapping,
            }),
            "causal_cav_preflight_sha256": preflight[window_id],
        })
    dependency_rows = [dict(row) for row in dependencies]
    all_event_ids = {
        event.event_id for values in events.values() for event in values
    }
    body = {
        "schema": _SEAL_SCHEMA,
        "source_lock_sha256": _source_lock_sha256(registry),
        "source_member_sha256": _source_member_sha256(sources.pins),
        "source_events_size_bytes": sources.pins.events_size_bytes,
        "source_events_line_count": sources.pins.events_line_count,
        "stage12_freeze_receipt_sha256": authority.freeze_sha256,
        "stage12_source_split_plan_sha256": authority.plan_sha256,
        "candidate_screen_preroll_rule_sha256": authority.rule_sha256,
        "pre_roll_ns": STAGE3_PREROLL_NS,
        "selector_registry_sha256": registry["registry_sha256"],
        "selector_implementation_sha256": registry["bindings"]["selector_py_sha256"],  # type: ignore[index]
        "projection_dependency_manifest": dependency_rows,
        "projection_dependency_aggregate_sha256": canonical_sha256(dependency_rows),
        "neutral_registry_sha256": canonical_sha256(
            [window.to_mapping() for window in windows]
        ),
        "selector_labels_sidecar_sha256": canonical_sha256(labels),
        "window_count": len(windows),
        "source_window_event_count": sum(len(values) for values in events.values()),
        "unique_source_event_count": len(all_event_ids),
        "warmup_event_count": sum(
            1 for values in events.values() for event in values if not event.is_query
        ),
        "query_event_count": sum(
            1 for values in events.values() for event in values if event.is_query
        ),
        "selected_pose_packet_count": sum(len(values) for values in poses.values()),
        "windows": window_seals,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _validate_seal(seal: object) -> Mapping[str, object]:
    if not isinstance(seal, Mapping) or frozenset(seal) != _SEAL_FIELDS:
        raise Stage3New108AdapterError("Stage3 adapter provenance field schema differs")
    if seal.get("schema") != _SEAL_SCHEMA:
        raise Stage3New108AdapterError("Stage3 adapter provenance schema differs")
    members = seal.get("source_member_sha256")
    if not isinstance(members, Mapping) or frozenset(members) != _SOURCE_FIELDS:
        raise Stage3New108AdapterError("Stage3 source member schema differs")
    dependencies = seal.get("projection_dependency_manifest")
    if not isinstance(dependencies, list) or not dependencies:
        raise Stage3New108AdapterError("Stage3 dependency manifest differs")
    paths = []
    for row in dependencies:
        if not isinstance(row, Mapping) or frozenset(row) != _DEPENDENCY_FIELDS:
            raise Stage3New108AdapterError("Stage3 dependency row schema differs")
        paths.append(row["path"])
    if paths != list(_DEPENDENCY_PATHS):
        raise Stage3New108AdapterError("Stage3 dependency paths differ")
    if seal.get("projection_dependency_aggregate_sha256") != canonical_sha256(dependencies):
        raise Stage3New108AdapterError("Stage3 dependency aggregate differs")
    windows = seal.get("windows")
    if not isinstance(windows, list):
        raise Stage3New108AdapterError("Stage3 per-window provenance differs")
    for row in windows:
        if not isinstance(row, Mapping) or frozenset(row) != _WINDOW_FIELDS:
            raise Stage3New108AdapterError("Stage3 per-window field schema differs")
    unsigned = dict(seal)
    aggregate = unsigned.pop("aggregate_sha256")
    if aggregate != canonical_sha256(unsigned):
        raise Stage3New108AdapterError("Stage3 aggregate provenance hash differs")
    return seal


def _project(
    registry: Mapping[str, object], sources: ValidatedSources,
) -> New108AdapterBundle:
    _authenticate_registry_snapshot(registry, sources)
    _source_lock_sha256(registry)
    _validate_calibration_snapshot(sources)
    authority = _stage12_authority()
    dependencies = _dependency_manifest()
    rows = _selector_windows(registry)
    windows = _neutral_registry(rows)
    labels = _labels(rows)
    source_poses = _load_pinned_pose_samples(sources)
    poses = _pose_inputs(windows, source_poses)
    events, evidence = _read_events(sources, rows, windows, poses)
    preflight = _preflight(windows, events, poses)
    if authority != _stage12_authority() or dependencies != _dependency_manifest():
        raise Stage3New108AdapterError("Stage3 authority changed during projection")
    seal = _seal(
        registry, sources, rows, windows, events, poses, labels, evidence,
        preflight, authority, dependencies,
    )
    return New108AdapterBundle(registry, windows, events, poses, labels, seal)


def build_locked_stage3_new108_adapter(
    dataset_directory: Path,
) -> New108AdapterBundle:
    """Build independent-reset 50 ms replay inputs for the locked 108 queries."""

    root = Path(dataset_directory)
    registry = select_full_source(root)
    if registry.get("window_count") != EXPECTED_WINDOW_COUNT:
        raise Stage3New108AdapterError("production selector did not return 108 windows")
    sources = validate_sources(root)
    try:
        bundle = _project(registry, sources)
    except New108AdapterError as exc:
        raise Stage3New108AdapterError(
            "legacy source authentication failed during Stage3 build"
        ) from exc
    return bundle


def _verify_against_pinned_source(
    bundle: New108AdapterBundle, expected_registry: Mapping[str, object],
    sources: ValidatedSources,
) -> str:
    if type(bundle) is not New108AdapterBundle:
        raise Stage3New108AdapterError("Stage3 adapter bundle type differs")
    _authenticate_registry_snapshot(expected_registry, sources)
    if bundle.selector_registry != expected_registry:
        raise Stage3New108AdapterError("selector registry differs from authority")
    seal = _validate_seal(bundle.provenance_seal)
    reconstructed = _project(expected_registry, sources)
    if bundle.neutral_registry != reconstructed.neutral_registry:
        raise Stage3New108AdapterError("Stage3 neutral bounds differ")
    if bundle.selector_labels != reconstructed.selector_labels:
        raise Stage3New108AdapterError("selector label sidecar differs")
    if bundle.event_streams != reconstructed.event_streams:
        raise Stage3New108AdapterError("Stage3 event inputs differ from pinned source")
    if bundle.pose_streams != reconstructed.pose_streams:
        raise Stage3New108AdapterError("Stage3 pose inputs differ from pinned source")
    if dict(seal) != dict(reconstructed.provenance_seal):
        raise Stage3New108AdapterError("Stage3 provenance differs from source")
    return str(seal["aggregate_sha256"])


def verify_stage3_new108_adapter(
    bundle: New108AdapterBundle, dataset_directory: Path,
) -> str:
    """Re-authenticate the cohort/source and reconstruct every Stage3 input."""

    root = Path(dataset_directory)
    registry = select_full_source(root)
    if registry.get("window_count") != EXPECTED_WINDOW_COUNT:
        raise Stage3New108AdapterError("production selector did not return 108 windows")
    sources = validate_sources(root)
    try:
        return _verify_against_pinned_source(bundle, registry, sources)
    except New108AdapterError as exc:
        raise Stage3New108AdapterError(
            "legacy source authentication failed during Stage3 verification"
        ) from exc


__all__ = [
    "STAGE3_PREROLL_NS", "Stage3New108AdapterError",
    "build_locked_stage3_new108_adapter", "verify_stage3_new108_adapter",
]
