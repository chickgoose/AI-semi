#!/usr/bin/env python3
"""Deterministic workload evaluation for the A8 frontier cycle model."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean

from non_crossing_frontier import FlatKRoundRobin, NonCrossingFrontierFabric


NUM_SOURCES = 16
LANES = 4
STIM_CYCLES = 512


def _add(events: dict[int, list[int]], cycle: int, sources: list[int]) -> None:
    if len(sources) != len(set(sources)):
        raise ValueError("workload emits a source more than once in one cycle")
    events[cycle].extend(sources)


def build_workloads() -> dict[str, dict[int, list[int]]]:
    workloads: dict[str, dict[int, list[int]]] = {}

    local: dict[int, list[int]] = defaultdict(list)
    dispersed: dict[int, list[int]] = defaultdict(list)
    mirror: dict[int, list[int]] = defaultdict(list)
    for cycle in range(8, STIM_CYCLES, 2):
        _add(local, cycle, [0, 1, 2, 3])
        _add(dispersed, cycle, [0, 5, 10, 15])
        _add(mirror, cycle, [12, 13, 14, 15])
    workloads["local"] = local
    workloads["dispersed"] = dispersed
    workloads["mirror"] = mirror

    moving_row: dict[int, list[int]] = defaultdict(list)
    moving_column: dict[int, list[int]] = defaultdict(list)
    moving_dispersed: dict[int, list[int]] = defaultdict(list)
    dispersed_groups = ([0, 5, 10, 15], [3, 6, 9, 12], [1, 7, 8, 14], [2, 4, 11, 13])
    for cycle in range(STIM_CYCLES):
        phase = cycle // 128
        row = [4 * phase + offset for offset in range(4)]
        column = [phase + 4 * offset for offset in range(4)]
        _add(moving_row, cycle, [row[(cycle + offset) % 4] for offset in range(3)])
        _add(moving_column, cycle, [column[(cycle + offset) % 4] for offset in range(3)])
        group = dispersed_groups[phase]
        _add(moving_dispersed, cycle, [group[(cycle + offset) % 4] for offset in range(3)])
    workloads["moving_row_hotspot"] = moving_row
    workloads["moving_column_hotspot"] = moving_column
    workloads["moving_dispersed_hotspot"] = moving_dispersed

    elephant_mouse: dict[int, list[int]] = defaultdict(list)
    for cycle in range(STIM_CYCLES):
        sources = [0]
        if cycle % 8 == 0:
            mouse = 1 + ((cycle // 8) % (NUM_SOURCES - 1))
            sources.append(mouse)
        _add(elephant_mouse, cycle, sources)
    workloads["elephant_mouse"] = elephant_mouse

    global_fanin: dict[int, list[int]] = defaultdict(list)
    for cycle in range(8, STIM_CYCLES, 8):
        _add(global_fanin, cycle, list(range(NUM_SOURCES)))
    workloads["global_fanin"] = global_fanin
    return workloads


def _percentile(values: list[int], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[rank])


def _jain(values: list[float]) -> float:
    if not values:
        return 1.0
    total = sum(values)
    squares = sum(value * value for value in values)
    return 1.0 if squares == 0 else total * total / (len(values) * squares)


@dataclass
class RunMetrics:
    workload: str
    scheduler: str
    generated: int
    accepted: int
    overrun: int
    delivered: int
    throughput: float
    latency_p95: float
    latency_p99: float
    latency_max: int
    demand_normalized_fairness: float
    max_source_wait: int
    frontier_moves: int
    frontier_distance: int
    frontier_reversals: int
    toggles_per_cycle: float
    toggles_per_delivered: float


def run_workload(name: str, events: dict[int, list[int]], scheduler: object) -> RunMetrics:
    pending: list[tuple[int, int] | None] = [None] * NUM_SOURCES
    generated = 0
    accepted = 0
    overrun = 0
    delivered = 0
    next_id = 0
    offered = [0] * NUM_SOURCES
    serviced = [0] * NUM_SOURCES
    latencies: list[int] = []
    waits: list[int] = []
    frontier_moves = 0
    frontier_distance = 0
    frontier_reversals = 0
    toggle_sum = 0
    cycle = 0
    drain_limit = STIM_CYCLES + 8 * NUM_SOURCES
    while cycle < drain_limit:
        for source in events.get(cycle, []):
            generated += 1
            offered[source] += 1
            if pending[source] is None:
                pending[source] = (next_id, cycle)
                accepted += 1
            else:
                overrun += 1
            next_id += 1
        request_mask = sum(1 << source for source, event in enumerate(pending) if event is not None)
        result = scheduler.step(request_mask, advance=True)
        if result.grant_mask & ~request_mask:
            raise AssertionError("scheduler granted a source without a pending occurrence")
        if len(result.grant_sources) != len(set(result.grant_sources)):
            raise AssertionError("scheduler duplicated a source grant")
        for source in result.grant_sources:
            event = pending[source]
            if event is None:
                raise AssertionError("grant did not map to an exact pending event")
            _, occurrence_cycle = event
            latency = cycle - occurrence_cycle
            latencies.append(latency)
            waits.append(latency)
            pending[source] = None
            serviced[source] += 1
            delivered += 1
        frontier_moves += int(result.frontier_distance > 0)
        frontier_distance += result.frontier_distance
        frontier_reversals += result.frontier_reversals
        toggle_sum += result.toggle_proxy
        cycle += 1
        if cycle >= STIM_CYCLES and not any(pending):
            break
    if any(pending):
        raise AssertionError(f"{name}: drain timeout")
    if accepted != delivered:
        raise AssertionError(f"{name}: accepted/delivered conservation failed")
    demand_ratios = [serviced[source] / offered[source] for source in range(NUM_SOURCES) if offered[source]]
    return RunMetrics(
        workload=name,
        scheduler=type(scheduler).__name__,
        generated=generated,
        accepted=accepted,
        overrun=overrun,
        delivered=delivered,
        throughput=delivered / STIM_CYCLES,
        latency_p95=_percentile(latencies, 0.95),
        latency_p99=_percentile(latencies, 0.99),
        latency_max=max(latencies, default=0),
        demand_normalized_fairness=_jain(demand_ratios),
        max_source_wait=max(waits, default=0),
        frontier_moves=frontier_moves,
        frontier_distance=frontier_distance,
        frontier_reversals=frontier_reversals,
        toggles_per_cycle=toggle_sum / cycle,
        toggles_per_delivered=toggle_sum / delivered if delivered else 0.0,
    )


def evaluate() -> dict[str, object]:
    rows: list[RunMetrics] = []
    for name, events in build_workloads().items():
        rows.append(run_workload(name, events, FlatKRoundRobin(NUM_SOURCES, LANES)))
        rows.append(run_workload(name, events, NonCrossingFrontierFabric(NUM_SOURCES, LANES)))
    by_key = {(row.workload, row.scheduler): row for row in rows}
    ratios = []
    fairness_deltas = []
    p99_deltas = []
    for name in build_workloads():
        flat = by_key[(name, "FlatKRoundRobin")]
        frontier = by_key[(name, "NonCrossingFrontierFabric")]
        ratios.append(frontier.delivered / flat.delivered if flat.delivered else 1.0)
        fairness_deltas.append(
            frontier.demand_normalized_fairness - flat.demand_normalized_fairness
        )
        p99_deltas.append(frontier.latency_p99 - flat.latency_p99)
    flat_proxy = FlatKRoundRobin(NUM_SOURCES, LANES).proxy()
    frontier_proxy = NonCrossingFrontierFabric(NUM_SOURCES, LANES).proxy()
    gate = {
        "minimum_delivered_ratio": min(ratios),
        "mean_delivered_ratio": fmean(ratios),
        "minimum_fairness_delta": min(fairness_deltas),
        "maximum_p99_latency_delta": max(p99_deltas),
        "comparator_ratio": (
            frontier_proxy["request_comparators"] / flat_proxy["request_comparators"]
        ),
        "wire_fanout_ratio": (
            frontier_proxy["global_request_wire_fanout"]
            / flat_proxy["global_request_wire_fanout"]
        ),
    }
    gate["go"] = bool(
        gate["minimum_delivered_ratio"] >= 0.90
        and gate["mean_delivered_ratio"] >= 0.97
        and gate["minimum_fairness_delta"] >= -0.05
        and gate["maximum_p99_latency_delta"] <= 16
        and gate["comparator_ratio"] <= 0.50
        and gate["wire_fanout_ratio"] <= 0.50
    )
    decision = "GO" if gate["go"] else "HOLD"
    return {
        "research_complete": True,
        "decision": decision,
        "completion_sentinel": f"A8_NCF_RESEARCH_COMPLETE_{decision}",
        "assumptions": {
            "num_sources": NUM_SOURCES,
            "lanes": LANES,
            "stim_cycles": STIM_CYCLES,
            "one_pending_occurrence_per_source": True,
            "delivery_lanes": LANES,
        },
        "rows": [asdict(row) for row in rows],
        "proxy": {"flat_rr": flat_proxy, "non_crossing_frontier": frontier_proxy},
        "go_gate": gate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-go",
        action="store_true",
        help="return nonzero when the completed research decision is HOLD",
    )
    args = parser.parse_args()
    report = evaluate()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if args.require_go and report["decision"] != "GO" else 0


if __name__ == "__main__":
    raise SystemExit(main())
