#!/usr/bin/env python3
"""Architecture-neutral, deterministic clean-slate AER trace generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
GENERATOR_VERSION = "2.0"
WORKLOADS = (
    "basic_sparse",
    "basic_simultaneous",
    "uniform",
    "elephant_mouse",
    "global_fanin",
    "local_cluster",
    "distributed_burst",
    "retrigger",
    "timing_pair",
    "backpressure_shock",
    "rate_shape",
    "matched_spatial",
    "moving_hotspot",
    "rotating_victim",
    "phase_transition",
)
EVENT_FIELDS = (
    "occurrence_cycle",
    "tb_only_event_id",
    "logical_source",
    "x",
    "y",
    "polarity",
    "event_type",
    "relation_id",
    "relation_role",
    "deadline",
)
MASK64 = (1 << 64) - 1


class ManifestError(ValueError):
    """Raised when a benchmark manifest is invalid."""


class SplitMix64:
    """Small fixed PRNG whose output is independent of Python's random module."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK64

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        value = self.state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
        return (value ^ (value >> 31)) & MASK64

    def randbelow(self, bound: int) -> int:
        if bound <= 0:
            raise ValueError("bound must be positive")
        limit = (1 << 64) - ((1 << 64) % bound)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % bound

    def probability(self, probability: Decimal) -> bool:
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        threshold = int(probability * Decimal(1 << 64))
        return self.next_u64() < threshold


@dataclass(frozen=True)
class RunConfig:
    name: str
    workload: str
    seed: int
    width: int
    height: int
    load: Decimal
    stim_cycles: int
    parameters: dict[str, Any]
    sink: dict[str, Any]

    @property
    def source_count(self) -> int:
        return self.width * self.height


class TraceBuilder:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.rng = SplitMix64(config.seed)
        self._sequence = 0
        self._events: list[
            tuple[int, int, int, int, int, int, str, int | None, str | None, int]
        ] = []
        self._source_permutation = self._build_source_permutation()
        self._source_cycle_keys: set[tuple[int, int]] = set()

    def _build_source_permutation(self) -> list[int]:
        count = self.config.source_count
        raw = self.config.parameters.get("source_permutation", {"mode": "identity"})
        if isinstance(raw, str):
            raw = {"mode": raw}
        if not isinstance(raw, dict):
            raise ManifestError(
                f"{self.config.name}.parameters.source_permutation must be a string or object"
            )
        mode = raw.get("mode", "identity")
        if mode == "identity":
            result = list(range(count))
        elif mode == "affine":
            multiplier = raw.get("multiplier", 1)
            offset = raw.get("offset", 0)
            if any(isinstance(value, bool) or not isinstance(value, int)
                   for value in (multiplier, offset)):
                raise ManifestError(
                    f"{self.config.name}.source_permutation affine values must be integers"
                )
            if math.gcd(multiplier, count) != 1:
                raise ManifestError(
                    f"{self.config.name}.source_permutation multiplier must be coprime to source count"
                )
            result = [((multiplier * source) + offset) % count for source in range(count)]
        elif mode in {"mirror_x", "mirror_y", "rotate_180", "transpose"}:
            if mode == "transpose" and self.config.width != self.config.height:
                raise ManifestError(
                    f"{self.config.name}.source_permutation transpose requires square geometry"
                )
            result = []
            for source in range(count):
                x, y = self.xy_for_source(source)
                if mode == "mirror_x":
                    x = self.config.width - 1 - x
                elif mode == "mirror_y":
                    y = self.config.height - 1 - y
                elif mode == "rotate_180":
                    x = self.config.width - 1 - x
                    y = self.config.height - 1 - y
                else:
                    x, y = y, x
                result.append(y * self.config.width + x)
        elif mode == "bit_reverse":
            if count & (count - 1):
                raise ManifestError(
                    f"{self.config.name}.source_permutation bit_reverse requires a power-of-two source count"
                )
            bits = max(1, (count - 1).bit_length())
            result = [int(f"{source:0{bits}b}"[::-1], 2) for source in range(count)]
        else:
            raise ManifestError(
                f"{self.config.name}.source_permutation has unsupported mode {mode!r}"
            )
        if sorted(result) != list(range(count)):
            raise ManifestError(f"{self.config.name}.source_permutation must be bijective")
        return result

    def xy_for_source(self, source: int) -> tuple[int, int]:
        return source % self.config.width, source // self.config.width

    def random_source(self, excluded: int | None = None) -> int:
        if excluded is None or self.config.source_count == 1:
            return self.rng.randbelow(self.config.source_count)
        candidate = self.rng.randbelow(self.config.source_count - 1)
        return candidate + (1 if candidate >= excluded else 0)

    def unique_sources(
        self, count: int, *, population: Iterable[int] | None = None
    ) -> list[int]:
        choices = list(range(self.config.source_count) if population is None else population)
        if count > len(choices):
            raise ManifestError(
                f"{self.config.name} requests {count} simultaneous unique sources "
                f"from a population of {len(choices)}"
            )
        for index in range(count):
            selected = index + self.rng.randbelow(len(choices) - index)
            choices[index], choices[selected] = choices[selected], choices[index]
        return choices[:count]

    def polarity(self) -> int:
        return 1 if (self.rng.next_u64() & 1) else -1

    def add(
        self,
        cycle: int,
        logical_source: int,
        x: int | None = None,
        y: int | None = None,
        *,
        event_type: str = "spike",
        polarity: int | None = None,
        relation_id: int | None = None,
        relation_role: str | None = None,
        deadline_slack: int | None = None,
    ) -> None:
        if not 0 <= cycle < self.config.stim_cycles:
            return
        if not 0 <= logical_source < self.config.source_count:
            raise ManifestError(f"logical_source {logical_source} is outside geometry")
        expected_x, expected_y = self.xy_for_source(logical_source)
        if x is not None and y is not None and (x, y) != (expected_x, expected_y):
            raise ManifestError(
                f"{self.config.name}: source {logical_source} does not match coordinate ({x}, {y})"
            )
        logical_source = self._source_permutation[logical_source]
        x, y = self.xy_for_source(logical_source)
        source_cycle_key = (cycle, logical_source)
        if source_cycle_key in self._source_cycle_keys:
            raise ManifestError(
                f"{self.config.name}: duplicate occurrence for source {logical_source} "
                f"at cycle {cycle}; common source boundary permits at most one"
            )
        self._source_cycle_keys.add(source_cycle_key)
        if not 0 <= x < self.config.width or not 0 <= y < self.config.height:
            raise ManifestError(f"coordinate ({x}, {y}) is outside geometry")
        if polarity is None:
            fixed_polarity = self.config.parameters.get("fixed_polarity")
            polarity = self.polarity() if fixed_polarity is None else fixed_polarity
        event_type = self.config.parameters.get("fixed_event_type", event_type)
        if isinstance(polarity, bool) or not isinstance(polarity, int) or polarity not in (-1, 1):
            raise ManifestError("polarity must be -1 or 1")
        if not isinstance(event_type, str) or not event_type:
            raise ManifestError("event_type must be a non-empty string")
        if relation_id is not None and (
            isinstance(relation_id, bool) or not isinstance(relation_id, int) or relation_id < 0
        ):
            raise ManifestError("relation_id must be a nonnegative integer or null")
        if relation_role is not None and (
            not isinstance(relation_role, str) or not relation_role
        ):
            raise ManifestError("relation_role must be a non-empty string or null")
        if (relation_id is None) != (relation_role is None):
            raise ManifestError("relation_id and relation_role must be set together")
        if deadline_slack is None:
            deadline_slack = integer_parameter(self.config, "deadline_slack", 32, 0)
        deadline = cycle + deadline_slack
        self._events.append(
            (
                cycle, self._sequence, logical_source, x, y, polarity, event_type,
                relation_id, relation_role, deadline,
            )
        )
        self._sequence += 1

    def events_per_cycle(self, load: Decimal | None = None) -> int:
        offered_load = self.config.load if load is None else load
        whole = int(offered_load)
        fraction = offered_load - Decimal(whole)
        return whole + int(self.rng.probability(fraction))

    def finalize(self) -> list[dict[str, Any]]:
        self._events.sort(key=lambda event: (event[0], event[1]))
        result: list[dict[str, Any]] = []
        for event_id, event in enumerate(self._events):
            (
                cycle, _, source, x, y, polarity, event_type,
                relation_id, relation_role, deadline,
            ) = event
            result.append(
                {
                    "occurrence_cycle": cycle,
                    "tb_only_event_id": event_id,
                    "logical_source": source,
                    "x": x,
                    "y": y,
                    "polarity": polarity,
                    "event_type": event_type,
                    "relation_id": relation_id,
                    "relation_role": relation_role,
                    "deadline": deadline,
                }
            )
        return result


def decimal_value(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ManifestError(f"{field} must be a decimal number") from error
    if not result.is_finite():
        raise ManifestError(f"{field} must be finite")
    return result


def integer_parameter(config: RunConfig, name: str, default: int, minimum: int) -> int:
    value = config.parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(f"{config.name}.parameters.{name} must be integer >= {minimum}")
    return value


def decimal_parameter(
    config: RunConfig, name: str, default: Decimal, minimum: Decimal = Decimal(0)
) -> Decimal:
    value = decimal_value(config.parameters.get(name, default), f"{config.name}.{name}")
    if value < minimum:
        raise ManifestError(f"{config.name}.parameters.{name} must be >= {minimum}")
    return value


def evenly_spaced_sources(count: int, source_count: int) -> list[int]:
    if count <= 1:
        return [source_count // 2]
    return [(index * (source_count - 1)) // (count - 1) for index in range(count)]


def generate_basic_sparse(builder: TraceBuilder) -> None:
    config = builder.config
    count = min(integer_parameter(config, "event_count", 8, 1), config.source_count)
    sources = evenly_spaced_sources(count, config.source_count)
    for index, source in enumerate(sources, start=1):
        cycle = (index * config.stim_cycles) // (count + 1)
        builder.add(cycle, source)


def generate_basic_simultaneous(builder: TraceBuilder) -> None:
    config = builder.config
    count = min(
        integer_parameter(config, "simultaneous_count", min(8, config.source_count), 1),
        config.source_count,
    )
    cycle = integer_parameter(config, "occurrence_cycle", config.stim_cycles // 2, 0)
    for source in evenly_spaced_sources(count, config.source_count):
        builder.add(cycle, source)


def generate_uniform(builder: TraceBuilder) -> None:
    for cycle in range(builder.config.stim_cycles):
        for source in builder.unique_sources(builder.events_per_cycle()):
            builder.add(cycle, source)


def generate_elephant_mouse(builder: TraceBuilder) -> None:
    config = builder.config
    hot_source = integer_parameter(config, "elephant_source", config.source_count // 2, 0)
    if hot_source >= config.source_count:
        raise ManifestError(f"{config.name}.elephant_source is outside geometry")
    share = decimal_parameter(config, "elephant_share", Decimal("0.8"))
    if share > 1:
        raise ManifestError(f"{config.name}.elephant_share must be <= 1")
    for cycle in range(config.stim_cycles):
        event_count = builder.events_per_cycle()
        sources: list[int] = []
        if event_count and builder.rng.probability(share):
            sources.append(hot_source)
        sources.extend(
            builder.unique_sources(
                event_count - len(sources),
                population=(source for source in range(config.source_count)
                            if source != hot_source),
            )
        )
        for source in sources:
            builder.add(cycle, source)


def generate_global_fanin(builder: TraceBuilder) -> None:
    config = builder.config
    count = min(
        integer_parameter(config, "fan_in_count", config.source_count, 1),
        config.source_count,
    )
    period = integer_parameter(config, "burst_period", 64, 1)
    sources = evenly_spaced_sources(count, config.source_count)
    for cycle in range(0, config.stim_cycles, period):
        for source in sources:
            builder.add(cycle, source)


def generate_local_cluster(builder: TraceBuilder) -> None:
    config = builder.config
    center_x = integer_parameter(config, "center_x", config.width // 2, 0)
    center_y = integer_parameter(config, "center_y", config.height // 2, 0)
    radius = integer_parameter(config, "radius", 1, 0)
    coordinates = [
        (x, y)
        for y in range(max(0, center_y - radius), min(config.height, center_y + radius + 1))
        for x in range(max(0, center_x - radius), min(config.width, center_x + radius + 1))
    ]
    if not coordinates:
        raise ManifestError(f"{config.name} cluster does not intersect geometry")
    for cycle in range(config.stim_cycles):
        event_count = builder.events_per_cycle()
        for coordinate_index in builder.unique_sources(
            event_count, population=range(len(coordinates))
        ):
            x, y = coordinates[coordinate_index]
            builder.add(cycle, y * config.width + x, x, y)


def generate_distributed_burst(builder: TraceBuilder) -> None:
    config = builder.config
    burst_count = integer_parameter(config, "burst_count", 4, 1)
    burst_length = integer_parameter(config, "burst_length", max(1, config.stim_cycles // 16), 1)
    regions = (
        (0, max(1, config.width // 2), 0, max(1, config.height // 2)),
        (config.width // 2, config.width, 0, max(1, config.height // 2)),
        (0, max(1, config.width // 2), config.height // 2, config.height),
        (config.width // 2, config.width, config.height // 2, config.height),
    )
    for burst in range(burst_count):
        start = ((burst + 1) * config.stim_cycles) // (burst_count + 1)
        x0, x1, y0, y1 = regions[burst % len(regions)]
        region_sources = [
            y * config.width + x
            for y in range(y0, y1)
            for x in range(x0, x1)
        ]
        for cycle in range(start, min(config.stim_cycles, start + burst_length)):
            for source in builder.unique_sources(
                builder.events_per_cycle(), population=region_sources
            ):
                builder.add(cycle, source)


def generate_retrigger(builder: TraceBuilder) -> None:
    config = builder.config
    repeats = integer_parameter(config, "repeats", 4, 2)
    interval = integer_parameter(config, "retrigger_interval", 1, 1)
    default_triggers = max(1, int((config.load * config.stim_cycles) / repeats))
    trigger_count = integer_parameter(config, "trigger_count", default_triggers, 1)
    spacing = max(1, config.stim_cycles // (trigger_count + 1))
    for trigger in range(trigger_count):
        source = builder.random_source()
        start = min(config.stim_cycles - 1, (trigger + 1) * spacing)
        for repeat_index in range(repeats):
            builder.add(
                start + repeat_index * interval,
                source,
            )


def generate_timing_pair(builder: TraceBuilder) -> None:
    config = builder.config
    pair_gap = integer_parameter(config, "pair_gap", 1, 1)
    if pair_gap >= config.stim_cycles:
        raise ManifestError(f"{config.name}.pair_gap must be smaller than stim_cycles")
    default_pairs = max(1, int((config.load * config.stim_cycles) / 2))
    pair_count = integer_parameter(config, "pair_count", default_pairs, 1)
    usable_cycles = config.stim_cycles - pair_gap
    spacing = max(1, usable_cycles // (pair_count + 1))
    tight_slack = integer_parameter(config, "pair_deadline_slack", pair_gap + 1, 0)
    reserved: dict[int, set[int]] = {}
    for pair in range(pair_count):
        start = min(usable_cycles - 1, (pair + 1) * spacing)
        source_a = builder.unique_sources(
            1,
            population=(
                source for source in range(config.source_count)
                if source not in reserved.get(start, set())
            ),
        )[0]
        source_b = builder.unique_sources(
            1,
            population=(
                source for source in range(config.source_count)
                if source != source_a
                and source not in reserved.get(start + pair_gap, set())
            ),
        )[0]
        reserved.setdefault(start, set()).add(source_a)
        reserved.setdefault(start + pair_gap, set()).add(source_b)
        builder.add(
            start,
            source_a,
            event_type="timing_a",
            relation_id=pair,
            relation_role="a",
            deadline_slack=tight_slack,
        )
        builder.add(
            start + pair_gap,
            source_b,
            event_type="timing_b",
            relation_id=pair,
            relation_role="b",
            deadline_slack=tight_slack,
        )
    background_load = decimal_parameter(config, "background_load", Decimal("0.0"))
    for cycle in range(config.stim_cycles):
        available = (
            source for source in range(config.source_count)
            if source not in reserved.get(cycle, set())
        )
        for source in builder.unique_sources(
            builder.events_per_cycle(background_load), population=available
        ):
            builder.add(cycle, source)


def generate_backpressure_shock(builder: TraceBuilder) -> None:
    config = builder.config
    shock_start = integer_parameter(config, "shock_start", config.stim_cycles // 3, 0)
    shock_cycles = integer_parameter(config, "shock_cycles", max(1, config.stim_cycles // 4), 1)
    background_load = decimal_parameter(config, "background_load", config.load / Decimal(8))
    shock_load = decimal_parameter(config, "shock_load", config.load * Decimal(4))
    shock_slack = integer_parameter(config, "shock_deadline_slack", 8, 0)
    for cycle in range(config.stim_cycles):
        in_shock = shock_start <= cycle < shock_start + shock_cycles
        cycle_load = shock_load if in_shock else background_load
        for source in builder.unique_sources(builder.events_per_cycle(cycle_load)):
            builder.add(cycle, source, deadline_slack=shock_slack if in_shock else None)


def generate_rate_shape(builder: TraceBuilder) -> None:
    """Same source sequence and event count; only temporal burst shape changes."""
    config = builder.config
    event_count = integer_parameter(
        config, "event_count", int(config.load * config.stim_cycles), 1
    )
    shape = config.parameters.get("shape", "smooth")
    if shape not in {"smooth", "bursty"}:
        raise ManifestError(f"{config.name}.parameters.shape must be smooth or bursty")
    burst_size = integer_parameter(config, "burst_size", min(8, config.source_count), 1)
    if burst_size > config.source_count:
        raise ManifestError(f"{config.name}.burst_size exceeds source count")
    if event_count > config.stim_cycles * config.source_count:
        raise ManifestError(f"{config.name}.event_count exceeds one event/source/cycle")
    offset = config.seed % config.source_count
    stride = integer_parameter(config, "source_stride", config.source_count - 1, 1)
    if math.gcd(stride, config.source_count) != 1:
        raise ManifestError(f"{config.name}.source_stride must be coprime to source count")
    batch_count = (event_count + burst_size - 1) // burst_size
    for index in range(event_count):
        source = (offset + index * stride) % config.source_count
        if shape == "smooth":
            cycle = ((index + 1) * config.stim_cycles) // (event_count + 1)
        else:
            batch = index // burst_size
            cycle = ((batch + 1) * config.stim_cycles) // (batch_count + 1)
        builder.add(cycle, source)


def _local_sources(config: RunConfig, count: int) -> list[int]:
    center_x = integer_parameter(config, "center_x", config.width // 2, 0)
    center_y = integer_parameter(config, "center_y", config.height // 2, 0)
    if center_x >= config.width or center_y >= config.height:
        raise ManifestError(f"{config.name}.matched spatial center is outside geometry")
    side = math.isqrt(count)
    if side * side == count and side <= config.width and side <= config.height:
        x0 = min(max(0, center_x), config.width - side)
        y0 = min(max(0, center_y), config.height - side)
        return [
            y * config.width + x
            for y in range(y0, y0 + side)
            for x in range(x0, x0 + side)
        ]
    ranked = sorted(
        range(config.source_count),
        key=lambda source: (
            abs((source % config.width) - center_x)
            + abs((source // config.width) - center_y),
            source,
        ),
    )
    return ranked[:count]


def generate_matched_spatial(builder: TraceBuilder) -> None:
    """Matched local/dispersed pair with identical times and demand-by-rank."""
    config = builder.config
    active_count = integer_parameter(config, "active_sources", min(4, config.source_count), 1)
    if active_count > config.source_count:
        raise ManifestError(f"{config.name}.active_sources exceeds source count")
    placement = config.parameters.get("placement", "local")
    if placement == "local":
        sources = _local_sources(config, active_count)
    elif placement == "dispersed":
        sources = evenly_spaced_sources(active_count, config.source_count)
    else:
        raise ManifestError(f"{config.name}.parameters.placement must be local or dispersed")
    burst_size = integer_parameter(config, "burst_size", 0, 0)
    if burst_size:
        if burst_size > active_count:
            raise ManifestError(f"{config.name}.burst_size exceeds active_sources")
        target = config.load * config.stim_cycles
        if target != target.to_integral_value() or int(target) % burst_size:
            raise ManifestError(
                f"{config.name} burst mode requires integral event count divisible by burst_size"
            )
        burst_count = int(target) // burst_size
        for burst in range(burst_count):
            cycle = ((burst + 1) * config.stim_cycles) // (burst_count + 1)
            for rank in range(burst_size):
                builder.add(cycle, sources[(rank + burst) % active_count])
    else:
        for cycle in range(config.stim_cycles):
            count = builder.events_per_cycle()
            for rank in builder.unique_sources(count, population=range(active_count)):
                builder.add(cycle, sources[rank])


def generate_moving_hotspot(builder: TraceBuilder) -> None:
    """Move one or more hot sources so adaptation must track nonstationary demand."""
    config = builder.config
    dwell = integer_parameter(config, "dwell_cycles", max(1, config.stim_cycles // 16), 1)
    hot_count = integer_parameter(config, "hotspot_count", 1, 1)
    if hot_count >= config.source_count:
        raise ManifestError(f"{config.name}.hotspot_count must be less than source count")
    hot_share = decimal_parameter(config, "hot_share", Decimal("0.8"))
    if hot_share > 1:
        raise ManifestError(f"{config.name}.hot_share must be <= 1")
    stride = config.source_count - 1
    layout = config.parameters.get("hotspot_layout", "dispersed")
    if layout not in {"dispersed", "row", "column"}:
        raise ManifestError(
            f"{config.name}.parameters.hotspot_layout must be dispersed, row, or column"
        )
    for cycle in range(config.stim_cycles):
        epoch = cycle // dwell
        anchor = (config.seed + epoch * stride) % config.source_count
        anchor_x, anchor_y = builder.xy_for_source(anchor)
        if layout == "row":
            if hot_count > config.width:
                raise ManifestError(f"{config.name}.hotspot_count exceeds row width")
            hot = [
                anchor_y * config.width + ((anchor_x + index) % config.width)
                for index in range(hot_count)
            ]
        elif layout == "column":
            if hot_count > config.height:
                raise ManifestError(f"{config.name}.hotspot_count exceeds column height")
            hot = [
                ((anchor_y + index) % config.height) * config.width + anchor_x
                for index in range(hot_count)
            ]
        else:
            if hot_count > min(config.width, config.height):
                raise ManifestError(
                    f"{config.name}.dispersed hotspot_count exceeds smaller geometry dimension"
                )
            hot = [
                ((anchor_y + index) % config.height) * config.width
                + ((anchor_x + index) % config.width)
                for index in range(hot_count)
            ]
        count = builder.events_per_cycle()
        selected: list[int] = []
        hot_slots = min(count, hot_count)
        for source in builder.unique_sources(hot_slots, population=hot):
            if builder.rng.probability(hot_share):
                selected.append(source)
        cold = (source for source in range(config.source_count) if source not in hot)
        selected.extend(builder.unique_sources(count - len(selected), population=cold))
        for source in selected:
            builder.add(cycle, source)


def generate_rotating_victim(builder: TraceBuilder) -> None:
    """Give every source a turn as the low-rate victim under aggressor traffic."""
    config = builder.config
    epoch_cycles = integer_parameter(config, "epoch_cycles", max(1, config.stim_cycles // config.source_count), 1)
    victim_period = integer_parameter(config, "victim_period", 16, 1)
    background_load = decimal_parameter(config, "background_load", config.load)
    for cycle in range(config.stim_cycles):
        victim = (config.seed + (cycle // epoch_cycles)) % config.source_count
        count = builder.events_per_cycle(background_load)
        aggressors = (source for source in range(config.source_count) if source != victim)
        selected = builder.unique_sources(count, population=aggressors)
        if cycle % victim_period == 0:
            selected.append(victim)
        for source in selected:
            builder.add(cycle, source)


def generate_phase_transition(builder: TraceBuilder) -> None:
    """Sparse, near-saturation, overload, post-sparse and drain phases."""
    config = builder.config
    phase_loads = (
        decimal_parameter(config, "sparse_load", Decimal("0.1")),
        decimal_parameter(config, "near_load", Decimal("0.9")),
        decimal_parameter(config, "overload_load", Decimal("1.5")),
        decimal_parameter(config, "post_load", Decimal("0.1")),
        decimal_parameter(config, "recovery_load", Decimal("0.0")),
    )
    for cycle in range(config.stim_cycles):
        eighth = (cycle * 8) // config.stim_cycles
        phase = 0 if eighth < 2 else 1 if eighth < 4 else 2 if eighth < 6 else 3 if eighth < 7 else 4
        for source in builder.unique_sources(builder.events_per_cycle(phase_loads[phase])):
            builder.add(cycle, source)


GENERATORS: dict[str, Callable[[TraceBuilder], None]] = {
    "basic_sparse": generate_basic_sparse,
    "basic_simultaneous": generate_basic_simultaneous,
    "uniform": generate_uniform,
    "elephant_mouse": generate_elephant_mouse,
    "global_fanin": generate_global_fanin,
    "local_cluster": generate_local_cluster,
    "distributed_burst": generate_distributed_burst,
    "retrigger": generate_retrigger,
    "timing_pair": generate_timing_pair,
    "backpressure_shock": generate_backpressure_shock,
    "rate_shape": generate_rate_shape,
    "matched_spatial": generate_matched_spatial,
    "moving_hotspot": generate_moving_hotspot,
    "rotating_victim": generate_rotating_victim,
    "phase_transition": generate_phase_transition,
}


def parse_run(raw: Any, index: int) -> RunConfig:
    if not isinstance(raw, dict):
        raise ManifestError(f"runs[{index}] must be an object")
    required = ("name", "workload", "seed", "geometry", "load", "stim_cycles")
    missing = [field for field in required if field not in raw]
    if missing:
        raise ManifestError(f"runs[{index}] missing: {', '.join(missing)}")
    name = raw["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ManifestError(f"runs[{index}].name is not a safe file stem")
    workload = raw["workload"]
    if workload not in GENERATORS:
        raise ManifestError(f"{name}.workload must be one of: {', '.join(WORKLOADS)}")
    seed = raw["seed"]
    stim_cycles = raw["stim_cycles"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > MASK64:
        raise ManifestError(f"{name}.seed must be an unsigned 64-bit integer")
    if isinstance(stim_cycles, bool) or not isinstance(stim_cycles, int) or stim_cycles <= 0:
        raise ManifestError(f"{name}.stim_cycles must be a positive integer")
    geometry = raw["geometry"]
    if not isinstance(geometry, dict):
        raise ManifestError(f"{name}.geometry must be an object")
    width = geometry.get("width")
    height = geometry.get("height")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height)):
        raise ManifestError(f"{name}.geometry width and height must be positive integers")
    load = decimal_value(raw["load"], f"{name}.load")
    if load < 0:
        raise ManifestError(f"{name}.load must be non-negative")
    parameters = raw.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ManifestError(f"{name}.parameters must be an object")
    sink = raw.get("sink", {"mode": "always"})
    if not isinstance(sink, dict):
        raise ManifestError(f"{name}.sink must be an object")
    sink_mode = sink.get("mode")
    if sink_mode not in {"always", "periodic", "shock"}:
        raise ManifestError(f"{name}.sink.mode must be always, periodic, or shock")
    if sink_mode == "periodic":
        period = sink.get("period")
        ready_cycles = sink.get("ready_cycles")
        if (isinstance(period, bool) or not isinstance(period, int) or period <= 0 or
                isinstance(ready_cycles, bool) or not isinstance(ready_cycles, int) or
                not 0 <= ready_cycles <= period):
            raise ManifestError(
                f"{name}.sink periodic requires period > 0 and 0 <= ready_cycles <= period"
            )
    if sink_mode == "shock":
        start = sink.get("start")
        cycles = sink.get("cycles")
        if (isinstance(start, bool) or not isinstance(start, int) or start < 0 or
                isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0 or
                start >= stim_cycles or start + cycles > stim_cycles):
            raise ManifestError(
                f"{name}.sink shock must fit inside the stimulus window"
            )
    return RunConfig(
        name, workload, seed, width, height, load, stim_cycles, parameters, sink
    )


def load_manifest(path: Path) -> tuple[dict[str, Any], list[RunConfig]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"manifest schema_version must be {SCHEMA_VERSION}")
    raw_runs = raw.get("runs")
    if not isinstance(raw_runs, list) or not raw_runs:
        raise ManifestError("manifest runs must be a non-empty array")
    runs = [parse_run(run, index) for index, run in enumerate(raw_runs)]
    names = [run.name for run in runs]
    if len(names) != len(set(names)):
        raise ManifestError("run names must be unique")
    return raw, runs


def canonical_run_config(config: RunConfig) -> dict[str, Any]:
    return {
        "name": config.name,
        "workload": config.workload,
        "seed": config.seed,
        "geometry": {"width": config.width, "height": config.height},
        "load": str(config.load),
        "stim_cycles": config.stim_cycles,
        "parameters": config.parameters,
        "sink": config.sink,
    }


def write_trace(path: Path, events: Iterable[dict[str, Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        for event in events:
            line = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
            output.write(line)
            digest.update(line.encode("ascii"))
            count += 1
    temporary.replace(path)
    return count, digest.hexdigest()


def generate_run(config: RunConfig, output_dir: Path) -> dict[str, Any]:
    builder = TraceBuilder(config)
    GENERATORS[config.workload](builder)
    events = builder.finalize()
    events_by_cycle: dict[int, int] = {}
    for event in events:
        cycle = int(event["occurrence_cycle"])
        events_by_cycle[cycle] = events_by_cycle.get(cycle, 0) + 1
    trace_name = f"{config.name}.events.jsonl"
    event_count, trace_sha256 = write_trace(output_dir / trace_name, events)
    report_group = (
        "uniform"
        if config.workload == "uniform"
        else re.sub(r"_s[0-9]+$", "", config.name)
    )
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "run": canonical_run_config(config),
        "report_group": report_group,
        "declared_mean_load": str(config.load),
        "actual_mean_load": str(Decimal(event_count) / Decimal(config.stim_cycles)),
        "peak_events_per_cycle": max(events_by_cycle.values(), default=0),
        "trace_file": trace_name,
        "trace_sha256": trace_sha256,
        "event_count": event_count,
        "event_schema": list(EVENT_FIELDS),
        "dut_payload_fields": ["x", "y", "polarity", "event_type"],
        "dut_sideband_fields": ["logical_source"],
        "tb_only_fields": [
            "occurrence_cycle", "tb_only_event_id", "relation_id",
            "relation_role", "deadline"
        ],
        "generation_contract": "trace_is_fully_generated_before_any_DUT_ready_is_observed",
    }
    metadata_path = output_dir / f"{config.name}.manifest.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def generate_manifest(manifest_path: Path, output_dir: Path) -> list[dict[str, Any]]:
    _, runs = load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [generate_run(run, output_dir) for run in runs]
    index = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "input_manifest": manifest_path.name,
        "runs": results,
    }
    (output_dir / "generation-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="input JSON manifest")
    parser.add_argument("--output-dir", type=Path, help="directory for generated traces")
    parser.add_argument("--list-workloads", action="store_true", help="list supported workloads")
    args = parser.parse_args(argv)
    if not args.list_workloads and (args.manifest is None or args.output_dir is None):
        parser.error("--manifest and --output-dir are required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.list_workloads:
        print("\n".join(WORKLOADS))
        return 0
    try:
        results = generate_manifest(args.manifest, args.output_dir)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for result in results:
        run = result["run"]
        print(
            f"generated name={run['name']} workload={run['workload']} "
            f"events={result['event_count']} sha256={result['trace_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
