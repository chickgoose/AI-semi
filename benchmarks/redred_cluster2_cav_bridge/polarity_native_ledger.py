"""Fail-closed verifier for raw Ganghee address-and-polarity observations.

The verifier reparses the authoritative ``cycle addr_mask polarity_mask``
trace and a raw, cycle-complete native ledger.  It does not consume event IDs,
joined event metadata, runner summaries, or a testbench shadow FIFO.  Instead it
reconstructs one two-entry polarity FIFO per source and checks the observed
overrun and registered native lane signals against that state.

The native interface cannot distinguish two queued occurrences at the same
source when their polarity values are equal.  Consequently this verifier proves
the per-source FIFO *polarity sequence*, but deliberately makes no independent
event-identity ordering claim inside such observationally identical runs.
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Deque, Dict, Optional, Sequence, Tuple


LEDGER_SCHEMA = "redred.cluster2_cav_bridge.polarity_native_ledger/v1"
IDENTITY_SCOPE = (
    "SOURCE_FIFO_POLARITY_SEQUENCE_ONLY;"
    "IDENTICAL_SAME_SOURCE_EQUAL_POLARITY_EVENTS_UNOBSERVABLE;"
    "EVENT_ID_ORDER_INDEPENDENCE_NOT_CLAIMED"
)
SCOPE_LINE = "SCOPE|" + IDENTITY_SCOPE
DUPLICATE_SCOPE = "REPEATED_RAW_CYCLE_OR_CYCLE_SOURCE_RETIREMENT"

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_CYCLE = (1 << 63) - 1
NATIVE_DRAIN_LIMIT = 100_000

_TRACE_LINE = re.compile(r"(0|[1-9][0-9]*) ([0-9a-f]{4}) ([0-9a-f]{4})\Z")
_UINT = re.compile(r"0|[1-9][0-9]*\Z")
_HEX4 = re.compile(r"[0-9a-f]{4}\Z")
_HEX1 = re.compile(r"[0-9a-f]\Z")
_BIT = re.compile(r"[01]\Z")
_ROW = re.compile(r"[0-3]\Z")


class PolarityNativeLedgerError(ValueError):
    """The address/polarity trace and raw native ledger are inconsistent."""


@dataclass(frozen=True)
class TraceOccurrence:
    occurrence_cycle: int
    source_index: int
    polarity: int


@dataclass(frozen=True)
class LaneObservation:
    valid: int
    row: int
    col_mask: int
    pol_mask: int


@dataclass(frozen=True)
class CycleObservation:
    cycle: int
    preedge_overrun: int
    lane0: LaneObservation
    lane1: LaneObservation


@dataclass(frozen=True)
class PolarityVerificationReport:
    schema: str
    generated: int
    delivered: int
    overrun: int
    phantom: int
    duplicate: int
    drain_empty: bool
    observed_cycles: int
    final_cycle: int
    trace_line_endings: str
    identity_scope: str
    identity_order_independence_claimed: bool
    duplicate_scope: str


def _trace_lines(payload: bytes) -> Tuple[Tuple[str, ...], str]:
    if not payload or len(payload) > MAX_INPUT_BYTES:
        raise PolarityNativeLedgerError("addrpol trace must be non-empty and bounded")
    if b"\r\n" in payload:
        remainder = payload.replace(b"\r\n", b"")
        if remainder.find(b"\r") >= 0 or remainder.find(b"\n") >= 0 or not payload.endswith(b"\r\n"):
            raise PolarityNativeLedgerError("addrpol trace has mixed or malformed line endings")
        normalized = payload.replace(b"\r\n", b"\n")
        line_endings = "CRLF"
    else:
        if b"\r" in payload or not payload.endswith(b"\n"):
            raise PolarityNativeLedgerError("addrpol trace has mixed or malformed line endings")
        normalized = payload
        line_endings = "LF"
    try:
        text = normalized.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise PolarityNativeLedgerError("addrpol trace must be ASCII") from error
    lines = tuple(text[:-1].split("\n"))
    if any(not line for line in lines):
        raise PolarityNativeLedgerError("addrpol trace contains a blank line")
    if len(lines) > MAX_RECORDS:
        raise PolarityNativeLedgerError("addrpol trace has too many records")
    return lines, line_endings


def _ledger_lines(payload: bytes) -> Tuple[str, ...]:
    if not payload or len(payload) > MAX_INPUT_BYTES or not payload.endswith(b"\n"):
        raise PolarityNativeLedgerError("raw polarity ledger must be non-empty, bounded, and LF-terminated")
    if b"\r" in payload:
        raise PolarityNativeLedgerError("raw polarity ledger must use canonical LF endings")
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeError as error:
        raise PolarityNativeLedgerError("raw polarity ledger must be ASCII") from error
    lines = tuple(text[:-1].split("\n"))
    if any(not line for line in lines):
        raise PolarityNativeLedgerError("raw polarity ledger contains a blank line")
    if len(lines) > MAX_RECORDS + 3:
        raise PolarityNativeLedgerError("raw polarity ledger has too many records")
    return lines


def _uint(token: str, where: str) -> int:
    if _UINT.fullmatch(token) is None:
        raise PolarityNativeLedgerError("%s is not a canonical unsigned integer" % where)
    value = int(token, 10)
    if value > MAX_CYCLE:
        raise PolarityNativeLedgerError("%s exceeds the supported bound" % where)
    return value


def parse_addrpol_trace(payload: bytes) -> Tuple[Tuple[TraceOccurrence, ...], str]:
    """Parse the address/polarity source independently of any ledger identity."""
    lines, line_endings = _trace_lines(payload)
    occurrences = []
    previous_cycle = -1
    for line_number, line in enumerate(lines, 1):
        match = _TRACE_LINE.fullmatch(line)
        if match is None:
            raise PolarityNativeLedgerError(
                "addrpol line %d is not canonical 'cycle addr_mask polarity_mask'" % line_number
            )
        cycle = _uint(match.group(1), "addrpol cycle")
        address_mask = int(match.group(2), 16)
        polarity_mask = int(match.group(3), 16)
        if cycle <= previous_cycle:
            raise PolarityNativeLedgerError("addrpol cycles must be strictly increasing")
        if address_mask == 0:
            raise PolarityNativeLedgerError("addrpol address mask must be nonzero")
        if polarity_mask & ~address_mask:
            raise PolarityNativeLedgerError("addrpol polarity is asserted without an address occurrence")
        previous_cycle = cycle
        for source in range(16):
            if address_mask & (1 << source):
                occurrences.append(TraceOccurrence(
                    occurrence_cycle=cycle,
                    source_index=source,
                    polarity=(polarity_mask >> source) & 1,
                ))
                if len(occurrences) > MAX_RECORDS:
                    raise PolarityNativeLedgerError("addrpol occurrence count exceeds limit")
    return tuple(occurrences), line_endings


def _parse_lane(fields: Sequence[str], offset: int, lane: int, line_number: int) -> LaneObservation:
    valid_token, row_token, col_token, pol_token = fields[offset:offset + 4]
    if _BIT.fullmatch(valid_token) is None or _ROW.fullmatch(row_token) is None:
        raise PolarityNativeLedgerError("cycle line %d lane%d valid/row is malformed" % (line_number, lane))
    if _HEX1.fullmatch(col_token) is None or _HEX1.fullmatch(pol_token) is None:
        raise PolarityNativeLedgerError("cycle line %d lane%d masks are malformed" % (line_number, lane))
    observation = LaneObservation(
        valid=int(valid_token, 10),
        row=int(row_token, 10),
        col_mask=int(col_token, 16),
        pol_mask=int(pol_token, 16),
    )
    if not observation.valid:
        if observation.row or observation.col_mask or observation.pol_mask:
            raise PolarityNativeLedgerError("invalid native lane must be canonical all-zero")
    else:
        if not observation.col_mask:
            raise PolarityNativeLedgerError("valid native lane has an empty column mask")
        if observation.pol_mask & ~observation.col_mask:
            raise PolarityNativeLedgerError("hw_polarity is asserted outside the observed column mask")
    return observation


def _parse_observations(lines: Sequence[str]) -> Tuple[Tuple[CycleObservation, ...], Tuple[int, ...]]:
    if len(lines) < 4 or lines[0] != "SCHEMA|" + LEDGER_SCHEMA:
        raise PolarityNativeLedgerError("raw polarity ledger schema differs")
    if lines[1] != SCOPE_LINE:
        raise PolarityNativeLedgerError("raw polarity ledger observational scope differs")
    summary = lines[-1].split("|")
    if len(summary) != 7 or summary[0] != "SUMMARY":
        raise PolarityNativeLedgerError("raw polarity ledger summary is malformed")
    summary_values = tuple(_uint(token, "raw polarity ledger summary") for token in summary[1:])
    observations = []
    previous_cycle = -1
    for line_number, line in enumerate(lines[2:-1], 3):
        fields = line.split("|")
        if len(fields) != 11 or fields[0] != "CYCLE":
            raise PolarityNativeLedgerError("raw cycle line %d is malformed" % line_number)
        cycle = _uint(fields[1], "raw observation cycle")
        if cycle != previous_cycle + 1:
            raise PolarityNativeLedgerError(
                "raw observations must contain each cycle exactly once starting at zero"
            )
        if _HEX4.fullmatch(fields[2]) is None:
            raise PolarityNativeLedgerError("raw pre-edge overrun mask is malformed")
        lane0 = _parse_lane(fields, 3, 0, line_number)
        lane1 = _parse_lane(fields, 7, 1, line_number)
        observations.append(CycleObservation(
            cycle=cycle,
            preedge_overrun=int(fields[2], 16),
            lane0=lane0,
            lane1=lane1,
        ))
        previous_cycle = cycle
    if not observations:
        raise PolarityNativeLedgerError("raw polarity ledger has no cycle observations")
    return tuple(observations), summary_values


def _validate_lane_geometry(observation: CycleObservation) -> None:
    lane0 = observation.lane0
    lane1 = observation.lane1
    if lane0.valid and lane0.row not in (0, 1, 2):
        raise PolarityNativeLedgerError("native lane0 selected an impossible row")
    if lane1.valid and lane1.row not in (0, 2, 3):
        raise PolarityNativeLedgerError("native lane1 selected an impossible row")
    if lane0.valid and lane1.valid:
        legal_pairs = {(0, 3), (1, 0), (1, 2), (1, 3), (2, 0), (2, 3)}
        if (lane0.row, lane1.row) not in legal_pairs:
            raise PolarityNativeLedgerError("native lanes selected an impossible row pair")
    elif lane0.valid and lane0.row not in (1, 2):
        raise PolarityNativeLedgerError("native lane0-only row is impossible")
    elif lane1.valid and lane1.row not in (0, 3):
        raise PolarityNativeLedgerError("native lane1-only row is impossible")


def verify_polarity_native_ledger(
    trace_payload: bytes,
    ledger_payload: bytes,
) -> PolarityVerificationReport:
    """Reparse and verify raw lanes against address/polarity FIFO semantics."""
    occurrences, trace_line_endings = parse_addrpol_trace(trace_payload)
    observations, summary = _parse_observations(_ledger_lines(ledger_payload))
    latest_occurrence = max(item.occurrence_cycle for item in occurrences)
    final_cycle = observations[-1].cycle
    if final_cycle <= latest_occurrence:
        raise PolarityNativeLedgerError("raw ledger lacks a post-trace drain witness")
    if final_cycle > latest_occurrence + NATIVE_DRAIN_LIMIT:
        raise PolarityNativeLedgerError("raw ledger exceeds the bounded native drain")
    final = observations[-1]
    if final.preedge_overrun or final.lane0.valid or final.lane1.valid:
        raise PolarityNativeLedgerError("final drain witness is not quiescent")

    arrivals_by_cycle = {}  # type: Dict[int, Dict[int, int]]
    for occurrence in occurrences:
        arrivals_by_cycle.setdefault(occurrence.occurrence_cycle, {})[
            occurrence.source_index
        ] = occurrence.polarity

    queues = [deque() for _ in range(16)]  # type: list[Deque[int]]
    delivered = 0
    overrun = 0
    retired_slots = set()
    for observation in observations:
        _validate_lane_geometry(observation)
        arrivals = arrivals_by_cycle.get(observation.cycle, {})
        address_mask = sum(1 << source for source in arrivals)
        expected_overrun = sum(
            1 << source for source in arrivals if len(queues[source]) == 2
        )
        if observation.preedge_overrun & ~address_mask:
            raise PolarityNativeLedgerError("pre-edge overrun is asserted without an arrival")
        if observation.preedge_overrun != expected_overrun:
            raise PolarityNativeLedgerError("pre-edge overrun differs from arrival-and-full")
        overrun += observation.preedge_overrun.bit_count()

        sources_this_cycle = set()
        for lane_number, lane in enumerate((observation.lane0, observation.lane1)):
            if not lane.valid:
                continue
            for column in range(4):
                if not (lane.col_mask & (1 << column)):
                    continue
                source = lane.row * 4 + column
                slot = (observation.cycle, source)
                if slot in retired_slots or source in sources_this_cycle:
                    raise PolarityNativeLedgerError("duplicate raw cycle/source retirement")
                retired_slots.add(slot)
                sources_this_cycle.add(source)
                queue = queues[source]
                if not queue:
                    raise PolarityNativeLedgerError("phantom native retirement from an empty source FIFO")
                hw_polarity = (lane.pol_mask >> column) & 1
                if hw_polarity != queue[0]:
                    raise PolarityNativeLedgerError(
                        "hw_polarity differs from the per-source FIFO front"
                    )
                queue.popleft()
                delivered += 1
        if len(sources_this_cycle) > 8:
            raise PolarityNativeLedgerError("more than eight events retired in one cycle")

        for source, polarity in arrivals.items():
            if observation.preedge_overrun & (1 << source):
                continue
            queues[source].append(polarity)
            if len(queues[source]) > 2:
                raise PolarityNativeLedgerError("native per-source FIFO exceeded depth two")

    if any(queues):
        raise PolarityNativeLedgerError("native polarity FIFO drain is incomplete")

    generated = len(occurrences)
    if generated != delivered + overrun:
        raise PolarityNativeLedgerError("generated != delivered + overrun")
    expected_summary = (generated, delivered, overrun, 0, 0, 1)
    if summary != expected_summary:
        raise PolarityNativeLedgerError("raw polarity ledger summary differs from verified facts")

    return PolarityVerificationReport(
        schema=LEDGER_SCHEMA,
        generated=generated,
        delivered=delivered,
        overrun=overrun,
        phantom=0,
        duplicate=0,
        drain_empty=True,
        observed_cycles=len(observations),
        final_cycle=final_cycle,
        trace_line_endings=trace_line_endings,
        identity_scope=IDENTITY_SCOPE,
        identity_order_independence_claimed=False,
        duplicate_scope=DUPLICATE_SCOPE,
    )


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addrpol_trace", type=Path)
    parser.add_argument("raw_polarity_ledger", type=Path)
    options = parser.parse_args(arguments)
    try:
        report = verify_polarity_native_ledger(
            options.addrpol_trace.read_bytes(),
            options.raw_polarity_ledger.read_bytes(),
        )
        sys.stdout.write(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")) + "\n")
    except (OSError, PolarityNativeLedgerError) as error:
        print("POLARITY_NATIVE_LEDGER_FAIL: %s" % error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
