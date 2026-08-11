#!/usr/bin/env python3
"""Cycle model and local workload driver for the A4 moving-block tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Event:
    source: int
    payload: int
    accepted_cycle: int


@dataclass(frozen=True)
class CycleResult:
    source_ready: tuple[bool, ...]
    retire_valid: bool
    retire_source: int
    retire_payload: int
    retired: Event | None
    movement: tuple[int, ...]


class MovingBlockTreeModel:
    """Complete binary tree with bounded combinational movement authority."""

    MAX_COMB_SKIP = 2

    def __init__(self, num_sources: int = 16, max_advance: int = 2):
        if num_sources < 2 or num_sources & (num_sources - 1):
            raise ValueError("num_sources must be a power of two >= 2")
        if not 1 <= max_advance <= self.MAX_COMB_SKIP:
            raise ValueError("max_advance must be one or two")
        self.num_sources = num_sources
        self.max_advance = max_advance
        self.total_nodes = 2 * num_sources - 1
        self.first_leaf = num_sources - 1
        self.nodes: list[Event | None] = [None] * self.total_nodes
        self.phase = [0] * (num_sources - 1)
        self.cycle = 0

    def reset(self) -> None:
        self.nodes = [None] * self.total_nodes
        self.phase = [0] * (self.num_sources - 1)
        self.cycle = 0

    def occupancy(self) -> int:
        return sum(item is not None for item in self.nodes)

    def step(
        self,
        source_valid: Sequence[bool],
        source_payload: Sequence[int],
        retire_ready: bool,
        rst_n: bool = True,
    ) -> CycleResult:
        if len(source_valid) != self.num_sources:
            raise ValueError("source_valid length mismatch")
        if len(source_payload) != self.num_sources:
            raise ValueError("source_payload length mismatch")
        if not rst_n:
            self.reset()
            return CycleResult(
                (False,) * self.num_sources, False, 0, 0, None, ()
            )

        before = [item for item in self.nodes if item is not None]
        before_position = {
            (item.source, item.payload, item.accepted_cycle): index
            for index, item in enumerate(self.nodes)
            if item is not None
        }
        root = self.nodes[0]
        retire_valid = root is not None
        retire_source = root.source if root is not None else 0
        retire_payload = root.payload if root is not None else 0
        retired = root if root is not None and retire_ready else None

        work = self.nodes.copy()
        phases = self.phase.copy()
        if retired is not None:
            work[0] = None

        accepted = [False] * self.num_sources
        moves = [0] * self.total_nodes
        for _microstep in range(self.max_advance):
            # A source is a virtual block immediately below its dedicated leaf.
            for source in range(self.num_sources):
                leaf = self.first_leaf + source
                if source_valid[source] and not accepted[source] and work[leaf] is None:
                    work[leaf] = Event(source, int(source_payload[source]), self.cycle)
                    accepted[source] = True

            # Heap order is root to leaves. An item cannot move twice in one
            # microstep because its destination was examined earlier.
            for parent in range(self.first_leaf):
                if work[parent] is not None:
                    continue
                left = 2 * parent + 1
                right = left + 1
                left_valid = work[left] is not None
                right_valid = work[right] is not None
                if not left_valid and not right_valid:
                    continue
                if left_valid and right_valid:
                    child = right if phases[parent] else left
                else:
                    child = left if left_valid else right
                work[parent] = work[child]
                work[child] = None
                moves[child] += 1
                phases[parent] = 1 if child == left else 0

        after = [item for item in work if item is not None]
        accepted_events = [
            Event(source, int(source_payload[source]), self.cycle)
            for source, did_accept in enumerate(accepted)
            if did_accept
        ]
        expected = before + accepted_events
        if retired is not None:
            expected.remove(retired)
        if sorted((e.source, e.payload, e.accepted_cycle) for e in after) != sorted(
            (e.source, e.payload, e.accepted_cycle) for e in expected
        ):
            raise AssertionError("cycle conservation violated")
        accepted_keys = {
            (event.source, event.payload, event.accepted_cycle)
            for event in accepted_events
        }
        for after_index, event in enumerate(work):
            if event is None:
                continue
            key = (event.source, event.payload, event.accepted_cycle)
            start_index = (
                self.first_leaf + event.source
                if key in accepted_keys
                else before_position[key]
            )
            start_depth = (start_index + 1).bit_length() - 1
            after_depth = (after_index + 1).bit_length() - 1
            if not 0 <= start_depth - after_depth <= self.max_advance:
                raise AssertionError("movement authority bound violated")

        self.nodes = work
        self.phase = phases
        self.cycle += 1
        return CycleResult(
            tuple(accepted),
            retire_valid,
            retire_source,
            retire_payload,
            retired,
            tuple(moves),
        )


@dataclass
class RunMetrics:
    offered: int
    accepted: int
    retired: int
    overrun: int
    cycles: int
    active_cycles: int
    output_bubbles: int
    latencies: list[int]
    e2e_latencies: list[int]

    @property
    def throughput(self) -> float:
        return self.retired / self.cycles if self.cycles else 0.0

    @property
    def active_throughput(self) -> float:
        return self.retired / self.active_cycles if self.active_cycles else 0.0


def run_occurrences(
    model: MovingBlockTreeModel,
    occurrences: Iterable[tuple[int, int]],
    retire_ready_pattern: Sequence[bool],
    drain_limit: int = 10000,
) -> RunMetrics:
    """Drive `(cycle, source)` occurrences through one-pending source latches."""

    by_cycle: dict[int, list[int]] = {}
    offered = 0
    for cycle, source in occurrences:
        by_cycle.setdefault(cycle, []).append(source)
        offered += 1
    last_offer = max(by_cycle, default=-1)
    pending: list[int | None] = [None] * model.num_sources
    pending_created: list[int | None] = [None] * model.num_sources
    sequence = [0] * model.num_sources
    accepted = 0
    retired = 0
    overrun = 0
    output_bubbles = 0
    latencies: list[int] = []
    e2e_latencies: list[int] = []
    accepted_at: dict[tuple[int, int], int] = {}
    occurrence_at: dict[tuple[int, int], int] = {}
    active_start: int | None = None
    active_end = 0

    for cycle in range(drain_limit):
        for source in by_cycle.get(cycle, ()):
            if pending[source] is not None:
                overrun += 1
            else:
                sequence[source] += 1
                pending[source] = (source << 24) | sequence[source]
                pending_created[source] = cycle
        valid = [item is not None for item in pending]
        payload = [item or 0 for item in pending]
        ready = retire_ready_pattern[cycle % len(retire_ready_pattern)]
        had_work = any(valid) or model.occupancy() > 0
        result = model.step(valid, payload, ready)
        if had_work and active_start is None:
            active_start = cycle
        if had_work:
            active_end = cycle + 1
        if ready and had_work and not result.retire_valid:
            output_bubbles += 1
        for source, did_accept in enumerate(result.source_ready):
            if did_accept:
                key = (source, payload[source])
                accepted_at[key] = cycle
                if pending_created[source] is None:
                    raise AssertionError("accepted source has no occurrence time")
                occurrence_at[key] = pending_created[source]
                accepted += 1
                pending[source] = None
                pending_created[source] = None
        if result.retired is not None:
            key = (result.retired.source, result.retired.payload)
            if key not in accepted_at:
                raise AssertionError(f"phantom or duplicate retire: {key}")
            latencies.append(cycle - accepted_at.pop(key) + 1)
            e2e_latencies.append(cycle - occurrence_at.pop(key) + 1)
            retired += 1
        if cycle > last_offer and not any(pending) and model.occupancy() == 0:
            return RunMetrics(
                offered,
                accepted,
                retired,
                overrun,
                cycle + 1,
                max(0, active_end - (active_start or 0)),
                output_bubbles,
                latencies,
                e2e_latencies,
            )
    raise AssertionError("drain limit exceeded (possible deadlock)")
