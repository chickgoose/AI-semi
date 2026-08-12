#!/usr/bin/env python3
"""Independent cycle reference for canonical WEIGHT=5 Fovea feeding A7 R1.

This is an executable behavioral oracle, not an RTL wrapper.  It intentionally
models only the frozen scalar address-only seam: one registered Fovea result can
handshake into one A7 R1 frame on each reference-clock edge.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from typing import Iterable, List, Optional, Sequence, Tuple


CENTER_MASK = 0b0110
PERIPH_MASK = 0b1001
WEIGHT = 5


class OracleViolation(RuntimeError):
    """An exact-once/order/address invariant failed."""


def _idx4(onehot: int) -> int:
    for index in range(4):
        if onehot & (1 << index):
            return index
    return 3  # Matches the canonical RTL default; ignored when grant is zero.


class Arbiter2:
    """Exact state transition of canonical arbiter2.v."""

    def __init__(self) -> None:
        self.last_gnt = 1

    def reset(self) -> None:
        self.last_gnt = 1

    def evaluate(self, req: int) -> int:
        prefer1 = self.last_gnt == 0
        gnt1 = bool(req & 0b10) and (prefer1 or not bool(req & 0b01))
        gnt0 = bool(req & 0b01) and not gnt1
        return int(gnt0) | (int(gnt1) << 1)

    def update(self, req: int, gnt: int) -> None:
        if req:
            self.last_gnt = (gnt >> 1) & 1


class Arbiter4Tree:
    """Three canonical 1-bit arbiters arranged as a two-level tree."""

    def __init__(self) -> None:
        self.lo = Arbiter2()
        self.hi = Arbiter2()
        self.top = Arbiter2()

    def reset(self) -> None:
        self.lo.reset()
        self.hi.reset()
        self.top.reset()

    def grant_and_update(self, req: int) -> int:
        req_lo = req & 0b0011
        req_hi = (req >> 2) & 0b0011
        gnt_lo = self.lo.evaluate(req_lo)
        gnt_hi = self.hi.evaluate(req_hi)
        group_req = int(bool(req_lo)) | (int(bool(req_hi)) << 1)
        group_gnt = self.top.evaluate(group_req)
        grant = gnt_lo if group_gnt & 1 else 0
        if group_gnt & 2:
            grant |= gnt_hi << 2
        self.lo.update(req_lo, gnt_lo)
        self.hi.update(req_hi, gnt_hi)
        self.top.update(group_req, group_gnt)
        return grant


class FoveaWeight5:
    """Canonical registered scalar Fovea model, including arbitration state."""

    def __init__(self) -> None:
        self.center = Arbiter4Tree()
        self.periphery = Arbiter4Tree()
        self.column = Arbiter4Tree()
        self.round = 0
        self.valid = False
        self.addr = 0

    def reset(self) -> None:
        self.center.reset()
        self.periphery.reset()
        self.column.reset()
        self.round = 0
        self.valid = False
        self.addr = 0

    def posedge(self, req: int, reset: bool = False) -> None:
        if reset:
            self.reset()
            return

        row_req = 0
        for row in range(4):
            if req & (0xF << (4 * row)):
                row_req |= 1 << row

        center_avail = bool(row_req & CENTER_MASK)
        periph_avail = bool(row_req & PERIPH_MASK)
        prefer_center = self.round != WEIGHT
        use_center = ((prefer_center and center_avail) or
                      (not prefer_center and not periph_avail and center_avail))
        use_periph = ((not prefer_center and periph_avail) or
                      (prefer_center and not center_avail and periph_avail))

        center_req = row_req & CENTER_MASK if use_center else 0
        periph_req = row_req & PERIPH_MASK if use_periph else 0
        center_gnt = self.center.grant_and_update(center_req)
        periph_gnt = self.periphery.grant_and_update(periph_req)
        row_gnt = center_gnt if use_center else (periph_gnt if use_periph else 0)

        row = _idx4(row_gnt)
        selected_columns = (req >> (4 * row)) & 0xF
        col_gnt = self.column.grant_and_update(selected_columns)
        self.valid = bool(row_gnt)
        self.addr = ((row & 0x3) << 2) | _idx4(col_gnt)
        if row_gnt:
            self.round = 0 if self.round == WEIGHT else self.round + 1


@dataclass(frozen=True)
class Event:
    occurrence: int
    address: int
    accept_cycle: int
    delivery_cycle: Optional[int] = None


class A7R1Endpoint:
    """Reference-edge model of A7@42377ca's frozen R1 observable contract."""

    def __init__(self) -> None:
        self.armed = False
        self.raw_pending: Optional[Event] = None
        self.retire_valid: Optional[Event] = None
        self.accepted: List[Event] = []
        self.delivered: List[Event] = []
        self._next_occurrence = 0

    @property
    def ready(self) -> bool:
        return self.armed

    @property
    def drain_idle(self) -> bool:
        return self.raw_pending is None and self.retire_valid is None

    def posedge(self, cycle: int, valid: bool, address: int,
                reset: bool = False, require_drained_reset: bool = True) -> bool:
        if reset:
            if require_drained_reset and not self.drain_idle:
                raise OracleViolation("RESET_WHILE_NOT_DRAINED")
            self.armed = False
            self.raw_pending = None
            self.retire_valid = None
            return False

        # The always-ready synchronous consumer samples the prior registered
        # retire_valid before the observer's update on this same edge.
        if self.retire_valid is not None:
            self.delivered.append(
                replace(self.retire_valid, delivery_cycle=cycle)
            )

        launch = bool(valid and self.ready)
        admitted = None
        if launch:
            admitted = Event(self._next_occurrence, address & 0xF, cycle)
            self._next_occurrence += 1
            self.accepted.append(admitted)

        # A frame admitted on the preceding edge committed at the intervening
        # burst-clock fall and is registered by the observer on this edge.
        self.retire_valid = self.raw_pending
        self.raw_pending = admitted
        self.armed = True
        return launch


def check_exact(accepted: Sequence[Event], delivered: Sequence[Event]) -> None:
    if len(delivered) < len(accepted):
        raise OracleViolation("DROP_DETECTED")
    if len(delivered) > len(accepted):
        raise OracleViolation("DUPLICATE_DETECTED")
    for expected, observed in zip(accepted, delivered):
        if observed.occurrence != expected.occurrence:
            raise OracleViolation("REORDER_DETECTED")
        if observed.address != expected.address:
            raise OracleViolation("ADDRESS_MISMATCH")
        if observed.delivery_cycle != expected.accept_cycle + 2:
            raise OracleViolation("LATENCY_MISMATCH")


@dataclass
class RunResult:
    fovea_addresses: List[int]
    accepted: List[Event]
    delivered: List[Event]
    accept_cycles: List[int]
    reset_cycles: List[int]


def run_full_contention(active_cycles: int = 24) -> RunResult:
    """Reset, run all 16 requests, drain, legally reset, and run again."""
    fovea = FoveaWeight5()
    endpoint = A7R1Endpoint()
    fovea_addresses: List[int] = []
    accept_cycles: List[int] = []
    reset_cycles: List[int] = []

    # Initial reset edge.
    endpoint.posedge(0, fovea.valid, fovea.addr, reset=True)
    fovea.posedge(0, reset=True)
    reset_cycles.append(0)

    cycle = 1
    for _ in range(active_cycles):
        old_valid, old_addr = fovea.valid, fovea.addr
        if endpoint.posedge(cycle, old_valid, old_addr):
            accept_cycles.append(cycle)
        fovea.posedge(0xFFFF)
        if fovea.valid:
            fovea_addresses.append(fovea.addr)
        cycle += 1

    # Stop requests.  Continue until the registered Fovea result and every A7
    # observer/consumer stage have drained.
    while True:
        old_valid, old_addr = fovea.valid, fovea.addr
        if endpoint.posedge(cycle, old_valid, old_addr):
            accept_cycles.append(cycle)
        fovea.posedge(0)
        cycle += 1
        if not fovea.valid and endpoint.drain_idle:
            break
        if cycle > active_cycles + 16:
            raise OracleViolation("DRAIN_TIMEOUT")

    endpoint.posedge(cycle, fovea.valid, fovea.addr, reset=True)
    fovea.posedge(0, reset=True)
    reset_cycles.append(cycle)
    cycle += 1

    # A post-reset event proves arm behavior and absence of stale retirement.
    for req in (0x0001, 0, 0, 0, 0):
        old_valid, old_addr = fovea.valid, fovea.addr
        if endpoint.posedge(cycle, old_valid, old_addr):
            accept_cycles.append(cycle)
        fovea.posedge(req)
        cycle += 1

    check_exact(endpoint.accepted, endpoint.delivered)
    return RunResult(fovea_addresses, endpoint.accepted, endpoint.delivered,
                     accept_cycles, reset_cycles)


def _event_dict(event: Event) -> dict:
    return {
        "occurrence": event.occurrence,
        "address": event.address,
        "accept_cycle": event.accept_cycle,
        "delivery_cycle": event.delivery_cycle,
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-cycles", type=int, default=24)
    args = parser.parse_args(argv)
    if args.active_cycles < 12:
        parser.error("--active-cycles must be at least 12")
    result = run_full_contention(args.active_cycles)
    first_twelve_rows = [address >> 2 for address in result.fovea_addresses[:12]]
    row_counts = [first_twelve_rows.count(row) for row in range(4)]
    print(json.dumps({
        "decision": "PASS",
        "active_cycles": args.active_cycles,
        "first_twelve_rows": first_twelve_rows,
        "first_twelve_row_counts": row_counts,
        "accepted": len(result.accepted),
        "delivered": len(result.delivered),
        "accept_cycles": result.accept_cycles,
        "reset_cycles": result.reset_cycles,
        "events": [_event_dict(event) for event in result.delivered],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
