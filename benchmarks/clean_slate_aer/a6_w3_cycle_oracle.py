#!/usr/bin/env python3
"""Independent cycle oracle for the committed A6 W3 encoder/decoder contract."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from a6_w3_elias_fano import EncodedBatch, Event, encode_batch


NUM_SOURCES = 16
MAX_BATCH = 16
LINK_WIDTH = 2
RX_CAPACITY = 2 * MAX_BATCH
TRANSACTIONS = (
    (0, tuple(range(16))),
    (1, tuple(range(16))),
    (2, tuple(range(16))),
    (3, (1, 7, 14)),
)


@dataclass(frozen=True)
class CycleRow:
    cycle: int
    accepted: int
    link_count: int
    link_data: int
    decoded_valid: int
    decoded_address: int
    retired: int
    retired_address: int
    retired_latency: int


def event_ready(cycle: int) -> bool:
    # Fill both 16-entry halves, then prove that the third EF marker waits until
    # exactly K slots (including the same-edge pop) are available.
    return cycle >= 45


def generate_rows() -> list[CycleRow]:
    transaction_index = 0
    active: tuple[tuple[Event, ...], EncodedBatch] | None = None
    active_beat = 0
    fifo: list[Event] = []
    rows: list[CycleRow] = []
    quiet = 0

    for cycle in range(512):
        accepted = int(active is None and transaction_index < len(TRANSACTIONS))
        retired_event = fifo[0] if fifo and event_ready(cycle) else None
        free_slots = RX_CAPACITY - len(fifo) + int(retired_event is not None)

        link_count = 0
        link_data = 0
        link_accepted = False
        if active is not None:
            events, frame = active
            beat = frame.beats[active_beat]
            if frame.mode == "elias_fano":
                link_ready = active_beat != 0 or free_slots >= MAX_BATCH
            else:
                link_ready = free_slots >= 1
            if link_ready:
                link_accepted = True
                link_count = beat.count
                link_data = int(beat.bits.ljust(2, "0"), 2)

        if retired_event is not None:
            fifo.pop(0)

        if link_accepted:
            events, frame = active
            active_beat += 1
            if frame.mode == "raw":
                beats_per_event = 2
                if active_beat % beats_per_event == 0:
                    fifo.append(events[active_beat // beats_per_event - 1])
            elif active_beat == frame.link_cycles:
                fifo.extend(events)
            if active_beat == frame.link_cycles:
                active = None
                active_beat = 0

        if accepted:
            occurrence, sources = TRANSACTIONS[transaction_index]
            events = tuple(
                Event(occurrence, transaction_index * MAX_BATCH + index, source)
                for index, source in enumerate(sources)
            )
            frame = encode_batch(
                sources, num_sources=NUM_SOURCES, max_batch=MAX_BATCH
            )
            active = (events, frame)
            active_beat = 0
            transaction_index += 1

        visible = fifo[0] if fifo else None
        rows.append(CycleRow(
            cycle=cycle,
            accepted=accepted,
            link_count=link_count,
            link_data=link_data,
            decoded_valid=int(visible is not None),
            decoded_address=visible.source if visible is not None else 0,
            retired=int(retired_event is not None),
            retired_address=retired_event.source if retired_event is not None else 0,
            retired_latency=(cycle - retired_event.occurrence_cycle)
            if retired_event is not None else 0,
        ))

        done = (
            transaction_index == len(TRANSACTIONS)
            and active is None and not fifo
        )
        quiet = quiet + 1 if done else 0
        if quiet == 5:
            return rows
    raise RuntimeError("cycle oracle failed to drain")


def write_rows(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "cycle accepted link_count link_data decoded_valid decoded_address "
        "retired retired_address retired_latency\n"
    )
    body = "".join(
        f"{row.cycle} {row.accepted} {row.link_count} {row.link_data} "
        f"{row.decoded_valid} {row.decoded_address} {row.retired} "
        f"{row.retired_address} {row.retired_latency}\n"
        for row in generate_rows()
    )
    path.write_text(header + body, encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_rows(args.output)
    print(f"A6_W3_CYCLE_ORACLE output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
