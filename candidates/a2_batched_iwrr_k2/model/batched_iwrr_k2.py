#!/usr/bin/env python3
"""Independent executable model for the A2 batched-IWRR-K2 boundary."""

from __future__ import annotations

from dataclasses import dataclass

CALENDAR = (1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2)
BATCHES = tuple((CALENDAR[2 * phase], CALENDAR[2 * phase + 1]) for phase in range(6))


@dataclass(frozen=True)
class Offer:
    valid: tuple[bool, bool]
    address: tuple[int, int]
    bitmap: int
    next_phase: int
    next_pointers: tuple[int, int, int, int]


def pick_column(row_mask: int, pointer: int) -> int | None:
    for offset in range(4):
        column = (pointer + offset) & 3
        if row_mask & (1 << column):
            return column
    return None


def offer(req: int, phase: int, pointers: tuple[int, int, int, int]) -> Offer:
    """Offer the current two-token batch, compacting nonempty survivors.

    An empty entitlement is waived, never borrowed or banked.  The caller
    commits both surviving event grants atomically and then advances one phase.
    """
    addresses: list[int] = []
    ptr = list(pointers)
    bitmap = 0
    for row in BATCHES[phase % 6]:
        column = pick_column((req >> (4 * row)) & 0xF, ptr[row])
        if column is None:
            continue
        source = 4 * row + column
        addresses.append(source)
        bitmap |= 1 << source
        ptr[row] = (column + 1) & 3
    padded = tuple(addresses + [0] * (2 - len(addresses)))
    return Offer((len(addresses) >= 1, len(addresses) >= 2), padded, bitmap,
                 (phase + 1) % 6, tuple(ptr))


class Scheduler:
    def __init__(self) -> None:
        self.phase = 0
        self.pointers = (0, 0, 0, 0)

    def reset(self) -> None:
        self.phase = 0
        self.pointers = (0, 0, 0, 0)

    def cycle(self, req: int, ready: bool) -> Offer:
        result = offer(req, self.phase, self.pointers)
        if not result.valid[0]:
            self.phase = result.next_phase
        elif ready:
            self.phase = result.next_phase
            self.pointers = result.next_pointers
        return result


def accepted_addresses(result: Offer, ready: bool) -> tuple[int, ...]:
    if not ready:
        return ()
    return tuple(result.address[i] for i in range(2) if result.valid[i])
