#!/usr/bin/env python3
"""Test-only strict oracle for the frozen W4 action/edge/symbol schedule.

This oracle is independent of the synthesizable link. It provides regression
diagnostics only; it is not runtime fault detection or containment hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Result:
    faults: tuple[str, ...]
    retired: tuple[int, ...]

    @property
    def passed(self) -> bool:
        return not self.faults


class StrictW4Oracle:
    VALID_KINDS = {"reset_assert", "reset_release", "accept", "rise", "fall"}

    def __init__(self, *, high_ps: int = 8000, low_ps: int = 8000,
                 tolerance_ps: int = 500, minimum_pulse_ps: int = 7000) -> None:
        self.high_ps = high_ps
        self.low_ps = low_ps
        self.tolerance_ps = tolerance_ps
        self.minimum_pulse_ps = minimum_pulse_ps

    def check(self, actions: Iterable[Action]) -> Result:
        pending: list[Action] = []
        faults: list[str] = []
        retired: list[int] = []
        reset_active = False
        frame_open = False
        frame_faulted = False
        rise_time: int | None = None
        low_symbol: int | None = None
        last_fall: int | None = None
        last_time = -1

        def fault(code: str) -> None:
            faults.append(code)

        for action in actions:
            if action.kind not in self.VALID_KINDS:
                fault("UNKNOWN_ACTION")
                continue
            if action.time_ps < last_time:
                fault("TIME_REORDER")
            last_time = max(last_time, action.time_ps)

            if action.kind == "reset_assert":
                if frame_open or pending:
                    fault("RESET_WITH_INFLIGHT")
                reset_active = True
                frame_open = False
                frame_faulted = False
                pending.clear()
                rise_time = None
                low_symbol = None
                last_fall = None
                continue
            if action.kind == "reset_release":
                reset_active = False
                continue
            if reset_active:
                fault("TRAFFIC_DURING_RESET")
                continue
            if action.kind == "accept":
                if action.address is None or not 0 <= action.address < 16:
                    fault("BAD_ADDRESS")
                elif action.expected_rise_ps is None or action.expected_fall_ps is None:
                    fault("MISSING_SCHEDULE")
                else:
                    pending.append(action)
                continue
            if action.kind == "rise":
                if frame_open:
                    fault("EXTRA_RISE_OPEN_FRAME")
                    continue
                if not pending:
                    fault("EXTRA_RISE_NO_ACCEPT")
                    continue
                expected = pending[0]
                faults_before_rise = len(faults)
                if abs(action.time_ps - int(expected.expected_rise_ps)) > self.tolerance_ps:
                    fault("RISE_TIME")
                if last_fall is not None:
                    gap = action.time_ps - last_fall
                    if abs(gap - self.low_ps) > self.tolerance_ps:
                        fault("LOW_DUTY_DISTORTION")
                    if gap < self.minimum_pulse_ps:
                        fault("RUNT_LOW")
                if not action.stable or action.data is None:
                    fault("UNSTABLE_OR_UNKNOWN_SYMBOL")
                elif action.data != (int(expected.address) & 3):
                    fault("LOW_SYMBOL")
                frame_open = True
                frame_faulted = len(faults) != faults_before_rise
                rise_time = action.time_ps
                low_symbol = action.data
                continue

            if not frame_open or rise_time is None:
                fault("FALL_WITHOUT_RISE")
                continue
            expected = pending[0]
            faults_before_fall = len(faults)
            width = action.time_ps - rise_time
            if width < self.minimum_pulse_ps:
                fault("RUNT_HIGH")
            if abs(width - self.high_ps) > self.tolerance_ps:
                fault("HIGH_DUTY_DISTORTION")
            if abs(action.time_ps - int(expected.expected_fall_ps)) > self.tolerance_ps:
                fault("FALL_TIME")
            if not action.stable or action.data is None:
                fault("UNSTABLE_OR_UNKNOWN_SYMBOL")
            elif action.data != ((int(expected.address) >> 2) & 3):
                fault("HIGH_SYMBOL")
            if low_symbol is not None and action.data is not None and action.stable:
                if ((action.data << 2) | low_symbol) != expected.address:
                    fault("RECONSTRUCTION")
            if not frame_faulted and len(faults) == faults_before_fall:
                # Retirement bookkeeping is diagnostic only. A failing trace is
                # rejected regardless of any prefix that retired correctly.
                retired.append(int(expected.address))
            pending.pop(0)
            frame_open = False
            frame_faulted = False
            rise_time = None
            low_symbol = None
            last_fall = action.time_ps

        if frame_open:
            fault("MISSING_FALL")
        if pending:
            fault("MISSING_FRAME")
        return Result(tuple(faults), tuple(retired))


def golden_schedule() -> list[Action]:
    return [
        Action(0, "reset_assert"),
        Action(16000, "reset_release"),
        Action(24000, "accept", address=0x9, expected_rise_ps=28000, expected_fall_ps=36000),
        Action(28000, "rise", data=0x1),
        Action(36000, "fall", data=0x2),
        Action(40000, "accept", address=0x6, expected_rise_ps=44000, expected_fall_ps=52000),
        Action(44000, "rise", data=0x2),
        Action(52000, "fall", data=0x1),
    ]
