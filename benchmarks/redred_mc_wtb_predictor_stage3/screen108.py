"""Locked, candidate-neutral evaluator for the consumed NEW108 screen.

This module never imports or executes a predictor.  It consumes a predictor's
already sealed, event-for-event geometry receipt, reconstructs the locked
NEW108 input through the public source-bound adapter, and reuses the frozen CAV
evaluator and causal reference bank.  Selector labels are joined only after
the candidate receipt has been authenticated.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Dict, Mapping, Optional, Sequence, Tuple

from benchmarks.redred_mc_wtb_causal_reference import (
    CausalReferenceBank,
    CausalReferenceConfig,
    ReferenceObservation,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import logical_cav_evaluator as cav_evaluator
from benchmarks.redred_mc_wtb_predictor_stage3.logical_cav_evaluator import (
    CAVRegistryEvaluation,
    CurrentCAVEvaluationError,
    evaluate_current_cav_registry_bounded,
    verify_current_cav_evaluation_integrity_bounded,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.new108_adapter import (
    New108AdapterBundle,
)
from benchmarks.redred_mc_wtb_so3_axis_audit.stage3_new108_adapter import (
    build_locked_stage3_new108_adapter,
    verify_stage3_new108_adapter,
)
from benchmarks.redred_mc_wtb_stage4_contract import canonical_sha256

RESULT_SCHEMA = "redred.mc_wtb_predictor_stage3.screen108_result/v2"
CANDIDATE_OUTPUT_SCHEMA = "redred.mc_wtb_predictor_stage3.candidate_output/v2"
STATUS_MEASURED = "SCREEN108_MEASURED_PROMOTION_NOT_AUTHORIZED"
STATUS_HOLD = "SCREEN108_METRICS_HOLD"
MODEL_ACCURACY_PASS = "MODEL_ACCURACY_PASS"
MODEL_ACCURACY_FAIL = "MODEL_ACCURACY_FAIL"
CNCP_EVIDENCE_GRADE = "DECLARED_UNVERIFIED"
CNCP_VERDICT = "CNCP_HOLD_UNVERIFIED"
PREROLL_NS = 50_000_000
REFERENCE_CAPACITY_PER_POLARITY = 256
REFERENCE_MAX_AGE_NS = 2_000_000
MOTION_BINS = ("LOW", "MID", "HIGH")
# Stage3 prewarm changes the neutral bounds/input aggregate.  The consumed
# query cohort and reporting labels remain fixed by these selector authorities.
EXPECTED_LABEL_SIDECAR_SHA256 = "2dd3be5aba43610bef999c2491978d3abb39b206cfa6c53cb658cee43c2b3ecb"
EXPECTED_SELECTOR_REGISTRY_SHA256 = "4d022cfde62c609c19c275add2e374d656babde3d4e1e6e1a849c5f384bb7e0d"
EXPECTED_EVALUATOR_SHA256 = "e6d23fd426817c14f41052f288841bfe11750d8b96eec5f02eb7e557c82aa462"
_STAGE3_ADAPTER_IMPLEMENTATION_PATH = (
    "benchmarks/redred_mc_wtb_so3_axis_audit/stage3_new108_adapter.py"
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.:/,+-]{0,510}[A-Za-z0-9])?\Z"
)
_OUTPUT_FIELDS = frozenset((
    "schema", "candidate_id", "adapter_aggregate_sha256",
    "neutral_input_sha256", "candidate_executable_sha256",
    "candidate_config_sha256", "windows", "aggregate_sha256",
))
_OUTPUT_WINDOW_FIELDS = frozenset(("window_id", "events", "events_sha256"))
_OUTPUT_EVENT_FIELDS = frozenset((
    "event_id", "event_content_sha256", "occurrence_cycle", "decision_cycle",
    "model_id", "predictor_state_version", "used_pose_ids", "route",
    "candidate_attempted", "candidate_used", "fallback_reason", "world_ray",
    "decision_sha256",
))
_ROUTES = frozenset(("candidate", "current_cav", "fresh_zoh", "sensor_fixed"))
_ROUTE_COUNT_FIELDS = frozenset(_ROUTES)
_CURRENT_CAV_FALLBACK_REASON = "candidate_failure"
_FRESH_ZOH_FALLBACK_REASON = "fresh_zoh_fallback"
_SENSOR_FIXED_FALLBACK_REASONS = frozenset((
    "no_occurrence_pose", "invalid_pose", "stale_pose",
))
_FALLBACK_REASONS = frozenset((
    _CURRENT_CAV_FALLBACK_REASON, _FRESH_ZOH_FALLBACK_REASON,
)) | _SENSOR_FIXED_FALLBACK_REASONS
_CNCP_FIELDS = frozenset((
    "B_ff", "B_sram", "read_ports", "write_ports", "O_pose", "O_event",
    "II_event", "critical_depth", "pipeline_bits", "max_wire_width",
    "numeric_risk", "state_class", "compute_class", "pipeline_class",
    "endpoint_target_ns", "event_lanes",
))
_CNCP_RESULT_FIELDS = frozenset((
    "evidence_grade", "verdict", "declared_values",
))
_OPERATION_FIELDS = frozenset(("add_compare", "fixed_multiply", "nonlinear"))
_RESULT_FIELDS = frozenset((
    "schema", "status", "candidate_id", "cohort", "provenance", "cncp",
    "groups", "windows", "gate", "claim_scope", "result_sha256",
))
_GROUP_FIELDS = frozenset((
    "group", "window_count", "query_event_count", "loss_s_sum", "loss_a_sum",
    "loss_p_sum", "pooled", "equal_window", "positive_windows_vs_s",
    "positive_windows_vs_a", "baseline_sensor_waste_events",
    "baseline_sensor_waste_rate", "candidate_sensor_waste_events",
    "candidate_sensor_waste_rate", "incremental_waste_events",
    "incremental_waste_rate", "candidate_use_events", "candidate_use_rate",
    "candidate_attempt_events", "candidate_attempt_rate", "route_counts",
    "fallback_events", "fallback_rate", "fallback_reasons",
    "candidate_use_sensor_waste_events", "candidate_use_sensor_waste_rate",
    "quality_harm_mass",
))
_EFFECT_FIELDS = frozenset(("E_A_S", "E_P_S", "I_P_A", "Delta_P_A"))
_WINDOW_RESULT_FIELDS = frozenset((
    "window_id", "motion_bin", "query_event_count", "query_event_ids_sha256",
    "candidate_output_events_sha256", "loss_s_sum", "loss_a_sum", "loss_p_sum",
    "E_A_S", "E_P_S", "I_P_A", "Delta_P_A", "positive_vs_s",
    "positive_vs_a", "candidate_use_events", "fallback_events",
    "candidate_attempt_events", "route_counts", "fallback_reasons",
))
_ACCURACY_CHECK_FIELDS = frozenset((
    "overall_I_P_A_positive", "MID_I_P_A_positive", "HIGH_I_P_A_positive",
    "LOW_I_P_A_not_below_minus_0p25pct", "sensor_waste_not_worse_than_A",
))
_GATE_FIELDS = frozenset((
    "accuracy_and_waste", "model_accuracy_verdict",
    "model_accuracy_gate_pass", "synthetic_pass_supplied",
    "promotion_authorized", "hardware_estimate_boundary_met",
    "rtl_ppa_authorized",
))
_CLAIM_SCOPE_FIELDS = frozenset((
    "development_only", "candidate_executed_by_runner",
    "source_selection_changed", "filter_or_selector_evaluated",
    "external_data_evaluated", "rtl_evaluated", "ppa_evaluated",
))


class Screen108Error(ValueError):
    """A locked input, candidate receipt, metric, or result invariant failed."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _file_bytes(path: Path, where: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Screen108Error("cannot read %s" % where) from exc


def _file_sha256(path: Path, where: str) -> str:
    return hashlib.sha256(_file_bytes(path, where)).hexdigest()


def _sha256(value: object, where: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise Screen108Error("%s must be lowercase SHA-256" % where)
    return value


def _identifier(value: object, where: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise Screen108Error("%s is not a canonical identifier" % where)
    return value


def _adapter_implementation_sha256(seal: Mapping[str, object]) -> str:
    dependencies = seal.get("projection_dependency_manifest")
    if not isinstance(dependencies, list):
        raise Screen108Error("adapter projection dependency manifest differs")
    matches = [
        row for row in dependencies
        if isinstance(row, Mapping)
        and row.get("path") == _STAGE3_ADAPTER_IMPLEMENTATION_PATH
    ]
    if len(matches) != 1 or frozenset(matches[0]) != frozenset(("path", "sha256")):
        raise Screen108Error("adapter implementation dependency differs")
    return _sha256(
        matches[0].get("sha256"), "adapter implementation dependency digest"
    )


def _nonnegative_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Screen108Error("%s must be a nonnegative integer" % where)
    return value


def _signed_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Screen108Error("%s must be a signed integer" % where)
    return value


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Screen108Error("%s must be finite" % where)
    result = float(value)
    if not math.isfinite(result):
        raise Screen108Error("%s must be finite" % where)
    return result


def _unit_ray(value: object, where: str) -> Tuple[float, float, float]:
    if type(value) not in (list, tuple) or len(value) != 3:  # type: ignore[arg-type]
        raise Screen108Error("%s must contain three components" % where)
    result = tuple(_finite(component, "%s component" % where) for component in value)  # type: ignore[union-attr]
    norm = math.sqrt(math.fsum(component * component for component in result))
    if abs(norm - 1.0) > 1.0e-9:
        raise Screen108Error("%s must be a unit ray" % where)
    return result  # type: ignore[return-value]


def _exact_mapping(value: object, fields: frozenset, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        raise Screen108Error("%s field schema differs" % where)
    return value


def _fallback_reason_counts(value: object, where: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or any(
        type(reason) is not str or reason not in _FALLBACK_REASONS
        for reason in value
    ):
        raise Screen108Error("%s fallback reason taxonomy differs" % where)
    checked = {}
    for reason, count in value.items():
        checked_count = _nonnegative_int(count, "%s fallback reason count" % where)
        if checked_count == 0:
            raise Screen108Error("%s fallback reason count must be positive" % where)
        checked[reason] = checked_count
    return checked


def _json_object(payload: bytes, where: str) -> Mapping[str, object]:
    def reject_constant(value: str) -> None:
        raise Screen108Error("%s contains non-finite JSON" % where)

    def reject_duplicates(pairs: Sequence[Tuple[str, object]]) -> Dict[str, object]:
        result = {}  # type: Dict[str, object]
        for key, value in pairs:
            if key in result:
                raise Screen108Error("%s contains a duplicate JSON key" % where)
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Screen108Error("cannot parse %s" % where) from exc
    if not isinstance(value, Mapping):
        raise Screen108Error("%s must be a JSON object" % where)
    return value


def _class_rank(value: str, prefix: str) -> int:
    if re.fullmatch(prefix + r"[0-4]", value) is None:
        raise Screen108Error("invalid %s class" % prefix)
    return int(value[1:])


def _state_class(bits: int) -> str:
    if bits <= 256:
        return "S0"
    if bits <= 1024:
        return "S1"
    if bits <= 4096:
        return "S2"
    return "S3"


def _pipeline_class(depth: int) -> str:
    if depth <= 1:
        return "P0"
    if depth <= 3:
        return "P1"
    if depth <= 8:
        return "P2"
    return "P3"


def validate_cncp(value: object) -> Mapping[str, object]:
    """Lint a flat CNCP declaration without treating it as feasibility evidence."""

    cncp = _exact_mapping(value, _CNCP_FIELDS, "CNCP")
    integers = {}
    for field in (
        "B_ff", "B_sram", "read_ports", "write_ports", "II_event",
        "critical_depth", "pipeline_bits", "max_wire_width", "event_lanes",
    ):
        integers[field] = _nonnegative_int(cncp[field], "CNCP %s" % field)
    for field in ("II_event", "critical_depth", "max_wire_width", "event_lanes"):
        if integers[field] == 0:
            raise Screen108Error("CNCP %s must be positive" % field)
    for field in ("O_pose", "O_event"):
        operations = cncp[field]
        if not isinstance(operations, Mapping) or frozenset(operations) != _OPERATION_FIELDS:
            raise Screen108Error("CNCP %s operator field schema differs" % field)
        for name, count in operations.items():
            _nonnegative_int(count, "CNCP %s operator count" % field)
    target = _finite(cncp["endpoint_target_ns"], "CNCP endpoint target")
    if target != 6.5 or integers["event_lanes"] != 2:
        raise Screen108Error("CNCP must retain the 6.5 ns two-lane endpoint")
    if integers["pipeline_bits"] > integers["B_ff"]:
        raise Screen108Error("CNCP pipeline bits must be included in B_ff")
    state_class = cncp["state_class"]
    compute_class = cncp["compute_class"]
    pipeline_class = cncp["pipeline_class"]
    numeric_risk = cncp["numeric_risk"]
    if state_class != _state_class(integers["B_ff"] + integers["B_sram"]):
        raise Screen108Error("CNCP state class differs from charged state")
    if type(compute_class) is not str or compute_class not in (
        "C0", "C1", "C2", "C3", "C4"
    ):
        raise Screen108Error("CNCP compute class differs")
    pose_ops = cncp["O_pose"]
    event_ops = cncp["O_event"]
    if event_ops["nonlinear"] > 0:  # type: ignore[index]
        minimum_compute_rank = 4
    elif pose_ops["nonlinear"] > 0:  # type: ignore[index]
        minimum_compute_rank = 2
    elif pose_ops["fixed_multiply"] > 0 or event_ops["fixed_multiply"] > 0:  # type: ignore[index]
        minimum_compute_rank = 1
    else:
        minimum_compute_rank = 0
    if _class_rank(compute_class, "C") < minimum_compute_rank:
        raise Screen108Error("CNCP compute class undercharges reported operators")
    if pipeline_class != _pipeline_class(integers["critical_depth"]):
        raise Screen108Error("CNCP pipeline class differs from critical depth")
    if numeric_risk not in ("N0", "N1", "N2", "N3"):
        raise Screen108Error("CNCP numeric risk differs")
    return dict(cncp)


def _decision_body(row: Mapping[str, object]) -> Mapping[str, object]:
    return {key: row[key] for key in _OUTPUT_EVENT_FIELDS if key != "decision_sha256"}


def seal_candidate_output(
    candidate_id: str,
    adapter_aggregate_sha256: str,
    neutral_input_sha256: str,
    candidate_executable_sha256: str,
    candidate_config_sha256: str,
    windows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    """Seal existing candidate decisions without executing candidate code."""

    sealed_windows = []
    for supplied_window in windows:
        if not isinstance(supplied_window, Mapping) or frozenset(supplied_window) != frozenset(("window_id", "events")):
            raise Screen108Error("unsealed candidate window field schema differs")
        supplied_events = supplied_window["events"]
        if not isinstance(supplied_events, list) or not supplied_events:
            raise Screen108Error("unsealed candidate events must be a nonempty list")
        events = []
        for supplied_event in supplied_events:
            expected = _OUTPUT_EVENT_FIELDS - frozenset(("decision_sha256",))
            event = _exact_mapping(supplied_event, expected, "unsealed candidate event")
            body = dict(event)
            events.append(dict(body, decision_sha256=canonical_sha256(body)))
        sealed_windows.append({
            "window_id": supplied_window["window_id"],
            "events": events,
            "events_sha256": canonical_sha256(events),
        })
    body = {
        "schema": CANDIDATE_OUTPUT_SCHEMA,
        "candidate_id": candidate_id,
        "adapter_aggregate_sha256": adapter_aggregate_sha256,
        "neutral_input_sha256": neutral_input_sha256,
        "candidate_executable_sha256": candidate_executable_sha256,
        "candidate_config_sha256": candidate_config_sha256,
        "windows": sealed_windows,
    }
    return dict(body, aggregate_sha256=canonical_sha256(body))


def _validate_candidate_output(
    value: object,
    bundle: New108AdapterBundle,
    baseline: CAVRegistryEvaluation,
    executable_sha256: str,
    config_sha256: str,
) -> Tuple[str, Mapping[str, Tuple[Mapping[str, object], ...]]]:
    output = _exact_mapping(value, _OUTPUT_FIELDS, "candidate output")
    if output["schema"] != CANDIDATE_OUTPUT_SCHEMA:
        raise Screen108Error("candidate output schema differs")
    candidate_id = _identifier(output["candidate_id"], "candidate ID")
    for field in (
        "adapter_aggregate_sha256", "neutral_input_sha256",
        "candidate_executable_sha256", "candidate_config_sha256",
        "aggregate_sha256",
    ):
        _sha256(output[field], "candidate output %s" % field)
    if output["adapter_aggregate_sha256"] != bundle.provenance_seal.get("aggregate_sha256"):
        raise Screen108Error("candidate output adapter binding differs")
    if output["neutral_input_sha256"] != baseline.neutral_input_sha256:
        raise Screen108Error("candidate output neutral input binding differs")
    if output["candidate_executable_sha256"] != executable_sha256:
        raise Screen108Error("candidate executable hash differs")
    if output["candidate_config_sha256"] != config_sha256:
        raise Screen108Error("candidate config hash differs")
    unsigned = dict(output)
    supplied_aggregate = unsigned.pop("aggregate_sha256")
    if supplied_aggregate != canonical_sha256(unsigned):
        raise Screen108Error("candidate output aggregate seal differs")

    supplied_windows = output["windows"]
    if not isinstance(supplied_windows, list):
        raise Screen108Error("candidate output windows must be a list")
    expected_ids = [window.registry.window_id for window in baseline.windows]
    observed_ids = [
        row.get("window_id") if isinstance(row, Mapping) else None
        for row in supplied_windows
    ]
    if observed_ids != expected_ids:
        raise Screen108Error("candidate output window order or identity differs")

    checked = {}  # type: Dict[str, Tuple[Mapping[str, object], ...]]
    for supplied, base_window in zip(supplied_windows, baseline.windows):
        row = _exact_mapping(supplied, _OUTPUT_WINDOW_FIELDS, "candidate output window")
        window_id = base_window.registry.window_id
        events = row["events"]
        if not isinstance(events, list):
            raise Screen108Error("candidate output events must be a list")
        if row["events_sha256"] != canonical_sha256(events):
            raise Screen108Error("candidate output window seal differs")
        expected_inputs = base_window.input_events
        if len(events) != len(expected_inputs):
            raise Screen108Error("candidate output event cardinality differs")
        poses_by_id = {pose.pose_id: pose for pose in base_window.input_poses}
        decisions = base_window.simulation.records
        previous_state = None  # type: Optional[int]
        previous_timestamp = None  # type: Optional[int]
        values = []
        for index, (event, expected_event, baseline_decision) in enumerate(
            zip(events, expected_inputs, decisions)
        ):
            decision = _exact_mapping(event, _OUTPUT_EVENT_FIELDS, "candidate event")
            if decision["decision_sha256"] != canonical_sha256(_decision_body(decision)):
                raise Screen108Error("candidate event decision seal differs")
            if decision["event_id"] != expected_event.event_id or decision["event_content_sha256"] != expected_event.event_content_sha256:
                raise Screen108Error("candidate event order or content identity differs")
            occurrence_cycle = _signed_int(
                decision["occurrence_cycle"], "candidate occurrence cycle"
            )
            cycle = _nonnegative_int(
                decision["decision_cycle"], "candidate decision cycle"
            )
            if occurrence_cycle != cycle - 1 or occurrence_cycle >= cycle:
                raise Screen108Error(
                    "candidate occurrence edge must equal decision edge minus one"
                )
            if cycle != baseline_decision.occurrence_cycle:
                raise Screen108Error("candidate decision edge differs from occurrence edge")
            state_version = _nonnegative_int(
                decision["predictor_state_version"], "predictor state version"
            )
            if previous_state is not None and state_version < previous_state:
                raise Screen108Error("predictor state version moved backwards")
            if previous_timestamp == expected_event.timestamp_ns and state_version != previous_state:
                raise Screen108Error("equal-timestamp cluster changed predictor state")
            previous_state = state_version
            previous_timestamp = expected_event.timestamp_ns
            model_id = _identifier(decision["model_id"], "candidate model ID")
            used_pose_ids = decision["used_pose_ids"]
            if not isinstance(used_pose_ids, list) or any(
                isinstance(pose_id, bool) or not isinstance(pose_id, int) or pose_id < 0
                for pose_id in used_pose_ids
            ):
                raise Screen108Error("candidate used pose IDs differ")
            if used_pose_ids != sorted(set(used_pose_ids)):
                raise Screen108Error("candidate used pose IDs are not unique and ordered")
            route = decision["route"]
            if type(route) is not str or route not in _ROUTES:
                raise Screen108Error("candidate route differs")
            for pose_id in used_pose_ids:
                pose = poses_by_id.get(pose_id)
                if (
                    pose is None
                    or pose.commit_cycle >= cycle
                    or pose.timestamp_ns > expected_event.timestamp_ns
                    or (
                        route != "sensor_fixed"
                        and (not pose.value_valid or not pose.arithmetic_valid)
                    )
                ):
                    raise Screen108Error("candidate used an unavailable pose")
            if type(decision["candidate_attempted"]) is not bool:
                raise Screen108Error("candidate_attempted must be an exact bool")
            if type(decision["candidate_used"]) is not bool:
                raise Screen108Error("candidate_used must be an exact bool")
            attempted = decision["candidate_attempted"]
            used = decision["candidate_used"]
            if used:
                if route != "candidate" or not attempted:
                    raise Screen108Error("candidate-use route evidence differs")
                if not used_pose_ids or decision["fallback_reason"] is not None:
                    raise Screen108Error("candidate-use receipt has fallback fields")
                if model_id == "CURRENT_CAV":
                    raise Screen108Error("current CAV must be recorded as baseline fallback")
                if model_id != candidate_id:
                    raise Screen108Error("candidate model identity differs from candidate ID")
                if (
                    baseline_decision.disposition != "corrected_world_ray"
                    or baseline_decision.disposition_reason != "causal_cav"
                ):
                    raise Screen108Error(
                        "candidate used geometry without exact causal_cav baseline"
                    )
                _unit_ray(decision["world_ray"], "candidate world ray")
            else:
                if model_id != "CURRENT_CAV":
                    raise Screen108Error("fallback model must be exact current CAV")
                if decision["world_ray"] is not None:
                    raise Screen108Error("fallback must not supply replacement geometry")
                if type(decision["fallback_reason"]) is not str or not decision["fallback_reason"]:
                    raise Screen108Error("fallback reason is missing")
                if route == "current_cav":
                    if (
                        not attempted
                        or decision["fallback_reason"]
                        != _CURRENT_CAV_FALLBACK_REASON
                        or baseline_decision.disposition != "corrected_world_ray"
                        or baseline_decision.disposition_reason != "causal_cav"
                        or used_pose_ids != list(baseline_decision.used_pose_ids)
                    ):
                        raise Screen108Error("current-CAV route differs from baseline")
                elif route == "fresh_zoh":
                    if (
                        attempted
                        or decision["fallback_reason"]
                        != _FRESH_ZOH_FALLBACK_REASON
                        or baseline_decision.disposition != "corrected_world_ray"
                        or baseline_decision.disposition_reason != "fresh_zoh_fallback"
                        or used_pose_ids != list(baseline_decision.used_pose_ids)
                    ):
                        raise Screen108Error("fresh-ZOH route differs from baseline")
                elif route == "sensor_fixed":
                    if (
                        attempted
                        or baseline_decision.disposition != "raw_bypass"
                        or baseline_decision.disposition_reason
                        not in _SENSOR_FIXED_FALLBACK_REASONS
                        or decision["fallback_reason"]
                        != baseline_decision.disposition_reason
                        or used_pose_ids != list(baseline_decision.used_pose_ids)
                    ):
                        raise Screen108Error("sensor-fixed route differs from baseline")
                else:
                    raise Screen108Error("fallback route differs")
            values.append(decision)
        checked[window_id] = tuple(values)
    return candidate_id, checked


def _effect(numerator: float, denominator: float, where: str) -> float:
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise Screen108Error("%s denominator is not positive" % where)
    value = 1.0 - numerator / denominator
    if not math.isfinite(value):
        raise Screen108Error("%s effect is non-finite" % where)
    return value


def _baseline_policy_rays(base_window: object) -> Tuple[Tuple[float, float, float], ...]:
    poses_by_id = {pose.pose_id: pose for pose in base_window.input_poses}
    values = []
    for event, decision in zip(base_window.input_events, base_window.simulation.records):
        # Match the pinned evaluator's world-reference history exactly.  It
        # retains an occurrence-ZOH world shadow even where runtime A bypasses;
        # query fallback loss below still takes A.policy_loss byte-for-byte.
        values.append(cav_evaluator._world_shadow(decision, event.sensor_ray, poses_by_id))
    return tuple(values)


def _window_losses(
    base_window: object,
    candidate_rows: Sequence[Mapping[str, object]],
) -> Tuple[Tuple[Mapping[str, object], ...], Mapping[str, object]]:
    baseline_rays = _baseline_policy_rays(base_window)
    candidate_rays = []
    for baseline_ray, row in zip(baseline_rays, candidate_rows):
        candidate_rays.append(
            _unit_ray(row["world_ray"], "candidate world ray")
            if row["candidate_used"] else baseline_ray
        )
    scores = CausalReferenceBank(CausalReferenceConfig(
        REFERENCE_CAPACITY_PER_POLARITY, REFERENCE_MAX_AGE_NS
    )).process(
        ReferenceObservation(event.event_id, event.timestamp_ns, event.polarity, ray)
        for event, ray in zip(base_window.input_events, candidate_rays)
    )
    scores_by_id = {score.event_id: score for score in scores}
    baseline_by_id = {event.decision.event_id: event for event in base_window.query_events}
    candidate_by_id = {row["event_id"]: row for row in candidate_rows}
    query = []
    for input_event in base_window.input_events:
        if not input_event.is_query:
            continue
        baseline_event = baseline_by_id.get(input_event.event_id)
        score = scores_by_id[input_event.event_id]
        row = candidate_by_id[input_event.event_id]
        if baseline_event is None or not score.reference_available or score.angular_cost_rad is None:
            raise Screen108Error("candidate query lacks the locked causal reference")
        p_loss = (
            _finite(score.angular_cost_rad, "candidate angular loss")
            if row["candidate_used"]
            else float(baseline_event.policy_loss)
        )
        query.append({
            "event_id": input_event.event_id,
            "S": float(baseline_event.sensor_loss),
            "A": float(baseline_event.policy_loss),
            "P": p_loss,
            "route": row["route"],
            "candidate_attempted": row["candidate_attempted"],
            "candidate_used": row["candidate_used"],
            "fallback_reason": row["fallback_reason"],
        })
    s_sum = math.fsum(row["S"] for row in query)
    a_sum = math.fsum(row["A"] for row in query)
    p_sum = math.fsum(row["P"] for row in query)
    e_a_s = _effect(a_sum, s_sum, "window A:S")
    e_p_s = _effect(p_sum, s_sum, "window P:S")
    i_p_a = _effect(p_sum, a_sum, "window P:A")
    fallback = Counter(
        str(row["fallback_reason"]) for row in query if not row["candidate_used"]
    )
    routes = Counter(str(row["route"]) for row in query)
    summary = {
        "query_event_count": len(query),
        "query_event_ids_sha256": canonical_sha256([row["event_id"] for row in query]),
        "loss_s_sum": s_sum,
        "loss_a_sum": a_sum,
        "loss_p_sum": p_sum,
        "E_A_S": e_a_s,
        "E_P_S": e_p_s,
        "I_P_A": i_p_a,
        "Delta_P_A": e_p_s - e_a_s,
        "positive_vs_s": e_p_s > 0.0,
        "positive_vs_a": i_p_a > 0.0,
        "candidate_use_events": sum(bool(row["candidate_used"]) for row in query),
        "candidate_attempt_events": sum(
            bool(row["candidate_attempted"]) for row in query
        ),
        "route_counts": {route: routes[route] for route in sorted(_ROUTES)},
        "fallback_events": sum(not bool(row["candidate_used"]) for row in query),
        "fallback_reasons": dict(sorted(fallback.items())),
    }
    return tuple(query), summary


def _summarize_group(group: str, windows: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    if not windows:
        raise Screen108Error("%s metric group is empty" % group)
    events = tuple(event for window in windows for event in window["events"])  # type: ignore[union-attr]
    s_sum = math.fsum(row["S"] for row in events)
    a_sum = math.fsum(row["A"] for row in events)
    p_sum = math.fsum(row["P"] for row in events)
    pooled_a = _effect(a_sum, s_sum, "%s pooled A:S" % group)
    pooled_p = _effect(p_sum, s_sum, "%s pooled P:S" % group)
    pooled_i = _effect(p_sum, a_sum, "%s pooled P:A" % group)
    equal = {}
    for field in _EFFECT_FIELDS:
        equal[field] = math.fsum(window[field] for window in windows) / len(windows)
    candidate_use = sum(bool(row["candidate_used"]) for row in events)
    candidate_attempt = sum(bool(row["candidate_attempted"]) for row in events)
    fallback = len(events) - candidate_use
    baseline_waste = sum(row["A"] >= row["S"] for row in events)
    sensor_waste = sum(row["P"] >= row["S"] for row in events)
    incremental_waste = sum(row["P"] >= row["A"] for row in events)
    used_waste = sum(
        bool(row["candidate_used"]) and row["P"] >= row["S"] for row in events
    )
    reasons = Counter(
        str(row["fallback_reason"]) for row in events if not row["candidate_used"]
    )
    routes = Counter(str(row["route"]) for row in events)
    return {
        "group": group,
        "window_count": len(windows),
        "query_event_count": len(events),
        "loss_s_sum": s_sum,
        "loss_a_sum": a_sum,
        "loss_p_sum": p_sum,
        "pooled": {
            "E_A_S": pooled_a,
            "E_P_S": pooled_p,
            "I_P_A": pooled_i,
            "Delta_P_A": pooled_p - pooled_a,
        },
        "equal_window": equal,
        "positive_windows_vs_s": sum(window["E_P_S"] > 0.0 for window in windows),
        "positive_windows_vs_a": sum(window["I_P_A"] > 0.0 for window in windows),
        "baseline_sensor_waste_events": baseline_waste,
        "baseline_sensor_waste_rate": float(baseline_waste) / len(events),
        "candidate_sensor_waste_events": sensor_waste,
        "candidate_sensor_waste_rate": float(sensor_waste) / len(events),
        "incremental_waste_events": incremental_waste,
        "incremental_waste_rate": float(incremental_waste) / len(events),
        "candidate_use_events": candidate_use,
        "candidate_use_rate": float(candidate_use) / len(events),
        "candidate_attempt_events": candidate_attempt,
        "candidate_attempt_rate": float(candidate_attempt) / len(events),
        "route_counts": {route: routes[route] for route in sorted(_ROUTES)},
        "fallback_events": fallback,
        "fallback_rate": float(fallback) / len(events),
        "fallback_reasons": dict(sorted(reasons.items())),
        "candidate_use_sensor_waste_events": used_waste,
        "candidate_use_sensor_waste_rate": (
            float(used_waste) / candidate_use if candidate_use else None
        ),
        "quality_harm_mass": math.fsum(max(0.0, row["P"] - row["S"]) for row in events) / s_sum,
    }


def _verify_freeze(root: Path) -> Mapping[str, str]:
    receipt_path = root / "benchmarks/redred_mc_wtb_predictor_stage12/checkpoint_a_freeze_receipt.json"
    receipt_bytes = _file_bytes(receipt_path, "Stage12 freeze receipt")
    receipt = _json_object(receipt_bytes, "Stage12 freeze receipt")
    if (
        receipt.get("schema") != "redred.mc_wtb_predictor_stage12.checkpoint_a_freeze_receipt/v1"
        or receipt.get("status") != "FROZEN_STAGE1_STAGE2_CHECKPOINT_A"
    ):
        raise Screen108Error("Stage12 freeze receipt authority differs")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 3:
        raise Screen108Error("Stage12 freeze artifact index differs")
    observed = {}
    for row in artifacts:
        if not isinstance(row, Mapping) or frozenset(row) != frozenset(("path", "sha256")):
            raise Screen108Error("Stage12 freeze artifact entry differs")
        relative = row["path"]
        if type(relative) is not str or relative.startswith("/") or ".." in Path(relative).parts:
            raise Screen108Error("Stage12 freeze artifact path differs")
        digest = _file_sha256(root / relative, "frozen Stage12 artifact")
        if digest != row["sha256"]:
            raise Screen108Error("frozen Stage12 artifact hash differs")
        observed[relative] = digest
    required = {
        "benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json",
        "docs/MC_WTB_PREDICTOR_STAGE12_CONTRACT_20260822.md",
        "docs/MC_WTB_STAGE12_ARCHITECTURE_CANDIDATES_20260822.md",
    }
    if set(observed) != required:
        raise Screen108Error("Stage12 freeze artifact set differs")
    observed["freeze_receipt"] = hashlib.sha256(receipt_bytes).hexdigest()
    return observed


def _evaluate_verified(
    bundle: New108AdapterBundle,
    baseline: CAVRegistryEvaluation,
    candidate_output: Mapping[str, object],
    executable_sha256: str,
    config_sha256: str,
    cncp_value: Mapping[str, object],
    frozen: Mapping[str, str],
    root: Path,
) -> Mapping[str, object]:
    if type(bundle) is not New108AdapterBundle:
        raise Screen108Error("NEW108 adapter bundle type differs")
    try:
        verified_input = verify_current_cav_evaluation_integrity_bounded(baseline)
    except CurrentCAVEvaluationError as exc:
        raise Screen108Error("current CAV evaluation integrity differs") from exc
    if verified_input != baseline.neutral_input_sha256:
        raise Screen108Error("current CAV neutral input verification differs")
    if len(bundle.neutral_registry) != len(baseline.windows):
        raise Screen108Error("adapter and evaluator window counts differ")
    if any(
        window.query_start_ns_inclusive - window.warmup_start_ns_inclusive != PREROLL_NS
        for window in bundle.neutral_registry
    ):
        raise Screen108Error("NEW108 window does not retain the locked 50 ms pre-roll")
    cncp = validate_cncp(cncp_value)
    candidate_id, candidate_rows = _validate_candidate_output(
        candidate_output, bundle, baseline, executable_sha256, config_sha256
    )
    seal = bundle.provenance_seal
    if seal.get("window_count") != len(baseline.windows):
        raise Screen108Error("adapter sealed window count differs")
    if seal.get("source_window_event_count") != sum(
        len(window.input_events) for window in baseline.windows
    ):
        raise Screen108Error("adapter sealed event count differs")
    if seal.get("selector_labels_sidecar_sha256") != canonical_sha256(bundle.selector_labels):
        raise Screen108Error("selector label sidecar seal differs")
    adapter_implementation_sha256 = _adapter_implementation_sha256(seal)

    metric_windows = []
    result_windows = []
    for base_window in baseline.windows:
        window_id = base_window.registry.window_id
        label = bundle.selector_labels.get(window_id)
        if not isinstance(label, Mapping) or label.get("motion_bin") not in MOTION_BINS:
            raise Screen108Error("locked motion-bin sidecar differs")
        query_events, summary = _window_losses(base_window, candidate_rows[window_id])
        metric = dict(summary, events=query_events, motion_bin=label["motion_bin"])
        metric_windows.append(metric)
        result_windows.append({
            "window_id": window_id,
            "motion_bin": label["motion_bin"],
            "candidate_output_events_sha256": canonical_sha256(list(candidate_rows[window_id])),
            **summary,
        })
    groups = [_summarize_group("OVERALL", metric_windows)]
    groups.extend(
        _summarize_group(motion_bin, [
            window for window in metric_windows if window["motion_bin"] == motion_bin
        ])
        for motion_bin in MOTION_BINS
    )
    group_index = {row["group"]: row for row in groups}
    accuracy_checks = {
        "overall_I_P_A_positive": group_index["OVERALL"]["pooled"]["I_P_A"] > 0.0,
        "MID_I_P_A_positive": group_index["MID"]["pooled"]["I_P_A"] > 0.0,
        "HIGH_I_P_A_positive": group_index["HIGH"]["pooled"]["I_P_A"] > 0.0,
        "LOW_I_P_A_not_below_minus_0p25pct": group_index["LOW"]["pooled"]["I_P_A"] >= -0.0025,
        "sensor_waste_not_worse_than_A": (
            group_index["OVERALL"]["candidate_sensor_waste_rate"]
            <= group_index["OVERALL"]["baseline_sensor_waste_rate"]
        ),
    }
    model_accuracy_pass = all(accuracy_checks.values())
    model_accuracy_verdict = (
        MODEL_ACCURACY_PASS if model_accuracy_pass else MODEL_ACCURACY_FAIL
    )
    cohort = {
        "sequence_id": "uzh_davis240c/shapes_rotation",
        "role": "DEVELOPMENT_CONSUMED",
        "window_count": len(baseline.windows),
        "selected_event_count": sum(len(window.input_events) for window in baseline.windows),
        "query_event_count": sum(len(window.query_events) for window in baseline.windows),
        "selected_pose_packet_count": sum(len(window.input_poses) for window in baseline.windows),
        "pre_roll_ns": PREROLL_NS,
        "ordered_query_ids_sha256": canonical_sha256([
            event.decision.event_id
            for window in baseline.windows for event in window.query_events
        ]),
    }
    schema_path = root / "benchmarks/redred_mc_wtb_predictor_stage3/screen108_result.schema.json"
    provenance = {
        "stage12_freeze_receipt_sha256": frozen["freeze_receipt"],
        "stage12_source_split_plan_sha256": frozen[
            "benchmarks/redred_mc_wtb_predictor_stage12/source_split_plan.json"
        ],
        "stage12_contract_sha256": frozen[
            "docs/MC_WTB_PREDICTOR_STAGE12_CONTRACT_20260822.md"
        ],
        "stage12_architecture_sha256": frozen[
            "docs/MC_WTB_STAGE12_ARCHITECTURE_CANDIDATES_20260822.md"
        ],
        "source_member_sha256": dict(seal["source_member_sha256"]),
        "selector_registry_sha256": seal["selector_registry_sha256"],
        "selector_implementation_sha256": seal["selector_implementation_sha256"],
        "adapter_aggregate_sha256": seal["aggregate_sha256"],
        "adapter_implementation_sha256": adapter_implementation_sha256,
        "neutral_registry_sha256": seal["neutral_registry_sha256"],
        "selector_labels_sidecar_sha256": seal["selector_labels_sidecar_sha256"],
        "neutral_input_sha256": baseline.neutral_input_sha256,
        "evaluator_implementation_sha256": _file_sha256(
            Path(cav_evaluator.__file__), "current CAV evaluator"
        ),
        "screen_runner_implementation_sha256": _file_sha256(Path(__file__), "screen runner"),
        "result_schema_sha256": _file_sha256(schema_path, "screen result schema"),
        "candidate_output_sha256": candidate_output["aggregate_sha256"],
        "candidate_executable_sha256": executable_sha256,
        "candidate_config_sha256": config_sha256,
    }
    body = {
        "schema": RESULT_SCHEMA,
        "status": STATUS_MEASURED if model_accuracy_pass else STATUS_HOLD,
        "candidate_id": candidate_id,
        "cohort": cohort,
        "provenance": provenance,
        "cncp": {
            "evidence_grade": CNCP_EVIDENCE_GRADE,
            "verdict": CNCP_VERDICT,
            "declared_values": cncp,
        },
        "groups": groups,
        "windows": result_windows,
        "gate": {
            "accuracy_and_waste": accuracy_checks,
            "model_accuracy_verdict": model_accuracy_verdict,
            "model_accuracy_gate_pass": model_accuracy_pass,
            "synthetic_pass_supplied": False,
            "promotion_authorized": False,
            "hardware_estimate_boundary_met": False,
            "rtl_ppa_authorized": False,
        },
        "claim_scope": {
            "development_only": True,
            "candidate_executed_by_runner": False,
            "source_selection_changed": False,
            "filter_or_selector_evaluated": False,
            "external_data_evaluated": False,
            "rtl_evaluated": False,
            "ppa_evaluated": False,
        },
    }
    result = dict(body, result_sha256=canonical_sha256(body))
    verify_screen108_result_envelope(result)
    return result


def verify_screen108_result_envelope(value: object) -> str:
    """Validate the exact public shape and self-seal of a screen result."""

    result = _exact_mapping(value, _RESULT_FIELDS, "screen result")
    if result["schema"] != RESULT_SCHEMA or result["status"] not in (STATUS_MEASURED, STATUS_HOLD):
        raise Screen108Error("screen result schema or status differs")
    _identifier(result["candidate_id"], "result candidate ID")
    cncp_result = _exact_mapping(result["cncp"], _CNCP_RESULT_FIELDS, "screen result CNCP")
    if (
        cncp_result["evidence_grade"] != CNCP_EVIDENCE_GRADE
        or cncp_result["verdict"] != CNCP_VERDICT
    ):
        raise Screen108Error("screen result CNCP evidence boundary differs")
    validate_cncp(cncp_result["declared_values"])
    if not isinstance(result["groups"], list) or [row.get("group") for row in result["groups"] if isinstance(row, Mapping)] != ["OVERALL", "LOW", "MID", "HIGH"]:
        raise Screen108Error("screen result metric groups differ")
    group_index = {}
    for row in result["groups"]:
        group = _exact_mapping(row, _GROUP_FIELDS, "screen result group")
        pooled = _exact_mapping(group["pooled"], _EFFECT_FIELDS, "pooled effects")
        _exact_mapping(group["equal_window"], _EFFECT_FIELDS, "equal-window effects")
        route_counts = _exact_mapping(
            group["route_counts"], _ROUTE_COUNT_FIELDS, "group route counts"
        )
        for field in _EFFECT_FIELDS:
            _finite(pooled[field], "screen result pooled %s" % field)
        query_count = _nonnegative_int(
            group["query_event_count"], "screen result query event count"
        )
        if query_count == 0:
            raise Screen108Error("screen result group is empty")
        s_sum = _finite(group["loss_s_sum"], "screen result sensor loss")
        a_sum = _finite(group["loss_a_sum"], "screen result baseline loss")
        p_sum = _finite(group["loss_p_sum"], "screen result candidate loss")
        expected_pooled = {
            "E_A_S": _effect(a_sum, s_sum, "verified pooled A:S"),
            "E_P_S": _effect(p_sum, s_sum, "verified pooled P:S"),
            "I_P_A": _effect(p_sum, a_sum, "verified pooled P:A"),
        }
        expected_pooled["Delta_P_A"] = (
            expected_pooled["E_P_S"] - expected_pooled["E_A_S"]
        )
        if dict(pooled) != expected_pooled:
            raise Screen108Error("screen result pooled effects differ from losses")
        candidate_use = _nonnegative_int(
            group["candidate_use_events"], "screen result candidate-use count"
        )
        candidate_attempt = _nonnegative_int(
            group["candidate_attempt_events"], "screen result candidate-attempt count"
        )
        fallback = _nonnegative_int(
            group["fallback_events"], "screen result fallback count"
        )
        fallback_reasons = _fallback_reason_counts(
            group["fallback_reasons"], "screen result group"
        )
        checked_routes = {
            route: _nonnegative_int(route_counts[route], "screen result route count")
            for route in _ROUTES
        }
        if (
            sum(checked_routes.values()) != query_count
            or checked_routes["candidate"] != candidate_use
            or checked_routes["candidate"] + checked_routes["current_cav"]
            != candidate_attempt
            or candidate_use + fallback != query_count
            or sum(fallback_reasons.values()) != fallback
            or fallback_reasons.get(_CURRENT_CAV_FALLBACK_REASON, 0)
            != checked_routes["current_cav"]
            or fallback_reasons.get(_FRESH_ZOH_FALLBACK_REASON, 0)
            != checked_routes["fresh_zoh"]
            or sum(
                fallback_reasons.get(reason, 0)
                for reason in _SENSOR_FIXED_FALLBACK_REASONS
            ) != checked_routes["sensor_fixed"]
        ):
            raise Screen108Error("screen result route counts differ")
        exact_rates = {
            "candidate_use_rate": float(candidate_use) / query_count,
            "candidate_attempt_rate": float(candidate_attempt) / query_count,
            "fallback_rate": float(fallback) / query_count,
        }
        for field, expected in exact_rates.items():
            if _finite(group[field], "screen result %s" % field) != expected:
                raise Screen108Error("screen result event rates differ")
        for field in ("baseline_sensor_waste_rate", "candidate_sensor_waste_rate"):
            _finite(group[field], "screen result %s" % field)
        group_index[group["group"]] = group
    if not isinstance(result["windows"], list) or not result["windows"]:
        raise Screen108Error("screen result windows differ")
    checked_windows = []
    for row in result["windows"]:
        window = _exact_mapping(row, _WINDOW_RESULT_FIELDS, "screen result window")
        route_counts = _exact_mapping(
            window["route_counts"], _ROUTE_COUNT_FIELDS, "window route counts"
        )
        query_count = _nonnegative_int(
            window["query_event_count"], "screen result window query count"
        )
        candidate_use = _nonnegative_int(
            window["candidate_use_events"], "screen result window candidate-use count"
        )
        candidate_attempt = _nonnegative_int(
            window["candidate_attempt_events"],
            "screen result window candidate-attempt count",
        )
        fallback = _nonnegative_int(
            window["fallback_events"], "screen result window fallback count"
        )
        checked_routes = {
            route: _nonnegative_int(
                route_counts[route], "screen result window route count"
            )
            for route in _ROUTES
        }
        fallback_reasons = _fallback_reason_counts(
            window["fallback_reasons"], "screen result window"
        )
        if (
            sum(checked_routes.values()) != query_count
            or checked_routes["candidate"] != candidate_use
            or checked_routes["candidate"] + checked_routes["current_cav"]
            != candidate_attempt
            or candidate_use + fallback != query_count
            or sum(fallback_reasons.values()) != fallback
            or fallback_reasons.get(_CURRENT_CAV_FALLBACK_REASON, 0)
            != checked_routes["current_cav"]
            or fallback_reasons.get(_FRESH_ZOH_FALLBACK_REASON, 0)
            != checked_routes["fresh_zoh"]
            or sum(
                fallback_reasons.get(reason, 0)
                for reason in _SENSOR_FIXED_FALLBACK_REASONS
            ) != checked_routes["sensor_fixed"]
        ):
            raise Screen108Error("screen result window route taxonomy differs")
        s_sum = _finite(window["loss_s_sum"], "screen result window sensor loss")
        a_sum = _finite(window["loss_a_sum"], "screen result window baseline loss")
        p_sum = _finite(window["loss_p_sum"], "screen result window candidate loss")
        expected_effects = {
            "E_A_S": _effect(a_sum, s_sum, "verified window A:S"),
            "E_P_S": _effect(p_sum, s_sum, "verified window P:S"),
            "I_P_A": _effect(p_sum, a_sum, "verified window P:A"),
        }
        expected_effects["Delta_P_A"] = (
            expected_effects["E_P_S"] - expected_effects["E_A_S"]
        )
        if any(window[field] != expected_effects[field] for field in _EFFECT_FIELDS):
            raise Screen108Error("screen result window effects differ from losses")
        if (
            type(window["positive_vs_s"]) is not bool
            or type(window["positive_vs_a"]) is not bool
            or window["positive_vs_s"] is not (expected_effects["E_P_S"] > 0.0)
            or window["positive_vs_a"] is not (expected_effects["I_P_A"] > 0.0)
        ):
            raise Screen108Error("screen result window positivity differs")
        checked_windows.append(window)
    for group_name, group in group_index.items():
        member_windows = (
            checked_windows
            if group_name == "OVERALL"
            else [window for window in checked_windows if window["motion_bin"] == group_name]
        )
        if (
            _nonnegative_int(group["window_count"], "screen result window count")
            != len(member_windows)
            or _nonnegative_int(
                group["positive_windows_vs_s"], "screen result positive S windows"
            ) != sum(window["E_P_S"] > 0.0 for window in member_windows)
            or _nonnegative_int(
                group["positive_windows_vs_a"], "screen result positive A windows"
            ) != sum(window["I_P_A"] > 0.0 for window in member_windows)
        ):
            raise Screen108Error("screen result positive-window counts differ")
    gate = _exact_mapping(result["gate"], _GATE_FIELDS, "screen result gate")
    accuracy = _exact_mapping(
        gate["accuracy_and_waste"], _ACCURACY_CHECK_FIELDS,
        "screen result accuracy checks",
    )
    if any(type(value) is not bool for value in accuracy.values()):
        raise Screen108Error("screen result accuracy checks must be exact bools")
    expected_accuracy = {
        "overall_I_P_A_positive": group_index["OVERALL"]["pooled"]["I_P_A"] > 0.0,
        "MID_I_P_A_positive": group_index["MID"]["pooled"]["I_P_A"] > 0.0,
        "HIGH_I_P_A_positive": group_index["HIGH"]["pooled"]["I_P_A"] > 0.0,
        "LOW_I_P_A_not_below_minus_0p25pct": (
            group_index["LOW"]["pooled"]["I_P_A"] >= -0.0025
        ),
        "sensor_waste_not_worse_than_A": (
            group_index["OVERALL"]["candidate_sensor_waste_rate"]
            <= group_index["OVERALL"]["baseline_sensor_waste_rate"]
        ),
    }
    if dict(accuracy) != expected_accuracy:
        raise Screen108Error("screen result accuracy checks differ from group metrics")
    model_accuracy_pass = all(expected_accuracy.values())
    expected_verdict = (
        MODEL_ACCURACY_PASS if model_accuracy_pass else MODEL_ACCURACY_FAIL
    )
    expected_status = STATUS_MEASURED if model_accuracy_pass else STATUS_HOLD
    if (
        gate["model_accuracy_gate_pass"] is not model_accuracy_pass
        or gate["model_accuracy_verdict"] != expected_verdict
        or result["status"] != expected_status
    ):
        raise Screen108Error("screen result model accuracy verdict differs")
    claim = _exact_mapping(result["claim_scope"], _CLAIM_SCOPE_FIELDS, "screen result claim scope")
    if (
        gate["synthetic_pass_supplied"] is not False
        or gate["promotion_authorized"] is not False
        or gate["hardware_estimate_boundary_met"] is not False
        or gate["rtl_ppa_authorized"] is not False
    ):
        raise Screen108Error("screen result authorization boundary differs")
    if (
        claim["development_only"] is not True
        or claim["candidate_executed_by_runner"] is not False
        or claim["source_selection_changed"] is not False
        or claim["filter_or_selector_evaluated"] is not False
        or claim["external_data_evaluated"] is not False
        or claim["rtl_evaluated"] is not False
        or claim["ppa_evaluated"] is not False
    ):
        raise Screen108Error("screen result claim boundary differs")
    supplied = _sha256(result["result_sha256"], "result digest")
    unsigned = dict(result)
    unsigned.pop("result_sha256")
    if supplied != canonical_sha256(unsigned):
        raise Screen108Error("screen result aggregate seal differs")
    return supplied


def run_locked_screen108(
    dataset_directory: Path,
    candidate_output_path: Path,
    candidate_executable_path: Path,
    candidate_config_path: Path,
    cncp: Mapping[str, object],
) -> Mapping[str, object]:
    """Reconstruct NEW108 and score a sealed output; never execute a candidate."""

    root = _repo_root()
    frozen = _verify_freeze(root)
    if _file_sha256(Path(cav_evaluator.__file__), "current CAV evaluator") != EXPECTED_EVALUATOR_SHA256:
        raise Screen108Error("pinned current CAV evaluator hash differs")
    if not callable(build_locked_stage3_new108_adapter) or not callable(
        verify_stage3_new108_adapter
    ):
        raise Screen108Error("locked Stage3 NEW108 adapter API is unavailable")
    bundle = build_locked_stage3_new108_adapter(Path(dataset_directory))
    if type(bundle) is not New108AdapterBundle:
        raise Screen108Error("locked Stage3 NEW108 adapter returned wrong type")
    adapter_digest = verify_stage3_new108_adapter(
        bundle, Path(dataset_directory)
    )
    if (
        adapter_digest != bundle.provenance_seal.get("aggregate_sha256")
        or bundle.provenance_seal.get("selector_labels_sidecar_sha256")
        != EXPECTED_LABEL_SIDECAR_SHA256
        or bundle.provenance_seal.get("selector_registry_sha256")
        != EXPECTED_SELECTOR_REGISTRY_SHA256
    ):
        raise Screen108Error("Stage3 adapter authority differs from locked NEW108")
    if any(
        window.query_start_ns_inclusive - window.warmup_start_ns_inclusive
        != PREROLL_NS
        for window in bundle.neutral_registry
    ):
        raise Screen108Error("Stage3 NEW108 cohort does not retain 50 ms pre-roll")
    baseline = evaluate_current_cav_registry_bounded(
        bundle.neutral_registry, bundle.event_streams, bundle.pose_streams,
    )
    candidate_output = _json_object(
        _file_bytes(Path(candidate_output_path), "candidate output"), "candidate output"
    )
    executable_sha256 = _file_sha256(Path(candidate_executable_path), "candidate executable")
    config_sha256 = _file_sha256(Path(candidate_config_path), "candidate config")
    return _evaluate_verified(
        bundle, baseline, candidate_output, executable_sha256, config_sha256,
        cncp, frozen, root,
    )


def verify_locked_screen108(
    result: Mapping[str, object],
    dataset_directory: Path,
    candidate_output_path: Path,
    candidate_executable_path: Path,
    candidate_config_path: Path,
    cncp: Mapping[str, object],
) -> str:
    """Reproduce a result from all public source-bound inputs."""

    expected = run_locked_screen108(
        dataset_directory, candidate_output_path, candidate_executable_path,
        candidate_config_path, cncp,
    )
    if result != expected:
        raise Screen108Error("screen result differs from source-bound reproduction")
    return verify_screen108_result_envelope(result)


def _exclusive_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(str(path), flags, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except OSError as exc:
        raise Screen108Error("cannot exclusively create output") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a sealed candidate output on locked NEW108 without executing it"
    )
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--candidate-executable", type=Path, required=True)
    parser.add_argument("--candidate-config", type=Path, required=True)
    parser.add_argument("--cncp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    cncp = _json_object(_file_bytes(args.cncp, "CNCP input"), "CNCP input")
    result = run_locked_screen108(
        args.dataset_dir, args.candidate_output, args.candidate_executable,
        args.candidate_config, cncp,
    )
    payload = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    _exclusive_write(args.output, payload)
    print("result_sha256=%s" % result["result_sha256"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Screen108Error as exc:
        print("screen108: %s" % exc, file=sys.stderr)
        sys.exit(2)


__all__ = [
    "CANDIDATE_OUTPUT_SCHEMA", "RESULT_SCHEMA", "Screen108Error",
    "run_locked_screen108", "seal_candidate_output", "validate_cncp",
    "verify_locked_screen108", "verify_screen108_result_envelope",
]
