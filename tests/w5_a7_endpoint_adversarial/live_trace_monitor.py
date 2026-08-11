#!/usr/bin/env python3
"""Independent live protocol monitor for the planned A7 W5 R1 endpoint.

The monitor consumes observations, not owner RTL internals.  At R1 every core
posedge with valid && ready accepts exactly one occurrence.  Continuous valid
may therefore accept a different address every cycle.  Stability is required
only while valid && !ready; no valid-edge detector or one-shot is implied.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Observation:
    tick: int
    kind: str
    occurrence: int | None = None
    address: int | None = None
    valid: bool | None = None
    ready: bool | None = None
    can_accept: bool | None = None
    data: int | None = None
    stable: bool = True
    drain_idle: bool | None = None
    retire_valid: bool | None = None
    launch_fire: bool | None = None


@dataclass(frozen=True)
class Fault:
    code: str
    tick: int
    detail: str


@dataclass(frozen=True)
class Result:
    faults: tuple[Fault, ...]
    accepted: tuple[tuple[int, int], ...]
    delivered: tuple[tuple[int, int], ...]
    aborted: tuple[tuple[int, int], ...]

    @property
    def passed(self) -> bool:
        return not self.faults


@dataclass(frozen=True)
class AcceptedOccurrence:
    occurrence: int
    address: int
    epoch: int


class LiveTraceMonitor:
    """Fail-closed handshake, DDR-frame, reset, and ref-observer oracle.

    The primary boundary is phase-related synchronous R1: RX commits at burst
    fall and a charged seen-toggle detector observes it at the next ref rise.
    Duplicate/drop mutations exercise that observation boundary; they do not
    assert an unrelated-clock or 2FF-CDC implementation.
    """

    KINDS = {
        "source", "launch", "rise", "fall", "observer_publish",
        "sink_sample", "drain",
        "reset_assert", "reset_release",
    }

    def check(self, observations: Iterable[Observation]) -> Result:
        faults: list[Fault] = []
        accepted_log: list[tuple[int, int]] = []
        delivered_log: list[tuple[int, int]] = []
        aborted_log: list[tuple[int, int]] = []
        accepted_ids: set[int] = set()
        launch_queue: deque[AcceptedOccurrence] = deque()
        edge_queue: deque[AcceptedOccurrence] = deque()
        observer_queue: deque[AcceptedOccurrence] = deque()
        sink_queue: deque[AcceptedOccurrence] = deque()
        aborted_queue: deque[AcceptedOccurrence] = deque()
        stalled: tuple[int, int] | None = None
        frame: tuple[AcceptedOccurrence, int | None, bool] | None = None
        reset_active = False
        epoch = 0
        last_tick = -1

        def add(code: str, item: Observation, detail: str) -> None:
            faults.append(Fault(code, item.tick, detail))

        for item in observations:
            if item.kind not in self.KINDS:
                add("UNKNOWN_OBSERVATION", item, item.kind)
                continue
            if item.tick < last_tick:
                add("TIME_REORDER", item, "observations must be monotonic")
            last_tick = max(last_tick, item.tick)

            if item.kind == "reset_assert":
                in_flight = (list(launch_queue) + list(edge_queue) +
                             list(observer_queue) + list(sink_queue))
                if frame is not None:
                    in_flight.append(frame[0])
                if in_flight or stalled is not None:
                    add("RESET_IN_FLIGHT", item, "reset interrupted a live transaction")
                seen: set[int] = set()
                for pending in in_flight:
                    if pending.occurrence not in seen:
                        aborted_queue.append(pending)
                        aborted_log.append((pending.occurrence, pending.address))
                        seen.add(pending.occurrence)
                launch_queue.clear()
                edge_queue.clear()
                observer_queue.clear()
                sink_queue.clear()
                frame = None
                stalled = None
                reset_active = True
                epoch += 1
                continue

            if item.kind == "reset_release":
                reset_active = False
                continue

            if item.kind == "source":
                if not all(isinstance(value, bool) for value in
                           (item.valid, item.ready, item.can_accept)):
                    add("BAD_SOURCE_SAMPLE", item, "valid/ready/can_accept must be bool")
                    continue
                if item.ready and not item.can_accept:
                    add("FALSE_READY", item, "ready asserted without endpoint capacity")
                if reset_active and item.ready:
                    add("READY_DURING_RESET", item, "ready must be low during reset")
                if not item.valid:
                    if stalled is not None:
                        add("VALID_DROPPED_UNDER_STALL", item,
                            "valid dropped before a stalled transaction was accepted")
                        stalled = None
                    continue
                if item.occurrence is None or item.address is None or not 0 <= item.address < 16:
                    add("BAD_SOURCE_EVENT", item, "source event needs unique ID and N16 address")
                    continue
                presented = (item.occurrence, item.address)
                if stalled is not None and presented != stalled:
                    add("STALL_DATA_CHANGED", item, "transaction changed while ready was low")
                if not item.ready:
                    if stalled is None:
                        stalled = presented
                    continue
                if stalled is not None and presented == stalled:
                    stalled = None
                if item.occurrence in accepted_ids:
                    add("DUPLICATE_HANDSHAKE", item, "occurrence accepted more than once")
                    continue
                accepted_ids.add(item.occurrence)
                accepted = AcceptedOccurrence(item.occurrence, item.address, epoch)
                launch_queue.append(accepted)
                accepted_log.append(presented)
                continue

            if item.kind == "drain":
                if not all(isinstance(value, bool) for value in
                           (item.drain_idle, item.retire_valid, item.launch_fire)):
                    add("BAD_DRAIN_SAMPLE", item,
                        "drain_idle/retire_valid/launch_fire must be bool")
                elif item.drain_idle and (item.retire_valid or item.launch_fire):
                    add("FALSE_DRAIN_IDLE", item,
                        "drain_idle high while output or launch is pending")
                continue

            if item.kind == "launch":
                if reset_active:
                    add("LAUNCH_DURING_RESET", item, "launch observed during reset")
                    continue
                if not launch_queue:
                    add("DUPLICATE_OR_PHANTOM_LAUNCH", item,
                        "launch has no accepted occurrence")
                    continue
                expected = launch_queue[0]
                if item.occurrence is not None and item.occurrence != expected.occurrence:
                    add("LAUNCH_ORDER", item, "launch occurrence differs from handshake order")
                    continue
                launch_queue.popleft()
                edge_queue.append(expected)
                continue

            if item.kind == "rise":
                if reset_active:
                    add("RISE_DURING_RESET", item, "link must remain idle in reset")
                    continue
                if frame is not None:
                    add("RISE_OVER_OPEN_FRAME", item, "second rise before closing fall")
                    continue
                if not edge_queue:
                    add("EXTRA_RISE", item, "rise has no launched occurrence")
                    continue
                expected = edge_queue[0]
                stable = item.stable and item.data is not None
                if not stable:
                    add("UNSTABLE_RISE_DATA", item, "rise data is unknown or unstable")
                elif item.data != (expected.address & 0x3):
                    add("WRONG_LOW_HALF", item, "rise must carry address[1:0]")
                frame = (expected, item.data, stable)
                continue

            if item.kind == "fall":
                if reset_active:
                    # An asynchronous reset-forced fall is not a frame commit.
                    continue
                if frame is None:
                    add("EXTRA_FALL", item, "fall has no open frame")
                    continue
                expected, low_data, low_stable = frame
                high_stable = item.stable and item.data is not None
                if not high_stable:
                    add("UNSTABLE_FALL_DATA", item, "fall data is unknown or unstable")
                elif item.data != ((expected.address >> 2) & 0x3):
                    add("WRONG_HIGH_HALF", item, "fall must carry address[3:2]")
                if low_stable and high_stable and low_data is not None and item.data is not None:
                    reconstructed = (item.data << 2) | low_data
                    if reconstructed != expected.address:
                        add("FRAME_RECONSTRUCTION", item, "decoded address differs from handshake")
                edge_queue.popleft()
                observer_queue.append(expected)
                frame = None
                continue

            if item.kind == "observer_publish":
                if reset_active:
                    add("PUBLISH_DURING_RESET", item, "retire_valid published during reset")
                    continue
                if not observer_queue:
                    add("OBSERVER_DUPLICATE_OR_PHANTOM", item,
                        "registered retire_valid has no completed frame")
                    continue
                expected = observer_queue.popleft()
                if item.address != expected.address:
                    add("OBSERVER_ADDRESS_MISMATCH", item,
                        "registered retire address differs from completed frame")
                sink_queue.append(expected)
                continue

            if reset_active:
                add("SINK_SAMPLE_DURING_RESET", item, "sink sampled delivery during reset")
                continue
            if not sink_queue:
                stale_index = next(
                    (index for index, old in enumerate(aborted_queue)
                     if item.address == old.address), None
                )
                if stale_index is not None:
                    old = aborted_queue[stale_index]
                    add("STALE_POST_RESET_EVENT", item,
                        f"delivery matches aborted occurrence {old.occurrence}")
                else:
                    add("SINK_DUPLICATE_OR_PHANTOM", item,
                        "sink sampled no registered pending output")
                continue
            expected = sink_queue.popleft()
            if item.address != expected.address:
                add("SINK_ADDRESS_MISMATCH", item,
                    "posedge sink observed changed event address")
            if item.occurrence is not None and item.occurrence != expected.occurrence:
                add("SINK_OCCURRENCE_ORDER", item,
                    "posedge sink reordered occurrences")
            delivered_log.append((expected.occurrence, expected.address))

        end_tick = max(last_tick, 0)
        if stalled is not None:
            faults.append(Fault("STALLED_TRANSACTION_AT_END", end_tick,
                                "trace ended while valid was waiting for ready"))
        if frame is not None:
            faults.append(Fault("MISSING_FALL", end_tick, "trace ended with an open frame"))
        if edge_queue:
            faults.append(Fault("MISSING_EDGE_FRAME", end_tick,
                                f"{len(edge_queue)} launch(es) lack complete edges"))
        if launch_queue:
            faults.append(Fault("MISSING_LAUNCH", end_tick,
                                f"{len(launch_queue)} accepted occurrence(s) never launched"))
        if observer_queue:
            faults.append(Fault("OBSERVER_DROP", end_tick,
                                f"{len(observer_queue)} frame(s) were not published"))
        if sink_queue:
            faults.append(Fault("SINK_DROP", end_tick,
                                f"{len(sink_queue)} registered output(s) were not sampled"))
        return Result(tuple(faults), tuple(accepted_log), tuple(delivered_log),
                      tuple(aborted_log))


def legal_frame(base: int, occurrence: int, address: int) -> list[Observation]:
    """One R1 handshake, one launch/frame, and one CDC delivery."""

    return [
        Observation(base, "source", occurrence, address, True, True, True),
        Observation(base + 1, "launch", occurrence),
        Observation(base + 2, "rise", data=address & 3),
        Observation(base + 3, "fall", data=(address >> 2) & 3),
        Observation(base + 4, "observer_publish", occurrence, address),
        Observation(base + 5, "sink_sample", occurrence, address),
    ]
