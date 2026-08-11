#!/usr/bin/env python3
"""Independent executable oracle for the A7 event-triggered DDR protocol.

The oracle consumes abstract, timestamped protocol actions.  It does not import
or simulate A7 RTL.  ``LegacyFaultChecker`` intentionally models only the raw
manual-clock checks present in A7 commit 31947a7, so mutation tests can expose
faults that checker would falsely accept.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


@dataclass(frozen=True)
class Action:
    time_ps: int
    kind: str
    address: int | None = None
    data: int | None = None
    expected_rise_ps: int | None = None
    expected_fall_ps: int | None = None
    stable: bool = True
    occurrence_id: int | None = None


@dataclass(frozen=True)
class Fault:
    code: str
    time_ps: int
    detail: str


@dataclass(frozen=True)
class OracleResult:
    faults: tuple[Fault, ...]
    retired: tuple[tuple[int, int], ...]
    aborted: tuple[int, ...]

    @property
    def passed(self) -> bool:
        return not self.faults


@dataclass(frozen=True)
class PendingOccurrence:
    occurrence_id: int
    address: int
    expected_rise_ps: int
    expected_fall_ps: int


class DDRProtocolOracle:
    """Fail-closed event/edge/symbol oracle with explicit reset aborts."""

    VALID_KINDS = {"accept", "rise", "fall", "reset_assert", "reset_release"}

    def __init__(
        self,
        *,
        expected_high_ps: int = 4000,
        expected_low_ps: int = 4000,
        timing_tolerance_ps: int = 250,
        minimum_pulse_ps: int = 1000,
    ) -> None:
        self.expected_high_ps = expected_high_ps
        self.expected_low_ps = expected_low_ps
        self.timing_tolerance_ps = timing_tolerance_ps
        self.minimum_pulse_ps = minimum_pulse_ps

    def check(self, actions: Iterable[Action]) -> OracleResult:
        pending: list[PendingOccurrence] = []
        faults: list[Fault] = []
        retired: list[tuple[int, int]] = []
        aborted: list[int] = []
        reset_active = False
        frame_open = False
        rise_time: int | None = None
        low_symbol: int | None = None
        last_fall_time: int | None = None
        last_time = -1
        next_id = 0

        def fault(code: str, action: Action, detail: str) -> None:
            faults.append(Fault(code, action.time_ps, detail))

        for action in actions:
            if action.kind not in self.VALID_KINDS:
                fault("UNKNOWN_ACTION", action, action.kind)
                continue
            if action.time_ps < last_time:
                fault("TIME_REORDER", action, "actions are not monotonic")
            last_time = max(last_time, action.time_ps)

            if action.kind == "accept":
                if reset_active:
                    fault("ACCEPT_DURING_RESET", action, "accept is not live during reset")
                    continue
                occurrence_id = next_id if action.occurrence_id is None else action.occurrence_id
                next_id = max(next_id + 1, occurrence_id + 1)
                if action.address is None or not 0 <= action.address < 16:
                    fault("BAD_ACCEPT_ADDRESS", action, "N16 address must be known")
                    continue
                if action.expected_rise_ps is None or action.expected_fall_ps is None:
                    fault("MISSING_EDGE_SCHEDULE", action, "accept lacks expected edge times")
                    continue
                pending.append(
                    PendingOccurrence(
                        occurrence_id,
                        action.address,
                        action.expected_rise_ps,
                        action.expected_fall_ps,
                    )
                )
                continue

            if action.kind == "reset_assert":
                if frame_open and pending:
                    aborted.append(pending[0].occurrence_id)
                aborted.extend(item.occurrence_id for item in pending[1:])
                pending.clear()
                reset_active = True
                frame_open = False
                rise_time = None
                low_symbol = None
                last_fall_time = None
                continue

            if action.kind == "reset_release":
                reset_active = False
                continue

            if reset_active:
                # Asynchronous reset may force a clock fall.  Such an edge is
                # neither a retirement nor a new frame while reset is active.
                if action.kind == "rise":
                    fault("RISE_DURING_RESET", action, "burst clock must remain low")
                continue

            if action.kind == "rise":
                if frame_open:
                    fault("EXTRA_RISE", action, "new rise before prior frame fall")
                    continue
                if not pending:
                    fault("EXTRA_RISE", action, "rise has no accepted occurrence")
                    continue
                expected = pending[0]
                if abs(action.time_ps - expected.expected_rise_ps) > self.timing_tolerance_ps:
                    fault("RISE_TIME", action, "rise is outside its source-synchronous window")
                if last_fall_time is not None:
                    expected_gap = expected.expected_rise_ps - last_fall_time
                    actual_gap = action.time_ps - last_fall_time
                    if (
                        expected_gap <= self.expected_low_ps + self.timing_tolerance_ps
                        and abs(actual_gap - self.expected_low_ps) > self.timing_tolerance_ps
                    ):
                        fault("LOW_DUTY_DISTORTION", action, "merged low phase distorted")
                if not action.stable or action.data is None:
                    fault("METASTABILITY_ABSTRACT", action, "low symbol is unknown or unstable")
                elif action.data != (expected.address & 0x3):
                    fault("LOW_SYMBOL", action, "low symbol does not match accepted address")
                frame_open = True
                rise_time = action.time_ps
                low_symbol = action.data
                continue

            if not frame_open or rise_time is None:
                fault("FALL_WITHOUT_RISE", action, "fall has no open frame")
                continue
            if not pending:
                fault("EXTRA_FALL", action, "fall has no accepted occurrence")
                frame_open = False
                continue
            expected = pending[0]
            high_width = action.time_ps - rise_time
            if high_width < self.minimum_pulse_ps:
                fault("RUNT_HIGH", action, "high pulse is below minimum")
            if abs(high_width - self.expected_high_ps) > self.timing_tolerance_ps:
                fault("HIGH_DUTY_DISTORTION", action, "high phase is outside duty window")
            if abs(action.time_ps - expected.expected_fall_ps) > self.timing_tolerance_ps:
                fault("FALL_TIME", action, "fall is outside its source-synchronous window")
            if not action.stable or action.data is None:
                fault("METASTABILITY_ABSTRACT", action, "high symbol is unknown or unstable")
            elif action.data != ((expected.address >> 2) & 0x3):
                fault("HIGH_SYMBOL", action, "high symbol does not match accepted address")
            if low_symbol is not None and action.data is not None and action.stable:
                reconstructed = (action.data << 2) | low_symbol
                if reconstructed != expected.address:
                    fault("RECONSTRUCTION", action, "retired address is corrupt")
            if not any(item.time_ps == action.time_ps for item in faults):
                retired.append((expected.occurrence_id, expected.address))
            pending.pop(0)
            frame_open = False
            rise_time = None
            low_symbol = None
            last_fall_time = action.time_ps

        if frame_open:
            faults.append(Fault("MISSING_FALL", last_time, "trace ended with an open frame"))
        if pending:
            faults.append(
                Fault("MISSING_FRAME", last_time, f"{len(pending)} accepted occurrence(s) remain")
            )
        return OracleResult(tuple(faults), tuple(retired), tuple(aborted))


class LegacyFaultChecker:
    """Behavioral model of A7 TB lines 190-205 at commit 31947a7."""

    def __init__(self, minimum_high_ps: int = 1000) -> None:
        self.minimum_high_ps = minimum_high_ps

    def passes(self, actions: Iterable[Action]) -> bool:
        reset_active = False
        frame_open = False
        rise_time = 0
        faults = 0
        for action in actions:
            if action.kind == "reset_assert":
                reset_active = True
                frame_open = False
            elif action.kind == "reset_release":
                reset_active = False
            elif reset_active:
                continue
            elif action.kind == "rise":
                rise_time = action.time_ps
                frame_open = True
            elif action.kind == "fall":
                if not frame_open:
                    faults += 1
                elif action.time_ps - rise_time < self.minimum_high_ps:
                    faults += 1
                frame_open = False
        # The A7 always blocks do not perform a general end-of-trace timeout.
        return faults == 0


def golden_back_to_back() -> list[Action]:
    return [
        Action(0, "accept", address=0x9, expected_rise_ps=1000, expected_fall_ps=5000, occurrence_id=0),
        Action(1000, "rise", data=0x1),
        Action(5000, "fall", data=0x2),
        Action(8000, "accept", address=0x6, expected_rise_ps=9000, expected_fall_ps=13000, occurrence_id=1),
        Action(9000, "rise", data=0x2),
        Action(13000, "fall", data=0x1),
    ]


def replace_action(actions: list[Action], index: int, **changes: object) -> list[Action]:
    mutated = list(actions)
    mutated[index] = replace(mutated[index], **changes)
    return mutated
