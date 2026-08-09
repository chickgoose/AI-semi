#!/usr/bin/env python3
"""Fail-closed identity-versus-affine pairwise contention comparison."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


class CrossMapError(ValueError):
    """Raised when either input is not a comparable frozen pairwise run."""


MEASUREMENT_STATES = {
    "COMPLETE",
    "PARTIAL_DROP_OR_CENSOR",
    "NO_EVALUABLE_PAIRS",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossMapError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CrossMapError(f"{path}: top-level JSON must be an object")
    return payload


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CrossMapError(f"cannot hash {path}: {exc}") from exc


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CrossMapError(f"{field} must be a positive integer")
    return value


def _manifest_contract(
    manifest: dict[str, Any], *, expected_mapping: str, label: str
) -> dict[str, Any]:
    run = manifest.get("run")
    if not isinstance(run, dict) or run.get("workload") != "pairwise_contention":
        raise CrossMapError(f"{label} manifest must describe pairwise_contention")
    if manifest.get("event_identity_mode") != "address_only":
        raise CrossMapError(f"{label} manifest must use address_only identity")

    geometry = run.get("geometry")
    if not isinstance(geometry, dict):
        raise CrossMapError(f"{label} geometry must be an object")
    width = _positive_int(geometry.get("width"), f"{label} geometry.width")
    height = _positive_int(geometry.get("height"), f"{label} geometry.height")
    source_count = width * height
    if source_count < 2:
        raise CrossMapError(f"{label} pairwise run requires at least two sources")

    parameters = run.get("parameters", {})
    if not isinstance(parameters, dict):
        raise CrossMapError(f"{label} parameters must be an object")
    repeats = _positive_int(parameters.get("pair_repeats", 1), f"{label} pair_repeats")
    frozen = manifest.get("logical_source_permutation")
    if (
        not isinstance(frozen, list)
        or len(frozen) != source_count
        or any(isinstance(value, bool) or not isinstance(value, int) for value in frozen)
        or sorted(frozen) != list(range(source_count))
    ):
        raise CrossMapError(f"{label} manifest permutation must be a frozen bijection")

    raw_mapping = parameters.get("source_permutation", "identity")
    if isinstance(raw_mapping, str):
        raw_mapping = {"mode": raw_mapping}
    if not isinstance(raw_mapping, dict):
        raise CrossMapError(f"{label} source_permutation must be a string or object")
    mode = raw_mapping.get("mode", "identity")
    if expected_mapping == "identity":
        if mode != "identity" or frozen != list(range(source_count)):
            raise CrossMapError("identity manifest does not freeze identity mapping")
    else:
        if mode != "affine":
            raise CrossMapError("affine manifest must declare affine mapping")
        multiplier = raw_mapping.get("multiplier")
        offset = raw_mapping.get("offset")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (multiplier, offset)
        ):
            raise CrossMapError("affine multiplier and offset must be integers")
        if math.gcd(multiplier, source_count) != 1:
            raise CrossMapError("affine multiplier must be coprime to source count")
        expected = [
            (multiplier * source + offset) % source_count
            for source in range(source_count)
        ]
        if frozen != expected:
            raise CrossMapError("affine frozen permutation disagrees with parameters")

    pair_count = math.comb(source_count, 2)
    trace_sha256 = manifest.get("trace_sha256")
    generator_version = manifest.get("generator_version")
    if (
        not isinstance(trace_sha256, str)
        or len(trace_sha256) != 64
        or any(character not in "0123456789abcdef" for character in trace_sha256)
    ):
        raise CrossMapError(f"{label} manifest must freeze a lowercase SHA256")
    if not isinstance(generator_version, str) or not generator_version:
        raise CrossMapError(f"{label} manifest must freeze generator_version")
    return {
        "run": run,
        "parameters": parameters,
        "source_count": source_count,
        "repeats": repeats,
        "pairs_per_repeat": pair_count,
        "expected_trials": repeats * pair_count,
        "permutation": frozen,
        "trace_sha256": trace_sha256,
        "generator_version": generator_version,
        "report_group": manifest.get("report_group", run.get("name")),
    }


def _normalized_run(contract: dict[str, Any]) -> dict[str, Any]:
    run = contract["run"]
    parameters = dict(contract["parameters"])
    parameters.pop("source_permutation", None)
    return {
        "workload": run.get("workload"),
        "seed": run.get("seed"),
        "geometry": run.get("geometry"),
        "load": str(run.get("load")),
        "stim_cycles": run.get("stim_cycles"),
        "sink": run.get("sink", {"mode": "always"}),
        "parameters": parameters,
    }


def _validate_report_provenance(
    report: dict[str, Any], contract: dict[str, Any], *, label: str
) -> None:
    if report.get("trace_sha256") != contract["trace_sha256"]:
        raise CrossMapError(f"{label} report trace SHA256 disagrees with manifest")
    if report.get("generator_version") != contract["generator_version"]:
        raise CrossMapError(f"{label} report generator version disagrees with manifest")
    if report.get("logical_source_permutation") != contract["permutation"]:
        raise CrossMapError(f"{label} report permutation disagrees with manifest")
    if report.get("test") != contract["report_group"]:
        raise CrossMapError(f"{label} report test disagrees with manifest")
    if str(report.get("seed")) != str(contract["run"].get("seed")):
        raise CrossMapError(f"{label} report seed disagrees with manifest")
    if report.get("pair_count") != contract["expected_trials"]:
        raise CrossMapError(f"{label} report pair_count disagrees with repeat cardinality")
    candidate = report.get("candidate")
    if not isinstance(candidate, str) or not candidate:
        raise CrossMapError(f"{label} report candidate must be nonempty")


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def _same_optional_number(actual: Any, expected: int | float | None, field: str) -> None:
    if expected is None:
        if actual is not None:
            raise CrossMapError(f"{field} must be null")
        return
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise CrossMapError(f"{field} must be numeric")
    if not math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12):
        raise CrossMapError(f"{field} disagrees with trials")


def _trial_order(trial: dict[str, Any], label: str) -> tuple[str, int | None]:
    result = trial.get("result")
    states = (trial.get("event_state_a"), trial.get("event_state_b"))
    allowed_states = {"source_overrun", "pending", "accepted", "delivered"}
    if any(state not in allowed_states for state in states):
        raise CrossMapError(f"{label} has an invalid event state")
    has_drop = "source_overrun" in states
    has_censor = any(state in {"pending", "accepted"} for state in states)
    expected_result = (
        "dropped_and_censored"
        if has_drop and has_censor
        else "dropped"
        if has_drop
        else "censored"
        if has_censor
        else "evaluable"
    )
    if result != expected_result:
        raise CrossMapError(f"{label} trial result disagrees with event states")
    if result != "evaluable":
        if any(
            trial.get(field) is not None
            for field in (
                "delivery_a", "delivery_b", "completion_latency_cycles",
                "service_skew_cycles",
            )
        ):
            raise CrossMapError(f"{label} incomplete trial carries exact timing fields")
        return "NOT_EVALUABLE", None
    delivery_a = trial.get("delivery_a")
    delivery_b = trial.get("delivery_b")
    completion = trial.get("completion_latency_cycles")
    skew = trial.get("service_skew_cycles")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (delivery_a, delivery_b, completion, skew)
    ):
        raise CrossMapError(f"{label} evaluable trial has invalid timing fields")
    if skew != abs(delivery_a - delivery_b):
        raise CrossMapError(f"{label} trial skew disagrees with delivery cycles")
    if delivery_a < delivery_b:
        return "A_FIRST", -1
    if delivery_b < delivery_a:
        return "B_FIRST", 1
    return "SAME_CYCLE", 0


def _validated_trials(
    report: dict[str, Any], contract: dict[str, Any], *, label: str
) -> dict[int, dict[str, Any]]:
    raw_trials = report.get("trials")
    if not isinstance(raw_trials, list):
        raise CrossMapError(f"{label} report trials must be an array")
    if len(raw_trials) != contract["expected_trials"]:
        raise CrossMapError(f"{label} report is missing or has extra trials")

    canonical_pairs = list(itertools.combinations(range(contract["source_count"]), 2))
    by_relation: dict[int, dict[str, Any]] = {}
    for index, trial in enumerate(raw_trials):
        location = f"{label} trial[{index}]"
        if not isinstance(trial, dict):
            raise CrossMapError(f"{location} must be an object")
        relation_id = trial.get("relation_id")
        if isinstance(relation_id, bool) or not isinstance(relation_id, int):
            raise CrossMapError(f"{location} relation_id must be an integer")
        if relation_id in by_relation:
            raise CrossMapError(f"{label} report has duplicate relation_id {relation_id}")
        if not 0 <= relation_id < contract["expected_trials"]:
            raise CrossMapError(f"{location} relation_id is outside expected range")

        expected_repeat = relation_id // contract["pairs_per_repeat"]
        expected_pair = canonical_pairs[relation_id % contract["pairs_per_repeat"]]
        expected_physical = (
            contract["permutation"][expected_pair[0]],
            contract["permutation"][expected_pair[1]],
        )
        actual_contract = (
            trial.get("repeat_index"),
            trial.get("canonical_source_a"),
            trial.get("canonical_source_b"),
            trial.get("physical_source_a"),
            trial.get("physical_source_b"),
        )
        expected_contract = (
            expected_repeat,
            expected_pair[0],
            expected_pair[1],
            expected_physical[0],
            expected_physical[1],
        )
        if actual_contract != expected_contract:
            raise CrossMapError(
                f"{location} repeat/canonical/physical contract mismatch"
            )
        for source_field, expected_source in (
            ("source_a", expected_physical[0]),
            ("source_b", expected_physical[1]),
        ):
            if source_field in trial and trial[source_field] != expected_source:
                raise CrossMapError(f"{location} legacy source field mismatch")
        order, order_code = _trial_order(trial, location)
        overlap_count = trial.get("overlapping_prior_pair_count")
        overlap_flag = trial.get("overlaps_previous_pair")
        if (
            isinstance(overlap_count, bool)
            or not isinstance(overlap_count, int)
            or overlap_count < 0
        ):
            raise CrossMapError(
                f"{location} overlapping_prior_pair_count must be nonnegative"
            )
        if not isinstance(overlap_flag, bool) or overlap_flag != (overlap_count > 0):
            raise CrossMapError(f"{location} overlap fields disagree")
        normalized = dict(trial)
        normalized["order"] = order
        normalized["order_code"] = order_code
        normalized["has_drop"] = "source_overrun" in {
            trial.get("event_state_a"), trial.get("event_state_b")
        }
        normalized["has_censor"] = any(
            state in {"pending", "accepted"}
            for state in (trial.get("event_state_a"), trial.get("event_state_b"))
        )
        by_relation[relation_id] = normalized

    expected_ids = set(range(contract["expected_trials"]))
    if set(by_relation) != expected_ids:
        raise CrossMapError(f"{label} report relation IDs are not complete and contiguous")
    _validate_report_summary(report, by_relation, contract, label=label)
    return by_relation


def _validate_report_summary(
    report: dict[str, Any],
    trials: dict[int, dict[str, Any]],
    contract: dict[str, Any],
    *,
    label: str,
) -> None:
    required = {
        "evaluable_pairs", "dropped_pairs", "censored_pairs", "nonevaluable_pairs",
        "measurement_state", "mean_pair_completion_latency_cycles",
        "p95_pair_completion_latency_cycles", "max_pair_completion_latency_cycles",
        "mean_pair_service_skew_cycles", "p95_pair_service_skew_cycles",
        "max_pair_service_skew_cycles", "a_first_pairs", "b_first_pairs",
        "same_cycle_pairs", "overlap_pairs", "max_overlapping_prior_pairs",
        "isolation_state", "worst_completion_pair", "worst_skew_pair",
        "pair_aggregates",
    }
    missing = sorted(required - set(report))
    if missing:
        raise CrossMapError(
            f"{label} report is not current pairwise schema; missing {', '.join(missing)}"
        )

    rows = list(trials.values())
    evaluable = [row for row in rows if row["result"] == "evaluable"]
    dropped = sum(row["has_drop"] for row in rows)
    censored = sum(row["has_censor"] for row in rows)
    nonevaluable = len(rows) - len(evaluable)
    expected_state = (
        "NO_EVALUABLE_PAIRS"
        if not evaluable
        else "COMPLETE"
        if len(evaluable) == len(rows)
        else "PARTIAL_DROP_OR_CENSOR"
    )
    if report.get("measurement_state") not in MEASUREMENT_STATES:
        raise CrossMapError(f"{label} report measurement_state is invalid")
    expected_counts = {
        "evaluable_pairs": len(evaluable),
        "dropped_pairs": dropped,
        "censored_pairs": censored,
        "nonevaluable_pairs": nonevaluable,
    }
    for field, expected in expected_counts.items():
        if report.get(field) != expected:
            raise CrossMapError(f"{label} report {field} disagrees with trials")
    if report.get("measurement_state") != expected_state:
        raise CrossMapError(f"{label} report measurement_state disagrees with trials")

    overlap_pairs = sum(bool(row["overlaps_previous_pair"]) for row in rows)
    max_overlap = max(
        (int(row["overlapping_prior_pair_count"]) for row in rows), default=0
    )
    if report.get("overlap_pairs") != overlap_pairs:
        raise CrossMapError(f"{label} report overlap_pairs disagrees with trials")
    if report.get("max_overlapping_prior_pairs") != max_overlap:
        raise CrossMapError(
            f"{label} report max_overlapping_prior_pairs disagrees with trials"
        )
    expected_isolation = "OVERLAP_OBSERVED" if overlap_pairs else "QUIESCENT"
    if report.get("isolation_state") != expected_isolation:
        raise CrossMapError(f"{label} report isolation_state disagrees with trials")

    completions = [int(row["completion_latency_cycles"]) for row in evaluable]
    skews = [int(row["service_skew_cycles"]) for row in evaluable]
    expected_metrics = {
        "mean_pair_completion_latency_cycles": (
            statistics.fmean(completions) if completions else None
        ),
        "p95_pair_completion_latency_cycles": _percentile(completions, 95),
        "max_pair_completion_latency_cycles": max(completions) if completions else None,
        "mean_pair_service_skew_cycles": statistics.fmean(skews) if skews else None,
        "p95_pair_service_skew_cycles": _percentile(skews, 95),
        "max_pair_service_skew_cycles": max(skews) if skews else None,
    }
    for field, expected in expected_metrics.items():
        _same_optional_number(report.get(field), expected, f"{label} report {field}")
    expected_orders = {
        "a_first_pairs": sum(row["order"] == "A_FIRST" for row in evaluable),
        "b_first_pairs": sum(row["order"] == "B_FIRST" for row in evaluable),
        "same_cycle_pairs": sum(row["order"] == "SAME_CYCLE" for row in evaluable),
    }
    for field, expected in expected_orders.items():
        if report.get(field) != expected:
            raise CrossMapError(f"{label} report {field} disagrees with trials")

    aggregates = report.get("pair_aggregates")
    if not isinstance(aggregates, list) or len(aggregates) != contract["pairs_per_repeat"]:
        raise CrossMapError(f"{label} report pair_aggregates cardinality is invalid")
    aggregate_keys: set[tuple[int, int]] = set()
    for index, aggregate in enumerate(aggregates):
        if not isinstance(aggregate, dict):
            raise CrossMapError(f"{label} pair_aggregates[{index}] must be an object")
        key = (aggregate.get("canonical_source_a"), aggregate.get("canonical_source_b"))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in key):
            raise CrossMapError(f"{label} pair_aggregates[{index}] has invalid sources")
        if key in aggregate_keys:
            raise CrossMapError(f"{label} report has duplicate canonical pair aggregate")
        aggregate_keys.add(key)
        pair_trials = [
            row for row in rows
            if (row["canonical_source_a"], row["canonical_source_b"]) == key
        ]
        if len(pair_trials) != contract["repeats"]:
            raise CrossMapError(f"{label} report has an unknown canonical pair aggregate")
        expected_physical = (
            contract["permutation"][key[0]], contract["permutation"][key[1]]
        )
        if (
            aggregate.get("physical_source_a"), aggregate.get("physical_source_b")
        ) != expected_physical:
            raise CrossMapError(
                f"{label} pair aggregate {key} physical sources disagree with permutation"
            )
        pair_evaluable = [row for row in pair_trials if row["result"] == "evaluable"]
        expected_pair_counts = {
            "trial_count": len(pair_trials),
            "evaluable_trials": len(pair_evaluable),
            "dropped_trials": sum(row["has_drop"] for row in pair_trials),
            "censored_trials": sum(row["has_censor"] for row in pair_trials),
            "overlap_trials": sum(row["overlaps_previous_pair"] for row in pair_trials),
        }
        for field, expected in expected_pair_counts.items():
            if aggregate.get(field) != expected:
                raise CrossMapError(
                    f"{label} pair aggregate {key} {field} disagrees with trials"
                )
        pair_completions = [
            int(row["completion_latency_cycles"]) for row in pair_evaluable
        ]
        pair_skews = [int(row["service_skew_cycles"]) for row in pair_evaluable]
        expected_pair_metrics = {
            "mean_completion_latency_cycles": (
                statistics.fmean(pair_completions) if pair_completions else None
            ),
            "max_completion_latency_cycles": (
                max(pair_completions) if pair_completions else None
            ),
            "mean_service_skew_cycles": (
                statistics.fmean(pair_skews) if pair_skews else None
            ),
            "max_service_skew_cycles": max(pair_skews) if pair_skews else None,
        }
        for field, expected in expected_pair_metrics.items():
            _same_optional_number(
                aggregate.get(field), expected, f"{label} pair aggregate {key} {field}"
            )


def _optional_delta(affine: Any, identity: Any) -> int | None:
    if affine is None or identity is None:
        return None
    return int(affine) - int(identity)


def compare(
    identity_manifest: dict[str, Any],
    identity_report: dict[str, Any],
    affine_manifest: dict[str, Any],
    affine_report: dict[str, Any],
) -> dict[str, Any]:
    identity_contract = _manifest_contract(
        identity_manifest, expected_mapping="identity", label="identity"
    )
    affine_contract = _manifest_contract(
        affine_manifest, expected_mapping="affine", label="affine"
    )
    if _normalized_run(identity_contract) != _normalized_run(affine_contract):
        raise CrossMapError("identity and affine manifests describe different run contracts")
    if identity_contract["generator_version"] != affine_contract["generator_version"]:
        raise CrossMapError("identity and affine generator versions differ")

    _validate_report_provenance(identity_report, identity_contract, label="identity")
    _validate_report_provenance(affine_report, affine_contract, label="affine")
    if identity_report["candidate"] != affine_report["candidate"]:
        raise CrossMapError("identity and affine reports use different candidates")

    identity_trials = _validated_trials(identity_report, identity_contract, label="identity")
    affine_trials = _validated_trials(affine_report, affine_contract, label="affine")
    if set(identity_trials) != set(affine_trials):
        raise CrossMapError("identity and affine relation sets differ")

    rankability_reasons: list[str] = []
    for label, report in (("identity", identity_report), ("affine", affine_report)):
        if report["measurement_state"] != "COMPLETE":
            rankability_reasons.append(
                f"{label}_measurement_state={report['measurement_state']}"
            )
        if report["overlap_pairs"]:
            rankability_reasons.append(
                f"{label}_overlap_pairs={report['overlap_pairs']}"
            )
    rankable = not rankability_reasons

    rows: list[dict[str, Any]] = []
    by_pair: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for relation_id in sorted(identity_trials):
        identity = identity_trials[relation_id]
        affine = affine_trials[relation_id]
        canonical = (identity["canonical_source_a"], identity["canonical_source_b"])
        if canonical != (
            affine["canonical_source_a"], affine["canonical_source_b"]
        ) or identity["repeat_index"] != affine["repeat_index"]:
            raise CrossMapError(f"relation {relation_id} canonical join key mismatch")

        both_evaluable = (
            identity["result"] == "evaluable" and affine["result"] == "evaluable"
        )
        row = {
            "relation_id": relation_id,
            "repeat_index": identity["repeat_index"],
            "canonical_source_a": canonical[0],
            "canonical_source_b": canonical[1],
            "identity_physical_source_a": identity["physical_source_a"],
            "identity_physical_source_b": identity["physical_source_b"],
            "affine_physical_source_a": affine["physical_source_a"],
            "affine_physical_source_b": affine["physical_source_b"],
            "identity_result": identity["result"],
            "affine_result": affine["result"],
            "identity_overlaps_previous_pair": identity["overlaps_previous_pair"],
            "affine_overlaps_previous_pair": affine["overlaps_previous_pair"],
            "identity_overlapping_prior_pair_count": identity[
                "overlapping_prior_pair_count"
            ],
            "affine_overlapping_prior_pair_count": affine[
                "overlapping_prior_pair_count"
            ],
            "comparison_state": (
                "BOTH_EVALUABLE"
                if both_evaluable
                else "IDENTITY_INCOMPLETE"
                if identity["result"] != "evaluable" and affine["result"] == "evaluable"
                else "AFFINE_INCOMPLETE"
                if identity["result"] == "evaluable" and affine["result"] != "evaluable"
                else "BOTH_INCOMPLETE"
            ),
            "identity_completion_latency_cycles": identity.get("completion_latency_cycles"),
            "affine_completion_latency_cycles": affine.get("completion_latency_cycles"),
            "completion_delta_affine_minus_identity": (
                _optional_delta(
                    affine.get("completion_latency_cycles"),
                    identity.get("completion_latency_cycles"),
                )
                if both_evaluable else None
            ),
            "identity_service_skew_cycles": identity.get("service_skew_cycles"),
            "affine_service_skew_cycles": affine.get("service_skew_cycles"),
            "skew_delta_affine_minus_identity": (
                _optional_delta(
                    affine.get("service_skew_cycles"),
                    identity.get("service_skew_cycles"),
                )
                if both_evaluable else None
            ),
            "identity_order": identity["order"],
            "affine_order": affine["order"],
            "order_delta_affine_minus_identity": (
                affine["order_code"] - identity["order_code"]
                if both_evaluable else None
            ),
            "order_changed": both_evaluable and identity["order"] != affine["order"],
            "identity_drop": identity["has_drop"],
            "affine_drop": affine["has_drop"],
            "drop_delta_affine_minus_identity": int(affine["has_drop"]) - int(identity["has_drop"]),
            "identity_censor": identity["has_censor"],
            "affine_censor": affine["has_censor"],
            "censor_delta_affine_minus_identity": int(affine["has_censor"]) - int(identity["has_censor"]),
            "identity_dropped_and_censored": (
                identity["has_drop"] and identity["has_censor"]
            ),
            "affine_dropped_and_censored": (
                affine["has_drop"] and affine["has_censor"]
            ),
            "dropped_and_censored_delta_affine_minus_identity": (
                int(affine["has_drop"] and affine["has_censor"])
                - int(identity["has_drop"] and identity["has_censor"])
            ),
        }
        rows.append(row)
        by_pair[canonical].append(row)

    comparable = [row for row in rows if row["comparison_state"] == "BOTH_EVALUABLE"]
    completion_deltas = [row["completion_delta_affine_minus_identity"] for row in comparable]
    skew_deltas = [row["skew_delta_affine_minus_identity"] for row in comparable]
    pair_rows = []
    for canonical, trials in sorted(by_pair.items()):
        comparable_pair = [row for row in trials if row["comparison_state"] == "BOTH_EVALUABLE"]
        pair_completion = [row["completion_delta_affine_minus_identity"] for row in comparable_pair]
        pair_skew = [row["skew_delta_affine_minus_identity"] for row in comparable_pair]
        pair_rows.append({
            "canonical_source_a": canonical[0],
            "canonical_source_b": canonical[1],
            "trial_count": len(trials),
            "comparable_trials": len(comparable_pair),
            "mean_completion_delta_affine_minus_identity": (
                statistics.fmean(pair_completion)
                if rankable and pair_completion else None
            ),
            "max_completion_delta_affine_minus_identity": (
                max(pair_completion) if rankable and pair_completion else None
            ),
            "mean_skew_delta_affine_minus_identity": (
                statistics.fmean(pair_skew) if rankable and pair_skew else None
            ),
            "max_skew_delta_affine_minus_identity": (
                max(pair_skew) if rankable and pair_skew else None
            ),
            "order_changed_trials": sum(row["order_changed"] for row in trials),
            "drop_delta_affine_minus_identity": sum(
                row["drop_delta_affine_minus_identity"] for row in trials
            ),
            "censor_delta_affine_minus_identity": sum(
                row["censor_delta_affine_minus_identity"] for row in trials
            ),
            "dropped_and_censored_delta_affine_minus_identity": sum(
                row["dropped_and_censored_delta_affine_minus_identity"]
                for row in trials
            ),
            "identity_dropped_and_censored_trials": sum(
                row["identity_dropped_and_censored"] for row in trials
            ),
            "affine_dropped_and_censored_trials": sum(
                row["affine_dropped_and_censored"] for row in trials
            ),
            "identity_overlap_trials": sum(
                row["identity_overlaps_previous_pair"] for row in trials
            ),
            "affine_overlap_trials": sum(
                row["affine_overlaps_previous_pair"] for row in trials
            ),
        })

    overlap_strata = {
        "BOTH_ISOLATED": sum(
            not row["identity_overlaps_previous_pair"]
            and not row["affine_overlaps_previous_pair"]
            for row in rows
        ),
        "IDENTITY_ONLY_OVERLAP": sum(
            row["identity_overlaps_previous_pair"]
            and not row["affine_overlaps_previous_pair"]
            for row in rows
        ),
        "AFFINE_ONLY_OVERLAP": sum(
            not row["identity_overlaps_previous_pair"]
            and row["affine_overlaps_previous_pair"]
            for row in rows
        ),
        "BOTH_OVERLAP": sum(
            row["identity_overlaps_previous_pair"]
            and row["affine_overlaps_previous_pair"]
            for row in rows
        ),
    }

    return {
        "delta_convention": "affine_minus_identity",
        "order_code_contract": {"A_FIRST": -1, "SAME_CYCLE": 0, "B_FIRST": 1},
        "candidate": identity_report["candidate"],
        "rankable": rankable,
        "rankability_reasons": rankability_reasons,
        "latency_skew_aggregate_scope": (
            "ALL_TRIALS_ISOLATED_COMPLETE" if rankable else "SUPPRESSED_NOT_RANKABLE"
        ),
        "seed": str(identity_contract["run"]["seed"]),
        "source_count": identity_contract["source_count"],
        "pair_repeats": identity_contract["repeats"],
        "identity_trace_sha256": identity_contract["trace_sha256"],
        "affine_trace_sha256": affine_contract["trace_sha256"],
        "identity_permutation": identity_contract["permutation"],
        "affine_permutation": affine_contract["permutation"],
        "trial_count": len(rows),
        "both_evaluable_trials": len(comparable),
        "incomplete_comparisons": len(rows) - len(comparable),
        "identity_measurement_state": identity_report["measurement_state"],
        "affine_measurement_state": affine_report["measurement_state"],
        "identity_evaluable_pairs": identity_report["evaluable_pairs"],
        "affine_evaluable_pairs": affine_report["evaluable_pairs"],
        "identity_dropped_pairs": identity_report["dropped_pairs"],
        "affine_dropped_pairs": affine_report["dropped_pairs"],
        "identity_censored_pairs": identity_report["censored_pairs"],
        "affine_censored_pairs": affine_report["censored_pairs"],
        "identity_dropped_and_censored_pairs": sum(
            row["identity_dropped_and_censored"] for row in rows
        ),
        "affine_dropped_and_censored_pairs": sum(
            row["affine_dropped_and_censored"] for row in rows
        ),
        "identity_overlap_pairs": identity_report["overlap_pairs"],
        "affine_overlap_pairs": affine_report["overlap_pairs"],
        "overlap_strata": overlap_strata,
        "mean_completion_delta_affine_minus_identity": (
            statistics.fmean(completion_deltas)
            if rankable and completion_deltas else None
        ),
        "max_completion_regression_affine_minus_identity": (
            max(completion_deltas) if rankable and completion_deltas else None
        ),
        "mean_skew_delta_affine_minus_identity": (
            statistics.fmean(skew_deltas) if rankable and skew_deltas else None
        ),
        "max_skew_regression_affine_minus_identity": (
            max(skew_deltas) if rankable and skew_deltas else None
        ),
        "order_changed_trials": sum(row["order_changed"] for row in rows),
        "drop_delta_affine_minus_identity": sum(
            row["drop_delta_affine_minus_identity"] for row in rows
        ),
        "censor_delta_affine_minus_identity": sum(
            row["censor_delta_affine_minus_identity"] for row in rows
        ),
        "dropped_and_censored_delta_affine_minus_identity": sum(
            row["dropped_and_censored_delta_affine_minus_identity"] for row in rows
        ),
        "canonical_pair_aggregates": pair_rows,
        "trials": rows,
    }


def analyze_paths(
    identity_manifest_path: Path,
    identity_report_path: Path,
    affine_manifest_path: Path,
    affine_report_path: Path,
) -> dict[str, Any]:
    result = compare(
        _read_json(identity_manifest_path),
        _read_json(identity_report_path),
        _read_json(affine_manifest_path),
        _read_json(affine_report_path),
    )
    result["input_sha256"] = {
        "identity_manifest": _sha256(identity_manifest_path),
        "identity_report": _sha256(identity_report_path),
        "affine_manifest": _sha256(affine_manifest_path),
        "affine_report": _sha256(affine_report_path),
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-manifest", type=Path, required=True)
    parser.add_argument("--identity-report", type=Path, required=True)
    parser.add_argument("--affine-manifest", type=Path, required=True)
    parser.add_argument("--affine-report", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze_paths(
            args.identity_manifest,
            args.identity_report,
            args.affine_manifest,
            args.affine_report,
        )
    except CrossMapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
