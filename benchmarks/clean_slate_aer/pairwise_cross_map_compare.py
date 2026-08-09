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


def _trial_order(trial: dict[str, Any], label: str) -> tuple[str, int | None]:
    result = trial.get("result")
    states = (trial.get("event_state_a"), trial.get("event_state_b"))
    allowed_states = {"source_overrun", "pending", "accepted", "delivered"}
    if any(state not in allowed_states for state in states):
        raise CrossMapError(f"{label} has an invalid event state")
    has_drop = "source_overrun" in states
    has_censor = any(state in {"pending", "accepted"} for state in states)
    expected_result = "dropped" if has_drop else "censored" if has_censor else "evaluable"
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
    return by_relation


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
                statistics.fmean(pair_completion) if pair_completion else None
            ),
            "max_completion_delta_affine_minus_identity": (
                max(pair_completion) if pair_completion else None
            ),
            "mean_skew_delta_affine_minus_identity": (
                statistics.fmean(pair_skew) if pair_skew else None
            ),
            "max_skew_delta_affine_minus_identity": max(pair_skew) if pair_skew else None,
            "order_changed_trials": sum(row["order_changed"] for row in trials),
            "drop_delta_affine_minus_identity": sum(
                row["drop_delta_affine_minus_identity"] for row in trials
            ),
            "censor_delta_affine_minus_identity": sum(
                row["censor_delta_affine_minus_identity"] for row in trials
            ),
        })

    return {
        "delta_convention": "affine_minus_identity",
        "order_code_contract": {"A_FIRST": -1, "SAME_CYCLE": 0, "B_FIRST": 1},
        "candidate": identity_report["candidate"],
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
        "mean_completion_delta_affine_minus_identity": (
            statistics.fmean(completion_deltas) if completion_deltas else None
        ),
        "max_completion_regression_affine_minus_identity": (
            max(completion_deltas) if completion_deltas else None
        ),
        "mean_skew_delta_affine_minus_identity": (
            statistics.fmean(skew_deltas) if skew_deltas else None
        ),
        "max_skew_regression_affine_minus_identity": max(skew_deltas) if skew_deltas else None,
        "order_changed_trials": sum(row["order_changed"] for row in rows),
        "drop_delta_affine_minus_identity": sum(
            row["drop_delta_affine_minus_identity"] for row in rows
        ),
        "censor_delta_affine_minus_identity": sum(
            row["censor_delta_affine_minus_identity"] for row in rows
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
