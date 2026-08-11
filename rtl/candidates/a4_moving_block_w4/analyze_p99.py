#!/usr/bin/env python3
"""Separate admission-survivor and scheduling causes of the W3 p99 delta."""

from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass

from model import MovingBlockTreeModel


@dataclass
class DetailedRun:
    offered: set[int]
    accepted: set[int]
    dropped: set[int]
    latency: dict[int, int]


def percentile(values: list[int], pct: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * pct / 100) - 1)]


def load_events(path: pathlib.Path) -> list[dict[str, int]]:
    events = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            events.append(
                {
                    "id": int(item["tb_only_event_id"]),
                    "cycle": int(item["occurrence_cycle"]),
                    "source": int(item["logical_source"]),
                }
            )
    return events


def detailed_replay(events: list[dict[str, int]], max_advance: int) -> DetailedRun:
    model = MovingBlockTreeModel(16, max_advance)
    by_cycle: dict[int, list[dict[str, int]]] = {}
    for event in events:
        by_cycle.setdefault(event["cycle"], []).append(event)
    last_offer = max(by_cycle, default=-1)
    pending: list[dict[str, int] | None] = [None] * 16
    offered = {event["id"] for event in events}
    accepted: set[int] = set()
    dropped: set[int] = set()
    accepted_at: dict[int, int] = {}
    occurrence_at: dict[int, int] = {}
    latency: dict[int, int] = {}

    for cycle in range(10000):
        for event in by_cycle.get(cycle, ()):
            source = event["source"]
            if pending[source] is None:
                pending[source] = event
            else:
                dropped.add(event["id"])
        valid = [event is not None for event in pending]
        payload = [event["id"] if event is not None else 0 for event in pending]
        result = model.step(valid, payload, True)
        for source, did_accept in enumerate(result.source_ready):
            if did_accept:
                event = pending[source]
                if event is None:
                    raise AssertionError("accept without pending event")
                event_id = event["id"]
                accepted.add(event_id)
                accepted_at[event_id] = cycle
                occurrence_at[event_id] = event["cycle"]
                pending[source] = None
        if result.retired is not None:
            event_id = result.retired.payload
            if event_id in latency or event_id not in accepted_at:
                raise AssertionError("duplicate or phantom retirement")
            latency[event_id] = cycle - occurrence_at[event_id] + 1
        if cycle > last_offer and not any(event is not None for event in pending) and model.occupancy() == 0:
            break
    else:
        raise AssertionError("detailed replay drain timeout")
    if accepted != set(latency) or offered != accepted | dropped:
        raise AssertionError("detailed replay conservation failure")
    return DetailedRun(offered, accepted, dropped, latency)


def analyze_generated_suite(generated_suite: pathlib.Path) -> dict[str, object]:
    index = json.loads((generated_suite / "generation-index.json").read_text())
    fixed_all: list[int] = []
    moving_all: list[int] = []
    fixed_common: list[int] = []
    moving_common: list[int] = []
    moving_only: list[int] = []
    fixed_only: list[int] = []
    common_changed = 0
    moving_tail47_common = 0
    moving_tail47_only = 0
    run_rows = []
    for metadata in index["runs"]:
        name = metadata["run"]["name"]
        events = load_events(generated_suite / metadata["trace_file"])
        fixed = detailed_replay(events, 1)
        moving = detailed_replay(events, 2)
        common = fixed.accepted & moving.accepted
        only_moving = moving.accepted - fixed.accepted
        only_fixed = fixed.accepted - moving.accepted
        fixed_all.extend(fixed.latency.values())
        moving_all.extend(moving.latency.values())
        fixed_common.extend(fixed.latency[event] for event in common)
        moving_common.extend(moving.latency[event] for event in common)
        moving_only.extend(moving.latency[event] for event in only_moving)
        fixed_only.extend(fixed.latency[event] for event in only_fixed)
        changed = sum(fixed.latency[event] != moving.latency[event] for event in common)
        common_changed += changed
        moving_tail47_common += sum(moving.latency[event] >= 47 for event in common)
        moving_tail47_only += sum(moving.latency[event] >= 47 for event in only_moving)
        if changed or only_moving or only_fixed:
            run_rows.append(
                {
                    "name": name,
                    "common_events": len(common),
                    "common_latency_changed": changed,
                    "moving_only_accepted": len(only_moving),
                    "fixed_only_accepted": len(only_fixed),
                    "fixed_common_p99": percentile(
                        [fixed.latency[event] for event in common], 99
                    ),
                    "moving_common_p99": percentile(
                        [moving.latency[event] for event in common], 99
                    ),
                }
            )
    return {
        "fixed_all_count": len(fixed_all),
        "moving_all_count": len(moving_all),
        "fixed_all_p99": percentile(fixed_all, 99),
        "moving_all_p99": percentile(moving_all, 99),
        "common_count": len(fixed_common),
        "fixed_common_p99": percentile(fixed_common, 99),
        "moving_common_p99": percentile(moving_common, 99),
        "common_latency_changed": common_changed,
        "moving_only_count": len(moving_only),
        "moving_only_p99": percentile(moving_only, 99),
        "fixed_only_count": len(fixed_only),
        "fixed_only_p99": percentile(fixed_only, 99),
        "moving_latency_ge47_common": moving_tail47_common,
        "moving_latency_ge47_moving_only": moving_tail47_only,
        "affected_runs": run_rows,
    }
