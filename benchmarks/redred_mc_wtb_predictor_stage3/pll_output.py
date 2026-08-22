"""Locked, score-blind Stage-3 output generator for the SO(3) PLL.

The generator consumes the neutral NEW108 event/pose streams and the frozen
current-CAV cycle receipts.  It never reads selector labels or event scores.
Each 50 ms pre-roll window gets a fresh :class:`SO3PLLModel`; events on an edge
are predicted before pose packets committed on that edge are applied.

Only a locked PLL forecast supplies replacement geometry.  Every other case
is recorded as an exact ``CURRENT_CAV`` fallback with no replacement ray, so
the candidate-neutral screen reuses its frozen CAV/ZOH/sensor-fixed decision
byte for byte.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import re
from pathlib import Path
from typing import Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_pose_recovery import (
    GeometryError,
    rotate_sensor_ray_to_world,
)
from benchmarks.redred_mc_wtb_predictor_stage3.so3_pll import (
    SO3PLLConfig,
    SO3PLLModel,
)
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_output/v1"
CANDIDATE_ID = "SO3_PLL_A5_V1_LOCKED_B5"
MODEL_ID = CANDIDATE_ID
PREROLL_NS = 50_000_000
LOCKED_PLL_CONFIG = SO3PLLConfig()

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE_CONFIG_SCHEMA = "redred.mc_wtb_predictor_stage3.so3_pll_config/v1"


class PLLOutputError(ValueError):
    """A neutral input, replay, binding, or output invariant failed."""


def locked_config_mapping() -> Mapping[str, object]:
    """Return the complete, outcome-independent numeric configuration."""

    return {
        "schema": _CANDIDATE_CONFIG_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "pll": asdict(LOCKED_PLL_CONFIG),
        "reset": "each_window_warmup_start",
        "pre_roll_ns": PREROLL_NS,
        "same_edge_priority": "events_before_pose_commits",
        "fallback": "exact_current_cav",
    }


def locked_config_bytes() -> bytes:
    """Return the exact bytes whose file hash binds the locked configuration."""

    return canonical_json_bytes(locked_config_mapping())


def locked_config_sha256() -> str:
    return hashlib.sha256(locked_config_bytes()).hexdigest()


def generator_executable_sha256() -> str:
    """Hash this generator exactly as supplied to the screen."""

    try:
        payload = Path(__file__).read_bytes()
    except OSError as exc:
        raise PLLOutputError("cannot read PLL output generator") from exc
    return hashlib.sha256(payload).hexdigest()


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise PLLOutputError("%s must be lowercase SHA-256" % where)
    return value


def _seal_windows(
    adapter_sha256: str,
    neutral_input_sha256: str,
    executable_sha256: str,
    config_sha256: str,
    windows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    """Apply the exact candidate-output/v1 event, window, and aggregate seals."""

    sealed_windows = []
    for window in windows:
        supplied_events = window["events"]
        events = []
        for supplied in supplied_events:  # type: ignore[union-attr]
            body = dict(supplied)
            events.append(dict(body, decision_sha256=canonical_sha256(body)))
        sealed_windows.append({
            "window_id": window["window_id"],
            "events": events,
            "events_sha256": canonical_sha256(events),
        })
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "adapter_aggregate_sha256": adapter_sha256,
        "neutral_input_sha256": neutral_input_sha256,
        "candidate_executable_sha256": executable_sha256,
        "candidate_config_sha256": config_sha256,
        "windows": sealed_windows,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _bound_windows(bundle: object, baseline: object) -> Tuple[object, ...]:
    """Validate only neutral identities and cycle evidence needed by replay."""

    try:
        registry = tuple(bundle.neutral_registry)  # type: ignore[attr-defined]
        event_streams = bundle.event_streams  # type: ignore[attr-defined]
        pose_streams = bundle.pose_streams  # type: ignore[attr-defined]
        windows = tuple(baseline.windows)  # type: ignore[attr-defined]
    except (AttributeError, TypeError) as exc:
        raise PLLOutputError("neutral bundle or baseline shape differs") from exc
    if not registry or len(registry) != len(windows):
        raise PLLOutputError("neutral registry and baseline windows differ")
    identifiers = tuple(row.window_id for row in registry)
    if len(set(identifiers)) != len(identifiers):
        raise PLLOutputError("neutral registry window IDs are duplicated")
    if (
        set(event_streams) != set(identifiers)
        or set(pose_streams) != set(identifiers)
    ):
        raise PLLOutputError("neutral stream window IDs differ")
    for registry_row, window in zip(registry, windows):
        window_id = registry_row.window_id
        if window.registry != registry_row:
            raise PLLOutputError("baseline registry identity differs")
        if tuple(event_streams[window_id]) != tuple(window.input_events):
            raise PLLOutputError("baseline event inputs differ from neutral stream")
        if tuple(pose_streams[window_id]) != tuple(window.input_poses):
            raise PLLOutputError("baseline pose inputs differ from neutral stream")
        if (
            registry_row.query_start_ns_inclusive
            - registry_row.warmup_start_ns_inclusive
            != PREROLL_NS
        ):
            raise PLLOutputError("window does not have the locked 50 ms pre-roll")
        if len(window.simulation.records) != len(window.input_events):
            raise PLLOutputError("baseline changed neutral event cardinality")
    return windows


def _fallback_row(
    event: object,
    cycle: int,
    state_version: int,
    reason: str,
) -> Mapping[str, object]:
    return {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "decision_cycle": cycle,
        "model_id": "CURRENT_CAV",
        "predictor_state_version": state_version,
        "used_pose_ids": [],
        "candidate_used": False,
        "fallback_reason": reason,
        "world_ray": None,
    }


def _replay_window(window: object) -> Mapping[str, object]:
    events = tuple(window.input_events)
    decisions = tuple(window.simulation.records)
    if not events:
        raise PLLOutputError("neutral event stream is empty")

    event_edges = {}  # type: Dict[int, list]
    previous_cycle = -1
    previous_timestamp = -1
    for index, (event, decision) in enumerate(zip(events, decisions)):
        cycle = decision.occurrence_cycle
        if (
            decision.event_id != event.event_id
            or decision.event_timestamp_ns != event.timestamp_ns
        ):
            raise PLLOutputError("baseline event cycle receipt identity differs")
        if cycle < 0 or cycle < previous_cycle or event.timestamp_ns < previous_timestamp:
            raise PLLOutputError("neutral events are not chronological by edge")
        previous_cycle = cycle
        previous_timestamp = event.timestamp_ns
        event_edges.setdefault(cycle, []).append((index, event, decision))

    last_event_cycle = previous_cycle
    pose_edges = {}  # type: Dict[int, list]
    for pose in window.input_poses:
        # State is reset at cycle zero.  A support pose committed before that
        # boundary remains baseline evidence, but cannot initialize this PLL.
        if 0 <= pose.commit_cycle <= last_event_cycle:
            pose_edges.setdefault(pose.commit_cycle, []).append(pose)

    model = SO3PLLModel(LOCKED_PLL_CONFIG)
    rows = [None] * len(events)
    for cycle in sorted(set(event_edges) | set(pose_edges)):
        # Read the immutable pre-edge state for every event before publishing
        # any pose residual update from this cycle.
        for index, event, baseline_decision in event_edges.get(cycle, ()):
            pll = model.predict(event.timestamp_ns, cycle)
            state_version = 0 if pll.state_version is None else pll.state_version
            if not pll.candidate_used or pll.quaternion_xyzw is None:
                candidate_reason = pll.reason.split(":", 1)[0]
                rows[index] = _fallback_row(
                    event,
                    cycle,
                    state_version,
                    "SO3_PLL:%s" % candidate_reason,
                )
                continue
            anchor = pll.anchor_pose_id
            occurrence_ids = set(baseline_decision.occurrence_pose_ids)
            if (
                anchor is None
                or anchor not in occurrence_ids
                or baseline_decision.disposition != "corrected_world_ray"
            ):
                rows[index] = _fallback_row(
                    event, cycle, state_version, "SO3_PLL:baseline_guard"
                )
                continue
            try:
                world_ray = rotate_sensor_ray_to_world(
                    pll.quaternion_xyzw, event.sensor_ray
                )
            except GeometryError:
                rows[index] = _fallback_row(
                    event, cycle, state_version, "SO3_PLL:world_ray_failure"
                )
                continue
            rows[index] = {
                "event_id": event.event_id,
                "event_content_sha256": event.event_content_sha256,
                "decision_cycle": cycle,
                "model_id": MODEL_ID,
                "predictor_state_version": state_version,
                "used_pose_ids": [anchor],
                "candidate_used": True,
                "fallback_reason": None,
                "world_ray": list(world_ray),
            }
        for pose in pose_edges.get(cycle, ()):
            model.commit_pose(
                pose.pose_id,
                pose.timestamp_ns,
                pose.commit_cycle,
                pose.quaternion_xyzw,
                valid=bool(pose.value_valid and pose.arithmetic_valid),
            )

    if any(row is None for row in rows):
        raise PLLOutputError("PLL replay omitted a neutral event")
    return {"window_id": window.registry.window_id, "events": rows}


def generate_locked_pll_output(bundle: object, baseline: object) -> Mapping[str, object]:
    """Generate sealed, screen108-compatible SO3-PLL decisions in memory."""

    windows = _bound_windows(bundle, baseline)
    try:
        adapter_sha256 = bundle.provenance_seal.get("aggregate_sha256")  # type: ignore[attr-defined]
        neutral_input_sha256 = baseline.neutral_input_sha256  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise PLLOutputError("neutral provenance binding is missing") from exc
    return _seal_windows(
        _sha256(adapter_sha256, "adapter aggregate"),
        _sha256(neutral_input_sha256, "neutral input"),
        generator_executable_sha256(),
        locked_config_sha256(),
        tuple(_replay_window(window) for window in windows),
    )


__all__ = (
    "CANDIDATE_ID",
    "CANDIDATE_OUTPUT_SCHEMA",
    "LOCKED_PLL_CONFIG",
    "MODEL_ID",
    "PLLOutputError",
    "PREROLL_NS",
    "generate_locked_pll_output",
    "generator_executable_sha256",
    "locked_config_bytes",
    "locked_config_mapping",
    "locked_config_sha256",
)
