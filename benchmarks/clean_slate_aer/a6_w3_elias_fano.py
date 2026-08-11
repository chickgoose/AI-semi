#!/usr/bin/env python3
"""Executable model for the A6 W3 Elias--Fano monotone-dequeue link.

The format deliberately does not reuse a bitmap or combinatorial rank.  Raw
addresses use full-width beats with no header.  At a raw-address boundary, a
one-bit link beat containing ``1`` is an Elias--Fano escape marker.  It is
followed by a fixed-width event count and a conventional high-unary/low-bits
Elias--Fano representation of a strictly increasing source sequence.

Occurrence time and TB-only identity are transport provenance, not DUT payload.
The bounded-window batcher therefore keeps the original Event objects and joins
them back to decoded sources.  It never infers or transmits those TB-only fields.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence


class CodecError(ValueError):
    """Raised when an Elias--Fano stream is malformed or ambiguous."""


@dataclass(frozen=True, order=True)
class Event:
    occurrence_cycle: int
    sequence: int
    source: int


@dataclass(frozen=True)
class Beat:
    """One physical link transfer; bits are serialized left-to-right."""

    bits: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.bits) <= 2 or set(self.bits) - {"0", "1"}:
            raise CodecError("a link beat must contain one or two binary bits")

    @property
    def count(self) -> int:
        return len(self.bits)


@dataclass(frozen=True)
class EncodedBatch:
    mode: str
    sources: tuple[int, ...]
    beats: tuple[Beat, ...]
    payload_bits: int
    framing_bits: int
    low_width: int
    high_bits: int

    @property
    def valid_bits(self) -> int:
        return sum(beat.count for beat in self.beats)

    @property
    def link_cycles(self) -> int:
        return len(self.beats)


@dataclass(frozen=True)
class EventBatch:
    events: tuple[Event, ...]
    opened_cycle: int
    closed_cycle: int
    close_reason: str

    @property
    def sources(self) -> tuple[int, ...]:
        return tuple(event.source for event in self.events)

    @property
    def max_wait(self) -> int:
        return max(
            (self.closed_cycle - event.occurrence_cycle for event in self.events),
            default=0,
        )


def address_width(num_sources: int) -> int:
    if num_sources < 2 or num_sources & (num_sources - 1):
        raise ValueError("num_sources must be a power of two of at least two")
    return (num_sources - 1).bit_length()


def count_width(max_batch: int) -> int:
    if max_batch < 1:
        raise ValueError("max_batch must be positive")
    return max(1, max_batch.bit_length())


def low_width(num_sources: int, event_count: int) -> int:
    if not 0 < event_count <= num_sources:
        raise ValueError("event_count must be in 1..num_sources")
    return max(0, (num_sources // event_count).bit_length() - 1)


def _chunks(bits: str, width: int = 2) -> tuple[Beat, ...]:
    return tuple(Beat(bits[index:index + width]) for index in range(0, len(bits), width))


def _validate_sources(
    sources: Sequence[int], num_sources: int, max_batch: int
) -> tuple[int, ...]:
    values = tuple(sources)
    if len(values) > max_batch:
        raise CodecError("batch exceeds max_batch")
    if any(isinstance(source, bool) or not isinstance(source, int)
           for source in values):
        raise CodecError("source must be an integer")
    if any(source < 0 or source >= num_sources for source in values):
        raise CodecError("source outside configured universe")
    if any(left >= right for left, right in zip(values, values[1:])):
        raise CodecError("Elias--Fano batch must be strictly source-monotone")
    return values


def elias_fano_payload(
    sources: Sequence[int], num_sources: int
) -> tuple[str, int, int]:
    """Return high-unary then low-bit payload and its component sizes."""

    values = tuple(sources)
    if not values:
        return "", 0, 0
    width = low_width(num_sources, len(values))
    low_mask = (1 << width) - 1
    previous_high = 0
    high_parts: list[str] = []
    low_parts: list[str] = []
    for source in values:
        high = source >> width
        if high < previous_high:
            raise CodecError("non-monotone Elias--Fano high part")
        high_parts.append("0" * (high - previous_high))
        high_parts.append("1")
        previous_high = high
        if width:
            low_parts.append(format(source & low_mask, f"0{width}b"))
    high_stream = "".join(high_parts)
    return high_stream + "".join(low_parts), width, len(high_stream)


def encode_batch(
    sources: Sequence[int],
    *,
    num_sources: int,
    max_batch: int,
    force_mode: str | None = None,
) -> EncodedBatch:
    """Encode one canonical unique-address batch with a cycle-based raw escape."""

    values = _validate_sources(sources, num_sources, max_batch)
    aw = address_width(num_sources)
    cw = count_width(max_batch)
    raw_bits = "".join(format(source, f"0{aw}b") for source in values)
    raw_beats = _chunks(raw_bits)

    if not values:
        payload, lw, high_count = "", 0, 0
    else:
        payload, lw, high_count = elias_fano_payload(values, num_sources)
    header = format(len(values), f"0{cw}b")
    ef_beats = (Beat("1"),) + _chunks(header + payload)

    if force_mode not in {None, "raw", "elias_fano"}:
        raise ValueError("force_mode must be raw, elias_fano, or None")
    use_ef = force_mode == "elias_fano" or (
        force_mode is None and len(values) == 0
    )
    if force_mode is None and values:
        # A tie is raw: Elias--Fano must pay for its marker and count in cycles.
        use_ef = len(ef_beats) < len(raw_beats)
    if force_mode == "raw" and not values:
        raise CodecError("an empty batch has no raw wire representation")

    if use_ef:
        return EncodedBatch(
            "elias_fano", values, ef_beats, len(payload), 1 + cw, lw, high_count
        )
    return EncodedBatch("raw", values, raw_beats, len(raw_bits), 0, 0, 0)


class StreamDecoder:
    """Fail-closed decoder for concatenated raw words and Elias--Fano frames."""

    def __init__(self, *, num_sources: int, max_batch: int) -> None:
        self.num_sources = num_sources
        self.max_batch = max_batch
        self.aw = address_width(num_sources)
        self.cw = count_width(max_batch)
        self.state = "raw"
        self.raw_bits = ""
        self.header = ""
        self.event_count = 0
        self.low_width = 0
        self.high_cursor = 0
        self.high_values: list[int] = []
        self.low_bits = ""
        self.output: list[int] = []

    def feed(self, beat: Beat) -> list[int]:
        emitted: list[int] = []
        if self.state == "raw":
            if beat.count == 1:
                if self.raw_bits or beat.bits != "1":
                    raise CodecError("illegal Elias--Fano marker at raw boundary")
                self.state = "header"
                self.header = ""
                return emitted
            self.raw_bits += beat.bits
            while len(self.raw_bits) >= self.aw:
                word, self.raw_bits = self.raw_bits[:self.aw], self.raw_bits[self.aw:]
                source = int(word, 2)
                if source >= self.num_sources:
                    raise CodecError("raw source outside configured universe")
                self.output.append(source)
                emitted.append(source)
            return emitted

        cursor = 0
        while cursor < beat.count:
            bit = beat.bits[cursor]
            cursor += 1
            if self.state == "header":
                self.header += bit
                if len(self.header) == self.cw:
                    self.event_count = int(self.header, 2)
                    if self.event_count > self.max_batch:
                        raise CodecError("Elias--Fano event count exceeds max_batch")
                    if self.event_count == 0:
                        self._finish_frame(emitted)
                    else:
                        self.low_width = low_width(self.num_sources, self.event_count)
                        self.high_cursor = 0
                        self.high_values = []
                        self.state = "high"
            elif self.state == "high":
                if bit == "0":
                    self.high_cursor += 1
                    if (self.high_cursor << self.low_width) >= self.num_sources:
                        raise CodecError("Elias--Fano high part exceeds universe")
                else:
                    self.high_values.append(self.high_cursor)
                    if len(self.high_values) == self.event_count:
                        if self.low_width == 0:
                            values = list(self.high_values)
                            self._validate_decoded(values)
                            self.output.extend(values)
                            emitted.extend(values)
                            self._finish_frame(emitted, already_emitted=True)
                        else:
                            self.low_bits = ""
                            self.state = "low"
            elif self.state == "low":
                self.low_bits += bit
                required = self.event_count * self.low_width
                if len(self.low_bits) == required:
                    values = []
                    for index, high in enumerate(self.high_values):
                        start = index * self.low_width
                        low = int(self.low_bits[start:start + self.low_width], 2)
                        values.append((high << self.low_width) | low)
                    self._validate_decoded(values)
                    self.output.extend(values)
                    emitted.extend(values)
                    self._finish_frame(emitted, already_emitted=True)
            else:  # pragma: no cover - defensive state corruption guard
                raise CodecError("decoder entered an unknown state")

            if self.state == "raw" and cursor != beat.count:
                raise CodecError("trailing bits after Elias--Fano frame")
        return emitted

    def _validate_decoded(self, values: Sequence[int]) -> None:
        if len(values) != self.event_count:
            raise CodecError("decoded Elias--Fano event-count mismatch")
        if any(value >= self.num_sources for value in values):
            raise CodecError("decoded Elias--Fano source outside universe")
        if any(left >= right for left, right in zip(values, values[1:])):
            raise CodecError("decoded Elias--Fano sources are not strictly increasing")

    def _finish_frame(
        self, emitted: list[int], *, already_emitted: bool = False
    ) -> None:
        if self.event_count == 0 and not already_emitted:
            # Empty batches are framing events, not logical AER occurrences.
            pass
        self.state = "raw"
        self.header = ""
        self.event_count = 0
        self.low_width = 0
        self.high_cursor = 0
        self.high_values = []
        self.low_bits = ""

    def finish(self) -> None:
        if self.state != "raw" or self.raw_bits:
            raise CodecError("truncated link stream")


def decode_beats(
    beats: Iterable[Beat], *, num_sources: int, max_batch: int
) -> list[int]:
    decoder = StreamDecoder(num_sources=num_sources, max_batch=max_batch)
    for beat in beats:
        decoder.feed(beat)
    decoder.finish()
    return decoder.output


def batch_events(
    events: Iterable[Event], *, max_batch: int, window_cycles: int
) -> list[EventBatch]:
    """Form bounded, source-unique batches and order each batch by source.

    A refire closes the older batch before the new occurrence is admitted.  A
    partial final batch closes at its bounded timeout, not at an unbounded EOF.
    """

    if max_batch < 1 or window_cycles < 0:
        raise ValueError("max_batch must be positive and window_cycles nonnegative")
    ordered = sorted(events)
    if len({event.sequence for event in ordered}) != len(ordered):
        raise CodecError("TB-only sequence identities must be unique")
    batches: list[EventBatch] = []
    current: list[Event] = []
    sources: set[int] = set()
    opened = 0

    def flush(closed: int, reason: str) -> None:
        nonlocal current, sources, opened
        if not current:
            return
        monotone = tuple(sorted(current, key=lambda event: event.source))
        batches.append(EventBatch(monotone, opened, closed, reason))
        current = []
        sources = set()

    for event in ordered:
        if event.occurrence_cycle < 0:
            raise CodecError("occurrence cycle must be nonnegative")
        if current and event.occurrence_cycle > opened + window_cycles:
            flush(opened + window_cycles, "timeout")
        if current and event.source in sources:
            flush(event.occurrence_cycle, "refire")
        if not current:
            opened = event.occurrence_cycle
        current.append(event)
        sources.add(event.source)
        if len(current) == max_batch:
            flush(event.occurrence_cycle, "full")
    if current:
        flush(opened + window_cycles, "partial")
    return batches


def restore_provenance(
    batch: EventBatch, decoded_sources: Sequence[int]
) -> tuple[Event, ...]:
    """Join decoded addresses to scoreboard-only provenance without copying it."""

    by_source = {event.source: event for event in batch.events}
    if len(by_source) != len(batch.events):
        raise CodecError("batch provenance contains a source refire")
    if tuple(decoded_sources) != tuple(sorted(by_source)):
        raise CodecError("decoded address set does not match accepted batch")
    return tuple(by_source[source] for source in decoded_sources)


def concatenate_beats(frames: Iterable[EncodedBatch]) -> Iterator[Beat]:
    for frame in frames:
        yield from frame.beats
