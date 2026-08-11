#!/usr/bin/env python3
"""Convert exact generator-v4 traces into independent dual-lockstep vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from model import MovingBlockReference


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else 0


@dataclass
class Metrics:
    accepted: int = 0
    retired: int = 0
    fixed_window: int = 0
    latencies: list[int] = field(default_factory=list)

    def document(self) -> dict[str, int | float]:
        return {
            "accepted": self.accepted,
            "retired": self.retired,
            "fixed_window_delivered": self.fixed_window,
            "latency_sum": sum(self.latencies),
            "mean_occurrence_to_delivery": (
                sum(self.latencies) / len(self.latencies) if self.latencies else 0.0
            ),
            "p95_occurrence_to_delivery": percentile(self.latencies, 0.95),
            "p99_occurrence_to_delivery": percentile(self.latencies, 0.99),
            "max_occurrence_to_delivery": max(self.latencies, default=0),
        }


class Driver:
    def __init__(self, max_advance: int):
        self.model = MovingBlockReference(max_advance)
        self.pending = [0] * 16
        self.metrics = Metrics()
        self.overrun = 0

    def add(self, source: int, occurrence: int, event_id: int) -> None:
        if self.pending[source]:
            self.overrun += 1
        else:
            payload = (source << 24) | ((event_id + 1) & 0xffffff)
            self.pending[source] = ((occurrence + 1) << 32) | payload

    def step(self, cycle: int, stim_cycles: int):
        before = self.pending.copy()
        result = self.model.step(before)
        for source in range(16):
            if (result.ready_mask >> source) & 1:
                if before[source] == 0:
                    raise AssertionError("accepted without pending causal credit")
                self.pending[source] = 0
                self.metrics.accepted += 1
        if result.retire_valid:
            occurrence = (result.retire_token >> 32) - 1
            self.metrics.retired += 1
            self.metrics.latencies.append(cycle - occurrence + 1)
            if cycle < stim_cycles:
                self.metrics.fixed_window += 1
        return before, result

    def drained(self) -> bool:
        return not any(self.pending) and self.model.occupancy() == 0


def load_trace(path: Path) -> dict[int, list[tuple[int, int]]]:
    by_cycle: dict[int, list[tuple[int, int]]] = {}
    seen_ids: set[int] = set()
    seen_source_cycles: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            event = json.loads(line)
            cycle = int(event["occurrence_cycle"])
            source = int(event["logical_source"])
            event_id = int(event["tb_only_event_id"])
            if cycle < 0 or not 0 <= source < 16:
                raise ValueError(f"invalid normalized event {path}:{line_number}")
            if event_id in seen_ids or (cycle, source) in seen_source_cycles:
                raise ValueError(f"duplicate event/source-cycle {path}:{line_number}")
            seen_ids.add(event_id)
            seen_source_cycles.add((cycle, source))
            by_cycle.setdefault(cycle, []).append((source, event_id))
    return by_cycle


def metadata_for(trace: Path) -> dict:
    name = trace.name.removesuffix(".events.jsonl")
    return json.loads((trace.parent / f"{name}.manifest.json").read_text())


def vector_fields(pending: list[int], result) -> list[str]:
    valid_mask = sum((token != 0) << source for source, token in enumerate(pending))
    fields = [f"{valid_mask:04x}"]
    fields.extend(f"{token:016x}" for token in pending)
    fields.extend([
        f"{result.ready_mask:04x}", str(int(result.retire_valid)),
        f"{result.retire_source:x}",
    ])
    return fields


def generate_one(trace: Path, output: Path) -> dict:
    meta = metadata_for(trace)
    if meta.get("generator_version") != "4.0" or meta.get("event_identity_mode") != "address_only":
        raise ValueError(f"metadata is not generator-v4 address-only: {trace}")
    stim_cycles = int(meta["run"]["stim_cycles"])
    events = load_trace(trace)
    moving = Driver(2)
    fixed = Driver(1)
    cycle_lines: list[str] = []
    for reset_cycle in (-2, -1):
        moving_result = moving.model.step([0] * 16, rst_n=False)
        fixed_result = fixed.model.step([0] * 16, rst_n=False)
        cycle_lines.append(" ".join(
            [str(reset_cycle), "0"]
            + vector_fields([0] * 16, moving_result)
            + vector_fields([0] * 16, fixed_result)
        ))
    offered = sum(len(items) for items in events.values())
    for cycle in range(stim_cycles + offered + 128):
        for source, event_id in events.get(cycle, []):
            moving.add(source, cycle, event_id)
            fixed.add(source, cycle, event_id)
        moving_pending, moving_result = moving.step(cycle, stim_cycles)
        fixed_pending, fixed_result = fixed.step(cycle, stim_cycles)
        cycle_lines.append(" ".join(
            [str(cycle), "1"]
            + vector_fields(moving_pending, moving_result)
            + vector_fields(fixed_pending, fixed_result)
        ))
        if cycle >= stim_cycles and moving.drained() and fixed.drained():
            break
    else:
        raise AssertionError(f"drain timeout: {trace}")
    if moving.metrics.accepted != moving.metrics.retired:
        raise AssertionError("moving reference conservation failure")
    if fixed.metrics.accepted != fixed.metrics.retired:
        raise AssertionError("fixed reference conservation failure")

    quiet_cycle = cycle + 1
    moving_pending, moving_result = moving.step(quiet_cycle, stim_cycles)
    fixed_pending, fixed_result = fixed.step(quiet_cycle, stim_cycles)
    if (moving_result.retire_valid or fixed_result.retire_valid
            or moving_result.ready_mask or fixed_result.ready_mask):
        raise AssertionError("reference not quiet after drain")
    cycle_lines.append(" ".join(
        [str(quiet_cycle), "1"]
        + vector_fields(moving_pending, moving_result)
        + vector_fields(fixed_pending, fixed_result)
    ))

    moving_doc = moving.metrics.document()
    fixed_doc = fixed.metrics.document()
    header = [
        "W4V1", str(stim_cycles), str(len(cycle_lines)), str(offered),
        str(moving.overrun), str(moving_doc["accepted"]),
        str(moving_doc["retired"]), str(moving_doc["fixed_window_delivered"]),
        str(moving_doc["latency_sum"]), str(moving_doc["max_occurrence_to_delivery"]),
        str(fixed.overrun), str(fixed_doc["accepted"]), str(fixed_doc["retired"]),
        str(fixed_doc["fixed_window_delivered"]), str(fixed_doc["latency_sum"]),
        str(fixed_doc["max_occurrence_to_delivery"]),
    ]
    output.write_text(" ".join(header) + "\n" + "\n".join(cycle_lines) + "\n", encoding="ascii")
    return {
        "trace": trace.name.removesuffix(".events.jsonl"),
        "trace_sha256": sha256(trace),
        "vector": str(output),
        "vector_sha256": sha256(output),
        "stim_cycles": stim_cycles,
        "vector_cycles": len(cycle_lines),
        "offered": offered,
        "moving": {"overrun": moving.overrun, **moving_doc},
        "fixed": {"overrun": fixed.overrun, **fixed_doc},
        "moving_latencies": moving.metrics.latencies,
        "fixed_latencies": fixed.metrics.latencies,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", action="append", required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    args = parser.parse_args()
    args.vectors.mkdir(parents=True)
    runs = []
    counts: dict[str, int] = {}
    for item in args.suite:
        suite, raw_directory = item.split("=", 1)
        traces = sorted(Path(raw_directory).glob("*.events.jsonl"))
        counts[suite] = len(traces)
        for trace in traces:
            output = args.vectors / f"{suite}__{trace.name.removesuffix('.events.jsonl')}.txt"
            runs.append({"suite": suite, **generate_one(trace, output)})
    if counts != {"full50": 50, "capacity22": 22}:
        raise SystemExit(f"W4 exact suite-count failure: {counts}")
    args.index.write_text(json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n")
    print(f"W4_VECTOR_PREP_PASS runs={len(runs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
