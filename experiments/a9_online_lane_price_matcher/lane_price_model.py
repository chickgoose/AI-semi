#!/usr/bin/env python3
"""Cycle model for the A9 online primal-dual lane-price matcher.

The matcher is deliberately not a max-pressure or age arbiter.  A source emits
one proposal to one of two statically legal lanes.  A lane resolves only its
fixed adjacency list.  Lane prices move by one from observed FIFO occupancy or
an actual output stall; they never inspect source age, deficit, or request
weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Event:
    occurrence: int
    event_id: int
    source: int


@dataclass
class Metrics:
    generated: int = 0
    overrun: int = 0
    accepted: int = 0
    delivered: int = 0
    accepted_by_source: list[int] = field(default_factory=list)
    delivered_by_source: list[int] = field(default_factory=list)
    occurrence_latencies: list[int] = field(default_factory=list)
    price_bit_toggles: int = 0
    price_updates: int = 0
    proposal_rejects: int = 0
    escape_entries: int = 0
    max_escape_wait: int = 0
    measured_delivered: int = 0

    @staticmethod
    def percentile(values: Sequence[int], percentile: int) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        rank = max(1, math.ceil(percentile * len(ordered) / 100))
        return ordered[rank - 1]

    def jain_service_ratio(self, generated_by_source: Sequence[int]) -> float:
        ratios = [
            delivered / generated
            for delivered, generated in zip(self.delivered_by_source, generated_by_source)
            if generated
        ]
        if not ratios or sum(value * value for value in ratios) == 0:
            return 1.0
        return sum(ratios) ** 2 / (len(ratios) * sum(value * value for value in ratios))


def legal_lanes(source: int, sources: int, lanes: int) -> tuple[int, int]:
    """Return the fixed, balanced degree-two source adjacency.

    Home lanes are interleaved by low source bits.  The alternate offset uses
    the source quotient, so affine and bit-reverse address permutations cannot
    collapse every source onto the same lane pair.
    """
    if lanes < 2 or sources % lanes:
        raise ValueError("lanes must divide sources and be at least two")
    home = source % lanes
    group = source // lanes
    alternate = (home + 1 + (group % (lanes - 1))) % lanes
    return home, alternate


def adjacency(sources: int, lanes: int) -> list[list[int]]:
    result = [[] for _ in range(lanes)]
    for source in range(sources):
        for lane in legal_lanes(source, sources, lanes):
            result[lane].append(source)
    return result


def bit_toggles(before: int, after: int) -> int:
    return (before ^ after).bit_count()


class BaseFabric:
    """Common finite source-latch and lane-FIFO measurement boundary."""

    def __init__(self, sources: int = 16, lanes: int = 4, lane_depth: int = 2) -> None:
        if sources <= 0 or lanes <= 0 or lane_depth <= 0 or sources % lanes:
            raise ValueError("invalid fabric dimensions")
        self.sources = sources
        self.lanes = lanes
        self.lane_depth = lane_depth
        self.pending: list[Event | None] = [None] * sources
        self.queues: list[list[Event]] = [[] for _ in range(lanes)]
        self.route_lock: list[int | None] = [None] * sources
        self.outstanding = [0] * sources
        self.generated_by_source = [0] * sources
        self.metrics = Metrics(
            accepted_by_source=[0] * sources,
            delivered_by_source=[0] * sources,
        )
        self.cycle = 0

    def _arrive(self, events: Iterable[Event]) -> None:
        for event in events:
            if event.source < 0 or event.source >= self.sources:
                raise AssertionError("event source outside fabric")
            self.metrics.generated += 1
            self.generated_by_source[event.source] += 1
            if self.pending[event.source] is not None:
                self.metrics.overrun += 1
            else:
                self.pending[event.source] = event

    def _retire(self, ready: Sequence[bool], measured: bool) -> None:
        if len(ready) != self.lanes:
            raise ValueError("ready width mismatch")
        for lane in range(self.lanes):
            if ready[lane] and self.queues[lane]:
                event = self.queues[lane].pop(0)
                source = event.source
                self.metrics.delivered += 1
                self.metrics.delivered_by_source[source] += 1
                self.metrics.occurrence_latencies.append(self.cycle - event.occurrence)
                if measured:
                    self.metrics.measured_delivered += 1
                if self.outstanding[source] <= 0:
                    raise AssertionError("retirement underflow")
                self.outstanding[source] -= 1
                if self.outstanding[source] == 0:
                    self.route_lock[source] = None

    def _accept(self, grants: Sequence[tuple[int, int]]) -> None:
        used_sources: set[int] = set()
        used_lanes: set[int] = set()
        for source, lane in grants:
            if source in used_sources or lane in used_lanes:
                raise AssertionError("matching is not one-source/one-lane")
            if self.pending[source] is None:
                raise AssertionError("grant without pending event")
            if len(self.queues[lane]) >= self.lane_depth:
                raise AssertionError("grant into full lane")
            lock = self.route_lock[source]
            if lock is not None and lock != lane:
                raise AssertionError("route-lock violation")
            if lane not in legal_lanes(source, self.sources, self.lanes):
                raise AssertionError("illegal source/lane match")
            event = self.pending[source]
            assert event is not None
            self.pending[source] = None
            self.queues[lane].append(event)
            self.metrics.accepted += 1
            self.metrics.accepted_by_source[source] += 1
            if self.outstanding[source] == 0:
                self.route_lock[source] = lane
            self.outstanding[source] += 1
            used_sources.add(source)
            used_lanes.add(lane)

    def step(self, events: Iterable[Event], ready: Sequence[bool], measured: bool = True) -> None:
        self._arrive(events)
        self._retire(ready, measured)
        self._accept(self._match(ready))
        self._post_step(ready)
        self._check_conservation()
        self.cycle += 1

    def _match(self, ready: Sequence[bool]) -> list[tuple[int, int]]:
        raise NotImplementedError

    def _post_step(self, ready: Sequence[bool]) -> None:
        del ready

    def _check_conservation(self) -> None:
        stored = sum(event is not None for event in self.pending) + sum(
            len(queue) for queue in self.queues
        )
        if self.metrics.accepted - self.metrics.delivered != sum(self.outstanding):
            raise AssertionError("accepted/retired outstanding mismatch")
        if self.metrics.generated - self.metrics.overrun - self.metrics.delivered != stored:
            raise AssertionError("generated event conservation mismatch")
        for source in range(self.sources):
            queued = sum(event.source == source for queue in self.queues for event in queue)
            if queued != self.outstanding[source]:
                raise AssertionError("per-source outstanding mismatch")

    def drained(self) -> bool:
        return all(event is None for event in self.pending) and not any(self.queues)

    def run(
        self,
        events_by_cycle: dict[int, list[Event]],
        stim_cycles: int,
        ready_fn=None,
        drain_limit: int = 100000,
    ) -> Metrics:
        ready_fn = ready_fn or (lambda cycle: [True] * self.lanes)
        for cycle in range(stim_cycles):
            if self.cycle != cycle:
                raise AssertionError("cycle discontinuity")
            self.step(events_by_cycle.get(cycle, ()), ready_fn(cycle), measured=True)
        drain_cycles = 0
        while not self.drained() and drain_cycles < drain_limit:
            self.step((), [True] * self.lanes, measured=False)
            drain_cycles += 1
        if not self.drained():
            raise AssertionError("drain timeout")
        return self.metrics


class LanePriceMatcher(BaseFabric):
    """One-proposal online primal-dual matcher with bounded local escape."""

    def __init__(
        self,
        sources: int = 16,
        lanes: int = 4,
        lane_depth: int = 2,
        price_bits: int = 3,
        reject_bits: int = 2,
        price_enabled: bool = True,
    ) -> None:
        super().__init__(sources, lanes, lane_depth)
        if price_bits <= 0 or reject_bits <= 0:
            raise ValueError("price and reject widths must be positive")
        self.price_bits = price_bits
        self.reject_bits = reject_bits
        self.price_enabled = price_enabled
        self.price_max = (1 << price_bits) - 1
        self.reject_max = (1 << reject_bits) - 1
        self.prices = [0] * lanes
        self.rejects = [0] * sources
        self.escape_lane: list[int | None] = [None] * sources
        self.escape_wait = [0] * sources
        self.incoming = adjacency(sources, lanes)
        self.tie_cursor = [0] * lanes
        self.last_proposal: list[int | None] = [None] * sources

    def _normal_proposal(self, source: int) -> int:
        legal = legal_lanes(source, self.sources, self.lanes)
        first, second = legal
        if self.price_enabled and self.prices[first] < self.prices[second]:
            return first
        if self.price_enabled and self.prices[second] < self.prices[first]:
            return second
        # A fixed, source-local dual tie: no age, request vector, or max scan.
        return legal[(source // self.lanes) & 1]

    def proposal(self, source: int) -> int:
        if self.route_lock[source] is not None:
            return int(self.route_lock[source])
        if self.escape_lane[source] is not None:
            return int(self.escape_lane[source])
        return self._normal_proposal(source)

    def _lane_winner(self, lane: int, proposers: set[int]) -> int | None:
        incoming = self.incoming[lane]
        if not incoming:
            return None
        start = self.tie_cursor[lane]
        for offset in range(len(incoming)):
            source = incoming[(start + offset) % len(incoming)]
            if source in proposers:
                return source
        return None

    def _match(self, ready: Sequence[bool]) -> list[tuple[int, int]]:
        del ready
        proposals: list[set[int]] = [set() for _ in range(self.lanes)]
        proposed_lane: dict[int, int] = {}
        for source, event in enumerate(self.pending):
            if event is None:
                self.last_proposal[source] = None
                continue
            lane = self.proposal(source)
            self.last_proposal[source] = lane
            proposed_lane[source] = lane
            proposals[lane].add(source)

        grants: list[tuple[int, int]] = []
        granted: set[int] = set()
        for lane in range(self.lanes):
            if len(self.queues[lane]) >= self.lane_depth:
                continue
            winner = self._lane_winner(lane, proposals[lane])
            if winner is not None:
                grants.append((winner, lane))
                granted.add(winner)
                index = self.incoming[lane].index(winner)
                self.tie_cursor[lane] = (index + 1) % len(self.incoming[lane])

        for source, lane in proposed_lane.items():
            if source in granted:
                self.rejects[source] = 0
                if self.escape_lane[source] is not None:
                    self.metrics.max_escape_wait = max(
                        self.metrics.max_escape_wait, self.escape_wait[source]
                    )
                self.escape_lane[source] = None
                self.escape_wait[source] = 0
                continue
            self.metrics.proposal_rejects += 1
            if self.escape_lane[source] is not None:
                self.escape_wait[source] += 1
                continue
            if self.rejects[source] == self.reject_max:
                first, second = legal_lanes(source, self.sources, self.lanes)
                self.escape_lane[source] = second if lane == first else first
                self.escape_wait[source] = 0
                self.rejects[source] = 0
                self.metrics.escape_entries += 1
            else:
                self.rejects[source] += 1
        return grants

    def _post_step(self, ready: Sequence[bool]) -> None:
        if not self.price_enabled:
            return
        for lane in range(self.lanes):
            before = self.prices[lane]
            stalled = bool(self.queues[lane]) and not ready[lane]
            full = len(self.queues[lane]) == self.lane_depth
            empty_available = not self.queues[lane] and ready[lane]
            if stalled or full:
                self.prices[lane] = min(self.price_max, before + 1)
            elif empty_available:
                self.prices[lane] = max(0, before - 1)
            after = self.prices[lane]
            if after != before:
                self.metrics.price_updates += 1
                self.metrics.price_bit_toggles += bit_toggles(before, after)

    def control_state_bits(self) -> int:
        lane_cursor_bits = max(1, math.ceil(math.log2(max(map(len, self.incoming)))))
        outstanding_bits = math.ceil(math.log2(self.lane_depth * self.lanes + 1))
        # Escape and route-lock each store valid plus one bit selecting one of
        # the source's two fixed legal lanes, not an arbitrary global lane ID.
        return (
            self.lanes * (self.price_bits + lane_cursor_bits)
            + self.sources * (
                self.reject_bits + 2 + 2 + outstanding_bits
            )
        )

    def comparator_depth_proxy(self) -> int:
        source_price_compare = 1
        lane_local_select = math.ceil(math.log2(max(map(len, self.incoming))))
        return source_price_compare + lane_local_select


class FlatRoundRobin(BaseFabric):
    """Central one-grant/cycle round-robin comparison baseline."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cursor = 0

    def _match(self, ready: Sequence[bool]) -> list[tuple[int, int]]:
        del ready
        for offset in range(self.sources):
            source = (self.cursor + offset) % self.sources
            if self.pending[source] is None:
                continue
            lanes = (
                (self.route_lock[source],)
                if self.route_lock[source] is not None
                else legal_lanes(source, self.sources, self.lanes)
            )
            for lane in lanes:
                assert lane is not None
                if len(self.queues[lane]) < self.lane_depth:
                    self.cursor = (source + 1) % self.sources
                    return [(source, lane)]
        return []

    def control_state_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.sources)))

    def comparator_depth_proxy(self) -> int:
        return self.sources


class ExactKGrant(BaseFabric):
    """Central maximum-cardinality K-grant reference with a global source view."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cursor = 0

    def _match(self, ready: Sequence[bool]) -> list[tuple[int, int]]:
        del ready
        available = {
            lane for lane in range(self.lanes)
            if len(self.queues[lane]) < self.lane_depth
        }
        candidates = [
            (self.cursor + offset) % self.sources
            for offset in range(self.sources)
            if self.pending[(self.cursor + offset) % self.sources] is not None
        ]
        lane_owner: dict[int, int] = {}

        def augment(source: int, visited: set[int]) -> bool:
            legal = (
                (self.route_lock[source],)
                if self.route_lock[source] is not None
                else legal_lanes(source, self.sources, self.lanes)
            )
            for lane in legal:
                assert lane is not None
                if lane not in available or lane in visited:
                    continue
                visited.add(lane)
                owner = lane_owner.get(lane)
                if owner is None or augment(owner, visited):
                    lane_owner[lane] = source
                    return True
            return False

        for source in candidates:
            augment(source, set())
        grants = [(source, lane) for lane, source in sorted(lane_owner.items())]
        if grants:
            self.cursor = (grants[-1][0] + 1) % self.sources
        return grants

    def control_state_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.sources)))

    def comparator_depth_proxy(self) -> int:
        return math.ceil(math.log2(self.sources)) * self.lanes


def events_from_rows(rows: Iterable[dict[str, object]]) -> tuple[dict[int, list[Event]], int]:
    by_cycle: dict[int, list[Event]] = {}
    maximum = 0
    for row in rows:
        event = Event(
            occurrence=int(row["occurrence_cycle"]),
            event_id=int(row["tb_only_event_id"]),
            source=int(row["logical_source"]),
        )
        by_cycle.setdefault(event.occurrence, []).append(event)
        maximum = max(maximum, event.occurrence)
    return by_cycle, maximum
