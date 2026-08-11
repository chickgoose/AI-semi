#!/usr/bin/env python3
"""Compare the W3 moving-block model with its fixed one-step twin."""

from __future__ import annotations

import json
import math
import statistics

from model import MovingBlockTreeModel, run_occurrences


def percentile(values: list[int], pct: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * pct / 100) - 1)]


def scenarios() -> dict[str, tuple[list[tuple[int, int]], list[bool]]]:
    sparse = [(cycle, (cycle // 12) % 16) for cycle in range(0, 480, 12)]
    b16 = [
        (start, source)
        for start in range(0, 512, 16)
        for source in range(16)
    ]
    global_fanin = [
        (start, source)
        for start in range(0, 384, 8)
        for source in range(16)
    ]
    branch_merge = [
        (cycle, source)
        for cycle in range(0, 480, 4)
        for source in (0, 1, 8, 9)
    ]
    mixed = []
    for cycle in range(640):
        if cycle < 160:
            if cycle % 8 == 0:
                mixed.extend((cycle, source) for source in range(16))
        elif cycle < 288:
            mixed.extend((cycle, source) for source in range(16))
        elif cycle % 13 == 0:
            mixed.append((cycle, cycle % 16))
    return {
        "isolated_sparse": (sparse, [True]),
        "b16": (b16, [True]),
        "global_fanin": (global_fanin, [True]),
        "branch_merge": (branch_merge, [True, True, True, False]),
        "shock_recovery_no_reset": (
            mixed,
            [True] * 19 + [False] * 41 + [True] * 37,
        ),
    }


def structure(num_sources: int, addr_width: int, max_advance: int) -> dict[str, int]:
    source_width = max(1, math.ceil(math.log2(num_sources)))
    nodes = 2 * num_sources - 1
    return {
        "node_slots": nodes,
        "branch_phase_bits": num_sources - 1,
        "register_bits": nodes * (addr_width + source_width + 1) + num_sources - 1,
        "max_comb_skip_edges": max_advance,
        "child_control_checks": 2 * max_advance * (num_sources - 1),
        "max_local_branch_fanout": 2 * max_advance,
    }


def main() -> None:
    report: dict[str, object] = {
        "contract": {
            "num_sources": 16,
            "single_retire_peak_events_per_cycle": 1,
            "moving_max_advance": 2,
            "fixed_max_advance": 1,
        },
        "structure": {
            "n16_fixed": structure(16, 32, 1),
            "n16_moving": structure(16, 32, 2),
            "n64_fixed": structure(64, 32, 1),
            "n64_moving": structure(64, 32, 2),
        },
        "workloads": {},
    }
    for name, (occurrences, ready) in scenarios().items():
        variants = {}
        for label, advance in (("fixed", 1), ("moving", 2)):
            metrics = run_occurrences(
                MovingBlockTreeModel(16, advance), occurrences, ready
            )
            variants[label] = {
                "offered": metrics.offered,
                "accepted": metrics.accepted,
                "overrun": metrics.overrun,
                "retired": metrics.retired,
                "cycles": metrics.cycles,
                "throughput": round(metrics.throughput, 6),
                "active_throughput": round(metrics.active_throughput, 6),
                "output_bubbles": metrics.output_bubbles,
                "mean_latency": round(statistics.mean(metrics.latencies), 6),
                "p95_latency": percentile(metrics.latencies, 95),
                "p99_latency": percentile(metrics.latencies, 99),
                "max_latency": max(metrics.latencies),
                "mean_e2e_latency": round(
                    statistics.mean(metrics.e2e_latencies), 6
                ),
                "p95_e2e_latency": percentile(metrics.e2e_latencies, 95),
                "p99_e2e_latency": percentile(metrics.e2e_latencies, 99),
                "max_e2e_latency": max(metrics.e2e_latencies),
            }
        report["workloads"][name] = variants
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
