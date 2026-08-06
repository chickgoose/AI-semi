#!/usr/bin/env python3
"""Validate a generated JSONL trace and emit a simulator-portable numeric trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any


class TracePreparationError(ValueError):
    """Raised when a trace cannot be connected to the normalized SV bench."""


REQUIRED_FIELDS = (
    "occurrence_cycle",
    "tb_only_event_id",
    "logical_source",
    "x",
    "y",
    "polarity",
    "event_type",
    "deadline",
)


def load_metadata(path: Path) -> dict[str, Any]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TracePreparationError(f"cannot read run manifest {path}: {error}") from error
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise TracePreparationError("run manifest schema_version must be 1")
    run = metadata.get("run")
    if not isinstance(run, dict):
        raise TracePreparationError("run manifest is missing run metadata")
    geometry = run.get("geometry")
    if not isinstance(geometry, dict):
        raise TracePreparationError("run manifest is missing geometry")
    for field in ("width", "height"):
        if not isinstance(geometry.get(field), int) or geometry[field] <= 0:
            raise TracePreparationError(f"geometry.{field} must be a positive integer")
    if not isinstance(run.get("stim_cycles"), int) or run["stim_cycles"] <= 0:
        raise TracePreparationError("run.stim_cycles must be a positive integer")
    return metadata


def load_events(path: Path) -> tuple[list[dict[str, Any]], str]:
    events: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for line_number, raw_line in enumerate(source, start=1):
                digest.update(raw_line)
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError as error:
                    raise TracePreparationError(
                        f"{path}:{line_number}: invalid JSON: {error}"
                    ) from error
                if not isinstance(event, dict):
                    raise TracePreparationError(f"{path}:{line_number}: event must be an object")
                missing = [field for field in REQUIRED_FIELDS if field not in event]
                if missing:
                    raise TracePreparationError(
                        f"{path}:{line_number}: missing fields: {', '.join(missing)}"
                    )
                events.append(event)
    except OSError as error:
        raise TracePreparationError(f"cannot read trace {path}: {error}") from error
    return events, digest.hexdigest()


def checked_integer(event: dict[str, Any], field: str, event_id: int) -> int:
    value = event[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TracePreparationError(f"event {event_id}: {field} must be an integer")
    return value


def encode_events(
    metadata: dict[str, Any], events: list[dict[str, Any]], addr_width: int
) -> tuple[list[tuple[int, int, int, int, int]], int]:
    run = metadata["run"]
    width = run["geometry"]["width"]
    height = run["geometry"]["height"]
    source_count = width * height
    stim_cycles = run["stim_cycles"]
    event_types = sorted({event["event_type"] for event in events})
    if any(not isinstance(name, str) or not name for name in event_types):
        raise TracePreparationError("event_type must be a non-empty string")
    type_codes = {name: index for index, name in enumerate(event_types)}
    type_bits = max(1, (max(1, len(type_codes)) - 1).bit_length())
    encoded: list[tuple[int, int, int, int, int]] = []
    previous_key = (-1, -1)

    for expected_id, event in enumerate(events):
        occurrence = checked_integer(event, "occurrence_cycle", expected_id)
        trace_id = checked_integer(event, "tb_only_event_id", expected_id)
        source = checked_integer(event, "logical_source", expected_id)
        x = checked_integer(event, "x", expected_id)
        y = checked_integer(event, "y", expected_id)
        polarity = checked_integer(event, "polarity", expected_id)
        deadline = checked_integer(event, "deadline", expected_id)
        if trace_id != expected_id:
            raise TracePreparationError(
                f"event IDs must be contiguous: expected {expected_id}, got {trace_id}"
            )
        if not 0 <= occurrence < stim_cycles:
            raise TracePreparationError(f"event {trace_id}: occurrence is outside stim_cycles")
        if not 0 <= source < source_count:
            raise TracePreparationError(f"event {trace_id}: logical_source is outside geometry")
        if not 0 <= x < width or not 0 <= y < height:
            raise TracePreparationError(f"event {trace_id}: coordinate is outside geometry")
        if source != y * width + x:
            raise TracePreparationError(
                f"event {trace_id}: logical_source must equal its AER coordinate"
            )
        if polarity not in (-1, 1):
            raise TracePreparationError(f"event {trace_id}: polarity must be -1 or 1")
        if deadline < occurrence:
            raise TracePreparationError(f"event {trace_id}: deadline precedes occurrence")
        key = (occurrence, trace_id)
        if key < previous_key:
            raise TracePreparationError("trace must be sorted by occurrence cycle and event ID")
        previous_key = key

        coordinate = y * width + x
        event_address = (((coordinate << 1) | int(polarity > 0)) << type_bits) | type_codes[event["event_type"]]
        if event_address >= (1 << addr_width):
            raise TracePreparationError(
                f"event {trace_id}: encoded address {event_address} exceeds ADDR_WIDTH={addr_width}"
            )
        encoded.append((occurrence, trace_id, source, event_address, deadline))
    return encoded, source_count


def encode_sink(run: dict[str, Any]) -> tuple[int, int, int]:
    sink = run.get("sink", {"mode": "always"})
    if not isinstance(sink, dict):
        raise TracePreparationError("run.sink must be an object")
    mode = sink.get("mode")
    if mode == "always":
        return 0, 0, 0
    if mode == "periodic":
        period = sink.get("period")
        ready_cycles = sink.get("ready_cycles")
        if (isinstance(period, bool) or not isinstance(period, int) or period <= 0 or
                isinstance(ready_cycles, bool) or not isinstance(ready_cycles, int) or
                not 0 <= ready_cycles <= period):
            raise TracePreparationError("invalid periodic sink schedule")
        return 1, period, ready_cycles
    if mode == "shock":
        start = sink.get("start")
        cycles = sink.get("cycles")
        stim_cycles = run["stim_cycles"]
        if (isinstance(start, bool) or not isinstance(start, int) or start < 0 or
                isinstance(cycles, bool) or not isinstance(cycles, int) or cycles <= 0 or
                start >= stim_cycles or start + cycles > stim_cycles):
            raise TracePreparationError("invalid shock sink schedule")
        return 2, start, cycles
    raise TracePreparationError("sink mode must be always, periodic, or shock")


def prepare_trace(trace_path: Path, manifest_path: Path, output_path: Path, addr_width: int) -> dict[str, Any]:
    if addr_width <= 0:
        raise TracePreparationError("addr_width must be positive")
    metadata = load_metadata(manifest_path)
    events, trace_sha256 = load_events(trace_path)
    if metadata.get("trace_file") != trace_path.name:
        raise TracePreparationError("trace filename does not match run manifest")
    if metadata.get("trace_sha256") != trace_sha256:
        raise TracePreparationError("trace SHA256 does not match run manifest")
    if metadata.get("event_count") != len(events):
        raise TracePreparationError("event_count does not match run manifest")
    encoded, source_count = encode_events(metadata, events, addr_width)
    run = metadata["run"]
    load_milli = Decimal(str(run["load"])) * 1000
    if load_milli != load_milli.to_integral_value():
        raise TracePreparationError("run.load requires more than three decimal places")

    sink_mode, sink_arg0, sink_arg1 = encode_sink(run)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="ascii", newline="\n") as output:
        output.write(
            f"2 {len(encoded)} {run['stim_cycles']} {source_count} {int(load_milli)} "
            f"{sink_mode} {sink_arg0} {sink_arg1}\n"
        )
        for occurrence, trace_id, source, address, deadline in encoded:
            output.write(f"{occurrence} {trace_id} {source} {address} {deadline}\n")
    temporary.replace(output_path)
    return {
        "name": run["name"],
        "event_count": len(encoded),
        "stim_cycles": run["stim_cycles"],
        "source_count": source_count,
        "load_milli": int(load_milli),
        "sink_mode": sink_mode,
        "output": str(output_path),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--addr-width", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = prepare_trace(args.trace, args.run_manifest, args.output, args.addr_width)
    except TracePreparationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        "TRACE_PREPARED "
        + " ".join(f"{key}={value}" for key, value in result.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
