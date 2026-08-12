#!/usr/bin/env python3
"""Independent address-only fovea -> A7 boundary oracle.

Occurrence IDs are scoreboard-only metadata.  They never reconstruct an
address or create a DUT event.  A native result must consume a real outstanding
request credit, while an A7 R1 event is admitted on every valid && ready sample.
The endpoint output is available one cycle later and is sampled by a real
pre-NBA synchronous sink two cycles after admission.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Event:
    cycle: int
    kind: str
    occurrence: int | None = None
    address: int | None = None
    valid: bool | None = None
    ready: bool | None = None
    drain_idle: bool | None = None
    launch_fire: bool | None = None
    retire_valid: bool | None = None


@dataclass(frozen=True)
class Fault:
    code: str
    cycle: int
    detail: str


@dataclass(frozen=True)
class Result:
    faults: tuple[Fault, ...]
    native: tuple[tuple[int, int], ...]
    accepted: tuple[tuple[int, int], ...]
    retired: tuple[tuple[int, int], ...]

    @property
    def passed(self) -> bool:
        return not self.faults


@dataclass(frozen=True)
class Occurrence:
    ident: int
    address: int
    cycle: int
    epoch: int
    first_after_reset: bool = False
    continuous_valid: bool = False


class FoveaA7Oracle:
    """Fail-closed causal-credit, handshake, latency, and drain checker."""

    KINDS = {
        "reset_assert", "reset_release", "request", "native", "source",
        "launch", "available", "retire", "drain",
    }

    def check(self, events: Iterable[Event]) -> Result:
        faults: list[Fault] = []
        native_log: list[tuple[int, int]] = []
        accepted_log: list[tuple[int, int]] = []
        retired_log: list[tuple[int, int]] = []
        request_credit: dict[int, deque[Occurrence]] = defaultdict(deque)
        launch_due: deque[Occurrence] = deque()
        available_due: deque[Occurrence] = deque()
        retire_due: deque[Occurrence] = deque()
        aborted: set[int] = set()
        seen_ids: set[int] = set()
        reset_active = False
        first_accept_pending = True
        prior_source_valid = False
        epoch = 0
        last_cycle = -1

        def add(code: str, event: Event, detail: str) -> None:
            faults.append(Fault(code, event.cycle, detail))

        def validate_identity(event: Event) -> bool:
            if event.occurrence is None or event.address is None:
                add("BAD_EVENT", event, "occurrence and address are required")
                return False
            if not 0 <= event.address < 16:
                add("BAD_ADDRESS", event, "address-only N16 requires address 0..15")
                return False
            return True

        for event in events:
            if event.kind not in self.KINDS:
                add("UNKNOWN_EVENT", event, event.kind)
                continue
            if event.cycle < last_cycle:
                add("TIME_REORDER", event, "event cycles must be monotonic")
            last_cycle = max(last_cycle, event.cycle)

            if event.kind == "reset_assert":
                for queue in request_credit.values():
                    aborted.update(item.ident for item in queue)
                for queue in (launch_due, available_due, retire_due):
                    aborted.update(item.ident for item in queue)
                    queue.clear()
                request_credit.clear()
                reset_active = True
                first_accept_pending = True
                prior_source_valid = False
                epoch += 1
                continue

            if event.kind == "reset_release":
                reset_active = False
                prior_source_valid = False
                continue

            if event.kind == "request":
                if reset_active:
                    add("REQUEST_DURING_RESET", event, "request credit cannot form in reset")
                    continue
                if not validate_identity(event):
                    continue
                if event.occurrence in seen_ids:
                    add("DUPLICATE_OCCURRENCE_ID", event, "occurrence ID was reused")
                    continue
                seen_ids.add(event.occurrence)
                request_credit[event.address].append(
                    Occurrence(event.occurrence, event.address, event.cycle, epoch)
                )
                continue

            if event.kind == "native":
                if reset_active:
                    add("NATIVE_DURING_RESET", event, "native result must be reset-quiet")
                    continue
                if event.address is None or not 0 <= event.address < 16:
                    add("BAD_NATIVE_ADDRESS", event, "native result needs an N16 address")
                    continue
                credits = request_credit[event.address]
                if not credits:
                    add("NATIVE_DUPLICATE_NO_REQUEST", event,
                        "native result has no outstanding request-mask credit")
                    continue
                expected = credits[0]
                if event.cycle <= expected.cycle:
                    add("STALE_RETRIGGER_CAUSALITY", event,
                        "result cannot be caused by a same-cycle retrigger")
                    continue
                credits.popleft()
                if event.occurrence is not None and event.occurrence != expected.ident:
                    add("NATIVE_OCCURRENCE_ORDER", event,
                        "scoreboard occurrence differs from oldest address credit")
                native_log.append((expected.ident, expected.address))
                continue

            if event.kind == "source":
                if not isinstance(event.valid, bool) or not isinstance(event.ready, bool):
                    add("BAD_SOURCE_SAMPLE", event, "valid and ready must be boolean")
                    continue
                if reset_active and event.ready:
                    add("READY_DURING_RESET", event, "ready must be low in reset")
                accepted_now = event.valid and event.ready and not reset_active
                if accepted_now:
                    if not validate_identity(event):
                        prior_source_valid = event.valid
                        continue
                    if event.occurrence in seen_ids:
                        add("DUPLICATE_HANDSHAKE", event, "occurrence accepted twice")
                    else:
                        seen_ids.add(event.occurrence)
                        item = Occurrence(
                            event.occurrence, event.address, event.cycle, epoch,
                            first_after_reset=first_accept_pending,
                            continuous_valid=prior_source_valid,
                        )
                        first_accept_pending = False
                        launch_due.append(item)
                        accepted_log.append((item.ident, item.address))
                prior_source_valid = event.valid
                continue

            if event.kind == "launch":
                if reset_active:
                    add("LAUNCH_DURING_RESET", event, "launch must be reset-quiet")
                    continue
                if not launch_due:
                    add("PHANTOM_OR_DUPLICATE_LAUNCH", event,
                        "launch has no valid-ready admission")
                    continue
                expected = launch_due[0]
                if event.cycle != expected.cycle:
                    add("LAUNCH_TIMING", event, "R1 launch must equal the handshake edge")
                if event.occurrence != expected.ident or event.address != expected.address:
                    add("LAUNCH_MISMATCH", event, "launch changed occurrence or address")
                launch_due.popleft()
                available_due.append(expected)
                continue

            if event.kind == "available":
                if event.occurrence in aborted:
                    add("STALE_POST_RESET_EVENT", event,
                        "aborted pre-reset occurrence became available")
                    continue
                if not available_due:
                    add("PHANTOM_OR_DUPLICATE_AVAILABLE", event,
                        "availability has no launched occurrence")
                    continue
                expected = available_due[0]
                if event.cycle != expected.cycle + 1:
                    add("AVAILABILITY_LATENCY", event,
                        "registered output must be available admission+1")
                if event.occurrence != expected.ident or event.address != expected.address:
                    add("AVAILABLE_ADDRESS_SWAP", event,
                        "available occurrence/address differs from admission order")
                available_due.popleft()
                retire_due.append(expected)
                continue

            if event.kind == "retire":
                if event.occurrence in aborted:
                    add("STALE_POST_RESET_EVENT", event,
                        "aborted pre-reset occurrence reached the sink")
                    continue
                if not retire_due:
                    add("PHANTOM_OR_DUPLICATE_RETIRE", event,
                        "sink sampled no registered pending output")
                    continue
                expected = retire_due[0]
                if event.cycle != expected.cycle + 2:
                    add("SINK_LATENCY", event,
                        "pre-NBA sink must retire at admission+2")
                if event.occurrence != expected.ident or event.address != expected.address:
                    add("RETIRED_ADDRESS_SWAP", event,
                        "sink occurrence/address differs from admission order")
                retire_due.popleft()
                retired_log.append((expected.ident, expected.address))
                continue

            if not all(isinstance(value, bool) for value in
                       (event.drain_idle, event.launch_fire, event.retire_valid)):
                add("BAD_DRAIN_SAMPLE", event,
                    "drain_idle/launch_fire/retire_valid must be boolean")
                continue
            pipeline_busy = bool(launch_due or available_due or retire_due)
            if event.drain_idle and (event.launch_fire or event.retire_valid or pipeline_busy):
                add("PREMATURE_DRAIN", event,
                    "drain_idle hid launch, registered output, or in-flight state")

        end = Event(max(last_cycle, 0), "drain")
        while launch_due:
            item = launch_due.popleft()
            if item.first_after_reset:
                add("RESET_FIRST_EDGE_LOSS", end,
                    f"first legal post-reset occurrence {item.ident} was dropped")
            elif item.continuous_valid:
                add("VALID_EDGE_DETECTOR_DROP", end,
                    f"continuous-valid occurrence {item.ident} was suppressed")
            else:
                add("MISSING_LAUNCH", end, f"occurrence {item.ident} was not launched")
        if available_due:
            add("MISSING_AVAILABLE", end,
                f"{len(available_due)} launched occurrence(s) lack output availability")
        if retire_due:
            add("MISSING_RETIRE", end,
                f"{len(retire_due)} available occurrence(s) lack sink retirement")
        for address, credits in request_credit.items():
            if credits:
                add("MISSING_NATIVE", end,
                    f"address {address} retains {len(credits)} request credit(s)")

        return Result(tuple(faults), tuple(native_log), tuple(accepted_log),
                      tuple(retired_log))


def legal_endpoint_event(cycle: int, occurrence: int, address: int) -> list[Event]:
    return [
        Event(cycle, "source", occurrence, address, valid=True, ready=True),
        Event(cycle, "launch", occurrence, address),
        Event(cycle + 1, "available", occurrence, address),
        Event(cycle + 2, "retire", occurrence, address),
    ]
