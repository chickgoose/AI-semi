#!/usr/bin/env python3
"""Aggregate architecture-neutral clean-slate AER benchmark CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TextIO


REQUIRED_COLUMNS = (
    "test",
    "seed",
    "load_pct",
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "total_cycles",
    "avg_e2e_latency",
    "max_e2e_latency",
    "avg_internal_latency",
    "max_internal_latency",
    "throughput",
    "fairness",
    "max_request_wait",
    "avg_timing_error",
    "max_timing_error",
)

INTEGER_COLUMNS = (
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "total_cycles",
)

FLOAT_COLUMNS = tuple(
    column
    for column in REQUIRED_COLUMNS
    if column not in {"test", "seed", *INTEGER_COLUMNS}
)

SUMMARY_COLUMNS = (
    "candidate",
    "test",
    "load_pct",
    "runs",
    "seed_count",
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "delivery_ratio",
    "acceptance_ratio",
    "overrun_ratio",
    "end_to_end_ratio",
    "avg_total_cycles",
    "avg_throughput",
    "worst_throughput",
    "avg_e2e_latency",
    "worst_e2e_latency",
    "avg_internal_latency",
    "worst_internal_latency",
    "avg_request_wait",
    "worst_request_wait",
    "avg_timing_error",
    "worst_timing_error",
    "avg_fairness",
    "worst_fairness",
    "performance_state",
    "knee_load_pct",
    "tail_latency_factor",
    "tail_wait_factor",
    "tail_timing_error_factor",
    "tail_degraded",
    "correctness_issues",
    "event_metrics_state",
    "event_runs",
    "event_seed_count",
    "event_rows",
    "delivered_event_rows",
    "undelivered_event_rows",
    "censored_event_rows",
    "p50_e2e_latency_cycles",
    "p95_e2e_latency_cycles",
    "p99_e2e_latency_cycles",
    "p50_internal_latency_cycles",
    "p95_internal_latency_cycles",
    "p99_internal_latency_cycles",
    "deadline_events",
    "deadline_misses",
    "deadline_censored",
    "deadline_miss_ratio",
    "service_sources_expected",
    "service_sources_delivered",
    "service_sources_unobserved",
    "service_gap_samples",
    "p95_service_gap_cycles",
    "p99_service_gap_cycles",
    "max_service_gap_cycles",
    "service_window_cycles",
    "service_source_windows",
    "min_service_per_source_window",
    "zero_service_source_windows",
    "zero_service_source_window_ratio",
)

EVENT_REQUIRED_COLUMNS = (
    "test",
    "seed",
    "load_pct",
    "tb_only_event_id",
    "logical_source",
    "source_count",
    "occurrence_cycle",
    "accept_cycle",
    "delivery_cycle",
    "deadline_cycle",
    "observation_end_cycle",
    "event_state",
)

EVENT_STATES = ("source_overrun", "pending", "accepted", "delivered")

EVENT_RUN_COLUMNS = (
    "candidate",
    "test",
    "seed",
    "load_pct",
    "event_rows",
    "delivered_event_rows",
    "undelivered_event_rows",
    "censored_event_rows",
    "p50_e2e_latency_cycles",
    "p95_e2e_latency_cycles",
    "p99_e2e_latency_cycles",
    "p50_internal_latency_cycles",
    "p95_internal_latency_cycles",
    "p99_internal_latency_cycles",
    "deadline_events",
    "deadline_misses",
    "deadline_censored",
    "deadline_miss_ratio",
    "service_sources_expected",
    "service_sources_delivered",
    "service_sources_unobserved",
    "service_gap_samples",
    "p95_service_gap_cycles",
    "p99_service_gap_cycles",
    "max_service_gap_cycles",
    "service_window_cycles",
    "service_source_windows",
    "min_service_per_source_window",
    "zero_service_source_windows",
    "zero_service_source_window_ratio",
)


class InputError(ValueError):
    """Raised for malformed benchmark input."""


@dataclass(frozen=True)
class Run:
    candidate: str
    test: str
    seed: str
    load_pct: float
    stim_cycles: int
    generated: int
    source_overrun: int
    accepted: int
    delivered: int
    errors: int
    total_cycles: int
    avg_e2e_latency: float
    max_e2e_latency: float
    avg_internal_latency: float
    max_internal_latency: float
    throughput: float
    fairness: float
    max_request_wait: float
    avg_timing_error: float
    max_timing_error: float


@dataclass(frozen=True)
class Event:
    candidate: str
    test: str
    seed: str
    load_pct: float
    tb_only_event_id: int
    logical_source: int
    source_count: int
    occurrence_cycle: int
    accept_cycle: int | None
    delivery_cycle: int | None
    deadline_cycle: int | None
    observation_end_cycle: int
    event_state: str

    @property
    def run_key(self) -> tuple[str, str, str, float]:
        return (self.candidate, self.test, self.seed, self.load_pct)


def _parse_nonnegative_int(value: str, column: str, location: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be an integer") from exc
    if parsed < 0:
        raise InputError(f"{location}: {column} must be nonnegative")
    return parsed


def _parse_finite_float(value: str, column: str, location: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be numeric") from exc
    if not math.isfinite(parsed):
        raise InputError(f"{location}: {column} must be finite")
    if parsed < 0:
        raise InputError(f"{location}: {column} must be nonnegative")
    return parsed


def _parse_optional_nonnegative_int(
    value: str | None, column: str, location: str
) -> int | None:
    if value is None or not value.strip():
        return None
    return _parse_nonnegative_int(value, column, location)


def read_runs(paths: Iterable[Path]) -> list[Run]:
    runs: list[Run] = []
    for path in paths:
        try:
            stream = path.open(newline="", encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot read {path}: {exc}") from exc
        with stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            missing = [column for column in REQUIRED_COLUMNS if column not in columns]
            if missing:
                raise InputError(f"{path}: missing columns: {', '.join(missing)}")
            for line_number, row in enumerate(reader, start=2):
                location = f"{path}:{line_number}"
                test = (row.get("test") or "").strip()
                seed = (row.get("seed") or "").strip()
                candidate = (row.get("candidate") or "unspecified").strip()
                if not candidate:
                    raise InputError(f"{location}: candidate must not be empty")
                if not test:
                    raise InputError(f"{location}: test must not be empty")
                if not seed:
                    raise InputError(f"{location}: seed must not be empty")
                values: dict[str, object] = {
                    "candidate": candidate,
                    "test": test,
                    "seed": seed,
                }
                for column in INTEGER_COLUMNS:
                    values[column] = _parse_nonnegative_int(row[column], column, location)
                for column in FLOAT_COLUMNS:
                    values[column] = _parse_finite_float(row[column], column, location)
                runs.append(Run(**values))
    if not runs:
        raise InputError("no benchmark rows found")
    return runs


def _validate_event(event: Event, location: str) -> None:
    if event.source_count <= 0:
        raise InputError(f"{location}: source_count must be positive")
    if event.logical_source >= event.source_count:
        raise InputError(f"{location}: logical_source must be less than source_count")
    if event.observation_end_cycle < event.occurrence_cycle:
        raise InputError(f"{location}: observation ends before occurrence")
    if event.deadline_cycle is not None and event.deadline_cycle < event.occurrence_cycle:
        raise InputError(f"{location}: deadline precedes occurrence")
    if event.accept_cycle is not None:
        if event.accept_cycle < event.occurrence_cycle:
            raise InputError(f"{location}: accept precedes occurrence")
        if event.accept_cycle > event.observation_end_cycle:
            raise InputError(f"{location}: accept exceeds observation end")
    if event.delivery_cycle is not None:
        if event.accept_cycle is None:
            raise InputError(f"{location}: delivery requires accept_cycle")
        if event.delivery_cycle < event.accept_cycle:
            raise InputError(f"{location}: delivery precedes accept")
        if event.delivery_cycle > event.observation_end_cycle:
            raise InputError(f"{location}: delivery exceeds observation end")

    state_cycles = {
        "source_overrun": (False, False),
        "pending": (False, False),
        "accepted": (True, False),
        "delivered": (True, True),
    }
    expected_accept, expected_delivery = state_cycles[event.event_state]
    if (event.accept_cycle is not None) != expected_accept:
        raise InputError(f"{location}: event_state disagrees with accept_cycle")
    if (event.delivery_cycle is not None) != expected_delivery:
        raise InputError(f"{location}: event_state disagrees with delivery_cycle")


def read_events(paths: Iterable[Path]) -> list[Event]:
    events: list[Event] = []
    identities: set[tuple[str, str, str, float, int]] = set()
    run_contracts: dict[tuple[str, str, str, float], tuple[int, int]] = {}
    for path in paths:
        try:
            stream = path.open(newline="", encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot read {path}: {exc}") from exc
        with stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            missing = [column for column in EVENT_REQUIRED_COLUMNS if column not in columns]
            if missing:
                raise InputError(f"{path}: missing event columns: {', '.join(missing)}")
            for line_number, row in enumerate(reader, start=2):
                location = f"{path}:{line_number}"
                test = (row.get("test") or "").strip()
                seed = (row.get("seed") or "").strip()
                candidate = (row.get("candidate") or "unspecified").strip()
                state = (row.get("event_state") or "").strip()
                if not candidate:
                    raise InputError(f"{location}: candidate must not be empty")
                if not test:
                    raise InputError(f"{location}: test must not be empty")
                if not seed:
                    raise InputError(f"{location}: seed must not be empty")
                if state not in EVENT_STATES:
                    raise InputError(
                        f"{location}: event_state must be one of: {', '.join(EVENT_STATES)}"
                    )
                event = Event(
                    candidate=candidate,
                    test=test,
                    seed=seed,
                    load_pct=_parse_finite_float(row["load_pct"], "load_pct", location),
                    tb_only_event_id=_parse_nonnegative_int(
                        row["tb_only_event_id"], "tb_only_event_id", location
                    ),
                    logical_source=_parse_nonnegative_int(
                        row["logical_source"], "logical_source", location
                    ),
                    source_count=_parse_nonnegative_int(
                        row["source_count"], "source_count", location
                    ),
                    occurrence_cycle=_parse_nonnegative_int(
                        row["occurrence_cycle"], "occurrence_cycle", location
                    ),
                    accept_cycle=_parse_optional_nonnegative_int(
                        row.get("accept_cycle"), "accept_cycle", location
                    ),
                    delivery_cycle=_parse_optional_nonnegative_int(
                        row.get("delivery_cycle"), "delivery_cycle", location
                    ),
                    deadline_cycle=_parse_optional_nonnegative_int(
                        row.get("deadline_cycle"), "deadline_cycle", location
                    ),
                    observation_end_cycle=_parse_nonnegative_int(
                        row["observation_end_cycle"], "observation_end_cycle", location
                    ),
                    event_state=state,
                )
                _validate_event(event, location)
                identity = (*event.run_key, event.tb_only_event_id)
                if identity in identities:
                    raise InputError(f"{location}: duplicate tb_only_event_id in run")
                identities.add(identity)
                contract = (event.source_count, event.observation_end_cycle)
                previous_contract = run_contracts.setdefault(event.run_key, contract)
                if contract != previous_contract:
                    raise InputError(
                        f"{location}: source_count and observation_end_cycle must be constant per run"
                    )
                events.append(event)
    if not events:
        raise InputError("no per-event rows found")
    return events


def _safe_ratio(numerator: float, denominator: float, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def _weighted_mean(rows: Sequence[Run], attribute: str) -> float:
    weight = sum(row.delivered for row in rows)
    if weight:
        return sum(getattr(row, attribute) * row.delivered for row in rows) / weight
    return statistics.fmean(getattr(row, attribute) for row in rows)


def _correctness_issues(rows: Sequence[Run]) -> list[str]:
    issues: set[str] = set()
    if any(row.errors for row in rows):
        issues.add("scoreboard_errors")
    if any(row.source_overrun > row.generated for row in rows):
        issues.add("overrun_exceeds_generated")
    if any(row.accepted > row.generated - row.source_overrun for row in rows):
        issues.add("accepted_exceeds_retained_arrivals")
    if any(row.delivered > row.accepted for row in rows):
        issues.add("delivered_exceeds_accepted")
    if any(row.delivered < row.accepted for row in rows):
        issues.add("accepted_delivery_mismatch")
    return sorted(issues)


def _aggregate_group(
    candidate: str, test: str, load_pct: float, rows: Sequence[Run]
) -> dict[str, object]:
    generated = sum(row.generated for row in rows)
    overrun = sum(row.source_overrun for row in rows)
    accepted = sum(row.accepted for row in rows)
    delivered = sum(row.delivered for row in rows)
    retained = generated - overrun
    issues = _correctness_issues(rows)
    return {
        "candidate": candidate,
        "test": test,
        "load_pct": load_pct,
        "runs": len(rows),
        "seed_count": len({row.seed for row in rows}),
        "stim_cycles": sum(row.stim_cycles for row in rows),
        "generated": generated,
        "source_overrun": overrun,
        "accepted": accepted,
        "delivered": delivered,
        "errors": sum(row.errors for row in rows),
        # Delivery isolates post-accept transport correctness. Acceptance uses
        # only arrivals retained by the source queue. Overrun is reported
        # separately so saturation is not mislabeled as functional failure.
        "delivery_ratio": _safe_ratio(delivered, accepted),
        "acceptance_ratio": _safe_ratio(accepted, retained),
        "overrun_ratio": _safe_ratio(overrun, generated, empty=0.0),
        "end_to_end_ratio": _safe_ratio(delivered, generated),
        "avg_total_cycles": statistics.fmean(row.total_cycles for row in rows),
        "avg_throughput": statistics.fmean(row.throughput for row in rows),
        "worst_throughput": min(row.throughput for row in rows),
        "avg_e2e_latency": _weighted_mean(rows, "avg_e2e_latency"),
        "worst_e2e_latency": max(row.max_e2e_latency for row in rows),
        "avg_internal_latency": _weighted_mean(rows, "avg_internal_latency"),
        "worst_internal_latency": max(row.max_internal_latency for row in rows),
        "avg_request_wait": statistics.fmean(row.max_request_wait for row in rows),
        "worst_request_wait": max(row.max_request_wait for row in rows),
        "avg_timing_error": _weighted_mean(rows, "avg_timing_error"),
        "worst_timing_error": max(row.max_timing_error for row in rows),
        "avg_fairness": statistics.fmean(row.fairness for row in rows),
        "worst_fairness": min(row.fairness for row in rows),
        "performance_state": "CORRECTNESS_FAIL" if issues else "PASS",
        "knee_load_pct": None,
        "tail_latency_factor": None,
        "tail_wait_factor": None,
        "tail_timing_error_factor": None,
        "tail_degraded": False,
        "correctness_issues": ";".join(issues),
    }


def _nearest_rank(values: Sequence[int], percentile: int) -> int | None:
    """Return the nearest-rank percentile: sorted[ceil(p*N/100)-1]."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[max(1, rank) - 1]


def _source_window_stats(
    delivery_cycles: Sequence[int], *, window_cycles: int, observation_end_cycle: int
) -> tuple[int, int | None, int]:
    """Return full-window count, minimum service, and zero-service windows."""
    observation_cycles = observation_end_cycle + 1
    if observation_cycles < window_cycles:
        return 0, None, 0
    window_starts = observation_cycles - window_cycles + 1
    deltas: dict[int, int] = {}
    for delivery_cycle in delivery_cycles:
        first_start = max(0, delivery_cycle - window_cycles + 1)
        last_start = min(delivery_cycle, window_starts - 1)
        if first_start <= last_start:
            deltas[first_start] = deltas.get(first_start, 0) + 1
            after = last_start + 1
            deltas[after] = deltas.get(after, 0) - 1

    minimum: int | None = None
    zero_windows = 0
    current = 0
    previous = 0
    for position in sorted(deltas):
        if position > window_starts:
            break
        segment_length = position - previous
        if segment_length:
            minimum = current if minimum is None else min(minimum, current)
            if current == 0:
                zero_windows += segment_length
        current += deltas[position]
        previous = position
    if previous < window_starts:
        segment_length = window_starts - previous
        minimum = current if minimum is None else min(minimum, current)
        if current == 0:
            zero_windows += segment_length
    return window_starts, minimum, zero_windows


def _validate_event_summary_contract(runs: Sequence[Run], events: Sequence[Event]) -> None:
    runs_by_key: dict[tuple[str, str, str, float], Run] = {}
    for run in runs:
        key = (run.candidate, run.test, run.seed, run.load_pct)
        if key in runs_by_key:
            raise InputError(
                "per-event input requires unique summary rows by "
                "(candidate, test, seed, load_pct)"
            )
        runs_by_key[key] = run

    events_by_key: dict[tuple[str, str, str, float], list[Event]] = {}
    for event in events:
        if event.run_key not in runs_by_key:
            raise InputError(
                "per-event run has no matching summary row: "
                f"candidate={event.candidate} test={event.test} "
                f"seed={event.seed} load_pct={event.load_pct}"
            )
        events_by_key.setdefault(event.run_key, []).append(event)

    for key, event_rows in events_by_key.items():
        run = runs_by_key[key]
        state_counts: dict[str, int] = {state: 0 for state in EVENT_STATES}
        for event in event_rows:
            state_counts[event.event_state] += 1
        accepted = state_counts["accepted"] + state_counts["delivered"]
        if len(event_rows) != run.generated:
            raise InputError(f"per-event row count does not match generated for run {key}")
        if state_counts["source_overrun"] != run.source_overrun:
            raise InputError(f"per-event source_overrun count does not match summary for run {key}")
        if accepted != run.accepted:
            raise InputError(f"per-event accepted count does not match summary for run {key}")
        if state_counts["delivered"] != run.delivered:
            raise InputError(f"per-event delivered count does not match summary for run {key}")


def _event_metrics(
    summary_rows: Sequence[Run],
    event_rows: Sequence[Event],
    *,
    service_window_cycles: int,
) -> dict[str, object]:
    expected_run_keys = {
        (row.candidate, row.test, row.seed, row.load_pct) for row in summary_rows
    }
    observed_run_keys = {event.run_key for event in event_rows}
    if not event_rows:
        return {
            "event_metrics_state": "NOT_PROVIDED",
            "event_runs": 0,
            "event_seed_count": 0,
        }

    delivered = [event for event in event_rows if event.event_state == "delivered"]
    e2e_latencies = [
        event.delivery_cycle - event.occurrence_cycle
        for event in delivered
        if event.delivery_cycle is not None
    ]
    internal_latencies = [
        event.delivery_cycle - event.accept_cycle
        for event in delivered
        if event.delivery_cycle is not None and event.accept_cycle is not None
    ]
    undelivered = [event for event in event_rows if event.event_state != "delivered"]
    censored = [event for event in event_rows if event.event_state in {"pending", "accepted"}]

    deadline_events = 0
    deadline_misses = 0
    deadline_censored = 0
    for event in event_rows:
        if event.deadline_cycle is None:
            continue
        deadline_events += 1
        if event.event_state == "source_overrun":
            deadline_misses += 1
        elif event.delivery_cycle is not None:
            if event.delivery_cycle > event.deadline_cycle:
                deadline_misses += 1
        elif event.observation_end_cycle >= event.deadline_cycle:
            deadline_misses += 1
        else:
            deadline_censored += 1
    deadline_evaluable = deadline_events - deadline_censored

    events_by_run: dict[tuple[str, str, str, float], list[Event]] = {}
    for event in event_rows:
        events_by_run.setdefault(event.run_key, []).append(event)
    service_gaps: list[int] = []
    expected_sources = 0
    delivered_sources = 0
    source_windows = 0
    zero_source_windows = 0
    minimum_window_service: int | None = None
    for run_events in events_by_run.values():
        source_count = run_events[0].source_count
        observation_end = run_events[0].observation_end_cycle
        delivery_by_source: dict[int, list[int]] = {
            source: [] for source in range(source_count)
        }
        for event in run_events:
            if event.delivery_cycle is not None:
                delivery_by_source[event.logical_source].append(event.delivery_cycle)
        expected_sources += source_count
        for cycles in delivery_by_source.values():
            cycles.sort()
            if cycles:
                delivered_sources += 1
            service_gaps.extend(right - left for left, right in zip(cycles, cycles[1:]))
            windows, source_minimum, source_zero = _source_window_stats(
                cycles,
                window_cycles=service_window_cycles,
                observation_end_cycle=observation_end,
            )
            source_windows += windows
            zero_source_windows += source_zero
            if source_minimum is not None:
                minimum_window_service = (
                    source_minimum
                    if minimum_window_service is None
                    else min(minimum_window_service, source_minimum)
                )

    return {
        "event_metrics_state": (
            "COMPLETE" if observed_run_keys == expected_run_keys else "PARTIAL"
        ),
        "event_runs": len(observed_run_keys),
        "event_seed_count": len({event.seed for event in event_rows}),
        "event_rows": len(event_rows),
        "delivered_event_rows": len(delivered),
        "undelivered_event_rows": len(undelivered),
        "censored_event_rows": len(censored),
        "p50_e2e_latency_cycles": _nearest_rank(e2e_latencies, 50),
        "p95_e2e_latency_cycles": _nearest_rank(e2e_latencies, 95),
        "p99_e2e_latency_cycles": _nearest_rank(e2e_latencies, 99),
        "p50_internal_latency_cycles": _nearest_rank(internal_latencies, 50),
        "p95_internal_latency_cycles": _nearest_rank(internal_latencies, 95),
        "p99_internal_latency_cycles": _nearest_rank(internal_latencies, 99),
        "deadline_events": deadline_events,
        "deadline_misses": deadline_misses,
        "deadline_censored": deadline_censored,
        "deadline_miss_ratio": (
            deadline_misses / deadline_evaluable if deadline_evaluable else None
        ),
        "service_sources_expected": expected_sources,
        "service_sources_delivered": delivered_sources,
        "service_sources_unobserved": expected_sources - delivered_sources,
        "service_gap_samples": len(service_gaps),
        "p95_service_gap_cycles": _nearest_rank(service_gaps, 95),
        "p99_service_gap_cycles": _nearest_rank(service_gaps, 99),
        "max_service_gap_cycles": max(service_gaps) if service_gaps else None,
        "service_window_cycles": service_window_cycles,
        "service_source_windows": source_windows,
        "min_service_per_source_window": minimum_window_service,
        "zero_service_source_windows": zero_source_windows,
        "zero_service_source_window_ratio": (
            zero_source_windows / source_windows if source_windows else None
        ),
    }


def _factor(value: float, reference: float) -> float | None:
    if reference > 0:
        return value / reference
    return None


def aggregate_runs(
    runs: Sequence[Run],
    *,
    events: Sequence[Event] | None = None,
    service_window_cycles: int = 64,
    acceptance_floor: float = 0.99,
    overrun_ceiling: float = 0.01,
    tail_factor: float = 1.5,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if service_window_cycles <= 0:
        raise InputError("service_window_cycles must be positive")
    if events is not None:
        _validate_event_summary_contract(runs, events)
    grouped: dict[tuple[str, str, float], list[Run]] = {}
    for run in runs:
        grouped.setdefault((run.candidate, run.test, run.load_pct), []).append(run)
    summaries = [
        _aggregate_group(candidate, test, load_pct, rows)
        for (candidate, test, load_pct), rows in sorted(grouped.items())
    ]
    event_groups: dict[tuple[str, str, float], list[Event]] = {}
    if events is not None:
        for event in events:
            event_groups.setdefault(
                (event.candidate, event.test, event.load_pct), []
            ).append(event)
    for summary in summaries:
        group_key = (
            str(summary["candidate"]),
            str(summary["test"]),
            float(summary["load_pct"]),
        )
        summary.update(
            _event_metrics(
                grouped[group_key],
                event_groups.get(group_key, ()),
                service_window_cycles=service_window_cycles,
            )
        )

    test_reports: list[dict[str, object]] = []
    tests = sorted(
        {(str(summary["candidate"]), str(summary["test"])) for summary in summaries}
    )
    for candidate, test in tests:
        sweep = [
            summary
            for summary in summaries
            if summary["candidate"] == candidate and summary["test"] == test
        ]
        knee_index: int | None = None
        for index, summary in enumerate(sweep):
            if summary["performance_state"] == "CORRECTNESS_FAIL":
                continue
            if (
                float(summary["acceptance_ratio"]) < acceptance_floor
                or float(summary["overrun_ratio"]) > overrun_ceiling
            ):
                knee_index = index
                break

        knee_load = sweep[knee_index]["load_pct"] if knee_index is not None else None
        reference = None
        if knee_index is not None:
            reference = next(
                (
                    item
                    for item in reversed(sweep[:knee_index])
                    if item["performance_state"] != "CORRECTNESS_FAIL"
                ),
                None,
            )
        for index, summary in enumerate(sweep):
            summary["knee_load_pct"] = knee_load
            if summary["performance_state"] == "CORRECTNESS_FAIL":
                continue
            if knee_index is not None and index >= knee_index:
                summary["performance_state"] = "SATURATED"
                if reference is not None:
                    summary["tail_latency_factor"] = _factor(
                        float(summary["avg_e2e_latency"]),
                        float(reference["avg_e2e_latency"]),
                    )
                    summary["tail_wait_factor"] = _factor(
                        float(summary["avg_request_wait"]),
                        float(reference["avg_request_wait"]),
                    )
                    summary["tail_timing_error_factor"] = _factor(
                        float(summary["avg_timing_error"]),
                        float(reference["avg_timing_error"]),
                    )
                    factors = (
                        summary["tail_latency_factor"],
                        summary["tail_wait_factor"],
                        summary["tail_timing_error_factor"],
                    )
                    summary["tail_degraded"] = any(
                        factor is not None and float(factor) >= tail_factor
                        for factor in factors
                    )

        test_reports.append(
            {
                "candidate": candidate,
                "test": test,
                "knee_load_pct": knee_load,
                "correctness": (
                    "FAIL"
                    if any(item["performance_state"] == "CORRECTNESS_FAIL" for item in sweep)
                    else "PASS"
                ),
                "tail_degraded": any(bool(item["tail_degraded"]) for item in sweep),
                "loads": len(sweep),
                "runs": sum(int(item["runs"]) for item in sweep),
                "seeds": max(int(item["seed_count"]) for item in sweep),
            }
        )
    return summaries, test_reports


def summarize_event_runs(
    runs: Sequence[Run],
    events: Sequence[Event],
    *,
    service_window_cycles: int = 64,
) -> list[dict[str, object]]:
    """Calculate exact (test, seed, load_pct) metrics for supplied event runs."""
    if service_window_cycles <= 0:
        raise InputError("service_window_cycles must be positive")
    _validate_event_summary_contract(runs, events)
    runs_by_key = {
        (run.candidate, run.test, run.seed, run.load_pct): run for run in runs
    }
    events_by_key: dict[tuple[str, str, str, float], list[Event]] = {}
    for event in events:
        events_by_key.setdefault(event.run_key, []).append(event)
    summaries: list[dict[str, object]] = []
    for key in sorted(events_by_key, key=lambda item: (item[0], item[1], item[3], item[2])):
        candidate, test, seed, load_pct = key
        metrics = _event_metrics(
            [runs_by_key[key]],
            events_by_key[key],
            service_window_cycles=service_window_cycles,
        )
        summaries.append(
            {
                "candidate": candidate,
                "test": test,
                "seed": seed,
                "load_pct": load_pct,
                **metrics,
            }
        )
    return summaries


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.9g}"
    return value


def write_csv(summaries: Sequence[dict[str, object]], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for summary in summaries:
        writer.writerow({key: _csv_value(summary.get(key)) for key in SUMMARY_COLUMNS})


def write_event_run_csv(summaries: Sequence[dict[str, object]], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=EVENT_RUN_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for summary in summaries:
        writer.writerow({key: _csv_value(summary.get(key)) for key in EVENT_RUN_COLUMNS})


def write_json(
    summaries: Sequence[dict[str, object]],
    tests: Sequence[dict[str, object]],
    stream: TextIO,
    *,
    acceptance_floor: float,
    overrun_ceiling: float,
    tail_factor: float,
    service_window_cycles: int = 64,
    event_runs: Sequence[dict[str, object]] = (),
) -> None:
    payload = {
        "policy": {
            "acceptance_floor": acceptance_floor,
            "overrun_ceiling": overrun_ceiling,
            "tail_factor": tail_factor,
            "service_window_cycles": service_window_cycles,
        },
        "tests": list(tests),
        "loads": list(summaries),
        "event_runs": list(event_runs),
    }
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")


def _unit_interval(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="input benchmark CSV files")
    parser.add_argument(
        "--events",
        action="append",
        type=Path,
        default=[],
        metavar="EVENT_CSV",
        help="optional per-event CSV; repeat for multiple files",
    )
    parser.add_argument("-o", "--output", type=Path, help="output path (default: stdout)")
    parser.add_argument(
        "--event-output",
        type=Path,
        help="optional exact per-(candidate,test,seed,load) event-metric CSV output",
    )
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--acceptance-floor", type=_unit_interval, default=0.99)
    parser.add_argument("--overrun-ceiling", type=_unit_interval, default=0.01)
    parser.add_argument("--tail-factor", type=_positive_float, default=1.5)
    parser.add_argument(
        "--service-window-cycles",
        type=_positive_int,
        default=64,
        help="full sliding-window width in cycles (default: 64)",
    )
    parser.add_argument(
        "--fail-on-correctness",
        action="store_true",
        help="return status 2 if any grouped load has correctness errors",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        runs = read_runs(args.inputs)
        events = read_events(args.events) if args.events else None
        if args.event_output and events is None:
            raise InputError("--event-output requires at least one --events file")
        summaries, tests = aggregate_runs(
            runs,
            events=events,
            service_window_cycles=args.service_window_cycles,
            acceptance_floor=args.acceptance_floor,
            overrun_ceiling=args.overrun_ceiling,
            tail_factor=args.tail_factor,
        )
        event_run_summaries = (
            summarize_event_runs(
                runs,
                events,
                service_window_cycles=args.service_window_cycles,
            )
            if events is not None
            else []
        )
    except InputError as exc:
        parser.error(str(exc))

    if args.output:
        stream = args.output.open("w", newline="", encoding="utf-8")
    else:
        stream = sys.stdout
    try:
        if args.format == "json":
            write_json(
                summaries,
                tests,
                stream,
                acceptance_floor=args.acceptance_floor,
                overrun_ceiling=args.overrun_ceiling,
                tail_factor=args.tail_factor,
                service_window_cycles=args.service_window_cycles,
                event_runs=event_run_summaries,
            )
        else:
            write_csv(summaries, stream)
    finally:
        if args.output:
            stream.close()

    if args.event_output:
        try:
            with args.event_output.open("w", newline="", encoding="utf-8") as event_stream:
                write_event_run_csv(event_run_summaries, event_stream)
        except OSError as exc:
            parser.error(f"cannot write {args.event_output}: {exc}")

    if args.fail_on_correctness and any(test["correctness"] == "FAIL" for test in tests):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
