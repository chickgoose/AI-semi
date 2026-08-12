#!/usr/bin/env python3
"""Independent executable model for the A2 batched-IWRR-K2 boundary."""

from __future__ import annotations

from dataclasses import dataclass

CALENDAR = (1, 2, 0, 1, 2, 3, 1, 2, 1, 2, 1, 2)


@dataclass(frozen=True)
class Offer:
    count: int
    address: tuple[int, int]
    bitmap: int
    next_cursor: int
    next_pointers: tuple[int, int, int, int]


def pick_column(row_mask: int, pointer: int) -> int | None:
    for offset in range(4):
        column = (pointer + offset) & 3
        if row_mask & (1 << column):
            return column
    return None


def choose_row(req: int, preferred: int) -> int | None:
    """Prefer the calendar row, then fall back cyclically without credit."""
    for offset in range(4):
        row = (preferred + offset) & 3
        if req & (0xF << (4 * row)):
            return row
    return None


def offer(req: int, cursor: int, pointers: tuple[int, int, int, int]) -> Offer:
    """Offer up to two ordered event grants and their post-commit state."""
    work = req & 0xFFFF
    scan = cursor % 12
    ptr = list(pointers)
    addresses: list[int] = []
    bitmap = 0
    for _ in range(2):
        row = choose_row(work, CALENDAR[scan])
        if row is None:
            break
        column = pick_column((work >> (4 * row)) & 0xF, ptr[row])
        assert column is not None
        source = 4 * row + column
        addresses.append(source)
        bitmap |= 1 << source
        work &= ~(1 << source)
        ptr[row] = (column + 1) & 3
        scan = (scan + 1) % 12
    padded = tuple(addresses + [0] * (2 - len(addresses)))
    return Offer(len(addresses), padded, bitmap, scan, tuple(ptr))


class Scheduler:
    def __init__(self) -> None:
        self.cursor = 0
        self.pointers = (0, 0, 0, 0)
        self.held: Offer | None = None

    def reset(self) -> None:
        self.cursor = 0
        self.pointers = (0, 0, 0, 0)
        self.held = None

    def cycle(self, req: int, bundle_ready: bool) -> Offer:
        result = self.held or offer(req, self.cursor, self.pointers)
        if result.count:
            if bundle_ready:
                self.cursor = result.next_cursor
                self.pointers = result.next_pointers
                self.held = None
            elif self.held is None:
                self.held = result
        return result


def accepted_addresses(result: Offer, bundle_ready: bool) -> tuple[int, ...]:
    return result.address[:result.count] if bundle_ready else ()
