#!/usr/bin/env python3
"""Cycle-accurate address-only models for the A3 passivity experiment.

The candidate is not a FIFO abstraction.  It is a bank of registered transport
slots.  An event moves at most one slot toward a retire endpoint per cycle;
the vacancy produced by that move is the counter-propagating empty-slot credit.
Each lane owns a bounded energy tank.  Forward progress replenishes its tank,
and admitting an event whose home lane is elsewhere spends one quantum.

The raw rule cannot start an idle zero-energy lane.  The escaped rule permits
exactly the stateless bootstrap case ``lane_empty && energy == 0``.  It neither
uses request age nor chooses a maximum-pressure request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class Mode(str, Enum):
    BASELINE = "baseline_elastic_credit"
    RAW = "raw_energy_tank"
    ESCAPE = "empty_lane_bootstrap_escape"


@dataclass(frozen=True)
class Event:
    source: int
    token: int
    occurrence_cycle: int


@dataclass
class Metrics:
    generated: int = 0
    overrun: int = 0
    accepted: int = 0
    retired: int = 0
    state_toggles: int = 0
    bootstrap_admissions: int = 0
    borrowed_admissions: int = 0
    avoidable_idle_cycles: int = 0
    cycles: int = 0
    max_latency: int = 0
    latency_sum: int = 0

    @property
    def mean_latency(self) -> float:
        return self.latency_sum / self.retired if self.retired else 0.0


def bit_toggles(before: int, after: int) -> int:
    return (before ^ after).bit_count()


class CreditFabric:
    """Registered lane fabric with exact source pending and empty-slot credit."""

    def __init__(
        self,
        *,
        sources: int = 16,
        lanes: int = 4,
        depth: int = 2,
        energy_max: int = 3,
        mode: Mode = Mode.ESCAPE,
        pointer_seed: int = 0,
    ) -> None:
        if sources <= 0 or lanes <= 0 or depth <= 0 or energy_max <= 0:
            raise ValueError("all dimensions and energy_max must be positive")
        if sources % lanes:
            raise ValueError("sources must divide evenly across lanes")
        self.sources = sources
        self.lanes = lanes
        self.depth = depth
        self.energy_max = energy_max
        self.mode = mode
        self.source_bits = max(1, (sources - 1).bit_length())
        self.pointer_bits = max(1, (sources - 1).bit_length())
        self.energy_bits = max(1, energy_max.bit_length())

        self.pending: list[Event | None] = [None] * sources
        self.inflight: list[bool] = [False] * sources
        self.slots: list[list[Event | None]] = [
            [None] * depth for _ in range(lanes)
        ]
        self.energy = [0] * lanes
        self.pointer = [pointer_seed % sources for _ in range(lanes)]
        self.next_token = [0] * sources
        self.last_retired = [-1] * sources
        self.metrics = Metrics()

    def home_lane(self, source: int) -> int:
        return source % self.lanes

    def stored_count(self) -> int:
        return sum(event is not None for lane in self.slots for event in lane)

    def pending_count(self) -> int:
        return sum(event is not None for event in self.pending)

    def lane_empty(self, lane: int) -> bool:
        return all(event is None for event in self.slots[lane])

    def quiescent(self) -> bool:
        return self.pending_count() == 0 and self.stored_count() == 0

    def state_bits(self) -> int:
        # Exact source pending belongs to the common ingress seam and is not
        # charged.  Token and occurrence cycle are TB-only identities.
        slot_bits = self.lanes * self.depth * (1 + self.source_bits)
        shared_order_bits = self.sources
        pointer_bits = self.lanes * self.pointer_bits
        energy_bits = 0 if self.mode is Mode.BASELINE else self.lanes * self.energy_bits
        return slot_bits + shared_order_bits + pointer_bits + energy_bits

    def potential(self) -> int:
        # A pending event has one more unit of work than a newly admitted event.
        # A slot at stage k has depth-k units remaining.  Forward progress may
        # replenish at most the unit it dissipates, so with no new occurrence
        # this potential cannot increase.
        pending_work = self.pending_count() * (self.depth + 1)
        slot_work = sum(
            self.depth - stage
            for lane in self.slots
            for stage, event in enumerate(lane)
            if event is not None
        )
        tank = 0 if self.mode is Mode.BASELINE else sum(self.energy)
        return pending_work + slot_work + tank

    def _encoded_state(self) -> int:
        """Pack synthesizable state for a deterministic toggle proxy."""
        value = 0
        shift = 0

        def append(field: int, width: int) -> None:
            nonlocal value, shift
            value |= (field & ((1 << width) - 1)) << shift
            shift += width

        for lane in self.slots:
            for event in lane:
                append(int(event is not None), 1)
                append(0 if event is None else event.source, self.source_bits)
        for flag in self.inflight:
            append(int(flag), 1)
        for pointer in self.pointer:
            append(pointer, self.pointer_bits)
        if self.mode is not Mode.BASELINE:
            for energy in self.energy:
                append(energy, self.energy_bits)
        return value

    def _choose(self, lane: int, candidates: Iterable[int]) -> int | None:
        pool = set(candidates)
        if not pool:
            return None
        start = self.pointer[lane]
        return next(
            source
            for offset in range(self.sources)
            if (source := (start + offset) % self.sources) in pool
        )

    def _assert_invariants(self) -> None:
        assert all(0 <= energy <= self.energy_max for energy in self.energy)
        assert self.metrics.accepted - self.metrics.retired == self.stored_count()
        internal: list[Event] = [
            event for lane in self.slots for event in lane if event is not None
        ]
        assert len({(event.source, event.token) for event in internal}) == len(internal)
        for source in range(self.sources):
            assert self.inflight[source] == any(
                event.source == source for event in internal
            )

    def step(self, occurrence_mask: int, ready_mask: int | None = None) -> dict[str, int]:
        if occurrence_mask < 0 or occurrence_mask >= (1 << self.sources):
            raise ValueError("occurrence mask outside source width")
        if ready_mask is None:
            ready_mask = (1 << self.lanes) - 1
        if ready_mask < 0 or ready_mask >= (1 << self.lanes):
            raise ValueError("ready mask outside lane width")

        cycle = self.metrics.cycles
        potential_before = self.potential()
        state_before = self._encoded_state()

        # The common seam has exactly one pending occurrence per source.
        for source in range(self.sources):
            if occurrence_mask & (1 << source):
                self.metrics.generated += 1
                if self.pending[source] is not None:
                    self.metrics.overrun += 1
                else:
                    token = self.next_token[source]
                    self.next_token[source] += 1
                    self.pending[source] = Event(source, token, cycle)

        old_slots = [lane.copy() for lane in self.slots]
        new_slots = [lane.copy() for lane in self.slots]
        progress = [0] * self.lanes
        retired_events: list[Event] = []

        # Retire first, then move each pre-edge event by at most one stage.
        for lane in range(self.lanes):
            tail = old_slots[lane][-1]
            if tail is not None and ready_mask & (1 << lane):
                new_slots[lane][-1] = None
                retired_events.append(tail)
                progress[lane] += 1
            for stage in range(self.depth - 2, -1, -1):
                event = old_slots[lane][stage]
                if event is None:
                    continue
                destination_was_empty = old_slots[lane][stage + 1] is None
                destination_retired = (
                    stage + 1 == self.depth - 1
                    and old_slots[lane][stage + 1] is not None
                    and bool(ready_mask & (1 << lane))
                )
                if destination_was_empty or destination_retired:
                    new_slots[lane][stage] = None
                    new_slots[lane][stage + 1] = event
                    progress[lane] += 1

        self.slots = new_slots
        for event in retired_events:
            assert event.token > self.last_retired[event.source]
            self.last_retired[event.source] = event.token
            self.inflight[event.source] = False
            self.metrics.retired += 1
            latency = cycle - event.occurrence_cycle + 1
            self.metrics.max_latency = max(self.metrics.max_latency, latency)
            self.metrics.latency_sum += latency

        # Admission is a two-phase matching: home traffic first, then unused
        # lanes may borrow.  Baseline and tank use the same rotating chooser.
        available = {lane for lane in range(self.lanes) if self.slots[lane][0] is None}
        eligible = {
            source
            for source, event in enumerate(self.pending)
            if event is not None and not self.inflight[source]
        }
        admissions: list[tuple[int, int, bool]] = []
        for lane in range(self.lanes):
            if lane not in available:
                continue
            source = self._choose(
                lane, (candidate for candidate in eligible if self.home_lane(candidate) == lane)
            )
            if source is not None:
                admissions.append((lane, source, False))
                available.remove(lane)
                eligible.remove(source)

        spend = [0] * self.lanes
        bootstrap_lanes: set[int] = set()
        for lane in sorted(available):
            source = self._choose(
                lane, (candidate for candidate in eligible if self.home_lane(candidate) != lane)
            )
            if source is None:
                continue
            allowed = self.mode is Mode.BASELINE
            bootstrap = False
            if self.mode is Mode.RAW:
                allowed = self.energy[lane] > spend[lane]
            elif self.mode is Mode.ESCAPE:
                allowed = self.energy[lane] > spend[lane]
                bootstrap = self.energy[lane] == 0 and self.lane_empty(lane)
                allowed = allowed or bootstrap
            if not allowed:
                continue
            if self.mode is not Mode.BASELINE and not bootstrap:
                spend[lane] += 1
            if bootstrap:
                bootstrap_lanes.add(lane)
            admissions.append((lane, source, True))
            eligible.remove(source)

        for lane, source, borrowed in admissions:
            event = self.pending[source]
            assert event is not None and self.slots[lane][0] is None
            self.pending[source] = None
            self.inflight[source] = True
            self.slots[lane][0] = event
            self.pointer[lane] = (source + 1) % self.sources
            self.metrics.accepted += 1
            if borrowed:
                self.metrics.borrowed_admissions += 1
            if lane in bootstrap_lanes:
                self.metrics.bootstrap_admissions += 1

        routable_pending = any(
            event is not None and not self.inflight[source]
            for source, event in enumerate(self.pending)
        )
        empty_ready_lane = any(
            self.slots[lane][0] is None and ready_mask & (1 << lane)
            for lane in range(self.lanes)
        )
        if (
            routable_pending
            and empty_ready_lane
            and not admissions
            and not retired_events
            and sum(progress) == 0
        ):
            self.metrics.avoidable_idle_cycles += 1

        if self.mode is not Mode.BASELINE:
            for lane in range(self.lanes):
                assert spend[lane] <= self.energy[lane]
                self.energy[lane] = min(
                    self.energy_max, self.energy[lane] - spend[lane] + progress[lane]
                )

        self.metrics.cycles += 1
        self.metrics.state_toggles += bit_toggles(state_before, self._encoded_state())
        self._assert_invariants()
        potential_after = self.potential()
        if occurrence_mask == 0:
            assert potential_after <= potential_before, (
                f"potential increased without injection: {potential_before}->{potential_after}"
            )
        return {
            "accepted": len(admissions),
            "retired": len(retired_events),
            "progress": sum(progress),
            "potential_before": potential_before,
            "potential_after": potential_after,
        }

    def drain(self, *, limit: int = 4096, ready_mask: int | None = None) -> int:
        cycles = 0
        while not self.quiescent():
            if cycles >= limit:
                raise RuntimeError("drain limit exceeded")
            self.step(0, ready_mask)
            cycles += 1
        return cycles


def mask_from_sources(sources: Iterable[int]) -> int:
    mask = 0
    for source in sources:
        mask |= 1 << source
    return mask
