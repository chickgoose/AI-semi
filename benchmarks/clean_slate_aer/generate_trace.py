#!/usr/bin/env python3
"""Architecture-neutral, deterministic clean-slate AER trace generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.1"
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
)
EVENT_FIELDS = (
    "occurrence_cycle",
    "tb_only_event_id",
    "logical_source",
    "x",
    "y",
    "polarity",
    "event_type",
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

    @property
    def source_count(self) -> int:
        return self.width * self.height


class TraceBuilder:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.rng = SplitMix64(config.seed)
        self._sequence = 0
        self._events: list[tuple[int, int, int, int, int, int, str, int]] = []

    def xy_for_source(self, source: int) -> tuple[int, int]:
        return source % self.config.width, source // self.config.width

    def random_source(self, excluded: int | None = None) -> int:
        if excluded is None or self.config.source_count == 1:
            return self.rng.randbelow(self.config.source_count)
        candidate = self.rng.randbelow(self.config.source_count - 1)
        return candidate + (1 if candidate >= excluded else 0)

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
        deadline_slack: int | None = None,
    ) -> None:
        if not 0 <= cycle < self.config.stim_cycles:
            return
        if not 0 <= logical_source < self.config.source_count:
            raise ManifestError(f"logical_source {logical_source} is outside geometry")
        if x is None or y is None:
            x, y = self.xy_for_source(logical_source)
        if not 0 <= x < self.config.width or not 0 <= y < self.config.height:
            raise ManifestError(f"coordinate ({x}, {y}) is outside geometry")
        if polarity is None:
            polarity = self.polarity()
        if polarity not in (-1, 1):
            raise ManifestError("polarity must be -1 or 1")
        if deadline_slack is None:
            deadline_slack = integer_parameter(self.config, "deadline_slack", 32, 0)
        deadline = cycle + deadline_slack
        self._events.append(
            (cycle, self._sequence, logical_source, x, y, polarity, event_type, deadline)
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
            cycle, _, source, x, y, polarity, event_type, deadline = event
            result.append(
                {
                    "occurrence_cycle": cycle,
                    "tb_only_event_id": event_id,
                    "logical_source": source,
                    "x": x,
                    "y": y,
                    "polarity": polarity,
                    "event_type": event_type,
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
        for _ in range(builder.events_per_cycle()):
            source = builder.random_source()
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
        for _ in range(builder.events_per_cycle()):
            if builder.rng.probability(share):
                source = hot_source
            else:
                source = builder.random_source(excluded=hot_source)
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
        for _ in range(builder.events_per_cycle()):
            x, y = coordinates[builder.rng.randbelow(len(coordinates))]
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
        for cycle in range(start, min(config.stim_cycles, start + burst_length)):
            for _ in range(builder.events_per_cycle()):
                x = x0 + builder.rng.randbelow(max(1, x1 - x0))
                y = y0 + builder.rng.randbelow(max(1, y1 - y0))
                builder.add(cycle, y * config.width + x, x, y)


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
    for pair in range(pair_count):
        start = min(usable_cycles - 1, (pair + 1) * spacing)
        source_a = builder.random_source()
        source_b = builder.random_source(excluded=source_a)
        builder.add(start, source_a, event_type="timing_a", deadline_slack=tight_slack)
        builder.add(
            start + pair_gap,
            source_b,
            event_type="timing_b",
            deadline_slack=tight_slack,
        )


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
        for _ in range(builder.events_per_cycle(cycle_load)):
            builder.add(
                cycle,
                builder.random_source(),
                deadline_slack=shock_slack if in_shock else None,
            )


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
    return RunConfig(name, workload, seed, width, height, load, stim_cycles, parameters)


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
    trace_name = f"{config.name}.events.jsonl"
    event_count, trace_sha256 = write_trace(output_dir / trace_name, events)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "run": canonical_run_config(config),
        "trace_file": trace_name,
        "trace_sha256": trace_sha256,
        "event_count": event_count,
        "event_schema": list(EVENT_FIELDS),
        "dut_payload_fields": ["x", "y", "polarity", "event_type"],
        "dut_sideband_fields": ["logical_source"],
        "tb_only_fields": ["occurrence_cycle", "tb_only_event_id", "deadline"],
        "generation_contract": "trace_is_fully_generated_before_any_DUT_ready_is_observed",
    }
    metadata_path = output_dir / f"{config.name}.manifest.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
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
        newline="\n",
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
