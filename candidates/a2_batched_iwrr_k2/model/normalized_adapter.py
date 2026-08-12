#!/usr/bin/env python3
"""Independent state model for the charged A2 normalized K2 adapter."""

from __future__ import annotations

from dataclasses import dataclass

from batched_iwrr_k2 import Offer, Scheduler, offer


@dataclass(frozen=True)
class Record:
    source: int
    event: int


@dataclass(frozen=True)
class TransportResult:
    valid: tuple[bool, bool]
    output: tuple[Record | None, Record | None]
    retired: tuple[Record, ...]
    accepted: tuple[Record, ...]
    queue: tuple[Record, ...]
    offer_ready: bool


@dataclass(frozen=True)
class AdapterObservation:
    source_ready: int
    retire_valid: tuple[bool, bool]
    retire_output: tuple[Record | None, Record | None]
    drain_idle: bool
    queue_before: tuple[Record, ...]
    owner_cursor_before: int
    owner_pointers_before: tuple[int, int, int, int]
    owner_held_before: Offer | None
    owner_offer: Offer | None
    owner_fire: bool


def transport_step(
    queue: tuple[Record, ...],
    offered: tuple[Record, ...],
    retire_ready: tuple[bool, bool],
    reset_n: bool = True,
) -> TransportResult:
    """Apply one ordered retire/atomic-offer edge to the two-entry FIFO."""
    if len(queue) > 2 or len(offered) > 2:
        raise ValueError("K2 transport accepts at most two queued/offered records")

    output: tuple[Record | None, Record | None] = (
        queue[0] if queue else None,
        queue[1] if len(queue) == 2 else None,
    )
    if not reset_n:
        return TransportResult(
            (False, False), output, (), (), (), False,
        )

    lane0_valid = bool(queue)
    # Lane 1 is deliberately ready-qualified so it cannot bypass lane 0.
    lane1_valid = len(queue) == 2 and retire_ready[0] and retire_ready[1]
    retire_count = int(lane0_valid and retire_ready[0])
    if lane1_valid:
        retire_count = 2
    retired = queue[:retire_count]
    remaining = queue[retire_count:]
    offer_ready = len(offered) <= 2 - len(remaining)
    accepted = offered if offered and offer_ready else ()
    next_queue = remaining + accepted
    if len(next_queue) > 2:
        raise AssertionError("atomic capacity invariant violated")
    return TransportResult(
        (lane0_valid, lane1_valid), output, retired, accepted,
        next_queue, offer_ready,
    )


class NormalizedAdapter:
    """Combined independent owner and normalized transport reference model."""

    def __init__(self) -> None:
        self.owner = Scheduler()
        self.queue: tuple[Record, ...] = ()

    def reset(self) -> None:
        self.owner.reset()
        self.queue = ()

    def step(
        self,
        source_valid: int,
        source_event: tuple[int, ...],
        retire_ready: tuple[bool, bool],
        reset_n: bool = True,
    ) -> AdapterObservation:
        if len(source_event) != 16:
            raise ValueError("the N16 boundary requires sixteen source payloads")

        queue_before = self.queue
        cursor_before = self.owner.cursor
        pointers_before = self.owner.pointers
        held_before = self.owner.held

        if not reset_n:
            transport = transport_step(queue_before, (), retire_ready, False)
            observation = AdapterObservation(
                source_ready=0,
                retire_valid=transport.valid,
                retire_output=(None, None),
                drain_idle=True,
                queue_before=queue_before,
                owner_cursor_before=cursor_before,
                owner_pointers_before=pointers_before,
                owner_held_before=held_before,
                owner_offer=None,
                owner_fire=False,
            )
            self.reset()
            return observation

        candidate = held_before or offer(
            source_valid, cursor_before, pointers_before,
        )
        # Retire movement is evaluated before capacity, matching the RTL's
        # same-edge pop/refill behavior.  Payloads are added only after the
        # complete owner bundle is known to fit.
        empty_offer = transport_step(queue_before, (), retire_ready)
        whole_bundle_fits = candidate.count <= 2 - len(empty_offer.queue)
        owner_result = self.owner.cycle(source_valid, whole_bundle_fits)
        if owner_result != candidate:
            raise AssertionError("owner offer changed while deciding capacity")
        owner_fire = candidate.count != 0 and whole_bundle_fits
        offered_records = tuple(
            Record(source, source_event[source])
            for source in candidate.address[:candidate.count]
        )
        transport = transport_step(queue_before, offered_records, retire_ready)
        if bool(transport.accepted) != owner_fire:
            raise AssertionError("owner and transport acceptance diverged")
        self.queue = transport.queue

        return AdapterObservation(
            source_ready=candidate.bitmap if owner_fire else 0,
            retire_valid=transport.valid,
            retire_output=transport.output,
            drain_idle=(source_valid & 0xFFFF) == 0
            and held_before is None
            and not queue_before,
            queue_before=queue_before,
            owner_cursor_before=cursor_before,
            owner_pointers_before=pointers_before,
            owner_held_before=held_before,
            owner_offer=candidate,
            owner_fire=owner_fire,
        )
