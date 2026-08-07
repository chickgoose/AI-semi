#!/usr/bin/env python3
"""Measure sparse/saturation/overload/recovery phases from exact trace results."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import aggregate


PHASE_NAMES = ("sparse", "near_saturation", "overload", "post_sparse", "drain")


class PhaseMetricError(ValueError):
    """Raised when trace and event-result provenance do not match."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseMetricError(f"cannot read {path}: {exc}") from exc


def _read_trace(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseMetricError(f"cannot read trace {path}: {exc}") from exc
    for expected_id, row in enumerate(rows):
        if row.get("tb_only_event_id") != expected_id:
            raise PhaseMetricError("trace event IDs must be contiguous")
    return rows


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def analyze(trace_path: Path, manifest_path: Path, event_path: Path) -> dict[str, Any]:
    metadata = _read_json(manifest_path)
    run = metadata.get("run", {})
    if run.get("workload") != "phase_transition":
        raise PhaseMetricError("run manifest must describe phase_transition")
    stim_cycles = run.get("stim_cycles")
    if not isinstance(stim_cycles, int) or stim_cycles <= 0 or stim_cycles % 8:
        raise PhaseMetricError("phase_transition stim_cycles must be positive and divisible by eight")
    trace = _read_trace(trace_path)
    if metadata.get("trace_file") != trace_path.name:
        raise PhaseMetricError("trace filename does not match run manifest")
    if hashlib.sha256(trace_path.read_bytes()).hexdigest() != metadata.get("trace_sha256"):
        raise PhaseMetricError("trace SHA256 does not match run manifest")
    events = aggregate.read_events([event_path])
    if len(events) != len(trace):
        raise PhaseMetricError("event result count does not match trace")
    event_by_id = {event.tb_only_event_id: event for event in events}
    if set(event_by_id) != set(range(len(trace))):
        raise PhaseMetricError("event result IDs do not match trace")
    offsets = {
        event_by_id[event_id].occurrence_cycle - int(row["occurrence_cycle"])
        for event_id, row in enumerate(trace)
    }
    if len(offsets) != 1:
        raise PhaseMetricError("TB occurrence offset is not constant")
    offset = offsets.pop()
    eighth = stim_cycles // 8
    phase_ranges = ((0, 2 * eighth), (2 * eighth, 4 * eighth),
                    (4 * eighth, 6 * eighth), (6 * eighth, 7 * eighth),
                    (7 * eighth, 8 * eighth))

    backlog_delta = [0] * (max(stim_cycles, max(
        (event.observation_end_cycle - offset + 1 for event in events),
        default=stim_cycles,
    )) + 1)
    phase_rows: list[dict[str, Any]] = []
    grouped: list[list[aggregate.Event]] = [[] for _ in PHASE_NAMES]
    for event_id, row in enumerate(trace):
        event = event_by_id[event_id]
        occurrence = int(row["occurrence_cycle"])
        phase = next(
            index for index, (start, end) in enumerate(phase_ranges)
            if start <= occurrence < end
        )
        grouped[phase].append(event)
        if event.event_state != "source_overrun":
            occurrence = int(row["occurrence_cycle"])
            backlog_delta[occurrence] += 1
            if event.delivery_cycle is not None:
                delivery = event.delivery_cycle - offset
                if delivery >= len(backlog_delta):
                    backlog_delta.extend([0] * (delivery - len(backlog_delta) + 1))
                backlog_delta[delivery] -= 1

    backlog = 0
    backlog_by_cycle: list[int] = []
    for delta in backlog_delta:
        backlog += delta
        backlog_by_cycle.append(backlog)

    overrun_cycles = sorted(
        int(trace[event.tb_only_event_id]["occurrence_cycle"])
        for event in events
        if event.event_state == "source_overrun"
    )

    for phase_index, name in enumerate(PHASE_NAMES):
        start, end = phase_ranges[phase_index]
        phase_cycles = end - start
        phase_events = grouped[phase_index]
        delivered = [event for event in phase_events if event.delivery_cycle is not None]
        latencies = [
            event.delivery_cycle - event.occurrence_cycle
            for event in delivered
            if event.delivery_cycle is not None
        ]
        delivery_in_window = sum(
            event.delivery_cycle is not None
            and start <= event.delivery_cycle - offset < end
            for event in events
        )
        cumulative_overrun = bisect.bisect_left(overrun_cycles, end)
        phase_rows.append({
            "phase": name,
            "start_cycle": start,
            "end_cycle_exclusive": end,
            "generated": len(phase_events),
            "source_overrun": sum(event.event_state == "source_overrun" for event in phase_events),
            "accepted": sum(event.accept_cycle is not None for event in phase_events),
            "delivered_by_occurrence_phase": len(delivered),
            "delivered_in_phase_window": delivery_in_window,
            "completion_per_phase_cycle": delivery_in_window / phase_cycles,
            "p95_e2e_latency_cycles": _p95(latencies),
            "backlog_peak": max(backlog_by_cycle[start:end], default=0),
            "backlog_at_end": backlog_by_cycle[end - 1],
            "cumulative_overrun_at_end": cumulative_overrun,
            "loss_adjusted_pressure_peak": max(
                (
                    backlog_by_cycle[cycle]
                    + bisect.bisect_right(overrun_cycles, cycle)
                    for cycle in range(start, end)
                ),
                default=0,
            ),
        })

    recovery_start = phase_ranges[-1][0]
    recovery_deliveries = [
        event.delivery_cycle - offset
        for event in events
        if event.event_state != "source_overrun" and event.delivery_cycle is not None
    ]
    undelivered_retained = any(
        event.event_state in {"pending", "accepted"} for event in events
    )
    recovery_to_zero = None
    if not undelivered_retained:
        recovery_to_zero = max(0, max(recovery_deliveries, default=recovery_start) - recovery_start)

    first = events[0] if events else None
    return {
        "candidate": first.candidate if first else "unspecified",
        "test": first.test if first else metadata.get("report_group", run.get("name")),
        "seed": first.seed if first else str(run.get("seed", "")),
        "trace_sha256": metadata.get("trace_sha256"),
        "tb_cycle_offset": offset,
        "recovery_to_zero_cycles": recovery_to_zero,
        "recovery_censored": undelivered_retained,
        "recovery_lossless": not overrun_cycles,
        "phases": phase_rows,
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
    except (PhaseMetricError, aggregate.InputError) as exc:
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
