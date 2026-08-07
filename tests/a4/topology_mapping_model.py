#!/usr/bin/env python3
"""Generic radix-4 elastic-tree model for A4 placement/mapping studies.

This is candidate-only analysis code.  It deliberately models the same
one-entry, transfer-driven round-robin merge used by the N=16 RTL; it does not
add queues, compaction, tokens, prediction, or workload-dependent RTL state.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass
class Event:
    event_id: int
    source: int
    occurrence: int
    pair_id: int | None = None
    accepted: int | None = None
    delivered: int | None = None
    overrun: bool = False


class MergeNode:
    def __init__(self) -> None:
        self.phase = 0
        self.slot: Event | None = None

    def select(self, children: list[Event | None], ready: bool) -> int | None:
        if self.slot is not None and not ready:
            return None
        for offset in range(4):
            child = (self.phase + offset) & 3
            if children[child] is not None:
                return child
        return None

    def update(self, selected: int | None, children: list[Event | None], ready: bool) -> None:
        if self.slot is not None and not ready:
            assert selected is None
            return
        if selected is None:
            self.slot = None
        else:
            assert children[selected] is not None
            self.slot = children[selected]
            self.phase = (selected + 1) & 3


def ceil_power_of_four(n: int) -> int:
    ports = 1
    while ports < n:
        ports *= 4
    return ports


def tree_levels(n: int) -> int:
    return round(math.log(ceil_power_of_four(n), 4))


def morton_port(x: int, y: int, side: int) -> int:
    """Return root-first base-4 quadrant digits for a square power-of-two grid."""
    bits = (side - 1).bit_length()
    port = 0
    for bit in reversed(range(bits)):
        port = 4 * port + (((y >> bit) & 1) * 2 + ((x >> bit) & 1))
    return port


def inverse_morton(port: int, side: int) -> tuple[int, int]:
    bits = (side - 1).bit_length()
    digits = [0] * bits
    value = port
    for index in reversed(range(bits)):
        digits[index] = value & 3
        value >>= 2
    x = y = 0
    for digit in digits:
        x = (x << 1) | (digit & 1)
        y = (y << 1) | ((digit >> 1) & 1)
    return x, y


def reverse_base4(value: int, levels: int) -> int:
    result = 0
    for _ in range(levels):
        result = result * 4 + value % 4
        value //= 4
    return result


def named_mapping(name: str, n: int, weights: list[int] | None = None) -> list[int]:
    """Map each logical source to one unique padded-tree port."""
    ports = ceil_power_of_four(n)
    levels = tree_levels(n)
    side = math.isqrt(n)
    square = side * side == n and side & (side - 1) == 0

    if name == "identity":
        if square:
            return [morton_port(source % side, source // side, side) for source in range(n)]
        return list(range(n))
    if name == "interleaved":
        return list(range(n))
    if name == "bit_reversed":
        width = (ports - 1).bit_length()
        candidates = [int(f"{value:0{width}b}"[::-1], 2) for value in range(ports)]
        return candidates[:n]
    if name in ("placement_best", "placement_worst"):
        if weights is None or len(weights) != n:
            raise ValueError("placement mapping requires one weight per source")
        ranked_sources = sorted(range(n), key=lambda source: (-weights[source], source))
        port_order = list(range(ports))
        if name == "placement_best":
            port_order.sort(key=lambda port: reverse_base4(port, levels))
        result = [-1] * n
        for source, port in zip(ranked_sources, port_order):
            result[source] = port
        return result
    raise ValueError(f"unknown mapping: {name}")


def percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def mapping_wire_metrics(mapping: list[int]) -> dict[str, float | int]:
    n = len(mapping)
    ports = ceil_power_of_four(n)
    logical_side = math.ceil(math.sqrt(n))
    port_side = math.isqrt(ports)
    spans = []
    for source, port in enumerate(mapping):
        source_xy = (source % logical_side, source // logical_side)
        port_xy = inverse_morton(port, port_side)
        spans.append(abs(source_xy[0] - port_xy[0]) + abs(source_xy[1] - port_xy[1]))
    levels = tree_levels(n)
    return {
        "mapping_wire_span_max": max(spans, default=0),
        "mapping_wire_span_mean": sum(spans) / len(spans),
        "mapping_wire_span_total": sum(spans),
        "tree_edge_span_max": 2 ** max(0, levels - 1),
        "estimated_wire_span_max": max(max(spans, default=0), 2 ** max(0, levels - 1)),
        "merge_fanout_max": 4,
    }


class Radix4Tree:
    def __init__(self, n: int, mapping: list[int]) -> None:
        self.n = n
        self.ports = ceil_power_of_four(n)
        self.levels = tree_levels(n)
        if len(mapping) != n or len(set(mapping)) != n or any(port >= self.ports for port in mapping):
            raise ValueError("mapping must be a unique source-to-port assignment")
        self.mapping = mapping
        self.port_to_source = {port: source for source, port in enumerate(mapping)}
        self.layers = [
            [MergeNode() for _ in range(self.ports // (4 ** (level + 1)))]
            for level in range(self.levels)
        ]
        self.transfer_counts = [[0] * len(layer) for layer in self.layers]
        self.cycles = 0

    def empty(self) -> bool:
        return all(node.slot is None for layer in self.layers for node in layer)

    def step(self, pending: list[Event | None]) -> tuple[list[int], Event | None]:
        root = self.layers[-1][0]
        delivered = root.slot
        selections: list[list[int | None]] = [[None] * len(layer) for layer in self.layers]
        readiness: list[list[bool]] = [[False] * len(layer) for layer in self.layers]
        readiness[-1][0] = True
        children_by_level: list[list[list[Event | None]]] = []

        port_items = [pending[self.port_to_source[port]] if port in self.port_to_source else None
                      for port in range(self.ports)]
        for level, layer in enumerate(self.layers):
            lower_items = port_items if level == 0 else [node.slot for node in self.layers[level - 1]]
            groups = [lower_items[4 * index:4 * index + 4] for index in range(len(layer))]
            children_by_level.append(groups)

        for level in reversed(range(self.levels)):
            for node_index, node in enumerate(self.layers[level]):
                selected = node.select(children_by_level[level][node_index], readiness[level][node_index])
                selections[level][node_index] = selected
                if selected is not None and level > 0:
                    readiness[level - 1][4 * node_index + selected] = True

        accepted: list[int] = []
        for node_index, selected in enumerate(selections[0]):
            if selected is not None:
                port = 4 * node_index + selected
                source = self.port_to_source.get(port)
                assert source is not None
                accepted.append(source)

        for level, layer in enumerate(self.layers):
            for node_index, node in enumerate(layer):
                selected = selections[level][node_index]
                if selected is not None:
                    self.transfer_counts[level][node_index] += 1
                node.update(selected, children_by_level[level][node_index], readiness[level][node_index])
        self.cycles += 1
        return accepted, delivered

    def utilization_metrics(self, stim_cycles: int) -> dict[str, float]:
        result: dict[str, float] = {}
        for level, counts in enumerate(self.transfer_counts):
            per_link = [count / stim_cycles for count in counts]
            result[f"level{level}_link_util_mean"] = sum(per_link) / len(per_link)
            result[f"level{level}_link_util_max"] = max(per_link)
        for level in range(self.levels, 3):
            result[f"level{level}_link_util_mean"] = 0.0
            result[f"level{level}_link_util_max"] = 0.0
        return result


def run_trace(n: int, mapping: list[int], trace: list[Event], stim_cycles: int) -> dict[str, float | int]:
    model = Radix4Tree(n, mapping)
    pending: list[Event | None] = [None] * n
    by_cycle: dict[int, list[Event]] = {}
    delivery_order: list[list[int]] = [[] for _ in range(n)]
    delivered_ids: set[int] = set()
    for event in trace:
        by_cycle.setdefault(event.occurrence, []).append(event)

    cycle = 0
    while cycle < stim_cycles or any(pending) or not model.empty():
        if cycle > stim_cycles + 8 * model.ports:
            raise AssertionError("tree did not drain")
        for event in by_cycle.get(cycle, []):
            if pending[event.source] is None:
                pending[event.source] = event
            else:
                event.overrun = True
        accepted, delivered = model.step(pending)
        for source in accepted:
            event = pending[source]
            assert event is not None
            event.accepted = cycle
            pending[source] = None
        if delivered is not None:
            assert delivered.accepted is not None and delivered.delivered is None
            assert delivered.event_id not in delivered_ids
            delivered.delivered = cycle
            delivered_ids.add(delivered.event_id)
            delivery_order[delivered.source].append(delivered.event_id)
        cycle += 1

    delivered = [event for event in trace if event.delivered is not None]
    assert len(delivered) + sum(event.overrun for event in trace) == len(trace)
    assert len(delivered) == len(delivered_ids)
    for source in range(n):
        expected = [event.event_id for event in trace
                    if event.source == source and event.accepted is not None]
        assert delivery_order[source] == expected
    latencies = [event.delivered - event.occurrence for event in delivered]
    waits = [event.accepted - event.occurrence for event in delivered]
    pairs: dict[int, list[Event]] = {}
    generated_pair_sizes: dict[int, int] = {}
    for event in trace:
        if event.pair_id is not None:
            generated_pair_sizes[event.pair_id] = generated_pair_sizes.get(event.pair_id, 0) + 1
            if event.delivered is not None:
                pairs.setdefault(event.pair_id, []).append(event)
    complete_pair_latencies = [
        max(event.delivered for event in events) - min(event.occurrence for event in events)
        for pair_id, events in pairs.items() if len(events) == generated_pair_sizes[pair_id]
    ]
    result: dict[str, float | int] = {
        "generated": len(trace),
        "accepted": len(delivered),
        "overrun": sum(event.overrun for event in trace),
        "event_p99_latency": percentile(latencies, 0.99),
        "pair_p99_latency": percentile(complete_pair_latencies, 0.99),
        "generated_pairs": len(generated_pair_sizes),
        "complete_pairs": len(complete_pair_latencies),
        "pair_completion_ratio": (
            len(complete_pair_latencies) / len(generated_pair_sizes)
            if generated_pair_sizes else 1.0
        ),
        "max_request_wait": max(waits, default=0),
        "drain_cycles": max(0, cycle - stim_cycles),
    }
    # Include the finite drain in the utilization denominator so no physical
    # link can exceed one transfer/cycle.  All mappings see the same stimulus
    # window; drain length is reported separately.
    result.update(model.utilization_metrics(model.cycles))
    result.update(mapping_wire_metrics(mapping))
    return result


def clone_trace(trace: Iterable[Event]) -> list[Event]:
    return [Event(event.event_id, event.source, event.occurrence, event.pair_id) for event in trace]
