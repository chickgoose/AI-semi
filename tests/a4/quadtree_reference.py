#!/usr/bin/env python3
"""Cycle-accurate local falsification model for A4 and a flat RR reference."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


LEAF_SOURCES = ((0, 1, 4, 5), (2, 3, 6, 7), (8, 9, 12, 13), (10, 11, 14, 15))


@dataclass
class Event:
    event_id: int
    source: int
    occurrence: int
    deadline: int
    accepted: int | None = None
    delivered: int | None = None
    overrun: bool = False
    age: int = 0


class MergeNode:
    def __init__(self, radix: int = 4) -> None:
        self.radix = radix
        self.phase = 0
        self.slot: Event | None = None

    def select(self, valid: list[bool], out_ready: bool) -> int | None:
        if self.slot is not None and not out_ready:
            return None
        for offset in range(self.radix):
            child = (self.phase + offset) % self.radix
            if valid[child]:
                return child
        return None

    def update(self, selected: int | None, children: list[Event | None], out_ready: bool) -> None:
        old_slot = self.slot
        if old_slot is not None and not out_ready:
            assert selected is None
            assert self.slot is old_slot
            return
        if selected is None:
            self.slot = None
            return
        item = children[selected]
        assert item is not None
        item.age = min(255, item.age + 1)
        self.slot = item
        self.phase = (selected + 1) % self.radix


class A4Model:
    name = "a4-quadtree-model"

    def __init__(self) -> None:
        self.leaves = [MergeNode() for _ in range(4)]
        self.root = MergeNode()

    def empty(self) -> bool:
        return self.root.slot is None and all(leaf.slot is None for leaf in self.leaves)

    def step(self, pending: list[Event | None]) -> tuple[list[int], Event | None]:
        delivered = self.root.slot
        leaf_items = [leaf.slot for leaf in self.leaves]
        root_selected = self.root.select([item is not None for item in leaf_items], True)
        leaf_out_ready = [root_selected == index for index in range(4)]

        accepted_sources: list[int] = []
        leaf_selected: list[int | None] = []
        leaf_children: list[list[Event | None]] = []
        for leaf_index, sources in enumerate(LEAF_SOURCES):
            children = [pending[source] for source in sources]
            selected = self.leaves[leaf_index].select(
                [item is not None for item in children], leaf_out_ready[leaf_index]
            )
            leaf_children.append(children)
            leaf_selected.append(selected)
            if selected is not None:
                accepted_sources.append(sources[selected])

        assert len(accepted_sources) == len(set(accepted_sources))
        assert len(accepted_sources) <= 4
        old_root = self.root.slot
        old_leaves = [leaf.slot for leaf in self.leaves]
        self.root.update(root_selected, leaf_items, True)
        for index, leaf in enumerate(self.leaves):
            leaf.update(leaf_selected[index], leaf_children[index], leaf_out_ready[index])

        # No occupied slot may disappear except through its unique parent edge.
        if old_root is not None:
            assert delivered is old_root
        for index, old_leaf in enumerate(old_leaves):
            if old_leaf is not None and not leaf_out_ready[index]:
                assert self.leaves[index].slot is old_leaf
        return accepted_sources, delivered


class FlatRRModel:
    name = "mock-flat-rr-model"

    def __init__(self) -> None:
        self.node = MergeNode(radix=16)

    def empty(self) -> bool:
        return self.node.slot is None

    def step(self, pending: list[Event | None]) -> tuple[list[int], Event | None]:
        delivered = self.node.slot
        selected = self.node.select([item is not None for item in pending], True)
        self.node.update(selected, pending, True)
        return ([] if selected is None else [selected]), delivered


def percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def jain(values: Iterable[float]) -> float:
    values = list(values)
    if not values or sum(value * value for value in values) == 0:
        return 1.0
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))


def run_trace(model_type: type[A4Model] | type[FlatRRModel], trace: list[dict], metadata: dict) -> dict:
    model = model_type()
    stim_cycles = int(metadata["run"]["stim_cycles"])
    by_cycle: dict[int, list[Event]] = {}
    events: list[Event] = []
    offered_by_source = [0] * 16
    accepted_by_source = [0] * 16
    pending: list[Event | None] = [None] * 16
    delivered_ids: set[int] = set()
    source_delivery_order: list[list[int]] = [[] for _ in range(16)]
    measurement_delivered = 0

    for row in trace:
        event = Event(
            event_id=int(row["tb_only_event_id"]),
            source=int(row["logical_source"]),
            occurrence=int(row["occurrence_cycle"]),
            deadline=int(row["deadline"]),
        )
        events.append(event)
        by_cycle.setdefault(event.occurrence, []).append(event)

    cycle = 0
    drain_limit = stim_cycles + 20000
    while cycle < stim_cycles or any(pending) or not model.empty():
        if cycle >= drain_limit:
            raise AssertionError("drain timeout")
        for event in by_cycle.get(cycle, []):
            offered_by_source[event.source] += 1
            if pending[event.source] is None:
                pending[event.source] = event
            else:
                event.overrun = True

        accepted_sources, delivered = model.step(pending)
        for source in accepted_sources:
            event = pending[source]
            assert event is not None and event.accepted is None
            event.accepted = cycle
            accepted_by_source[source] += 1
            pending[source] = None

        if delivered is not None:
            assert delivered.accepted is not None
            assert delivered.event_id not in delivered_ids
            delivered.delivered = cycle
            delivered_ids.add(delivered.event_id)
            source_delivery_order[delivered.source].append(delivered.event_id)
            if cycle < stim_cycles:
                measurement_delivered += 1
        cycle += 1

    accepted = [event for event in events if event.accepted is not None]
    delivered = [event for event in events if event.delivered is not None]
    assert len(accepted) == len(delivered) == len(delivered_ids)
    assert len(events) == len(accepted) + sum(event.overrun for event in events)
    for source in range(16):
        expected = [event.event_id for event in accepted if event.source == source]
        assert source_delivery_order[source] == expected

    latencies = [event.delivered - event.occurrence for event in delivered if event.delivered is not None]
    waits = [event.accepted - event.occurrence for event in accepted if event.accepted is not None]
    service_ratios = [
        accepted_by_source[source] / offered_by_source[source]
        for source in range(16) if offered_by_source[source]
    ]
    return {
        "candidate": model.name,
        "name": metadata["run"]["name"],
        "report_group": metadata["report_group"],
        "workload": metadata["run"]["workload"],
        "seed": metadata["run"]["seed"],
        "load": metadata["run"]["load"],
        "trace_sha256": metadata["trace_sha256"],
        "generated": len(events),
        "source_overrun": sum(event.overrun for event in events),
        "accepted": len(accepted),
        "delivered": len(delivered),
        "errors": 0,
        "measurement_event_per_cycle": measurement_delivered / stim_cycles,
        "p95_e2e_latency": percentile(latencies, 0.95),
        "p99_e2e_latency": percentile(latencies, 0.99),
        "max_e2e_latency": max(latencies, default=0),
        "max_request_wait": max(waits, default=0),
        "demand_normalized_fairness": jain(service_ratios),
        "min_source_service_ratio": min(service_ratios, default=1.0),
        "drain_cycles": max(0, cycle - stim_cycles),
    }


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def validate_sha(trace_path: Path, metadata: dict) -> None:
    actual = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    if actual != metadata["trace_sha256"]:
        raise AssertionError(f"trace SHA mismatch for {trace_path.name}")


def random_falsification() -> None:
    for seed in range(100):
        rng = random.Random(seed)
        trace = []
        event_id = 0
        for cycle in range(256):
            for source in range(16):
                if rng.random() < 0.08:
                    trace.append({
                        "tb_only_event_id": event_id,
                        "logical_source": source,
                        "occurrence_cycle": cycle,
                        "deadline": cycle + 32,
                    })
                    event_id += 1
        metadata = {
            "run": {"name": f"fuzz-{seed}", "workload": "fuzz", "seed": seed,
                    "load": 1.28, "stim_cycles": 256},
            "report_group": "fuzz", "trace_sha256": "not-applicable",
        }
        run_trace(A4Model, trace, metadata)

    simultaneous = [
        {"tb_only_event_id": source, "logical_source": source,
         "occurrence_cycle": 0, "deadline": 32}
        for source in range(16)
    ]
    metadata = {"run": {"name": "all16", "workload": "proof", "seed": 0,
                         "load": 16, "stim_cycles": 1},
                "report_group": "proof", "trace_sha256": "not-applicable"}
    result = run_trace(A4Model, simultaneous, metadata)
    assert result["max_request_wait"] <= 15
    assert result["max_e2e_latency"] <= 17


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        random_falsification()

    rows = []
    for trace_path in sorted(args.trace_dir.glob("*.events.jsonl")):
        stem = trace_path.name.removesuffix(".events.jsonl")
        metadata = json.loads((args.trace_dir / f"{stem}.manifest.json").read_text(encoding="utf-8"))
        validate_sha(trace_path, metadata)
        trace = read_jsonl(trace_path)
        rows.append(run_trace(A4Model, trace, metadata))
        rows.append(run_trace(FlatRRModel, trace, metadata))
    if len(rows) != 92:
        raise AssertionError(f"expected 92 candidate-runs, got {len(rows)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"A4_REFERENCE PASS fuzz=100 frozen_runs=46 candidate_runs={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
