#!/usr/bin/env python3
"""Cycle model for an advisory limited-pointer activity directory.

The source-latch pending bitmap is authoritative.  Directory entries, their
valid bits, and the overflow bit are performance hints only.  They may be
mutated arbitrarily without changing the accepted-event truth state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import mean
from typing import Callable, Iterable


@dataclass(frozen=True)
class Event:
    event_id: int
    source: int
    occurrence: int


@dataclass
class RunResult:
    name: str
    source_count: int
    stim_cycles: int
    generated: int = 0
    accepted: int = 0
    delivered: int = 0
    overrun: int = 0
    fixed_window_delivered: int = 0
    drain_cycles: int = 0
    waits: list[int] = field(default_factory=list)
    e2e_latencies: list[int] = field(default_factory=list)
    hit_waits: list[int] = field(default_factory=list)
    fallback_waits: list[int] = field(default_factory=list)
    overflow_waits: list[int] = field(default_factory=list)
    update_to_service: list[int] = field(default_factory=list)
    accepted_ids: list[int] = field(default_factory=list)
    delivered_ids: list[int] = field(default_factory=list)
    source_services: list[int] = field(default_factory=list)
    metrics: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def percentile(values: list[int], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return float(ordered[index])

    @staticmethod
    def average(values: list[int]) -> float:
        return mean(values) if values else 0.0

    @property
    def fairness(self) -> float:
        active = [value for value in self.source_services if value]
        if not active:
            return 1.0
        return sum(active) ** 2 / (len(active) * sum(value * value for value in active))

    def summary(self) -> dict[str, int | float | str]:
        row: dict[str, int | float | str] = {
            "run": self.name,
            "source_count": self.source_count,
            "stim_cycles": self.stim_cycles,
            "generated": self.generated,
            "accepted": self.accepted,
            "delivered": self.delivered,
            "source_overrun": self.overrun,
            "fixed_window_delivered": self.fixed_window_delivered,
            "fixed_window_throughput": self.fixed_window_delivered / self.stim_cycles,
            "drain_cycles": self.drain_cycles,
            "avg_wait": self.average(self.waits),
            "p50_wait": self.percentile(self.waits, 0.50),
            "p95_wait": self.percentile(self.waits, 0.95),
            "p99_wait": self.percentile(self.waits, 0.99),
            "max_wait": max(self.waits, default=0),
            "avg_e2e_latency": self.average(self.e2e_latencies),
            "p95_e2e_latency": self.percentile(self.e2e_latencies, 0.95),
            "p99_e2e_latency": self.percentile(self.e2e_latencies, 0.99),
            "max_e2e_latency": max(self.e2e_latencies, default=0),
            "avg_hit_wait": self.average(self.hit_waits),
            "avg_fallback_wait": self.average(self.fallback_waits),
            "avg_overflow_wait": self.average(self.overflow_waits),
            "avg_update_to_service": self.average(self.update_to_service),
            "hit_accepts": len(self.hit_waits),
            "fallback_accepts": len(self.fallback_waits),
            "overflow_fallback_accepts": len(self.overflow_waits),
            "fairness": self.fairness,
        }
        row.update(self.metrics)
        return row


class FlatScan:
    """Deterministic rotating exact scan used only as the comparison baseline."""

    def __init__(self, source_count: int) -> None:
        self.source_count = source_count
        self.rr = 0
        self.metrics = {
            "hint_hits": 0, "hint_misses": 0, "overflow_triggers": 0,
            "watchdog_triggers": 0, "directory_updates": 0,
            "update_overflows": 0, "fallback_entries": 0,
            "fallback_services": 0, "fallback_recovery_cycles": 0,
            "miss_recovery_cycles": 0, "overflow_recovery_cycles": 0,
            "watchdog_recovery_cycles": 0,
            "select_examined_bits": 0, "truth_guard_bits": 0,
            "tag_comparisons": 0, "state_toggles": 0,
        }

    @property
    def state_bits(self) -> int:
        return max(1, (self.source_count - 1).bit_length())

    @property
    def hint_depth_proxy(self) -> int:
        return max(1, math.ceil(math.log2(self.source_count)))

    @property
    def fallback_stage_depth_proxy(self) -> int:
        return self.hint_depth_proxy

    def choose(self, pending_mask: int, cycle: int) -> tuple[int | None, str]:
        del cycle
        self.metrics["select_examined_bits"] += self.source_count
        if not pending_mask:
            return None, "idle"
        old_rr = self.rr
        for offset in range(self.source_count):
            source = (self.rr + offset) % self.source_count
            if pending_mask & (1 << source):
                self.rr = (source + 1) % self.source_count
                self.metrics["state_toggles"] += (old_rr ^ self.rr).bit_count()
                return source, "flat"
        raise AssertionError("nonempty exact bitmap produced no flat winner")

    def finish(self, post_mask: int, cycle: int) -> None:
        del post_mask, cycle


Mutation = Callable[["ActivityDirectory", int, int], None]


class ActivityDirectory:
    """Limited pointer hint with exact, bounded, one-cycle-entry fallback.

    Exact fallback has a one-cycle entry penalty and then sustains one exact
    service per cycle.  This models a registered fallback scan rather than
    claiming the flat scan disappeared from the design.
    """

    def __init__(self, source_count: int, pointers: int, *, watchdog_limit: int = 16,
                 mutation: Mutation | None = None) -> None:
        if pointers < 1 or pointers > source_count:
            raise ValueError("pointer count must be in [1, source_count]")
        self.source_count = source_count
        self.pointers = pointers
        self.watchdog_limit = watchdog_limit
        self.mutation = mutation
        self.entries = [0] * pointers
        self.valid = [False] * pointers
        self.insert_cycle = [-1] * pointers
        self.overflow = False
        self.fallback_mode = False
        self.fallback_reason = ""
        self.fallback_trigger_cycle = -1
        self.rr = 0
        self.hint_services_since_exact = 0
        self.prev_pending = 0
        self.metrics = {
            "hint_hits": 0, "hint_misses": 0, "overflow_triggers": 0,
            "watchdog_triggers": 0, "directory_updates": 0,
            "update_overflows": 0, "fallback_entries": 0,
            "fallback_services": 0, "fallback_recovery_cycles": 0,
            "miss_recovery_cycles": 0, "overflow_recovery_cycles": 0,
            "watchdog_recovery_cycles": 0,
            "select_examined_bits": 0, "truth_guard_bits": 0,
            "tag_comparisons": 0, "state_toggles": 0,
        }

    @property
    def state_bits(self) -> int:
        tag_bits = max(1, (self.source_count - 1).bit_length())
        watchdog_bits = max(1, self.watchdog_limit.bit_length())
        return (self.source_count + self.pointers * (tag_bits + 1) + 1 + 1 +
                tag_bits + watchdog_bits)

    @property
    def hint_depth_proxy(self) -> int:
        return max(1, math.ceil(math.log2(self.pointers)) + 1)

    @property
    def fallback_stage_depth_proxy(self) -> int:
        return max(1, math.ceil(math.log2(self.source_count)) // 2 + 1)

    def _encoded_state(self) -> int:
        tag_bits = max(1, (self.source_count - 1).bit_length())
        value = 0
        shift = 0
        for valid, entry in zip(self.valid, self.entries):
            value |= int(valid) << shift
            shift += 1
            value |= (entry & ((1 << tag_bits) - 1)) << shift
            shift += tag_bits
        value |= int(self.overflow) << shift
        shift += 1
        value |= int(self.fallback_mode) << shift
        shift += 1
        value |= self.rr << shift
        shift += tag_bits
        value |= self.hint_services_since_exact << shift
        shift += max(1, self.watchdog_limit.bit_length())
        value |= self.prev_pending << shift
        return value

    def _observe_new_pending(self, pending_mask: int, cycle: int) -> None:
        rising = pending_mask & ~self.prev_pending
        while rising:
            low = rising & -rising
            source = low.bit_length() - 1
            rising ^= low
            self.metrics["tag_comparisons"] += self.pointers
            present = any(valid and entry == source
                          for valid, entry in zip(self.valid, self.entries))
            if present:
                continue
            try:
                slot = self.valid.index(False)
            except ValueError:
                self.overflow = True
                self.metrics["update_overflows"] += 1
            else:
                self.entries[slot] = source
                self.valid[slot] = True
                self.insert_cycle[slot] = cycle
                self.metrics["directory_updates"] += 1

    def _exact_winner(self, pending_mask: int) -> int:
        self.metrics["select_examined_bits"] += self.source_count
        for offset in range(self.source_count):
            source = (self.rr + offset) % self.source_count
            if pending_mask & (1 << source):
                return source
        raise AssertionError("nonempty authoritative bitmap produced no exact winner")

    def _rebuild(self, pending_mask: int, cycle: int) -> None:
        self.valid = [False] * self.pointers
        self.insert_cycle = [-1] * self.pointers
        slot = 0
        for offset in range(self.source_count):
            source = (self.rr + offset) % self.source_count
            if pending_mask & (1 << source):
                if slot < self.pointers:
                    self.entries[slot] = source
                    self.valid[slot] = True
                    self.insert_cycle[slot] = cycle
                    slot += 1
                else:
                    break
        self.overflow = pending_mask.bit_count() > self.pointers

    def _enter_fallback(self, reason: str, cycle: int) -> tuple[None, str]:
        self.fallback_mode = True
        self.fallback_reason = reason
        self.fallback_trigger_cycle = cycle
        self.metrics["fallback_entries"] += 1
        if reason == "overflow":
            self.metrics["overflow_triggers"] += 1
        elif reason == "watchdog":
            self.metrics["watchdog_triggers"] += 1
        else:
            self.metrics["hint_misses"] += 1
        return None, reason + "_entry"

    def choose(self, pending_mask: int, cycle: int) -> tuple[int | None, str]:
        old_state = self._encoded_state()
        self._observe_new_pending(pending_mask, cycle)
        if self.mutation is not None:
            self.mutation(self, cycle, pending_mask)

        if self.fallback_mode:
            if not pending_mask:
                self.fallback_mode = False
                self.fallback_reason = ""
                self.overflow = False
                self._rebuild(0, cycle)
                selected: tuple[int | None, str] = (None, "idle")
            else:
                source = self._exact_winner(pending_mask)
                self.metrics["fallback_services"] += 1
                if self.fallback_trigger_cycle >= 0:
                    recovery = cycle - self.fallback_trigger_cycle
                    self.metrics["fallback_recovery_cycles"] += recovery
                    self.metrics[self.fallback_reason + "_recovery_cycles"] += recovery
                    self.fallback_trigger_cycle = -1
                selected = (source, "fallback_" + self.fallback_reason)
        else:
            if not pending_mask:
                # Exact OR guard is the only legal proof of empty when hints are stale.
                self.metrics["truth_guard_bits"] += self.source_count
                selected = (None, "idle")
            elif self.overflow:
                selected = self._enter_fallback("overflow", cycle)
            elif self.hint_services_since_exact >= self.watchdog_limit:
                selected = self._enter_fallback("watchdog", cycle)
            else:
                winner = None
                checked = 0
                for valid, entry in zip(self.valid, self.entries):
                    checked += 1
                    if valid and 0 <= entry < self.source_count:
                        if pending_mask & (1 << entry):
                            winner = entry
                            break
                self.metrics["select_examined_bits"] += checked
                if winner is not None:
                    self.metrics["hint_hits"] += 1
                    selected = (winner, "hint")
                else:
                    # The exact OR reduction detects false-empty without trusting hints.
                    self.metrics["truth_guard_bits"] += self.source_count
                    selected = self._enter_fallback("miss", cycle)

        self.metrics["state_toggles"] += (old_state ^ self._encoded_state()).bit_count()
        return selected

    def service_metadata(self, source: int, category: str, cycle: int) -> int | None:
        if category != "hint":
            return None
        candidates = [self.insert_cycle[index] for index in range(self.pointers)
                      if self.valid[index] and self.entries[index] == source]
        valid = [value for value in candidates if value >= 0]
        return cycle - min(valid) if valid else None

    def finish(self, source: int | None, post_mask: int, category: str, cycle: int) -> None:
        old_state = self._encoded_state()
        if source is not None:
            old_rr = self.rr
            self.rr = (source + 1) % self.source_count
            self.metrics["state_toggles"] += (old_rr ^ self.rr).bit_count()
            for index in range(self.pointers):
                if self.valid[index] and self.entries[index] == source:
                    self.valid[index] = False
                    self.insert_cycle[index] = -1
            if category == "hint":
                self.hint_services_since_exact += 1
            else:
                self.hint_services_since_exact = 0
                if post_mask.bit_count() <= self.pointers:
                    self.fallback_mode = False
                    self.fallback_reason = ""
                    self._rebuild(post_mask, cycle)
                else:
                    self.overflow = True
        self.prev_pending = post_mask
        self.metrics["state_toggles"] += (old_state ^ self._encoded_state()).bit_count()


def simulate(name: str, events: Iterable[Event], source_count: int, stim_cycles: int,
             policy: FlatScan | ActivityDirectory, *, drain_limit: int | None = None) -> RunResult:
    schedule: dict[int, list[Event]] = {}
    for event in events:
        if not 0 <= event.source < source_count:
            raise ValueError(f"event source {event.source} outside N={source_count}")
        schedule.setdefault(event.occurrence, []).append(event)
    latches: list[Event | None] = [None] * source_count
    result = RunResult(name=name, source_count=source_count, stim_cycles=stim_cycles,
                       source_services=[0] * source_count)
    pending_deliveries: list[tuple[int, Event, int, str]] = []
    limit = drain_limit or (stim_cycles + source_count * 256 + len(schedule) * 4 + 1024)
    cycle = 0
    while cycle < limit:
        still_pending_delivery: list[tuple[int, Event, int, str]] = []
        for delivery_cycle, event, accepted_cycle, category in pending_deliveries:
            if delivery_cycle <= cycle:
                result.delivered += 1
                result.delivered_ids.append(event.event_id)
                latency = delivery_cycle - event.occurrence
                result.e2e_latencies.append(latency)
                if delivery_cycle < stim_cycles:
                    result.fixed_window_delivered += 1
            else:
                still_pending_delivery.append((delivery_cycle, event, accepted_cycle, category))
        pending_deliveries = still_pending_delivery

        for event in schedule.get(cycle, []):
            result.generated += 1
            if latches[event.source] is not None:
                result.overrun += 1
            else:
                latches[event.source] = event

        pending_mask = sum((1 << source) for source, event in enumerate(latches)
                           if event is not None)
        source, category = policy.choose(pending_mask, cycle)
        update_latency = None
        if source is not None and isinstance(policy, ActivityDirectory):
            update_latency = policy.service_metadata(source, category, cycle)
        if source is not None:
            event = latches[source]
            if event is None:
                raise AssertionError("selector chose a false pending source")
            latches[source] = None
            result.accepted += 1
            result.accepted_ids.append(event.event_id)
            result.source_services[source] += 1
            wait = cycle - event.occurrence
            result.waits.append(wait)
            if category == "hint":
                result.hit_waits.append(wait)
                if update_latency is not None:
                    result.update_to_service.append(update_latency)
            elif category.startswith("fallback"):
                result.fallback_waits.append(wait)
                if "overflow" in category:
                    result.overflow_waits.append(wait)
            pending_deliveries.append((cycle + 1, event, cycle, category))
        post_mask = sum((1 << index) for index, event in enumerate(latches)
                        if event is not None)
        if isinstance(policy, ActivityDirectory):
            policy.finish(source, post_mask, category, cycle)
        else:
            policy.finish(post_mask, cycle)

        if cycle >= stim_cycles and not post_mask and not pending_deliveries:
            result.drain_cycles = cycle - stim_cycles
            break
        cycle += 1
    else:
        raise RuntimeError(f"drain timeout for {name}: pending={sum(x is not None for x in latches)}")

    if result.generated != result.accepted + result.overrun:
        raise AssertionError("source-latch conservation failed")
    if result.accepted != result.delivered:
        raise AssertionError("accepted/delivered conservation failed")
    if sorted(result.accepted_ids) != sorted(result.delivered_ids):
        raise AssertionError("loss, duplicate, or phantom delivery")
    result.metrics = dict(policy.metrics)
    result.metrics["policy_state_bits"] = policy.state_bits
    result.metrics["hint_depth_proxy"] = policy.hint_depth_proxy
    result.metrics["fallback_stage_depth_proxy"] = policy.fallback_stage_depth_proxy
    return result


def mutation(mode: str) -> Mutation:
    """Return deterministic hint-only corruption used by mutation tests."""
    def apply(directory: ActivityDirectory, cycle: int, pending_mask: int) -> None:
        if mode == "false_empty":
            directory.valid = [False] * directory.pointers
            directory.overflow = False
        elif mode == "out_of_range":
            directory.valid[0] = True
            directory.entries[0] = directory.source_count + 3
            directory.overflow = False
        elif mode == "duplicate_hot":
            if pending_mask:
                source = (pending_mask & -pending_mask).bit_length() - 1
                directory.valid = [True] * directory.pointers
                directory.entries = [source] * directory.pointers
                directory.overflow = False
        elif mode == "false_overflow_clear":
            directory.overflow = False
        elif mode == "rotating_corrupt":
            directory.valid = [True] * directory.pointers
            directory.entries = [((cycle * 3) + index + directory.source_count + 1)
                                 for index in range(directory.pointers)]
            directory.overflow = bool(cycle & 1)
        elif mode == "stale_valid":
            directory.valid = [True] * directory.pointers
            directory.entries = [0] * directory.pointers
            directory.overflow = False
        else:
            raise ValueError(f"unknown mutation mode {mode}")
    return apply
