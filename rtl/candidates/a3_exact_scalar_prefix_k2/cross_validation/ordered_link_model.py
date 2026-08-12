#!/usr/bin/env python3
"""Independent reference model for the charged A3 ordered K2 link.

This model intentionally contains no scheduler or RTL imports.  It describes
the link as a capacity-two sequence: retirement removes an ordered prefix and
an accepted offer appends zero, one, or two addresses atomically.
"""

from __future__ import annotations

from dataclasses import dataclass


CAPACITY = 2


@dataclass(frozen=True)
class LinkOutputs:
    offer_ready: bool
    retire_valid: int
    retire_addr0: int
    retire_addr1: int
    link_empty: bool
    retire_count: int


@dataclass(frozen=True)
class LinkTransition:
    before: tuple[int, ...]
    retired: tuple[int, ...]
    accepted: tuple[int, ...]
    after: tuple[int, ...]
    outputs: LinkOutputs


class OrderedLinkModel:
    """Capacity-two ordered transport with A5 ready-qualified lane 1."""

    def __init__(self) -> None:
        self.entries: tuple[int, ...] = ()

    @staticmethod
    def _check_inputs(
        offer_count: int, offer_addr0: int, offer_addr1: int, retire_ready: int
    ) -> None:
        if offer_count not in (0, 1, 2):
            raise ValueError(f"offer_count outside contract: {offer_count}")
        if not 0 <= offer_addr0 < 16 or not 0 <= offer_addr1 < 16:
            raise ValueError("offer address outside four-bit domain")
        if not 0 <= retire_ready < 4:
            raise ValueError("retire_ready outside two-bit domain")

    def observe(
        self, *, offer_count: int, offer_addr0: int, offer_addr1: int,
        retire_ready: int
    ) -> LinkOutputs:
        self._check_inputs(offer_count, offer_addr0, offer_addr1, retire_ready)
        count = len(self.entries)
        head_ready = bool(retire_ready & 1)
        tail_ready = bool(retire_ready & 2)
        retire_count = 0
        if count and head_ready:
            retire_count = 2 if count == 2 and tail_ready else 1
        remaining = count - retire_count
        offer_ready = offer_count <= CAPACITY - remaining
        return LinkOutputs(
            offer_ready=offer_ready,
            retire_valid=(int(count != 0) | (
                int(count == 2 and head_ready and tail_ready) << 1
            )),
            retire_addr0=self.entries[0] if count else 0,
            retire_addr1=self.entries[1] if count == 2 else 0,
            link_empty=(count == 0),
            retire_count=retire_count,
        )

    def step(
        self, *, rst: bool, offer_count: int, offer_addr0: int,
        offer_addr1: int, retire_ready: int
    ) -> LinkTransition:
        outputs = self.observe(
            offer_count=offer_count,
            offer_addr0=offer_addr0,
            offer_addr1=offer_addr1,
            retire_ready=retire_ready,
        )
        before = self.entries
        if rst:
            self.entries = ()
            return LinkTransition(before, (), (), self.entries, outputs)

        retired = before[:outputs.retire_count]
        remaining = before[outputs.retire_count:]
        offered = (offer_addr0, offer_addr1)[:offer_count]
        accepted = offered if offer_count and outputs.offer_ready else ()
        self.entries = remaining + accepted
        if len(self.entries) > CAPACITY:
            raise AssertionError("reference model overflowed capacity")
        return LinkTransition(before, retired, accepted, self.entries, outputs)

    def physical_state(self) -> tuple[int, int, int]:
        """Canonical RTL register image: vacant payload registers are zero."""

        return (
            len(self.entries),
            self.entries[0] if self.entries else 0,
            self.entries[1] if len(self.entries) == 2 else 0,
        )
