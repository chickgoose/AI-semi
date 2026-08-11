#!/usr/bin/env python3
"""Cycle models for the A8 non-crossing frontier fabric and flat RR reference.

This model deliberately contains no request-age or calendar state.  Each lane
owns one contiguous half-open source interval.  Ordered frontier registers are
the only ownership state, and local round-robin pointers provide fairness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


def _one_bits(mask: int, width: int) -> list[int]:
    return [index for index in range(width) if mask & (1 << index)]


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


@dataclass(frozen=True)
class StepResult:
    grant_mask: int
    grant_sources: tuple[int, ...]
    frontiers_before: tuple[int, ...]
    frontiers_after: tuple[int, ...]
    frontier_distance: int
    frontier_reversals: int
    toggle_proxy: int


class FlatKRoundRobin:
    """One rotating global scan selecting at most K distinct sources."""

    def __init__(self, num_sources: int = 16, lanes: int = 4) -> None:
        if not 1 <= lanes <= num_sources:
            raise ValueError("lanes must be in [1, num_sources]")
        self.num_sources = num_sources
        self.lanes = lanes
        self.pointer = 0
        self.previous_grant = 0
        self.state_toggles = 0

    def step(self, request_mask: int, advance: bool = True) -> StepResult:
        old_pointer = self.pointer
        grant_sources: list[int] = []
        if advance:
            for offset in range(self.num_sources):
                source = (self.pointer + offset) % self.num_sources
                if request_mask & (1 << source):
                    grant_sources.append(source)
                    if len(grant_sources) == self.lanes:
                        break
        grant_mask = sum(1 << source for source in grant_sources)
        if grant_sources:
            self.pointer = (grant_sources[-1] + 1) % self.num_sources
        pointer_toggle = _hamming(old_pointer, self.pointer)
        grant_toggle = _hamming(self.previous_grant, grant_mask)
        self.previous_grant = grant_mask
        self.state_toggles += pointer_toggle + grant_toggle
        return StepResult(
            grant_mask=grant_mask,
            grant_sources=tuple(grant_sources),
            frontiers_before=(0, self.num_sources),
            frontiers_after=(0, self.num_sources),
            frontier_distance=0,
            frontier_reversals=0,
            toggle_proxy=pointer_toggle + grant_toggle,
        )

    def proxy(self) -> dict[str, int]:
        address_bits = max(1, (self.num_sources - 1).bit_length())
        return {
            "policy_state_bits": address_bits,
            "request_comparators": self.lanes * self.num_sources,
            "global_request_wire_fanout": self.lanes * self.num_sources,
            "select_depth_proxy": self.num_sources,
        }


class NonCrossingFrontierFabric:
    """K contiguous territories with local RR and bounded frontier movement."""

    def __init__(
        self,
        num_sources: int = 16,
        lanes: int = 4,
        *,
        hysteresis: int = 1,
        cooldown_cycles: int = 2,
        emergency_cycles: int = 3,
        initial_frontiers: Sequence[int] | None = None,
    ) -> None:
        if not 1 <= lanes <= num_sources:
            raise ValueError("lanes must be in [1, num_sources]")
        if hysteresis < 1 or cooldown_cycles < 0 or emergency_cycles < 1:
            raise ValueError("invalid movement parameters")
        self.num_sources = num_sources
        self.lanes = lanes
        self.hysteresis = hysteresis
        self.cooldown_cycles = cooldown_cycles
        self.emergency_cycles = emergency_cycles
        if initial_frontiers is None:
            frontiers = [0]
            for lane in range(1, lanes):
                frontiers.append((lane * num_sources) // lanes)
            frontiers.append(num_sources)
        else:
            frontiers = list(initial_frontiers)
        self._check_frontiers(frontiers)
        self.frontiers = frontiers
        self.rr_pointer = [frontiers[lane] for lane in range(lanes)]
        self.cooldown = [0] * max(0, lanes - 1)
        self.overload_streak = [0] * lanes
        self.last_direction = [0] * max(0, lanes - 1)
        self.reverse_streak = [0] * max(0, lanes - 1)
        self.previous_grant = 0
        self.state_toggles = 0
        self.total_frontier_distance = 0
        self.total_frontier_reversals = 0

    def _check_frontiers(self, frontiers: Sequence[int]) -> None:
        if len(frontiers) != self.lanes + 1:
            raise ValueError("frontier vector must have lanes + 1 entries")
        if frontiers[0] != 0 or frontiers[-1] != self.num_sources:
            raise ValueError("outer frontiers must be 0 and num_sources")
        if any(left >= right for left, right in zip(frontiers, frontiers[1:])):
            raise ValueError("frontiers must be strictly increasing")

    def owner(self, source: int) -> int:
        if not 0 <= source < self.num_sources:
            raise ValueError("source outside fabric")
        for lane in range(self.lanes):
            if self.frontiers[lane] <= source < self.frontiers[lane + 1]:
                return lane
        raise AssertionError("source has no owner")

    def _lane_requests(self, request_mask: int, lane: int) -> list[int]:
        low = self.frontiers[lane]
        high = self.frontiers[lane + 1]
        return [source for source in range(low, high) if request_mask & (1 << source)]

    def _local_grant(self, request_mask: int, lane: int) -> int | None:
        low = self.frontiers[lane]
        high = self.frontiers[lane + 1]
        width = high - low
        start = self.rr_pointer[lane]
        if not low <= start < high:
            start = low
        for offset in range(width):
            source = low + ((start - low + offset) % width)
            if request_mask & (1 << source):
                return source
        return None

    def _movement(self, pressures: Sequence[int]) -> tuple[list[int], int, int]:
        old = list(self.frontiers)
        proposals = [0] * max(0, self.lanes - 1)
        scores = [0] * max(0, self.lanes - 1)
        for boundary in range(self.lanes - 1):
            if self.cooldown[boundary] > 0:
                continue
            left = boundary
            right = boundary + 1
            left_width = old[left + 1] - old[left]
            right_width = old[right + 1] - old[right]
            total_pressure = sum(pressures)
            left_pressure = sum(pressures[: boundary + 1])
            # Compare K*left_count with (boundary+1)*total_count.  Requiring
            # a full K-count unit of error prevents a single indivisible
            # request from bouncing across a frontier.  The calculation is a
            # K-lane pressure chain, not a source prefix/compaction fabric.
            quantile_error = (
                self.lanes * left_pressure - (boundary + 1) * total_pressure
            )
            threshold = self.lanes * self.hysteresis
            left_emergency = (
                self.overload_streak[left] >= self.emergency_cycles
                and pressures[right] == 0
            )
            right_emergency = (
                self.overload_streak[right] >= self.emergency_cycles
                and pressures[left] == 0
            )
            if left_width > 1 and (quantile_error >= threshold or left_emergency):
                # Shrink the overloaded left territory so the right lane takes
                # its highest-address source.  Ownership stays contiguous.
                proposals[boundary] = -1
                scores[boundary] = max(
                    quantile_error, threshold + int(left_emergency)
                )
            elif right_width > 1 and (-quantile_error >= threshold or right_emergency):
                proposals[boundary] = 1
                scores[boundary] = max(
                    -quantile_error, threshold + int(right_emergency)
                )

            direction = proposals[boundary]
            if (
                direction
                and self.last_direction[boundary]
                and direction != self.last_direction[boundary]
            ):
                self.reverse_streak[boundary] = min(
                    self.emergency_cycles, self.reverse_streak[boundary] + 1
                )
                if self.reverse_streak[boundary] < self.emergency_cycles:
                    proposals[boundary] = 0
                    scores[boundary] = 0
            else:
                self.reverse_streak[boundary] = 0

        # Apply proposals in descending imbalance order.  The live neighbor
        # checks make crossing impossible even when adjacent boundaries move.
        order = sorted(range(len(proposals)), key=lambda item: (-scores[item], item))
        new = list(old)
        distance = 0
        reversals = 0
        for boundary in order:
            direction = proposals[boundary]
            if direction == 0:
                continue
            frontier_index = boundary + 1
            candidate = new[frontier_index] + direction
            if not new[frontier_index - 1] < candidate < new[frontier_index + 1]:
                continue
            new[frontier_index] = candidate
            distance += 1
            if self.last_direction[boundary] and self.last_direction[boundary] != direction:
                reversals += 1
            self.last_direction[boundary] = direction
            self.cooldown[boundary] = self.cooldown_cycles
        return new, distance, reversals

    def step(self, request_mask: int, advance: bool = True) -> StepResult:
        old_frontiers = tuple(self.frontiers)
        old_rr = tuple(self.rr_pointer)
        old_cooldown = tuple(self.cooldown)
        old_streak = tuple(self.overload_streak)
        old_reverse_streak = tuple(self.reverse_streak)
        pressures = [len(self._lane_requests(request_mask, lane)) for lane in range(self.lanes)]
        for lane, pressure in enumerate(pressures):
            if pressure > 1:
                self.overload_streak[lane] = min(
                    self.emergency_cycles, self.overload_streak[lane] + 1
                )
            else:
                self.overload_streak[lane] = 0

        grant_sources: list[int] = []
        if advance:
            for lane in range(self.lanes):
                source = self._local_grant(request_mask, lane)
                if source is not None:
                    grant_sources.append(source)
                    low = self.frontiers[lane]
                    high = self.frontiers[lane + 1]
                    self.rr_pointer[lane] = low if source + 1 >= high else source + 1
        grant_mask = sum(1 << source for source in grant_sources)

        for boundary in range(len(self.cooldown)):
            if self.cooldown[boundary] > 0:
                self.cooldown[boundary] -= 1
        new_frontiers, distance, reversals = self._movement(pressures)
        self._check_frontiers(new_frontiers)
        self.frontiers = new_frontiers
        for lane in range(self.lanes):
            low = self.frontiers[lane]
            high = self.frontiers[lane + 1]
            if not low <= self.rr_pointer[lane] < high:
                self.rr_pointer[lane] = low

        state_toggle = 0
        for before, after in zip(old_frontiers[1:-1], self.frontiers[1:-1]):
            state_toggle += _hamming(before, after)
        for before, after in zip(old_rr, self.rr_pointer):
            state_toggle += _hamming(before, after)
        for before, after in zip(old_cooldown, self.cooldown):
            state_toggle += _hamming(before, after)
        for before, after in zip(old_streak, self.overload_streak):
            state_toggle += _hamming(before, after)
        for before, after in zip(old_reverse_streak, self.reverse_streak):
            state_toggle += _hamming(before, after)
        grant_toggle = _hamming(self.previous_grant, grant_mask)
        self.previous_grant = grant_mask
        self.state_toggles += state_toggle + grant_toggle
        self.total_frontier_distance += distance
        self.total_frontier_reversals += reversals
        return StepResult(
            grant_mask=grant_mask,
            grant_sources=tuple(grant_sources),
            frontiers_before=old_frontiers,
            frontiers_after=tuple(self.frontiers),
            frontier_distance=distance,
            frontier_reversals=reversals,
            toggle_proxy=state_toggle + grant_toggle,
        )

    def proxy(self) -> dict[str, int]:
        address_bits = max(1, (self.num_sources - 1).bit_length())
        counter_bits = max(1, self.emergency_cycles.bit_length())
        cooldown_bits = max(1, self.cooldown_cycles.bit_length())
        return {
            "policy_state_bits": (
                (self.lanes - 1) * address_bits
                + self.lanes * address_bits
                + self.lanes * counter_bits
                + (self.lanes - 1) * cooldown_bits
                + (self.lanes - 1) * 2
                + (self.lanes - 1) * counter_bits
            ),
            "request_comparators": self.num_sources + 2 * (self.lanes - 1),
            "global_request_wire_fanout": self.num_sources + 2 * self.lanes,
            "select_depth_proxy": max(
                (self.num_sources + self.lanes - 1) // self.lanes,
                2 * (self.lanes - 1),
            ),
        }


def validate_partition(frontiers: Iterable[int], num_sources: int, lanes: int) -> bool:
    values = tuple(frontiers)
    return (
        len(values) == lanes + 1
        and values[0] == 0
        and values[-1] == num_sources
        and all(left < right for left, right in zip(values, values[1:]))
    )
