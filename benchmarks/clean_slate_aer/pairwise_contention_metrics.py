#!/usr/bin/env python3
"""Measure address-pair service sensitivity from a frozen pairwise trace."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import aggregate


class PairwiseMetricError(ValueError):
    """Raised when pairwise trace and result provenance disagree."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PairwiseMetricError(f"cannot read {path}: {exc}") from exc


def _read_trace(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise PairwiseMetricError(f"cannot read trace {path}: {exc}") from exc


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def analyze(trace_path: Path, manifest_path: Path, event_path: Path) -> dict[str, Any]:
    metadata = _read_json(manifest_path)
    run = metadata.get("run", {})
    if run.get("workload") != "pairwise_contention":
        raise PairwiseMetricError("run manifest must describe pairwise_contention")
    if metadata.get("event_identity_mode") != "address_only":
        raise PairwiseMetricError("pairwise run must use address_only event identity")
    if metadata.get("trace_file") != trace_path.name:
        raise PairwiseMetricError("trace filename does not match run manifest")
    if hashlib.sha256(trace_path.read_bytes()).hexdigest() != metadata.get(
        "trace_sha256"
    ):
        raise PairwiseMetricError("trace SHA256 does not match run manifest")

    trace = _read_trace(trace_path)
    events = aggregate.read_events([event_path])
    if not events:
        raise PairwiseMetricError("event result is empty")
    event_by_id = {event.tb_only_event_id: event for event in events}
    if len(event_by_id) != len(events) or set(event_by_id) != set(range(len(trace))):
        raise PairwiseMetricError("event result IDs do not match trace")
    report_group = metadata.get("report_group", run.get("name"))
    expected_seed = str(run.get("seed", ""))
    expected_load_pct = int((Decimal(str(run.get("load"))) * 1000 + 5) // 10)
    first = events[0]
    if any(
        event.candidate != first.candidate
        or event.test != report_group
        or event.seed != expected_seed
        or event.load_pct != expected_load_pct
        for event in events
    ):
        raise PairwiseMetricError(
            "event result candidate/test/seed/load provenance disagrees with run manifest"
        )

    relations: dict[int, dict[str, tuple[dict[str, Any], aggregate.Event]]] = {}
    for event_id, row in enumerate(trace):
        relation_id = row.get("relation_id")
        relation_role = row.get("relation_role")
        if not isinstance(relation_id, int) or relation_role not in {"a", "b"}:
            raise PairwiseMetricError(f"event {event_id} has invalid pair relation")
        event = event_by_id[event_id]
        if event.logical_source != row.get("logical_source"):
            raise PairwiseMetricError(f"event {event_id} address disagrees with trace")
        members = relations.setdefault(relation_id, {})
        if relation_role in members:
            raise PairwiseMetricError(
                f"relation {relation_id} repeats role {relation_role}"
            )
        members[relation_role] = (row, event)
    if not relations or any(set(members) != {"a", "b"} for members in relations.values()):
        raise PairwiseMetricError("each relation must contain exactly A and B")

    cycle_offsets = {
        event_by_id[event_id].occurrence_cycle - int(row["occurrence_cycle"])
        for event_id, row in enumerate(trace)
    }
    if len(cycle_offsets) != 1:
        raise PairwiseMetricError("event result occurrence cycles lack one constant TB offset")

    source_count = int(run["geometry"]["width"]) * int(run["geometry"]["height"])
    canonical_pairs = list(itertools.combinations(range(source_count), 2))
    pairs_per_repeat = len(canonical_pairs)
    repeats = run.get("parameters", {}).get("pair_repeats", 1)
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise PairwiseMetricError("pair_repeats must be a positive integer")
    expected_relation_count = repeats * pairs_per_repeat
    if sorted(relations) != list(range(expected_relation_count)):
        raise PairwiseMetricError(
            "relation IDs must be contiguous and cover pair_repeats*C(N,2)"
        )
    if len(trace) != 2 * expected_relation_count:
        raise PairwiseMetricError("pairwise trace must contain two events per relation")
    permutation = metadata.get("logical_source_permutation")
    if (
        not isinstance(permutation, list)
        or len(permutation) != source_count
        or any(isinstance(value, bool) or not isinstance(value, int) for value in permutation)
        or sorted(permutation) != list(range(source_count))
    ):
        raise PairwiseMetricError(
            "run manifest must freeze a bijective logical_source_permutation"
        )
    completion_latencies: list[int] = []
    service_skews: list[int] = []
    dropped = 0
    censored = 0
    a_first = 0
    b_first = 0
    same_cycle = 0
    worst_completion_pair: dict[str, Any] | None = None
    worst_skew_pair: dict[str, Any] | None = None
    trial_rows: list[dict[str, Any]] = []
    pair_aggregates: dict[tuple[int, int], dict[str, Any]] = {}
    active_pair_ends: list[int] = []
    overlap_pairs = 0
    max_overlapping_prior_pairs = 0

    for relation_id, members in sorted(relations.items()):
        row_a, event_a = members["a"]
        row_b, event_b = members["b"]
        if row_a["occurrence_cycle"] != row_b["occurrence_cycle"]:
            raise PairwiseMetricError(
                f"relation {relation_id} is not simultaneous contention"
            )
        occurrence = min(event_a.occurrence_cycle, event_b.occurrence_cycle)
        active_pair_ends = [end for end in active_pair_ends if end >= occurrence]
        overlapping_prior_pairs = len(active_pair_ends)
        overlaps_previous = overlapping_prior_pairs != 0
        if overlaps_previous:
            overlap_pairs += 1
        max_overlapping_prior_pairs = max(
            max_overlapping_prior_pairs, overlapping_prior_pairs
        )
        canonical_pair = canonical_pairs[relation_id % pairs_per_repeat]
        repeat_index = relation_id // pairs_per_repeat
        expected_source_a = permutation[canonical_pair[0]]
        expected_source_b = permutation[canonical_pair[1]]
        if (
            event_a.logical_source != expected_source_a
            or event_b.logical_source != expected_source_b
        ):
            raise PairwiseMetricError(
                f"relation {relation_id} addresses disagree with frozen permutation"
            )
        trial: dict[str, Any] = {
            "relation_id": relation_id,
            "repeat_index": repeat_index,
            "canonical_source_a": canonical_pair[0],
            "canonical_source_b": canonical_pair[1],
            "physical_source_a": event_a.logical_source,
            "physical_source_b": event_b.logical_source,
            "overlaps_previous_pair": overlaps_previous,
            "overlapping_prior_pair_count": overlapping_prior_pairs,
            "event_state_a": event_a.event_state,
            "event_state_b": event_b.event_state,
        }
        aggregate_key = canonical_pair
        pair_aggregate = pair_aggregates.setdefault(
            aggregate_key,
            {
                "canonical_source_a": canonical_pair[0],
                "canonical_source_b": canonical_pair[1],
                "physical_source_a": expected_source_a,
                "physical_source_b": expected_source_b,
                "trial_count": 0,
                "evaluable_trials": 0,
                "dropped_trials": 0,
                "censored_trials": 0,
                "overlap_trials": 0,
                "completion_latencies": [],
                "service_skews": [],
            },
        )
        pair_aggregate["trial_count"] += 1
        pair_aggregate["overlap_trials"] += int(overlaps_previous)

        known_deliveries = [
            cycle
            for cycle in (event_a.delivery_cycle, event_b.delivery_cycle)
            if cycle is not None
        ]
        current_incomplete = any(
            event.event_state in {"pending", "accepted"}
            for event in (event_a, event_b)
        )
        current_end = max(known_deliveries) if known_deliveries else occurrence
        if current_incomplete:
            current_end = max(
                event_a.observation_end_cycle, event_b.observation_end_cycle
            )
        active_pair_ends.append(current_end)

        has_drop = "source_overrun" in {event_a.event_state, event_b.event_state}
        has_censor = current_incomplete
        if has_drop:
            dropped += 1
            pair_aggregate["dropped_trials"] += 1
        if has_censor:
            censored += 1
            pair_aggregate["censored_trials"] += 1
        if has_drop or has_censor:
            trial["result"] = (
                "dropped_and_censored"
                if has_drop and has_censor
                else "dropped" if has_drop else "censored"
            )
            trial_rows.append(trial)
            continue

        completion = max(event_a.delivery_cycle, event_b.delivery_cycle) - min(
            event_a.occurrence_cycle, event_b.occurrence_cycle
        )
        skew = abs(event_a.delivery_cycle - event_b.delivery_cycle)
        completion_latencies.append(completion)
        service_skews.append(skew)
        pair_aggregate["evaluable_trials"] += 1
        pair_aggregate["completion_latencies"].append(completion)
        pair_aggregate["service_skews"].append(skew)
        if event_a.delivery_cycle < event_b.delivery_cycle:
            a_first += 1
        elif event_b.delivery_cycle < event_a.delivery_cycle:
            b_first += 1
        else:
            same_cycle += 1

        pair = {
            "relation_id": relation_id,
            "repeat_index": repeat_index,
            "canonical_source_a": canonical_pair[0],
            "canonical_source_b": canonical_pair[1],
            "source_a": event_a.logical_source,
            "source_b": event_b.logical_source,
            "delivery_a": event_a.delivery_cycle,
            "delivery_b": event_b.delivery_cycle,
            "completion_latency_cycles": completion,
            "service_skew_cycles": skew,
        }
        trial.update(pair)
        trial["result"] = "evaluable"
        trial_rows.append(trial)
        if worst_completion_pair is None or (completion, skew, relation_id) > (
            worst_completion_pair["completion_latency_cycles"],
            worst_completion_pair["service_skew_cycles"],
            worst_completion_pair["relation_id"],
        ):
            worst_completion_pair = pair
        if worst_skew_pair is None or (skew, completion, relation_id) > (
            worst_skew_pair["service_skew_cycles"],
            worst_skew_pair["completion_latency_cycles"],
            worst_skew_pair["relation_id"],
        ):
            worst_skew_pair = pair

    aggregate_rows = []
    for aggregate_row in pair_aggregates.values():
        completions = aggregate_row.pop("completion_latencies")
        skews = aggregate_row.pop("service_skews")
        aggregate_row.update(
            {
                "mean_completion_latency_cycles": (
                    statistics.fmean(completions) if completions else None
                ),
                "max_completion_latency_cycles": max(completions) if completions else None,
                "mean_service_skew_cycles": (
                    statistics.fmean(skews) if skews else None
                ),
                "max_service_skew_cycles": max(skews) if skews else None,
            }
        )
        aggregate_rows.append(aggregate_row)

    measurement_state = (
        "NO_EVALUABLE_PAIRS"
        if not completion_latencies
        else "COMPLETE"
        if len(completion_latencies) == len(relations)
        else "PARTIAL_DROP_OR_CENSOR"
    )
    return {
        "candidate": first.candidate,
        "test": first.test,
        "seed": first.seed,
        "trace_sha256": metadata.get("trace_sha256"),
        "generator_version": metadata.get("generator_version"),
        "logical_source_permutation": permutation,
        "pair_count": len(relations),
        "evaluable_pairs": len(completion_latencies),
        "dropped_pairs": dropped,
        "censored_pairs": censored,
        "nonevaluable_pairs": len(relations) - len(completion_latencies),
        "measurement_state": measurement_state,
        "mean_pair_completion_latency_cycles": (
            statistics.fmean(completion_latencies) if completion_latencies else None
        ),
        "p95_pair_completion_latency_cycles": _percentile(
            completion_latencies, 95
        ),
        "max_pair_completion_latency_cycles": (
            max(completion_latencies) if completion_latencies else None
        ),
        "mean_pair_service_skew_cycles": (
            statistics.fmean(service_skews) if service_skews else None
        ),
        "p95_pair_service_skew_cycles": _percentile(service_skews, 95),
        "max_pair_service_skew_cycles": max(service_skews) if service_skews else None,
        "a_first_pairs": a_first,
        "b_first_pairs": b_first,
        "same_cycle_pairs": same_cycle,
        "overlap_pairs": overlap_pairs,
        "max_overlapping_prior_pairs": max_overlapping_prior_pairs,
        "isolation_state": "OVERLAP_OBSERVED" if overlap_pairs else "QUIESCENT",
        "worst_completion_pair": worst_completion_pair,
        "worst_skew_pair": worst_skew_pair,
        "pair_aggregates": aggregate_rows,
        "trials": trial_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.trace, args.run_manifest, args.events)
    except (PairwiseMetricError, aggregate.InputError) as exc:
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
