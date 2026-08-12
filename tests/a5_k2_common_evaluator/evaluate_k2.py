#!/usr/bin/env python3
"""Validate and compare exactly three candidate-owned K2 evidence bundles."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

from k2_oracle import (
    ContractError, EVIDENCE_SCHEMA, PolicyState, RESULT_SCHEMA, RETIRE_LANES,
    SOURCE_COUNT, advance_actual, file_sha256, fold_prefix, latency_summary,
    load_json, object_sha256, row_for_source, validate_vector_bundle,
)


REQUIRED_IDENTITY = ("id", "source_sha256", "binding_sha256", "runner_sha256")


def failure(failures: list[dict[str, Any]], code: str, cycle: int | None,
            detail: str) -> None:
    failures.append({"code": code, "cycle": cycle, "detail": detail})


def validate_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def validate_evidence(document: Any, vectors: dict[str, Any], path: Path) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema") != EVIDENCE_SCHEMA:
        raise ContractError(f"{path}: schema must be {EVIDENCE_SCHEMA}")
    candidate = document.get("candidate")
    if not isinstance(candidate, dict) or any(key not in candidate for key in REQUIRED_IDENTITY):
        raise ContractError(f"{path}: incomplete candidate identity")
    if not isinstance(candidate["id"], str) or not candidate["id"]:
        raise ContractError(f"{path}: invalid candidate id")
    for key in REQUIRED_IDENTITY[1:]:
        validate_sha(candidate[key], f"{path}: candidate.{key}")
    claims = candidate.get("claims")
    if not isinstance(claims, dict) or not isinstance(
            claims.get("full_future_trace_equivalence"), bool):
        raise ContractError(f"{path}: candidate claims must explicitly classify future equivalence")
    if document.get("vector_bundle_sha256") != vectors["bundle_sha256"]:
        raise ContractError(f"{path}: vector bundle SHA mismatch")
    runs = document.get("runs")
    if not isinstance(runs, list):
        raise ContractError(f"{path}: runs must be an array")
    by_name: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            raise ContractError(f"{path}: malformed run evidence")
        if run["name"] in by_name:
            raise ContractError(f"{path}: duplicate run {run['name']}")
        by_name[run["name"]] = run
    expected = {run["name"] for run in vectors["runs"] if "required" in run.get("tags", [])}
    known = {run["name"] for run in vectors["runs"]}
    unknown = sorted(set(by_name) - known)
    if unknown:
        raise ContractError(f"{path}: unknown runs: {', '.join(unknown)}")
    missing = sorted(expected - set(by_name))
    if missing:
        raise ContractError(f"{path}: missing required runs: {', '.join(missing)}")
    return {"candidate": candidate, "runs": by_name}


def validate_outputs(outputs: Any, run_name: str, cycle: int) -> list[dict[str, Any]]:
    if not isinstance(outputs, list) or len(outputs) != RETIRE_LANES:
        raise ContractError(f"{run_name} cycle {cycle}: outputs must contain two lane records")
    checked: list[dict[str, Any]] = []
    for lane, output in enumerate(outputs):
        if not isinstance(output, dict) or output.get("lane") != lane or not isinstance(
                output.get("valid"), bool):
            raise ContractError(f"{run_name} cycle {cycle}: malformed lane {lane}")
        if output["valid"]:
            if not isinstance(output.get("source"), int) or not 0 <= output["source"] < SOURCE_COUNT:
                raise ContractError(f"{run_name} cycle {cycle}: invalid output source")
            if not isinstance(output.get("event_id"), str) or not output["event_id"]:
                raise ContractError(f"{run_name} cycle {cycle}: valid output lacks event_id")
        elif set(output) != {"lane", "valid"}:
            raise ContractError(f"{run_name} cycle {cycle}: invalid output must omit payload fields")
        checked.append(output)
    return checked


def validate_accepts(accepts: Any, run_name: str, cycle: int) -> list[dict[str, Any]]:
    if not isinstance(accepts, list) or len(accepts) > RETIRE_LANES:
        raise ContractError(f"{run_name} cycle {cycle}: accepts must contain at most two records")
    checked: list[dict[str, Any]] = []
    for slot, accept in enumerate(accepts):
        if not isinstance(accept, dict) or accept.get("slot") != slot:
            raise ContractError(f"{run_name} cycle {cycle}: accepts must be a contiguous ordered prefix")
        if not isinstance(accept.get("source"), int) or not 0 <= accept["source"] < SOURCE_COUNT:
            raise ContractError(f"{run_name} cycle {cycle}: invalid accepted source")
        if not isinstance(accept.get("event_id"), str) or not accept["event_id"]:
            raise ContractError(f"{run_name} cycle {cycle}: accepted event lacks event_id")
        checked.append(accept)
    return checked


def evaluate_run(vector: dict[str, Any], observed: dict[str, Any],
                 thresholds: dict[str, Any]) -> dict[str, Any]:
    name = vector["name"]
    if observed.get("run_sha256") != vector["run_sha256"]:
        raise ContractError(f"{name}: observed run SHA mismatch")
    observations = observed.get("cycles")
    if not isinstance(observations, list) or len(observations) != len(vector["cycles"]):
        raise ContractError(f"{name}: observation cycle count mismatch")

    failures: list[dict[str, Any]] = []
    state = PolicyState()
    pending: dict[int, dict[str, Any]] = {}
    generated: dict[str, dict[str, Any]] = {}
    overrun: set[str] = set()
    accept_cycle: dict[str, int] = {}
    accept_source: dict[str, int] = {}
    accepted_order: list[str] = []
    live_order: list[str] = []
    retired_order: list[str] = []
    retire_cycle: dict[str, int] = {}
    reset_aborted: set[str] = set()
    reset_aborted_pending: set[str] = set()
    reset_aborted_inflight: set[str] = set()
    row_counts = [0, 0, 0, 0]
    persistent_rows: list[int] = []
    prefix_opportunities = 0
    prefix_matches = 0
    primary_matches = 0
    second_attempts = 0
    second_matches = 0
    duplicate_cycles = 0
    first_post_reset_accept: int | None = None
    latest_reset_release = 0
    prior_outputs: list[dict[str, Any]] | None = None
    prior_ready = [True, True]
    empty_since: int | None = None
    drain_lags: list[int] = []

    for cycle_index, (stimulus, observation) in enumerate(zip(vector["cycles"], observations)):
        if not isinstance(observation, dict) or observation.get("cycle") != cycle_index:
            raise ContractError(f"{name}: observation sequence mismatch at {cycle_index}")
        accepts = validate_accepts(observation.get("accepts"), name, cycle_index)
        outputs = validate_outputs(observation.get("outputs"), name, cycle_index)
        if not isinstance(observation.get("drain_idle"), bool):
            raise ContractError(f"{name} cycle {cycle_index}: drain_idle must be boolean")
        reset_n = stimulus["reset_n"]
        ready = stimulus["retire_ready"]

        if not reset_n:
            if accepts:
                failure(failures, "reset_accept", cycle_index, "source accepted while reset asserted")
            if any(output["valid"] for output in outputs):
                failure(failures, "reset_output", cycle_index, "retire output valid during reset")
            if observation["drain_idle"]:
                failure(failures, "reset_drain_idle", cycle_index, "drain_idle asserted during reset")
            pending_abort = {item["event_id"] for item in pending.values()}
            inflight_abort = set(live_order)
            reset_aborted_pending.update(pending_abort)
            reset_aborted_inflight.update(inflight_abort)
            reset_aborted.update(pending_abort | inflight_abort)
            pending.clear()
            live_order.clear()
            state = PolicyState()
            prior_outputs = None
            first_post_reset_accept = None
            latest_reset_release = cycle_index + 1
            empty_since = None
            continue

        for item in stimulus["occurrences"]:
            identifier = item["event_id"]
            source = item["source"]
            generated[identifier] = {"cycle": cycle_index, "source": source}
            if source in pending:
                overrun.add(identifier)
            else:
                pending[source] = item

        expected, _ = fold_prefix(pending, state)
        actual_sources = [item["source"] for item in accepts]
        if expected:
            prefix_opportunities += 1
        if actual_sources == expected[:len(actual_sources)]:
            prefix_matches += 1
        else:
            failure(failures, "prefix_mismatch", cycle_index,
                    f"expected prefix {expected}, observed {actual_sources}")
        if actual_sources:
            if actual_sources[0] == (expected[0] if expected else None):
                primary_matches += 1
            else:
                failure(failures, "primary_mismatch", cycle_index,
                        f"expected primary {expected[:1]}, observed {actual_sources[:1]}")
        if len(actual_sources) == 2:
            second_attempts += 1
            if len(expected) == 2 and actual_sources[1] == expected[1]:
                second_matches += 1
        if len(set(actual_sources)) != len(actual_sources):
            duplicate_cycles += 1
            failure(failures, "same_source_duplicate", cycle_index,
                    f"duplicate accepted sources {actual_sources}")

        if "persistent_weight" in vector.get("tags", []):
            for source in actual_sources:
                if len(persistent_rows) < int(
                        thresholds["hard_gates"]["persistent_commit_window"]):
                    persistent_rows.append(row_for_source(source))

        for accept in accepts:
            source = accept["source"]
            identifier = accept["event_id"]
            if source not in pending:
                failure(failures, "accept_nonpending", cycle_index,
                        f"source {source} was not pending")
                continue
            expected_id = pending[source]["event_id"]
            if identifier != expected_id:
                failure(failures, "stale_or_wrong_event", cycle_index,
                        f"source {source} expected {expected_id}, observed {identifier}")
                continue
            if identifier in accept_cycle:
                failure(failures, "duplicate_accept", cycle_index, identifier)
                continue
            del pending[source]
            accept_cycle[identifier] = cycle_index
            accept_source[identifier] = source
            accepted_order.append(identifier)
            live_order.append(identifier)
            row = row_for_source(source)
            row_counts[row] += 1
            state = advance_actual(state, source)
            if first_post_reset_accept is None:
                first_post_reset_accept = cycle_index

        if prior_outputs is not None:
            for lane in range(RETIRE_LANES):
                if prior_outputs[lane]["valid"] and not prior_ready[lane]:
                    if outputs[lane] != prior_outputs[lane]:
                        failure(failures, "lane_stall_corruption", cycle_index,
                                f"lane {lane} changed while stalled")
        if outputs[1]["valid"] and not outputs[0]["valid"]:
            failure(failures, "output_lane_hole", cycle_index,
                    "lane 1 was valid while ordered lane 0 was invalid")
        fires = [output for lane, output in enumerate(outputs)
                 if output["valid"] and ready[lane]]
        if outputs[0]["valid"] and not ready[0] and len(fires) > 0:
            if outputs[1]["valid"] and ready[1]:
                failure(failures, "younger_lane_bypass", cycle_index,
                        "lane 1 fired while older lane 0 was stalled")
        for output in fires:
            identifier = output["event_id"]
            if not live_order:
                failure(failures, "phantom_retire", cycle_index, identifier)
                continue
            expected_id = live_order[0]
            if identifier != expected_id:
                code = "reset_phantom" if identifier in reset_aborted else "retire_order_or_phantom"
                failure(failures, code, cycle_index,
                        f"expected {expected_id}, observed {identifier}")
                if identifier not in live_order:
                    continue
                live_order.remove(identifier)
            else:
                live_order.pop(0)
            if output["source"] != accept_source.get(identifier):
                failure(failures, "retire_source_mismatch", cycle_index, identifier)
            if identifier in retire_cycle:
                failure(failures, "duplicate_retire", cycle_index, identifier)
            retire_cycle[identifier] = cycle_index
            retired_order.append(identifier)

        if first_post_reset_accept is None and any(output["valid"] for output in outputs):
            failure(failures, "reset_phantom", cycle_index,
                    "output appeared before any post-reset acceptance")

        truly_empty = not pending and not live_order and not any(output["valid"] for output in outputs)
        if observation["drain_idle"] and not truly_empty:
            failure(failures, "false_drain_idle", cycle_index,
                    "drain_idle asserted with pending/inflight/output state")
        if truly_empty:
            if empty_since is None:
                empty_since = cycle_index
            if observation["drain_idle"]:
                drain_lags.append(cycle_index - empty_since)
                empty_since = None
        else:
            empty_since = None

        prior_outputs = outputs
        prior_ready = ready

    expected_live = [identifier for identifier in accepted_order if identifier not in reset_aborted]
    if retired_order != expected_live:
        failure(failures, "accepted_retired_order", None,
                f"accepted live {len(expected_live)} retired {len(retired_order)}")
    if pending:
        failure(failures, "pending_after_drain", None, f"sources {sorted(pending)}")
    if live_order:
        failure(failures, "inflight_after_drain", None, f"events {live_order[:4]}")
    unresolved_occurrences = (set(generated) - overrun - reset_aborted_pending -
                              set(accept_cycle))
    if unresolved_occurrences:
        failure(failures, "occurrence_conservation", None,
                f"unclassified events {sorted(unresolved_occurrences)[:4]}")
    unresolved_accepts = set(accept_cycle) - reset_aborted_inflight - set(retire_cycle)
    if unresolved_accepts:
        failure(failures, "accept_conservation", None,
                f"unclassified accepts {sorted(unresolved_accepts)[:4]}")
    if not observations[-1].get("drain_idle"):
        failure(failures, "drain_idle_missing", len(observations) - 1,
                "final cycle is not drained")
    max_drain_lag = int(thresholds["hard_gates"]["drain_idle_max_lag_cycles"])
    if any(lag > max_drain_lag for lag in drain_lags):
        failure(failures, "drain_idle_late", None, f"lags {drain_lags}")

    weight_window = int(thresholds["hard_gates"]["persistent_commit_window"])
    if "persistent_weight" in vector.get("tags", []):
        if len(persistent_rows) < weight_window:
            failure(failures, "weight_window_short", None,
                    f"need {weight_window}, observed {len(persistent_rows)}")
        else:
            counts = [persistent_rows.count(row) for row in range(4)]
            expected_counts = thresholds["hard_gates"]["persistent_row_counts"]
            if counts != expected_counts:
                failure(failures, "false_1_5_5_1", None,
                        f"expected {expected_counts}, observed {counts}")

    occurrence_to_accept = [accept_cycle[identifier] - generated[identifier]["cycle"]
                            for identifier in accept_cycle]
    accept_to_retire = [retire_cycle[identifier] - accept_cycle[identifier]
                        for identifier in retire_cycle if identifier in accept_cycle]
    if "sparse" in vector.get("tags", []):
        sparse_max = max(occurrence_to_accept, default=0)
        threshold = int(thresholds["hard_gates"]["sparse_max_occurrence_to_accept_cycles"])
        if overrun or len(accept_cycle) != len(generated) or sparse_max > threshold:
            failure(failures, "sparse_not_work_conserving", None,
                    f"generated={len(generated)} accepted={len(accept_cycle)} overrun={len(overrun)} max={sparse_max}")

    if any(item["code"] in {"primary_mismatch", "prefix_mismatch"} for item in failures):
        if not any(item["code"] == "primary_mismatch" for item in failures):
            grade = "PRIMARY_ONLY"
        else:
            grade = "FAIL"
    else:
        grade = "FULL"
    measurement_start, measurement_end = vector["measurement_window"]
    retired_in_window = sum(measurement_start <= cycle < measurement_end
                            for cycle in retire_cycle.values())
    return {
        "name": name,
        "origin": vector["origin"],
        "tags": vector.get("tags", []),
        "status": "PASS" if not failures else "FAIL",
        "hard_failures": failures,
        "accounting": {
            "generated": len(generated), "source_overrun": len(overrun),
            "source_overrun_ratio": len(overrun) / len(generated) if generated else 0.0,
            "accepted": len(accept_cycle), "reset_aborted": len(reset_aborted),
            "reset_aborted_pending": len(reset_aborted_pending),
            "reset_aborted_inflight": len(reset_aborted_inflight),
            "retired": len(retire_cycle), "pending_at_end": len(pending),
            "inflight_at_end": len(live_order),
        },
        "prefix": {
            "grade": grade, "opportunities": prefix_opportunities,
            "matching_prefix_cycles": prefix_matches, "matching_primary": primary_matches,
            "second_attempts": second_attempts, "matching_seconds": second_matches,
            "same_source_duplicate_cycles": duplicate_cycles,
        },
        "committed_row_counts": row_counts,
        "persistent_first_120_row_counts": (
            [persistent_rows.count(row) for row in range(4)] if persistent_rows else None),
        "latency_cycles": {
            "occurrence_to_accept": latency_summary(occurrence_to_accept),
            "accept_to_retire": latency_summary(accept_to_retire),
        },
        "fixed_window": {
            "cycles": measurement_end - measurement_start,
            "retired": retired_in_window,
            "event_per_cycle": retired_in_window / (measurement_end - measurement_start),
        },
        "drain_lags": drain_lags,
        "latest_reset_release": latest_reset_release,
        "_accepted_event_ids": sorted(accept_cycle),
        "_occurrence_to_accept_by_id": {
            identifier: accept_cycle[identifier] - generated[identifier]["cycle"]
            for identifier in accept_cycle
        },
        "_accept_to_retire_by_id": {
            identifier: retire_cycle[identifier] - accept_cycle[identifier]
            for identifier in retire_cycle if identifier in accept_cycle
        },
    }


def aggregate(candidate: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [item for run in runs for item in run["hard_failures"]]
    claims = candidate["claims"]
    if claims["full_future_trace_equivalence"]:
        failures.append({
            "code": "future_trace_overclaim", "cycle": None,
            "detail": "K2 frozen-cohort prefix does not imply equivalence under future arrivals",
        })
    return {
        "candidate": candidate,
        "status": "PASS" if not failures else "HOLD",
        "hard_failure_count": len(failures),
        "hard_failures": failures,
        "runs": runs,
    }


def band_delta(left: float | int | None, right: float | int | None,
               absolute: float, relative: float = 0.0) -> str:
    if left is None or right is None:
        return "UNAVAILABLE"
    tolerance = max(absolute, relative * max(abs(float(left)), abs(float(right))))
    difference = float(left) - float(right)
    if abs(difference) <= tolerance:
        return "TIE"
    return "LEFT_BETTER" if difference > 0 else "RIGHT_BETTER"


def compare(results: list[dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    dominated: set[str] = set()
    comparison = thresholds["comparison"]
    result_runs = [{run["name"]: run for run in result["runs"]} for result in results]
    common_names = sorted(set.intersection(*(set(runs) for runs in result_runs)))
    accepted_cohort = {
        name: set.intersection(*(
            set(runs[name]["_accepted_event_ids"]) for runs in result_runs))
        for name in common_names
    }
    retired_cohort = {
        name: accepted_cohort[name] & set.intersection(*(
            set(runs[name]["_accept_to_retire_by_id"]) for runs in result_runs))
        for name in common_names
    }
    for left_index in range(len(results)):
        for right_index in range(left_index + 1, len(results)):
            left = results[left_index]
            right = results[right_index]
            left_runs = {run["name"]: run for run in left["runs"]}
            right_runs = {run["name"]: run for run in right["runs"]}
            per_run = []
            for name in common_names:
                left_run, right_run = left_runs[name], right_runs[name]
                left_ids = set(left_run["_accepted_event_ids"])
                right_ids = set(right_run["_accepted_event_ids"])
                matched = sorted(accepted_cohort[name])
                left_occurrence = [left_run["_occurrence_to_accept_by_id"][item]
                                   for item in matched]
                right_occurrence = [right_run["_occurrence_to_accept_by_id"][item]
                                    for item in matched]
                retire_matched = sorted(retired_cohort[name])
                left_retire = [left_run["_accept_to_retire_by_id"][item]
                               for item in retire_matched]
                right_retire = [right_run["_accept_to_retire_by_id"][item]
                                for item in retire_matched]
                epc_band = comparison["fixed_window_event_per_cycle_equivalence_band"]
                occurrence_band = comparison["p99_occurrence_to_accept_equivalence_band"]
                retire_band = comparison["p99_accept_to_retire_equivalence_band"]
                overrun_band = comparison["overrun_ratio_equivalence_band"]
                left_occ_summary = latency_summary(left_occurrence)
                right_occ_summary = latency_summary(right_occurrence)
                left_ret_summary = latency_summary(left_retire)
                right_ret_summary = latency_summary(right_retire)
                per_run.append({
                    "name": name,
                    "event_per_cycle": band_delta(
                        left_run["fixed_window"]["event_per_cycle"],
                        right_run["fixed_window"]["event_per_cycle"],
                        epc_band["absolute"], epc_band["relative"]),
                    "source_overrun_ratio": band_delta(
                        # Lower overrun is better, hence reverse operands.
                        right_run["accounting"]["source_overrun_ratio"],
                        left_run["accounting"]["source_overrun_ratio"],
                        overrun_band["absolute"]),
                    "accepted_set": {
                        "all_candidate_matched": len(matched),
                        "matched": len(matched), "left_only": len(left_ids - right_ids),
                        "right_only": len(right_ids - left_ids),
                        "symmetric_difference": len(left_ids ^ right_ids),
                    },
                    "matched_occurrence_to_accept": {
                        "left": left_occ_summary, "right": right_occ_summary,
                        "p99_band": band_delta(
                            # Lower latency is better, hence reverse operands.
                            right_occ_summary["p99"], left_occ_summary["p99"],
                            occurrence_band["absolute_cycles"], occurrence_band["relative"]),
                    },
                    "matched_accept_to_retire": {
                        "left": left_ret_summary, "right": right_ret_summary,
                        "p99_band": band_delta(
                            right_ret_summary["p99"], left_ret_summary["p99"],
                            retire_band["absolute_cycles"], retire_band["relative"]),
                    },
                    "latency_comparison_scope": "event-ID cohort common to all three candidates",
                })
            directions = [direction for run in per_run for direction in (
                run["event_per_cycle"], run["source_overrun_ratio"],
                run["matched_occurrence_to_accept"]["p99_band"],
                run["matched_accept_to_retire"]["p99_band"])
                if direction != "UNAVAILABLE"]
            if left["status"] == "PASS" and right["status"] != "PASS":
                eligibility, verdict = "LEFT_ONLY_ELIGIBLE", "LEFT_ADVANCES"
            elif right["status"] == "PASS" and left["status"] != "PASS":
                eligibility, verdict = "RIGHT_ONLY_ELIGIBLE", "RIGHT_ADVANCES"
            elif left["status"] != "PASS" or right["status"] != "PASS":
                eligibility, verdict = "BOTH_INELIGIBLE", "NO_COMPARISON"
            elif "RIGHT_BETTER" not in directions and "LEFT_BETTER" in directions:
                eligibility, verdict = "BOTH_ELIGIBLE", "LEFT_DOMINATES"
                dominated.add(right["candidate"]["id"])
            elif "LEFT_BETTER" not in directions and "RIGHT_BETTER" in directions:
                eligibility, verdict = "BOTH_ELIGIBLE", "RIGHT_DOMINATES"
                dominated.add(left["candidate"]["id"])
            elif directions and all(direction == "TIE" for direction in directions):
                eligibility, verdict = "BOTH_ELIGIBLE", "TIE_WITHIN_BANDS"
            else:
                eligibility, verdict = "BOTH_ELIGIBLE", "PARETO_TRADEOFF"
            comparisons.append({
                "left": left["candidate"]["id"], "right": right["candidate"]["id"],
                "hard_gate": eligibility, "verdict": verdict,
                "per_run": per_run,
            })
    passing = {result["candidate"]["id"] for result in results if result["status"] == "PASS"}
    return {
        "rule": comparison["ranking"],
        "aggregate_score": None,
        "pareto_frontier": sorted(passing - dominated),
        "frontier_scope": "hard-gate-eligible candidates; all-three matched cohorts and frozen bands",
        "pairwise": comparisons,
    }


def evaluate_documents(vectors: dict[str, Any], evidences: list[tuple[Path, dict[str, Any]]],
                       thresholds: dict[str, Any]) -> dict[str, Any]:
    if len(evidences) != 3:
        raise ContractError("the common comparison requires exactly three candidate evidence files")
    vector_by_name = {run["name"]: run for run in vectors["runs"]}
    results = []
    ids: set[str] = set()
    for path, document in evidences:
        checked = validate_evidence(document, vectors, path)
        candidate = checked["candidate"]
        if candidate["id"] in ids:
            raise ContractError(f"duplicate candidate id {candidate['id']}")
        ids.add(candidate["id"])
        run_results = [evaluate_run(vector_by_name[name], checked["runs"][name], thresholds)
                       for name in vector_by_name if name in checked["runs"]]
        result = aggregate(candidate, run_results)
        result["evidence_path"] = str(path)
        result["evidence_sha256"] = file_sha256(path)
        results.append(result)
    comparison_result = compare(results, thresholds)
    for result in results:
        for run in result["runs"]:
            for key in [key for key in run if key.startswith("_")]:
                del run[key]
    return {
        "schema": RESULT_SCHEMA,
        "scope": "INDEPENDENT_COMMON_DIGITAL_N16_K2_TRANSACTION_EVALUATION",
        "vector_bundle_sha256": vectors["bundle_sha256"],
        "thresholds_sha256": object_sha256(thresholds),
        "future_trace_equivalence": "EXPLICITLY_NOT_CLAIMED",
        "candidates": results,
        "comparison": comparison_result,
        "status": "PASS" if all(result["status"] == "PASS" for result in results) else "HOLD",
        "non_qualification": [
            "physical PPA", "official competition release", "future-arrival trace equivalence",
            "candidate RTL not named by exact source SHA", "missing frozen-v4 owner evidence",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", required=True, type=Path)
    parser.add_argument("--thresholds", type=Path,
                        default=Path(__file__).with_name("thresholds.json"))
    parser.add_argument("--candidate", action="append", type=Path, required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(f"error: refusing to overwrite {args.output}", file=sys.stderr)
        return 2
    try:
        vectors = validate_vector_bundle(load_json(args.vectors))
        thresholds = load_json(args.thresholds)
        evidences = [(path, load_json(path)) for path in args.candidate]
        result = evaluate_documents(vectors, evidences, thresholds)
    except ContractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"A5_K2_EVALUATION_{result['status']} candidates={len(result['candidates'])}")
    return 0 if result["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
