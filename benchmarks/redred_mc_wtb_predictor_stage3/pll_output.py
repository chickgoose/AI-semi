"""Dependency-bound, score-blind output producer for the native SO(3) PLL.

Each neutral window constructs a new :class:`SO3PLLModel` at the 50 ms
pre-roll boundary.  An event occurrence is recorded one cycle before its
immutable decision edge.  Events read the old state before same-edge pose
commits publish a new state effective on the following edge.

The PLL is attempted only when the frozen baseline route is exact causal CAV.
Otherwise the receipt preserves the actual CAV-policy route (CAV, fresh ZOH,
or sensor-fixed), pose citations, and reason without supplying replacement
geometry.  Candidate-use rows cite the complete pose dependency chain of the
visible PLL state rather than an obsolete two-pose occurrence snapshot.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import re
from typing import Dict, Mapping, Optional, Sequence, Tuple

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


CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_output/v2"
PREROLL_NS = 50_000_000
LOCKED_PLL_CONFIG = SO3PLLConfig()
CANDIDATE_ID = LOCKED_PLL_CONFIG.candidate_id
MODEL_ID = CANDIDATE_ID

ROUTE_CANDIDATE = "CANDIDATE"
ROUTE_CURRENT_CAV = "CURRENT_CAV"
ROUTE_FRESH_ZOH = "FRESH_ZOH"
ROUTE_SENSOR_FIXED = "SENSOR_FIXED"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CONFIG_SCHEMA = "redred.mc_wtb_predictor_stage3.so3_pll_config/v2"
_DEPENDENCY_SCHEMA = "redred.mc_wtb_predictor_stage3.executable_closure/v1"
_RESET_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_reset/v1"
_STATE_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_state/v1"
_TRANSITION_SCHEMA = "redred.mc_wtb_predictor_stage3.pll_transition/v1"

_DEPENDENCY_PATHS = (
    "benchmarks/redred_mc_wtb_predictor_stage3/__init__.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/framework.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/pll_output.py",
    "benchmarks/redred_mc_wtb_predictor_stage3/so3_pll.py",
    "benchmarks/redred_mc_wtb_pose_recovery/__init__.py",
    "benchmarks/redred_mc_wtb_pose_recovery/geometry.py",
    "benchmarks/redred_mc_wtb_stage4_contract/__init__.py",
    "benchmarks/redred_mc_wtb_stage4_contract/contract.py",
    "benchmarks/redred_mc_wtb_stage4_contract/receipt.py",
)


class PLLOutputError(ValueError):
    """A neutral input, causal replay, state chain, or seal failed."""


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise PLLOutputError("%s must be lowercase SHA-256" % where)
    return value


def locked_config_mapping() -> Mapping[str, object]:
    """Return every material native configuration value and wrapper rule."""

    return {
        "schema": _CONFIG_SCHEMA,
        "candidate_id": LOCKED_PLL_CONFIG.candidate_id,
        "pll": asdict(LOCKED_PLL_CONFIG),
        "pre_roll_ns": PREROLL_NS,
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


def locked_config_bytes() -> bytes:
    return canonical_json_bytes(locked_config_mapping())


def locked_config_sha256() -> str:
    return hashlib.sha256(locked_config_bytes()).hexdigest()


def executable_dependency_manifest() -> Mapping[str, object]:
    """Return hashes for the complete repository-local execution closure."""

    root = Path(__file__).resolve().parents[2]
    files = []
    for relative in _DEPENDENCY_PATHS:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
            payload = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise PLLOutputError("cannot read executable dependency %s" % relative) from exc
        files.append({
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    return {"schema": _DEPENDENCY_SCHEMA, "files": files}


def generator_executable_sha256() -> str:
    """Hash the manifest of all repository-local executable dependencies."""

    return canonical_sha256(executable_dependency_manifest())


def _bound_windows(bundle: object, baseline: object) -> Tuple[object, ...]:
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
    if set(event_streams) != set(identifiers) or set(pose_streams) != set(identifiers):
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
        poses = tuple(window.input_poses)
        if len({pose.pose_id for pose in poses}) != len(poses):
            raise PLLOutputError("neutral pose IDs repeat")
        if any(
            left.pose_id >= right.pose_id
            or left.timestamp_ns >= right.timestamp_ns
            for left, right in zip(poses, poses[1:])
        ):
            raise PLLOutputError("neutral poses are not strictly chronological")
        active_cycles = [pose.commit_cycle for pose in poses if pose.commit_cycle >= 0]
        if len(set(active_cycles)) != len(active_cycles):
            raise PLLOutputError("post-reset pose commit cycles repeat")
    return windows


def _pose_evidence(
    baseline: object,
    event: object,
    poses_by_id: Mapping[int, object],
    decision_cycle: int,
) -> None:
    fields = (
        baseline.occurrence_pose_ids,
        baseline.occurrence_pose_timestamps_ns,
        baseline.occurrence_pose_commit_cycles,
        baseline.occurrence_pose_sha256,
    )
    if len({len(value) for value in fields}) != 1:
        raise PLLOutputError("baseline occurrence pose evidence differs")
    if tuple(baseline.occurrence_pose_ids) != tuple(
        sorted(set(baseline.occurrence_pose_ids))
    ):
        raise PLLOutputError("baseline occurrence pose IDs differ")
    for pose_id, timestamp, commit, digest in zip(*fields):
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.timestamp_ns != timestamp
            or pose.commit_cycle != commit
            or pose.pose_sha256 != digest
            or commit >= decision_cycle
            or timestamp > event.timestamp_ns
        ):
            raise PLLOutputError("baseline occurrence pose is not causal")
    used_fields = (
        baseline.used_pose_ids,
        baseline.used_pose_timestamps_ns,
        baseline.used_pose_commit_cycles,
        baseline.used_pose_sha256,
    )
    if len({len(value) for value in used_fields}) != 1:
        raise PLLOutputError("baseline used pose evidence differs")
    if not set(baseline.used_pose_ids).issubset(set(baseline.occurrence_pose_ids)):
        raise PLLOutputError("baseline used pose is not occurrence-visible")
    for pose_id, timestamp, commit, digest in zip(*used_fields):
        pose = poses_by_id.get(pose_id)
        if (
            pose is None
            or pose.timestamp_ns != timestamp
            or pose.commit_cycle != commit
            or pose.pose_sha256 != digest
            or commit >= decision_cycle
            or timestamp > event.timestamp_ns
        ):
            raise PLLOutputError("baseline used pose is not causal")
    if baseline.occurrence_pose_ids:
        latest = baseline.occurrence_pose_ids[-1]
        if event.causal_pose_source_index != latest:
            raise PLLOutputError("neutral causal pose source index differs")


def _baseline_route(baseline: object) -> str:
    if (
        baseline.disposition == "corrected_world_ray"
        and baseline.disposition_reason == "causal_cav"
    ):
        return ROUTE_CURRENT_CAV
    if (
        baseline.disposition == "corrected_world_ray"
        and baseline.disposition_reason == "fresh_zoh_fallback"
    ):
        return ROUTE_FRESH_ZOH
    if baseline.disposition == "raw_bypass" and baseline.disposition_reason in (
        "no_occurrence_pose",
        "invalid_pose",
        "stale_pose",
        "fifo_full_forced_bypass",
    ):
        return ROUTE_SENSOR_FIXED
    raise PLLOutputError("baseline route or reason is not exact")


def _reset_receipt(
    generation: int, window_id: str, warmup_start_ns: int
) -> Mapping[str, object]:
    initial_body = {
        "schema": _STATE_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "configuration_sha256": locked_config_sha256(),
        "reset_generation": generation,
        "version_id": None,
        "effective_cycle": 0,
        "publication_cycle": 0,
        "parent_state_sha256": None,
        "transition_reason": "window_reset",
        "native_state": None,
        "dependency_pose_ids": [],
    }
    initial_sha = canonical_sha256(initial_body)
    body = {
        "schema": _RESET_SCHEMA,
        "reset_generation": generation,
        "reset_cycle": 0,
        "window_id": window_id,
        "warmup_start_ns": warmup_start_ns,
        "candidate_id": CANDIDATE_ID,
        "configuration_sha256": locked_config_sha256(),
        "initial_state": initial_body,
        "initial_state_sha256": initial_sha,
        "prior_window_state_sha256": None,
    }
    return dict(body, reset_sha256=canonical_sha256(body))


def _state_body(
    state: ForecastState,
    generation: int,
    parent_sha256: str,
    transition_reason: str,
    dependencies: Sequence[int],
) -> Mapping[str, object]:
    return {
        "schema": _STATE_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "configuration_sha256": locked_config_sha256(),
        "reset_generation": generation,
        "version_id": state.state_version,
        "effective_cycle": state.effective_cycle,
        "publication_cycle": state.effective_cycle,
        "parent_state_sha256": parent_sha256,
        "transition_reason": transition_reason,
        "native_state": asdict(state),
        "dependency_pose_ids": list(dependencies),
    }


def _commit_pose(
    model: SO3PLLModel,
    pose: object,
    generation: int,
    reset: Mapping[str, object],
    states: Dict[int, Mapping[str, object]],
    dependencies: Sequence[int],
) -> Tuple[Mapping[str, object], Tuple[int, ...]]:
    parent = model.current_state
    if parent is None:
        parent_sha = str(reset["initial_state_sha256"])
    else:
        parent_sha = str(states[parent.state_version]["state_sha256"])
    try:
        native = model.commit_pose(
            pose.pose_id,
            pose.timestamp_ns,
            pose.commit_cycle,
            pose.quaternion_xyzw,
            valid=bool(pose.value_valid and pose.arithmetic_valid),
        )
    except SO3PLLError as exc:
        raise PLLOutputError("native PLL pose replay failed") from exc

    next_dependencies = tuple(dependencies)
    published_sha = None  # type: Optional[str]
    published_state = None  # type: Optional[Mapping[str, object]]
    if native.accepted:
        next_dependencies = tuple(dependencies) + (pose.pose_id,)
        state = model.current_state
        if state is None or state.state_version != native.published_state_version:
            raise PLLOutputError("native PLL publication identity differs")
        reason = native.fault_reason or native.update_kind
        body = _state_body(
            state, generation, parent_sha, reason, next_dependencies
        )
        published_sha = canonical_sha256(body)
        published_state = dict(body, state_sha256=published_sha)
        states[state.state_version] = published_state
    elif model.current_state != parent:
        raise PLLOutputError("rejected pose changed native PLL state")

    transition = {
        "schema": _TRANSITION_SCHEMA,
        "reset_generation": generation,
        "pose_id": pose.pose_id,
        "pose_sha256": pose.pose_sha256,
        "commit_cycle": pose.commit_cycle,
        "accepted": native.accepted,
        "source_state_version": native.source_state_version,
        "parent_state_sha256": parent_sha,
        "forecast_generation_cycle": native.forecast_generation_cycle,
        "published_state_version": native.published_state_version,
        "published_state": published_state,
        "published_state_sha256": published_sha,
        "effective_cycle": native.effective_cycle,
        "publication_cycle": native.effective_cycle,
        "transition_reason": native.fault_reason or native.update_kind,
        "dependency_pose_ids": list(next_dependencies),
        "native_receipt_sha256": canonical_sha256(asdict(native)),
    }
    return (
        dict(transition, transition_sha256=canonical_sha256(transition)),
        next_dependencies,
    )


def _state_pointer(
    state_version: Optional[int],
    reset: Mapping[str, object],
    states: Mapping[int, Mapping[str, object]],
) -> Mapping[str, object]:
    if state_version is None:
        return {
            "predictor_state_version": 0,
            "predictor_state_is_reset": True,
            "state_sha256": reset["initial_state_sha256"],
            "state_parent_sha256": None,
            "state_effective_cycle": 0,
            "state_publication_cycle": 0,
            "state_transition_reason": "window_reset",
            "state_dependency_pose_ids": [],
        }
    state = states.get(state_version)
    if state is None:
        raise PLLOutputError("native PLL decision cites an unknown state")
    return {
        "predictor_state_version": state_version,
        "predictor_state_is_reset": False,
        "state_sha256": state["state_sha256"],
        "state_parent_sha256": state["parent_state_sha256"],
        "state_effective_cycle": state["effective_cycle"],
        "state_publication_cycle": state["publication_cycle"],
        "state_transition_reason": state["transition_reason"],
        "state_dependency_pose_ids": state["dependency_pose_ids"],
    }


def _event_row(
    event: object,
    baseline: object,
    model: SO3PLLModel,
    reset: Mapping[str, object],
    states: Mapping[int, Mapping[str, object]],
    poses_by_id: Mapping[int, object],
) -> Mapping[str, object]:
    decision_cycle = baseline.occurrence_cycle
    if decision_cycle < 0:
        raise PLLOutputError("decision edge has no nonnegative prior occurrence edge")
    if (
        baseline.event_id != event.event_id
        or baseline.event_timestamp_ns != event.timestamp_ns
    ):
        raise PLLOutputError("baseline event identity differs")
    _pose_evidence(baseline, event, poses_by_id, decision_cycle)
    baseline_route = _baseline_route(baseline)
    attempted = baseline_route == ROUTE_CURRENT_CAV

    native = None
    if attempted:
        try:
            native = model.predict(event.timestamp_ns, decision_cycle)
        except SO3PLLError as exc:
            raise PLLOutputError("native PLL event replay failed") from exc
        state_version = native.state_version
    else:
        visible_versions = tuple(
            version
            for version, state in states.items()
            if state["effective_cycle"] <= decision_cycle
        )
        state_version = max(visible_versions) if visible_versions else None
    pointer = _state_pointer(state_version, reset, states)

    route = baseline_route
    candidate_used = False
    candidate_failure_reason = None
    candidate_quaternion = None
    world_ray = None
    used_pose_ids = tuple(baseline.used_pose_ids)
    fallback_reason = baseline.disposition_reason
    model_id = "CURRENT_CAV"

    if native is not None and native.candidate_used:
        if native.quaternion_xyzw is None:
            raise PLLOutputError("native PLL candidate lacks a quaternion")
        dependencies = tuple(pointer["state_dependency_pose_ids"])
        if not dependencies or native.anchor_pose_id != dependencies[-1]:
            raise PLLOutputError("native PLL anchor differs from state dependencies")
        for pose_id in dependencies:
            pose = poses_by_id.get(pose_id)
            if (
                pose is None
                or pose.commit_cycle >= decision_cycle
                or pose.timestamp_ns > event.timestamp_ns
                or not pose.value_valid
                or not pose.arithmetic_valid
            ):
                raise PLLOutputError("PLL state dependency is not causal and valid")
        try:
            world = rotate_sensor_ray_to_world(
                native.quaternion_xyzw, event.sensor_ray
            )
        except GeometryError as exc:
            raise PLLOutputError("PLL world-ray projection failed") from exc
        norm = sum(component * component for component in world) ** 0.5
        if abs(norm - 1.0) > 1.0e-12:
            raise PLLOutputError("PLL world ray is not normalized")
        route = ROUTE_CANDIDATE
        candidate_used = True
        candidate_quaternion = list(native.quaternion_xyzw)
        world_ray = list(world)
        used_pose_ids = dependencies
        fallback_reason = None
        model_id = CANDIDATE_ID
    elif native is not None:
        candidate_failure_reason = native.reason.split(":", 1)[0]

    body = {
        "event_id": event.event_id,
        "event_content_sha256": event.event_content_sha256,
        "occurrence_cycle": decision_cycle - 1,
        "decision_cycle": decision_cycle,
        "model_id": model_id,
        "configuration_sha256": locked_config_sha256(),
        "reset_generation": reset["reset_generation"],
        **pointer,
        "used_pose_ids": list(used_pose_ids),
        "candidate_attempted": attempted,
        "candidate_used": candidate_used,
        "candidate_failure_reason": candidate_failure_reason,
        "route": route,
        "fallback_reason": fallback_reason,
        "candidate_quaternion_xyzw": candidate_quaternion,
        "world_ray": world_ray,
    }
    return dict(body, decision_sha256=canonical_sha256(body))


def _replay_window(window: object, generation: int) -> Mapping[str, object]:
    events = tuple(window.input_events)
    decisions = tuple(window.simulation.records)
    poses = tuple(window.input_poses)
    if not events:
        raise PLLOutputError("neutral event stream is empty")
    poses_by_id = {pose.pose_id: pose for pose in poses}
    event_edges = {}  # type: Dict[int, list]
    previous_cycle = -1
    previous_timestamp = -1
    for index, (event, decision) in enumerate(zip(events, decisions)):
        cycle = decision.occurrence_cycle
        if cycle < previous_cycle or event.timestamp_ns < previous_timestamp:
            raise PLLOutputError("neutral events are not chronological by edge")
        previous_cycle = cycle
        previous_timestamp = event.timestamp_ns
        event_edges.setdefault(cycle, []).append((index, event, decision))

    last_event_cycle = previous_cycle
    pose_edges = {}  # type: Dict[int, list]
    for pose in poses:
        if 0 <= pose.commit_cycle <= last_event_cycle:
            pose_edges.setdefault(pose.commit_cycle, []).append(pose)

    reset = _reset_receipt(
        generation,
        window.registry.window_id,
        window.registry.warmup_start_ns_inclusive,
    )
    model = SO3PLLModel(LOCKED_PLL_CONFIG)
    states = {}  # type: Dict[int, Mapping[str, object]]
    dependencies = ()  # type: Tuple[int, ...]
    transitions = []
    rows = [None] * len(events)
    for cycle in sorted(set(event_edges) | set(pose_edges)):
        for index, event, baseline in event_edges.get(cycle, ()):
            rows[index] = _event_row(
                event, baseline, model, reset, states, poses_by_id
            )
        for pose in pose_edges.get(cycle, ()):
            transition, dependencies = _commit_pose(
                model, pose, generation, reset, states, dependencies
            )
            transitions.append(transition)

    if any(row is None for row in rows):
        raise PLLOutputError("PLL replay omitted a neutral event")
    body = {
        "window_id": window.registry.window_id,
        "baseline_decisions_sha256": canonical_sha256([
            decision.to_mapping() for decision in decisions
        ]),
        "reset": reset,
        "state_transitions": transitions,
        "state_transitions_sha256": canonical_sha256(transitions),
        "events": rows,
        "events_sha256": canonical_sha256(rows),
    }
    return dict(body, window_sha256=canonical_sha256(body))


def generate_locked_pll_output(bundle: object, baseline: object) -> Mapping[str, object]:
    """Replay native PLL windows and return the fully sealed v2 envelope."""

    windows = _bound_windows(bundle, baseline)
    try:
        adapter_sha = bundle.provenance_seal.get("aggregate_sha256")  # type: ignore[attr-defined]
        neutral_sha = baseline.neutral_input_sha256  # type: ignore[attr-defined]
    except AttributeError as exc:
        raise PLLOutputError("neutral provenance binding is missing") from exc
    dependencies = executable_dependency_manifest()
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "adapter_aggregate_sha256": _sha256(adapter_sha, "adapter aggregate"),
        "neutral_input_sha256": _sha256(neutral_sha, "neutral input"),
        "candidate_executable_sha256": canonical_sha256(dependencies),
        "candidate_executable_dependencies": dependencies,
        "candidate_config_sha256": locked_config_sha256(),
        "windows": [
            _replay_window(window, generation)
            for generation, window in enumerate(windows)
        ],
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def verify_locked_pll_output(
    value: object, bundle: object, baseline: object
) -> str:
    """Re-execute the dependency-bound producer and require exact equality."""

    expected = generate_locked_pll_output(bundle, baseline)
    if value != expected:
        raise PLLOutputError("PLL candidate output differs from exact native replay")
    return _sha256(expected["aggregate_sha256"], "PLL output aggregate")


__all__ = (
    "CANDIDATE_ID",
    "CANDIDATE_OUTPUT_SCHEMA",
    "LOCKED_PLL_CONFIG",
    "MODEL_ID",
    "PLLOutputError",
    "PREROLL_NS",
    "ROUTE_CANDIDATE",
    "ROUTE_CURRENT_CAV",
    "ROUTE_FRESH_ZOH",
    "ROUTE_SENSOR_FIXED",
    "executable_dependency_manifest",
    "generate_locked_pll_output",
    "generator_executable_sha256",
    "locked_config_bytes",
    "locked_config_mapping",
    "locked_config_sha256",
    "verify_locked_pll_output",
)
