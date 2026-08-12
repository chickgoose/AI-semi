#!/usr/bin/env python3
"""Generate independent model vectors for normalized A2 RTL lockstep."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from normalized_adapter import NormalizedAdapter  # noqa: E402


def pack(values: tuple[int, ...], width: int) -> int:
    result = 0
    mask = (1 << width) - 1
    for index, value in enumerate(values):
        result |= (value & mask) << (width * index)
    return result


def xorshift64(value: int) -> int:
    value ^= value << 13
    value ^= value >> 7
    value ^= value << 17
    return value & ((1 << 64) - 1)


class VectorWriter:
    def __init__(self) -> None:
        self.model = NormalizedAdapter()
        self.lines: list[str] = []

    def emit(
        self,
        reset_n: bool,
        ready_bits: int,
        source_valid: int,
        events: tuple[int, ...],
    ) -> int:
        ready = bool(ready_bits & 1), bool(ready_bits & 2)
        observation = self.model.step(source_valid, events, ready, reset_n)
        queue = observation.queue_before
        queue_sources = tuple(record.source for record in queue) + (0,) * (2 - len(queue))
        queue_events = tuple(record.event for record in queue) + (0,) * (2 - len(queue))
        outputs = observation.retire_output
        output_sources = tuple(record.source if record else 0 for record in outputs)
        output_events = tuple(record.event if record else 0 for record in outputs)
        held = observation.owner_held_before
        owner_offer = observation.owner_offer
        owner_count = owner_offer.count if owner_offer else 0
        owner_addresses = owner_offer.address if owner_offer else (0, 0)

        retire_count = int(bool(queue) and ready[0] and reset_n)
        if len(queue) == 2 and ready[0] and ready[1] and reset_n:
            retire_count = 2
        free_count = 2 - (len(queue) - retire_count)
        owner_ready = bool(reset_n and owner_count <= free_count)

        fields = (
            int(reset_n), ready_bits, source_valid & 0xFFFF,
            pack(events, 16), observation.source_ready,
            int(observation.retire_valid[0]) | (int(observation.retire_valid[1]) << 1),
            pack(output_events, 16), pack(output_sources, 4),
            int(observation.drain_idle), len(queue),
            pack(queue_sources, 4), pack(queue_events, 16),
            owner_count, pack(owner_addresses, 4), int(owner_ready),
            observation.owner_cursor_before,
            pack(observation.owner_pointers_before, 2), int(held is not None),
            int(bool(held and held.count == 2)),
            pack(held.address if held else (0, 0), 4),
        )
        self.lines.append(
            f"{fields[0]:x} {fields[1]:x} {fields[2]:04x} {fields[3]:064x} "
            f"{fields[4]:04x} {fields[5]:x} {fields[6]:08x} {fields[7]:02x} "
            f"{fields[8]:x} {fields[9]:x} {fields[10]:02x} {fields[11]:08x} "
            f"{fields[12]:x} {fields[13]:02x} {fields[14]:x} {fields[15]:x} "
            f"{fields[16]:02x} {fields[17]:x} {fields[18]:x} {fields[19]:02x}\n"
        )
        return observation.source_ready


def directed_exhaustive(writer: VectorWriter) -> set[tuple[int, int, int, int]]:
    coverage: set[tuple[int, int, int, int]] = set()
    tag = 1
    for queue_count in range(3):
        for reset_n in (0, 1):
            # The normalized wrapper forces owner_count=0 during reset; the
            # Python transport property separately checks hypothetical reset
            # dominance for offer counts one and two.
            offer_counts = range(3) if reset_n else (0,)
            for offer_count in offer_counts:
                for ready_bits in range(4):
                    zero_events = (0,) * 16
                    writer.emit(False, 0, 0, zero_events)
                    writer.emit(True, 0, 0, zero_events)
                    if queue_count:
                        seed_events = tuple(((tag << 4) | source) & 0xFFFF
                                            for source in range(16))
                        seed_req = (1 << queue_count) - 1
                        writer.emit(True, 0, seed_req, seed_events)
                        tag += 1
                    target_events = tuple(((tag << 4) | source) & 0xFFFF
                                          for source in range(16))
                    target_req = sum(1 << (8 + source) for source in range(offer_count))
                    writer.emit(bool(reset_n), ready_bits, target_req, target_events)
                    tag += 1
                    coverage.add((queue_count, offer_count, ready_bits, reset_n))
    return coverage


def random_legal_trace(writer: VectorWriter, cycles: int) -> None:
    rng = 0xA2AD_A970_5EED_0001
    pending: dict[int, int] = {}
    sequence = [0] * 16
    for cycle in range(cycles):
        rng = xorshift64(rng)
        reset_n = cycle not in {0, 1, 97, 1024, 4095, 8191}
        ready_bits = (rng >> 9) & 3
        if 200 <= cycle < 232:
            ready_bits = 0
        elif 232 <= cycle < 248:
            ready_bits = 1
        elif 248 <= cycle < 264:
            ready_bits = 3

        if not reset_n:
            pending.clear()
        else:
            attempts = 1 + int((rng >> 17) & 3)
            for offset in range(attempts):
                source = (rng >> (21 + 4 * offset)) & 15
                if source not in pending:
                    sequence[source] += 1
                    pending[source] = ((sequence[source] & 0xFFF) << 4) | source

        source_valid = sum(1 << source for source in pending)
        events = tuple(pending.get(source, 0) for source in range(16))
        accepted = writer.emit(reset_n, ready_bits, source_valid, events)
        if reset_n:
            for source in range(16):
                if accepted & (1 << source):
                    del pending[source]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--random-cycles", type=int, default=12000)
    args = parser.parse_args()

    writer = VectorWriter()
    coverage = directed_exhaustive(writer)
    expected = {
        (queue_count, offer_count, ready_bits, reset_n)
        for queue_count in range(3)
        for ready_bits in range(4)
        for reset_n in range(2)
        for offer_count in (range(3) if reset_n else (0,))
    }
    if coverage != expected:
        raise RuntimeError(f"abstract state coverage incomplete: {expected - coverage}")
    random_legal_trace(writer, args.random_cycles)
    args.output.write_text("".join(writer.lines), encoding="ascii")
    print(
        "A2_K2_ADAPTER_VECTORS_PASS "
        f"normalized_states={len(coverage)} cycles={len(writer.lines)} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
