#!/usr/bin/env python3
"""Bit-exact reference model for the A6 lossless AER address codec."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class EncodedStream:
    bits: str
    tokens: Counter


def _raw(address: int, address_width: int) -> str:
    return "101" + format(address, f"0{address_width}b")


def encode(
    addresses: Iterable[int],
    address_width: int = 4,
    initial_previous: int | None = None,
) -> EncodedStream:
    """Encode addresses in order; every input item is one occurrence."""
    if address_width < 1:
        raise ValueError("address_width must be positive")
    limit = 1 << address_width
    values = list(addresses)
    if any(not isinstance(value, int) or value < 0 or value >= limit for value in values):
        raise ValueError("address outside configured width")

    pieces: list[str] = []
    tokens: Counter = Counter()
    if initial_previous is not None and not 0 <= initial_previous < limit:
        raise ValueError("initial history outside configured width")
    previous: int | None = initial_previous

    def emit_repeat(count: int) -> None:
        while count:
            if count == 1:
                pieces.append("0")
                tokens["same"] += 1
                count = 0
            else:
                chunk = min(count, 9)
                pieces.append("100" + format(chunk - 2, "03b"))
                tokens["run"] += 1
                count -= chunk

    cursor = 0
    while cursor < len(values):
        address = values[cursor]
        end = cursor + 1
        while end < len(values) and values[end] == address:
            end += 1
        run_length = end - cursor

        if previous == address:
            emit_repeat(run_length)
        else:
            if previous is not None and address == previous + 1:
                pieces.append("110")
                tokens["delta_plus"] += 1
            elif previous is not None and address + 1 == previous:
                pieces.append("111")
                tokens["delta_minus"] += 1
            else:
                pieces.append(_raw(address, address_width))
                tokens["raw"] += 1
            previous = address
            emit_repeat(run_length - 1)
        cursor = end

    return EncodedStream("".join(pieces), tokens)


def decode_with_tokens(bits: str, address_width: int = 4) -> tuple[list[int], Counter]:
    """Strictly decode a complete stream, rejecting truncation/illegal history."""
    if address_width < 1:
        raise ValueError("address_width must be positive")
    if any(bit not in "01" for bit in bits):
        raise ValueError("bitstream must contain only zero and one")

    output: list[int] = []
    tokens: Counter = Counter()
    previous: int | None = None
    cursor = 0

    def require(count: int) -> str:
        nonlocal cursor
        if cursor + count > len(bits):
            raise ValueError("truncated token")
        value = bits[cursor : cursor + count]
        cursor += count
        return value

    while cursor < len(bits):
        first = require(1)
        if first == "0":
            if previous is None:
                raise ValueError("SAME before RAW history")
            output.append(previous)
            tokens["same"] += 1
            continue

        suffix = require(2)
        if suffix == "00":
            if previous is None:
                raise ValueError("RUN before RAW history")
            count = int(require(3), 2) + 2
            output.extend([previous] * count)
            tokens["run"] += 1
        elif suffix == "01":
            previous = int(require(address_width), 2)
            output.append(previous)
            tokens["raw"] += 1
        elif suffix == "10":
            if previous is None or previous == (1 << address_width) - 1:
                raise ValueError("illegal DELTA+1")
            previous += 1
            output.append(previous)
            tokens["delta_plus"] += 1
        else:
            if previous is None or previous == 0:
                raise ValueError("illegal DELTA-1")
            previous -= 1
            output.append(previous)
            tokens["delta_minus"] += 1
    return output, tokens


def decode(bits: str, address_width: int = 4) -> list[int]:
    return decode_with_tokens(bits, address_width)[0]


def serialize_cycles(bits: str, data_width: int = 2) -> list[tuple[int, int]]:
    """Return (valid-bit-count, data) cycles, first stream bit in data MSB."""
    if data_width != 2:
        raise ValueError("A6 fixed-pin model currently requires two data pins")
    cycles: list[tuple[int, int]] = []
    for start in range(0, len(bits), data_width):
        chunk = bits[start : start + data_width]
        data = int(chunk.ljust(data_width, "0"), 2)
        cycles.append((len(chunk), data))
    return cycles


def link_metrics(bits: str, events: int, data_width: int = 2) -> dict[str, float | int]:
    """Calculate fixed-pin cycles and transition proxies from actual link symbols."""
    cycles = serialize_cycles(bits, data_width)
    previous_count = 0
    previous_data = 0
    data_toggles = 0
    count_toggles = 0
    for count, data in cycles:
        data_toggles += (previous_data ^ data).bit_count()
        count_toggles += (previous_count ^ count).bit_count()
        previous_count = count
        previous_data = data
    # Return-to-idle transitions are physically charged once at stream drain.
    if cycles:
        data_toggles += previous_data.bit_count()
        count_toggles += previous_count.bit_count()
    pin_count = data_width + 2 + 1  # data, two-bit count, ready
    return {
        "link_cycles": len(cycles),
        "link_pins": pin_count,
        "data_toggles": data_toggles,
        "charged_toggles": data_toggles + count_toggles,
        "bits_per_event": len(bits) / events if events else 0.0,
        "events_per_link_cycle": events / len(cycles) if cycles else 0.0,
        "events_per_pin_cycle": events / (pin_count * len(cycles)) if cycles else 0.0,
        "data_toggles_per_event": data_toggles / events if events else 0.0,
        "charged_toggles_per_event": (data_toggles + count_toggles) / events if events else 0.0,
    }


def raw_bits(addresses: Sequence[int], address_width: int = 4) -> str:
    return "".join(format(address, f"0{address_width}b") for address in addresses)
