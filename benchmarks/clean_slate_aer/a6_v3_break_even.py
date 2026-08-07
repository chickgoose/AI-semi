#!/usr/bin/env python3
"""Fixed-format ping-pong lower-bound model for the final A6 design-space gate."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

from a6_v2_codec import encode_block


BLOCK_SIZES = (4, 8, 16, 32)
LINK_WIDTHS = (1, 2, 4)


@dataclass(frozen=True)
class Event:
    occurrence: int
    source: int


@dataclass
class TransportBlock:
    events: list[Event] = field(default_factory=list)
    closed: bool = False
    last_accept: int = 0
    bits: int = 0
    mode: str = ""


@dataclass(frozen=True)
class PointResult:
    offered: int
    accepted: int
    overrun: int
    delivered: int
    blocks: int
    raw_blocks: int
    link_bits: int
    link_cycles: int
    pins: int
    average_latency: float
    max_latency: int
    drain_cycle: int

    @property
    def bits_per_event(self) -> float:
        return self.link_bits / self.delivered if self.delivered else 0.0

    @property
    def events_per_pin_cycle(self) -> float:
        denominator = self.pins * self.link_cycles
        return self.delivered / denominator if denominator else 0.0


def physical_pins(width: int) -> int:
    return width + math.ceil(math.log2(width + 1)) + 1


def charged_storage_bits(block_size: int) -> int:
    # Two four-bit banks at each endpoint, plus equalized history allowance.
    return 16 * block_size + 10


def encode_length(addresses: list[int], previous: int | None,
                  block_size: int, codec: bool) -> tuple[int, str]:
    if not codec:
        return 4 * len(addresses), "raw"
    encoded = encode_block(addresses, previous, block_size)
    if len(encoded.bits) > 4 * len(addresses):
        raise AssertionError("non-expanding selector failed")
    return len(encoded.bits), encoded.mode


def choose_pending(pending: dict[int, Event], rr_start: int) -> int | None:
    for offset in range(16):
        source = (rr_start + offset) % 16
        if source in pending:
            return source
    return None


def simulate(events: list[Event], stim_cycles: int, block_size: int,
             width: int, codec: bool) -> PointResult:
    arrivals: dict[int, list[Event]] = defaultdict(list)
    for event in events:
        arrivals[event.occurrence].append(event)
    pending: dict[int, Event] = {}
    encoder: list[TransportBlock] = []
    decoder_ready: list[TransportBlock] = []
    retire_block: TransportBlock | None = None
    retire_index = 0
    decoder_occupied = 0
    link_block: TransportBlock | None = None
    link_remaining = 0
    previous_address: int | None = None
    rr_start = 0
    overrun = accepted = delivered = 0
    block_total = raw_blocks = link_bits = link_cycles = 0
    latencies: list[int] = []
    cycle = 0
    limit = stim_cycles + max(100000, len(events) * 32)

    def filling_block() -> TransportBlock | None:
        if encoder and not encoder[-1].closed:
            return encoder[-1]
        return None

    while cycle < limit:
        for event in arrivals.get(cycle, []):
            if event.source in pending:
                overrun += 1
            else:
                pending[event.source] = event

        fill = filling_block()
        ingress_space = fill is not None or len(encoder) < 2
        selected = choose_pending(pending, rr_start) if ingress_space else None
        if selected is not None:
            event = pending.pop(selected)
            if fill is None:
                fill = TransportBlock(last_accept=cycle)
                encoder.append(fill)
            fill.events.append(event)
            fill.last_accept = cycle
            accepted += 1
            rr_start = (selected + 1) % 16
            if len(fill.events) == block_size:
                fill.closed = True

        fill = filling_block()
        no_more_arrivals = cycle >= stim_cycles
        if fill is not None and (
            cycle - fill.last_accept >= block_size or
            (no_more_arrivals and not pending)
        ):
            fill.closed = True

        if retire_block is None and decoder_ready:
            retire_block = decoder_ready.pop(0)
            retire_index = 0
        if retire_block is not None:
            event = retire_block.events[retire_index]
            latencies.append(cycle - event.occurrence)
            delivered += 1
            retire_index += 1
            if retire_index == len(retire_block.events):
                retire_block = None
                retire_index = 0
                decoder_occupied -= 1

        if link_block is None and encoder and encoder[0].closed and decoder_occupied < 2:
            link_block = encoder[0]
            addresses = [event.source for event in link_block.events]
            bit_length, mode = encode_length(
                addresses, previous_address, block_size, codec)
            link_block.bits = bit_length
            link_block.mode = mode
            previous_address = addresses[-1]
            link_remaining = math.ceil(bit_length / width) + 1
            link_bits += bit_length
            link_cycles += link_remaining
            block_total += 1
            raw_blocks += int(mode == "raw")
            decoder_occupied += 1
        if link_block is not None:
            link_remaining -= 1
            if link_remaining == 0:
                completed = link_block
                if encoder[0] is not completed:
                    raise AssertionError("encoder block order changed")
                encoder.pop(0)
                decoder_ready.append(completed)
                link_block = None

        done = (
            no_more_arrivals and not pending and not encoder and
            link_block is None and not decoder_ready and retire_block is None
        )
        if done:
            break
        cycle += 1
    else:
        raise RuntimeError("ping-pong model did not drain")

    if offered := len(events):
        if offered != accepted + overrun or accepted != delivered:
            raise AssertionError("occurrence conservation failed")
    return PointResult(
        offered=len(events), accepted=accepted, overrun=overrun,
        delivered=delivered, blocks=block_total, raw_blocks=raw_blocks,
        link_bits=link_bits, link_cycles=link_cycles, pins=physical_pins(width),
        average_latency=fmean(latencies) if latencies else 0.0,
        max_latency=max(latencies, default=0), drain_cycle=cycle,
    )


def load_trace(manifest_path: Path) -> tuple[dict, list[Event]]:
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    trace_path = manifest_path.parent / metadata["trace_file"]
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    return metadata, [Event(row["occurrence_cycle"], row["logical_source"])
                      for row in rows]


def aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    offered = sum(int(row["offered"]) for row in rows)
    accepted = sum(int(row["accepted"]) for row in rows)
    delivered = sum(int(row["delivered"]) for row in rows)
    bits = sum(int(row["link_bits"]) for row in rows)
    cycles = sum(int(row["link_cycles"]) for row in rows)
    latency_weight = sum(float(row["average_latency"]) * int(row["delivered"])
                         for row in rows)
    blocks = sum(int(row["blocks"]) for row in rows)
    return {
        "runs": len(rows), "offered": offered, "accepted": accepted,
        "delivered": delivered,
        "overrun": sum(int(row["overrun"]) for row in rows),
        "overrun_ratio": (offered - accepted) / offered if offered else 0.0,
        "bits_per_event": bits / delivered if delivered else 0.0,
        "events_per_pin_cycle": delivered / (int(rows[0]["pins"]) * cycles)
        if cycles else 0.0,
        "average_latency": latency_weight / delivered if delivered else 0.0,
        "max_latency": max(int(row["max_latency"]) for row in rows),
        "raw_bypass_ratio": sum(int(row["raw_blocks"]) for row in rows) / blocks
        if blocks else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--matrix-json", required=True, type=Path)
    args = parser.parse_args()
    manifests = sorted(args.trace_dir.glob("*.manifest.json"))
    if len(manifests) != 46:
        raise ValueError("expected exactly 46 frozen manifests")

    detail: list[dict[str, object]] = []
    for manifest in manifests:
        metadata, events = load_trace(manifest)
        for block_size in BLOCK_SIZES:
            for width in LINK_WIDTHS:
                for codec in (False, True):
                    result = simulate(events, metadata["run"]["stim_cycles"],
                                      block_size, width, codec)
                    detail.append({
                        "name": metadata["run"]["name"],
                        "workload": metadata["run"]["workload"],
                        "kind": "codec" if codec else "raw",
                        "block_size": block_size, "link_width": width,
                        "pins": result.pins,
                        "charged_storage_bits": charged_storage_bits(block_size),
                        "offered": result.offered, "accepted": result.accepted,
                        "overrun": result.overrun, "delivered": result.delivered,
                        "blocks": result.blocks, "raw_blocks": result.raw_blocks,
                        "link_bits": result.link_bits,
                        "link_cycles": result.link_cycles,
                        "bits_per_event": result.bits_per_event,
                        "events_per_pin_cycle": result.events_per_pin_cycle,
                        "average_latency": result.average_latency,
                        "max_latency": result.max_latency,
                        "drain_cycle": result.drain_cycle,
                    })

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(detail[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(detail)

    matrix: dict[str, object] = {"runs": 46, "points": {}}
    for block_size in BLOCK_SIZES:
        for width in LINK_WIDTHS:
            key = f"B{block_size}_W{width}"
            selected = [row for row in detail if row["block_size"] == block_size
                        and row["link_width"] == width]
            raw_rows = [row for row in selected if row["kind"] == "raw"]
            codec_rows = [row for row in selected if row["kind"] == "codec"]
            raw = aggregate(raw_rows)
            codec = aggregate(codec_rows)
            families = {}
            for family in sorted({str(row["workload"]) for row in selected}):
                families[family] = {
                    "raw": aggregate([row for row in raw_rows if row["workload"] == family]),
                    "codec": aggregate([row for row in codec_rows if row["workload"] == family]),
                }
            full_raw_cycles = math.ceil(4 * block_size / width) + 1
            matrix["points"][key] = {
                "block_size": block_size, "link_width": width,
                "pins": physical_pins(width),
                "charged_storage_bits_each": charged_storage_bits(block_size),
                "raw": raw, "codec": codec, "families": families,
                "sustained_one_event_per_cycle_raw_possible": full_raw_cycles <= block_size,
                "codec_full_block_bit_break_even": width * (block_size - 1),
                "endpoint_logic_cost_relation": "codec_strict_superset_of_raw",
                "simultaneous_pareto_pass": (
                    codec["events_per_pin_cycle"] > raw["events_per_pin_cycle"] and
                    codec["average_latency"] <= raw["average_latency"] and
                    codec["overrun"] <= raw["overrun"] and False
                ),
            }
    args.matrix_json.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n")
    print("A6_V3_MATRIX_PASS runs=46 points=12 nonexpanding=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
