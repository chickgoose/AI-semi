"""Candidate-neutral projection of native Stage-3 receipts into screen108 v2.

The projector does not execute a candidate and accepts no neutral inputs,
baseline decisions, labels, or scores.  It authenticates the complete native
envelope, reduces each decision to the exact screen108 v2 vocabulary, and
seals the reduced output with the screen's own sealing function.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Dict, Mapping, Sequence, Tuple

from benchmarks.redred_mc_wtb_predictor_stage3 import (
    dspb_output,
    pll_output,
    rg3_output,
    screen108,
)
from benchmarks.redred_mc_wtb_predictor_stage3.dspb import DSPBConfig
from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)


PROJECTION_RECEIPT_SCHEMA = (
    "redred.mc_wtb_predictor_stage3.screen_projection_receipt/v1"
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROUTES = {
    "CANDIDATE": "candidate",
    "CURRENT_CAV": "current_cav",
    "FRESH_ZOH": "fresh_zoh",
    "SENSOR_FIXED": "sensor_fixed",
}
_SCREEN_EVENT_BODY_FIELDS = frozenset((
    "event_id", "event_content_sha256", "occurrence_cycle", "decision_cycle",
    "model_id", "predictor_state_version", "used_pose_ids", "route",
    "candidate_attempted", "candidate_used", "fallback_reason", "world_ray",
))
_RG3_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "adapter_aggregate_sha256",
    "neutral_input_sha256", "candidate_executable_sha256",
    "candidate_config_sha256", "windows", "aggregate_sha256",
))
_RG3_WINDOW_FIELDS = frozenset(("window_id", "events", "events_sha256"))
_RG3_EVENT_FIELDS = _SCREEN_EVENT_BODY_FIELDS | frozenset(("decision_sha256",))
_PLL_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "adapter_aggregate_sha256",
    "neutral_input_sha256", "candidate_executable_sha256",
    "candidate_executable_dependencies", "candidate_config_sha256", "windows",
    "aggregate_sha256",
))
_PLL_WINDOW_FIELDS = frozenset((
    "window_id", "baseline_decisions_sha256", "reset", "state_transitions",
    "state_transitions_sha256", "events", "events_sha256", "window_sha256",
))
_PLL_EVENT_FIELDS = frozenset((
    "event_id", "event_content_sha256", "occurrence_cycle", "decision_cycle",
    "model_id", "configuration_sha256", "reset_generation",
    "predictor_state_version", "predictor_state_is_reset", "state_sha256",
    "state_parent_sha256", "state_effective_cycle", "state_publication_cycle",
    "state_transition_reason", "state_dependency_pose_ids", "used_pose_ids",
    "candidate_attempted", "candidate_used", "candidate_failure_reason", "route",
    "fallback_reason", "candidate_quaternion_xyzw", "world_ray",
    "decision_sha256",
))
_PLL_RESET_FIELDS = frozenset((
    "schema", "reset_generation", "reset_cycle", "window_id", "warmup_start_ns",
    "candidate_id", "configuration_sha256", "initial_state",
    "initial_state_sha256", "prior_window_state_sha256", "reset_sha256",
))
_PLL_STATE_BODY_FIELDS = frozenset((
    "schema", "candidate_id", "configuration_sha256", "reset_generation",
    "version_id", "effective_cycle", "publication_cycle", "parent_state_sha256",
    "transition_reason", "native_state", "dependency_pose_ids",
))
_PLL_TRANSITION_FIELDS = frozenset((
    "schema", "reset_generation", "pose_id", "pose_sha256", "commit_cycle",
    "accepted", "source_state_version", "parent_state_sha256",
    "forecast_generation_cycle", "published_state_version", "published_state",
    "published_state_sha256", "effective_cycle", "publication_cycle",
    "transition_reason", "dependency_pose_ids", "native_receipt_sha256",
    "transition_sha256",
))
_DSPB_TOP_FIELDS = frozenset((
    "schema", "candidate_id", "adapter_aggregate_sha256",
    "neutral_input_sha256", "candidate_executable_sha256",
    "candidate_executable_manifest", "candidate_config_sha256",
    "candidate_config", "windows", "aggregate_sha256",
))
_DSPB_WINDOW_FIELDS = frozenset((
    "window_id", "reset_receipt", "state_receipts", "state_receipts_sha256",
    "pose_receipts", "pose_receipts_sha256", "events", "events_sha256",
    "window_sha256",
))
_DSPB_EVENT_FIELDS = frozenset((
    "event_id", "event_content_sha256", "event_timestamp_ns", "is_query",
    "occurrence_cycle", "decision_cycle", "model_id", "geometry_expert_id",
    "predictor_state_version", "predictor_state_sha256",
    "state_dependency_pose_ids", "pose_receipt_chain_sha256", "used_pose_ids",
    "used_pose_evidence", "route", "route_reason", "candidate_attempted",
    "candidate_used", "candidate_failure_reason", "fallback_reason",
    "output_quaternion_xyzw", "world_ray", "ray_derivation_receipt",
    "native_decision_sha256", "prior_decision_sha256", "decision_sha256",
))
_DSPB_RESET_FIELDS = frozenset((
    "schema", "reset_generation_sha256", "generation",
    "previous_window_state_sha256", "initial_state_sha256",
    "reset_receipt_sha256",
))
_DSPB_GENERATION_FIELDS = frozenset((
    "window_id", "warmup_start_ns_inclusive", "query_start_ns_inclusive",
    "query_end_ns_exclusive", "candidate_id", "candidate_config_sha256",
    "reset_cycle", "excluded_pre_reset_pose_ids",
))
_DSPB_STATE_FIELDS = frozenset((
    "schema", "window_id", "reset_generation_sha256", "state_version",
    "effective_cycle", "parent_state_sha256", "transition_pose_id",
    "dependency_pose_ids", "native_state", "state_sha256",
))
_DSPB_POSE_FIELDS = frozenset((
    "schema", "pose_id", "pose_content_sha256", "prior_state_sha256",
    "next_state_sha256", "previous_pose_receipt_sha256",
    "native_pose_receipt", "pose_receipt_sha256",
))
_DSPB_NATIVE_POSE_FIELDS = frozenset((
    "candidate_id", "config_sha256", "pose_id", "measurement_timestamp_ns",
    "commit_cycle", "prior_state_version", "next_state_version",
    "next_effective_cycle", "scored_forecasts", "next_credits",
    "next_selected_expert_id", "next_lock_reason", "receipt_sha256",
))
_DSPB_RAY_FIELDS = frozenset((
    "event_content_sha256", "route", "sensor_ray", "output_quaternion_xyzw",
    "world_ray", "ray_derivation_sha256",
))
_DSPB_USED_POSE_FIELDS = frozenset((
    "pose_id", "measurement_timestamp_ns", "commit_cycle",
    "pose_content_sha256", "value_valid", "arithmetic_valid",
))


class ScreenProjectionError(ValueError):
    """A native receipt or projection invariant failed closed."""


@dataclass(frozen=True)
class ScreenProjection:
    """Authenticated projection products; attributes cannot be reassigned."""

    screen_output: Mapping[str, object]
    projection_receipt: Mapping[str, object]
    executable_artifact_bytes: bytes
    config_bytes: bytes


def _mapping(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise ScreenProjectionError("%s field schema differs" % where)
    return value


def _list(value: object, where: str, nonempty: bool = False) -> list:
    if not isinstance(value, list) or (nonempty and not value):
        raise ScreenProjectionError("%s must be %sa list" % (
            where, "a nonempty " if nonempty else "",
        ))
    return value


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ScreenProjectionError("%s must be lowercase SHA-256" % where)
    return value


def _direct_seal(value: Mapping[str, object], field: str, where: str) -> str:
    supplied = _sha256(value.get(field), "%s %s" % (where, field))
    body = dict(value)
    del body[field]
    if supplied != canonical_sha256(body):
        raise ScreenProjectionError("%s seal differs" % where)
    return supplied


def _native_dspb_seal(value: Mapping[str, object], field: str, where: str) -> str:
    supplied = _sha256(value.get(field), "%s %s" % (where, field))
    body = dict(value)
    del body[field]
    try:
        payload = json.dumps(
            body, allow_nan=False, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ScreenProjectionError("%s is not native canonical JSON" % where) from exc
    if supplied != hashlib.sha256(payload).hexdigest():
        raise ScreenProjectionError("%s native seal differs" % where)
    return supplied


def _artifact_and_config(
    native: Mapping[str, object], schema: str
) -> Tuple[str, bytes, bytes]:
    if schema == rg3_output.CANDIDATE_OUTPUT_SCHEMA:
        candidate_id = rg3_output.RG3_OUTPUT_CANDIDATE_ID
        artifact = rg3_output.RG3_EXECUTABLE_MANIFEST_BYTES
        config = rg3_output.RG3_CONFIG_BYTES
    elif schema == dspb_output.CANDIDATE_OUTPUT_SCHEMA:
        candidate_id = DSPBConfig().candidate_id
        manifest = dspb_output.locked_dspb_executable_manifest()
        if native.get("candidate_executable_manifest") != manifest:
            raise ScreenProjectionError("DSPB executable manifest differs from HEAD")
        body = dict(manifest)
        _direct_seal(body, "manifest_sha256", "DSPB executable manifest")
        del body["manifest_sha256"]
        artifact = canonical_json_bytes(body)
        config = dspb_output.locked_dspb_config_bytes()
        if native.get("candidate_config") != DSPBConfig().to_mapping():
            raise ScreenProjectionError("DSPB native config differs from HEAD")
    elif schema == pll_output.CANDIDATE_OUTPUT_SCHEMA:
        candidate_id = pll_output.CANDIDATE_ID
        dependencies = pll_output.executable_dependency_manifest()
        if native.get("candidate_executable_dependencies") != dependencies:
            raise ScreenProjectionError("PLL executable dependencies differ from HEAD")
        artifact = canonical_json_bytes(dependencies)
        config = pll_output.locked_config_bytes()
    else:  # pragma: no cover - dispatch rejects this first
        raise ScreenProjectionError("unsupported native candidate schema")

    if native.get("candidate_id") != candidate_id:
        raise ScreenProjectionError("native candidate ID differs")
    artifact_sha = hashlib.sha256(artifact).hexdigest()
    config_sha = hashlib.sha256(config).hexdigest()
    if native.get("candidate_executable_sha256") != artifact_sha:
        raise ScreenProjectionError("native executable digest differs")
    if native.get("candidate_config_sha256") != config_sha:
        raise ScreenProjectionError("native config digest differs")
    return candidate_id, artifact, config


def _verify_rg3(native: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    _mapping(native, _RG3_TOP_FIELDS, "RG3 output")
    windows = _list(native["windows"], "RG3 windows", nonempty=True)
    for window_index, supplied in enumerate(windows):
        window = _mapping(supplied, _RG3_WINDOW_FIELDS, "RG3 window")
        events = _list(window["events"], "RG3 events", nonempty=True)
        if window["events_sha256"] != canonical_sha256(events):
            raise ScreenProjectionError("RG3 window event seal differs")
        for event_index, supplied_event in enumerate(events):
            event = _mapping(supplied_event, _RG3_EVENT_FIELDS, "RG3 event")
            _direct_seal(
                event, "decision_sha256",
                "RG3 event %d:%d" % (window_index, event_index),
            )
    return windows


def _verify_pll(native: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    _mapping(native, _PLL_TOP_FIELDS, "PLL output")
    windows = _list(native["windows"], "PLL windows", nonempty=True)
    for window_index, supplied in enumerate(windows):
        window = _mapping(supplied, _PLL_WINDOW_FIELDS, "PLL window")
        reset = _mapping(window["reset"], _PLL_RESET_FIELDS, "PLL reset")
        _direct_seal(reset, "reset_sha256", "PLL reset")
        initial = _mapping(
            reset.get("initial_state"), _PLL_STATE_BODY_FIELDS,
            "PLL initial state",
        )
        if reset.get("initial_state_sha256") != canonical_sha256(initial):
            raise ScreenProjectionError("PLL initial-state seal differs")
        state_hashes = {_sha256(reset["initial_state_sha256"], "PLL initial state")}

        transitions = _list(window["state_transitions"], "PLL transitions")
        if window["state_transitions_sha256"] != canonical_sha256(transitions):
            raise ScreenProjectionError("PLL transition-list seal differs")
        for transition in transitions:
            transition = _mapping(
                transition, _PLL_TRANSITION_FIELDS, "PLL transition"
            )
            _direct_seal(transition, "transition_sha256", "PLL transition")
            published = transition.get("published_state")
            published_sha = transition.get("published_state_sha256")
            if published is None:
                if published_sha is not None:
                    raise ScreenProjectionError("PLL absent state has a digest")
            else:
                published = _mapping(
                    published,
                    _PLL_STATE_BODY_FIELDS | frozenset(("state_sha256",)),
                    "PLL published state",
                )
                state_sha = _direct_seal(published, "state_sha256", "PLL state")
                if published_sha != state_sha:
                    raise ScreenProjectionError("PLL transition state binding differs")
                state_hashes.add(state_sha)

        events = _list(window["events"], "PLL events", nonempty=True)
        if window["events_sha256"] != canonical_sha256(events):
            raise ScreenProjectionError("PLL window event seal differs")
        for event_index, supplied_event in enumerate(events):
            event = _mapping(supplied_event, _PLL_EVENT_FIELDS, "PLL event")
            _direct_seal(
                event, "decision_sha256",
                "PLL event %d:%d" % (window_index, event_index),
            )
            if event["state_sha256"] not in state_hashes:
                raise ScreenProjectionError("PLL event cites an unknown state")
            if event["configuration_sha256"] != native["candidate_config_sha256"]:
                raise ScreenProjectionError("PLL event config binding differs")
        _direct_seal(window, "window_sha256", "PLL window")
    return windows


def _verify_dspb(native: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    _mapping(native, _DSPB_TOP_FIELDS, "DSPB output")
    windows = _list(native["windows"], "DSPB windows", nonempty=True)
    for supplied in windows:
        window = _mapping(supplied, _DSPB_WINDOW_FIELDS, "DSPB window")
        reset = _mapping(
            window["reset_receipt"], _DSPB_RESET_FIELDS, "DSPB reset"
        )
        reset_sha = _direct_seal(reset, "reset_receipt_sha256", "DSPB reset")
        generation = _mapping(
            reset.get("generation"), _DSPB_GENERATION_FIELDS,
            "DSPB reset generation",
        )
        if reset.get("reset_generation_sha256") != canonical_sha256(generation):
            raise ScreenProjectionError("DSPB reset-generation seal differs")

        states = _list(window["state_receipts"], "DSPB states", nonempty=True)
        if window["state_receipts_sha256"] != canonical_sha256(states):
            raise ScreenProjectionError("DSPB state-list seal differs")
        state_by_sha = {}
        for state in states:
            state = _mapping(state, _DSPB_STATE_FIELDS, "DSPB state")
            digest = _direct_seal(state, "state_sha256", "DSPB state")
            state_by_sha[digest] = state
        if reset.get("initial_state_sha256") not in state_by_sha:
            raise ScreenProjectionError("DSPB reset initial state binding differs")

        poses = _list(window["pose_receipts"], "DSPB pose receipts")
        if window["pose_receipts_sha256"] != canonical_sha256(poses):
            raise ScreenProjectionError("DSPB pose-list seal differs")
        prior_pose_sha = reset_sha
        for pose in poses:
            pose = _mapping(pose, _DSPB_POSE_FIELDS, "DSPB pose receipt")
            digest = _direct_seal(pose, "pose_receipt_sha256", "DSPB pose receipt")
            if pose.get("previous_pose_receipt_sha256") != prior_pose_sha:
                raise ScreenProjectionError("DSPB pose receipt chain differs")
            if pose.get("prior_state_sha256") not in state_by_sha or pose.get("next_state_sha256") not in state_by_sha:
                raise ScreenProjectionError("DSPB pose receipt cites an unknown state")
            native_pose = _mapping(
                pose.get("native_pose_receipt"), _DSPB_NATIVE_POSE_FIELDS,
                "DSPB native pose receipt",
            )
            _native_dspb_seal(native_pose, "receipt_sha256", "DSPB native pose receipt")
            prior_pose_sha = digest

        events = _list(window["events"], "DSPB events", nonempty=True)
        if window["events_sha256"] != canonical_sha256(events):
            raise ScreenProjectionError("DSPB window event seal differs")
        prior_decision = None
        for supplied_event in events:
            event = _mapping(supplied_event, _DSPB_EVENT_FIELDS, "DSPB event")
            digest = _direct_seal(event, "decision_sha256", "DSPB event")
            if event["prior_decision_sha256"] != prior_decision:
                raise ScreenProjectionError("DSPB decision chain differs")
            prior_decision = digest
            if event["predictor_state_sha256"] not in state_by_sha:
                raise ScreenProjectionError("DSPB event cites an unknown state")
            for evidence in _list(event["used_pose_evidence"], "DSPB used-pose evidence"):
                _mapping(evidence, _DSPB_USED_POSE_FIELDS, "DSPB used-pose evidence")
            ray = _mapping(
                event["ray_derivation_receipt"], _DSPB_RAY_FIELDS,
                "DSPB ray receipt",
            )
            _direct_seal(ray, "ray_derivation_sha256", "DSPB ray receipt")
            for field in ("event_content_sha256", "route", "world_ray"):
                if ray.get(field) != event[field]:
                    raise ScreenProjectionError("DSPB ray receipt binding differs")
        _direct_seal(window, "window_sha256", "DSPB window")
    return windows


def _project_event(
    native_event: Mapping[str, object], candidate_id: str, native_schema: str
) -> Mapping[str, object]:
    route_native = native_event.get("route")
    if route_native not in _ROUTES:
        raise ScreenProjectionError("native route differs")
    route = _ROUTES[route_native]  # type: ignore[index]
    attempted = native_event.get("candidate_attempted")
    used = native_event.get("candidate_used")
    if type(attempted) is not bool or type(used) is not bool:
        raise ScreenProjectionError("native attempt/use flags differ")
    if used != (route == "candidate") or (used and not attempted):
        raise ScreenProjectionError("native candidate route semantics differ")
    if route == "current_cav" and not attempted:
        raise ScreenProjectionError("native current-CAV attempt semantics differ")
    if route in ("fresh_zoh", "sensor_fixed") and attempted:
        raise ScreenProjectionError("native baseline-only route was attempted")

    occurrence = native_event.get("occurrence_cycle")
    decision = native_event.get("decision_cycle")
    state = native_event.get("predictor_state_version")
    for value, where, minimum in (
        (occurrence, "occurrence cycle", -1),
        (decision, "decision cycle", 0),
        (state, "state version", 0),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ScreenProjectionError("native %s differs" % where)
    if occurrence != decision - 1:  # type: ignore[operator]
        raise ScreenProjectionError("native occurrence/decision mapping differs")

    pose_ids = native_event.get("used_pose_ids")
    if not isinstance(pose_ids, list) or pose_ids != sorted(set(pose_ids)) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in pose_ids
    ):
        raise ScreenProjectionError("native used-pose IDs differ")
    fallback_reason = native_event.get("fallback_reason")
    if used:
        if fallback_reason is not None or not pose_ids:
            raise ScreenProjectionError("native candidate fallback fields differ")
        model_id = native_event.get("model_id")
        if model_id != candidate_id:
            raise ScreenProjectionError("native candidate model ID differs")
        world_ray = native_event.get("world_ray")
        if not isinstance(world_ray, list) or len(world_ray) != 3:
            raise ScreenProjectionError("native candidate world ray differs")
        components = []
        for component in world_ray:
            if isinstance(component, bool) or not isinstance(component, (int, float)) or not math.isfinite(component):
                raise ScreenProjectionError("native candidate world ray is non-finite")
            components.append(float(component))
        if abs(math.sqrt(math.fsum(value * value for value in components)) - 1.0) > 1.0e-12:
            raise ScreenProjectionError("native candidate world ray is not unit length")
        projected_ray = list(world_ray)
    else:
        if type(fallback_reason) is not str or not fallback_reason:
            raise ScreenProjectionError("native fallback reason is missing")
        # DSPB's native model_id names the predictor that attempted the row,
        # while screen108's model_id names the geometry source.  The latter is
        # therefore CURRENT_CAV on every fallback; the source value remains
        # bound by the native decision and aggregate hashes in the receipt.
        expected_native_model = (
            candidate_id
            if native_schema == dspb_output.CANDIDATE_OUTPUT_SCHEMA
            else "CURRENT_CAV"
        )
        if native_event.get("model_id") != expected_native_model:
            raise ScreenProjectionError("native fallback model ID differs")
        model_id = "CURRENT_CAV"
        projected_ray = None

    event_id = native_event.get("event_id")
    if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 0:
        raise ScreenProjectionError("native event ID differs")
    event_digest = _sha256(native_event.get("event_content_sha256"), "event content")
    return {
        "event_id": event_id,
        "event_content_sha256": event_digest,
        "occurrence_cycle": occurrence,
        "decision_cycle": decision,
        "model_id": model_id,
        "predictor_state_version": state,
        "used_pose_ids": list(pose_ids),
        "route": route,
        "candidate_attempted": attempted,
        "candidate_used": used,
        "fallback_reason": fallback_reason,
        "world_ray": projected_ray,
    }


def project_native_output(native_output: object) -> ScreenProjection:
    """Authenticate one RG3/DSPB/PLL native output and project screen108 v2."""

    if not isinstance(native_output, Mapping):
        raise ScreenProjectionError("native output must be a mapping")
    schema = native_output.get("schema")
    if schema == rg3_output.CANDIDATE_OUTPUT_SCHEMA:
        windows = _verify_rg3(native_output)
    elif schema == dspb_output.CANDIDATE_OUTPUT_SCHEMA:
        windows = _verify_dspb(native_output)
    elif schema == pll_output.CANDIDATE_OUTPUT_SCHEMA:
        windows = _verify_pll(native_output)
    else:
        raise ScreenProjectionError("unsupported native candidate schema")
    _direct_seal(native_output, "aggregate_sha256", "native output aggregate")

    candidate_id, artifact, config = _artifact_and_config(native_output, schema)
    projected_windows = []
    for window in windows:
        window_id = window.get("window_id")
        if type(window_id) is not str or not window_id:
            raise ScreenProjectionError("native window ID differs")
        projected_windows.append({
            "window_id": window_id,
            "events": [
                _project_event(event, candidate_id, schema)
                for event in window["events"]  # type: ignore[union-attr]
            ],
        })

    artifact_sha = hashlib.sha256(artifact).hexdigest()
    config_sha = hashlib.sha256(config).hexdigest()
    try:
        screen_output = screen108.seal_candidate_output(
            candidate_id,
            _sha256(native_output.get("adapter_aggregate_sha256"), "adapter aggregate"),
            _sha256(native_output.get("neutral_input_sha256"), "neutral input"),
            artifact_sha,
            config_sha,
            projected_windows,
        )
    except screen108.Screen108Error as exc:
        raise ScreenProjectionError("screen108 projection sealing failed") from exc

    receipt_windows = []
    for source, projected in zip(windows, screen_output["windows"]):  # type: ignore[index]
        source_window_sha = source.get("window_sha256")
        if source_window_sha is None:
            source_window_sha = canonical_sha256(source)
        event_bindings = [
            {
                "event_id": source_event["event_id"],
                "source_decision_sha256": source_event["decision_sha256"],
                "projected_decision_sha256": projected_event["decision_sha256"],
            }
            for source_event, projected_event in zip(
                source["events"], projected["events"]  # type: ignore[index]
            )
        ]
        window_body = {
            "window_id": source["window_id"],
            "source_window_sha256": source_window_sha,
            "source_events_sha256": source["events_sha256"],
            "projected_events_sha256": projected["events_sha256"],
            "event_bindings": event_bindings,
            "event_bindings_sha256": canonical_sha256(event_bindings),
        }
        receipt_windows.append(dict(
            window_body,
            window_projection_sha256=canonical_sha256(window_body),
        ))
    receipt_body = {
        "schema": PROJECTION_RECEIPT_SCHEMA,
        "candidate_id": candidate_id,
        "native_schema": schema,
        "native_aggregate_sha256": native_output["aggregate_sha256"],
        "projected_aggregate_sha256": screen_output["aggregate_sha256"],
        "candidate_executable_sha256": artifact_sha,
        "candidate_config_sha256": config_sha,
        "windows": receipt_windows,
    }
    receipt = dict(
        receipt_body,
        projection_receipt_sha256=canonical_sha256(receipt_body),
    )
    return ScreenProjection(screen_output, receipt, artifact, config)


__all__ = (
    "PROJECTION_RECEIPT_SCHEMA",
    "ScreenProjection",
    "ScreenProjectionError",
    "project_native_output",
)
