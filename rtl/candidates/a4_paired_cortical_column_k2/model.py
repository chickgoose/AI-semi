#!/usr/bin/env python3
"""Independent executable model for A4 Paired Cortical Column K2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    source_ready: int
    grant_count: int
    grant_addr0: int
    grant_addr1: int
    drain_idle: bool


class PairedCorticalColumnK2:
    def __init__(self, debt_width: int = 4) -> None:
        if debt_width <= 0:
            raise ValueError("debt_width must be positive")
        self.debt_max = (1 << debt_width) - 1
        self.reset()

    def reset(self) -> None:
        self.phase = 0
        self.token = 0
        self.columns = [0, 0, 0, 0]
        self.debt = [0, 0, 0, 0]
        self.debt_rr = 0
        self.fallback_rr = 0
        self.hold_requests: int | None = None

    @staticmethod
    def token_row(phase: int, token: int) -> int:
        if phase < 5:
            return 1 if token == 0 else 2
        return 0 if token == 0 else 3

    @staticmethod
    def choose_column(requests: int, start: int) -> int | None:
        for offset in range(4):
            column = (start + offset) & 3
            if requests & (1 << column):
                return column
        return None

    @staticmethod
    def advance_token(phase: int, token: int) -> tuple[int, int]:
        if token == 0:
            return phase, 1
        return (phase + 1) % 6, 0

    def _offer(self, requests: int) -> tuple[list[int], tuple[object, ...]]:
        phase = self.phase
        token = self.token
        columns = list(self.columns)
        debt = list(self.debt)
        debt_rr = self.debt_rr
        fallback_rr = self.fallback_rr
        selected_rows: set[int] = set()
        addresses: list[int] = []

        def row_choice(row: int) -> int | None:
            row_requests = (requests >> (4 * row)) & 0xF
            column = self.choose_column(row_requests, columns[row])
            return None if column is None else 4 * row + column

        debt_scan_start = debt_rr
        for offset in range(4):
            if len(addresses) >= 2:
                break
            row = (debt_scan_start + offset) & 3
            source = row_choice(row)
            if debt[row] and row not in selected_rows and source is not None:
                addresses.append(source)
                selected_rows.add(row)
                debt[row] -= 1
                columns[row] = ((source & 3) + 1) & 3
                debt_rr = (row + 1) & 3

        for _ in range(2):
            if len(addresses) >= 2:
                break
            scheduled = self.token_row(phase, token)
            source = row_choice(scheduled)
            consume = False
            if scheduled not in selected_rows and source is not None:
                consume = True
                columns[scheduled] = ((source & 3) + 1) & 3
            else:
                source = None
                for offset in range(4):
                    row = (fallback_rr + offset) & 3
                    candidate = row_choice(row)
                    if row not in selected_rows and candidate is not None:
                        source = candidate
                        columns[row] = ((candidate & 3) + 1) & 3
                        fallback_rr = (row + 1) & 3
                        break
                if source is not None and debt[scheduled] < self.debt_max:
                    debt[scheduled] += 1
                    consume = True
            if source is None:
                continue
            addresses.append(source)
            selected_rows.add(source >> 2)
            if consume:
                phase, token = self.advance_token(phase, token)

        policy = (phase, token, columns, debt, debt_rr, fallback_rr)
        return addresses, policy

    def step(self, source_valid: int, bundle_ready: bool, rst_n: bool = True) -> Result:
        source_valid &= 0xFFFF
        if not rst_n:
            self.reset()
            return Result(0, 0, 0, 0, True)

        was_holding = self.hold_requests is not None
        requests = source_valid if self.hold_requests is None else self.hold_requests
        addresses, policy = self._offer(requests)
        count = len(addresses)
        addr0 = addresses[0] if count > 0 else 0
        addr1 = addresses[1] if count > 1 else 0
        ready = 0
        if bundle_ready:
            for source in addresses:
                ready |= 1 << source
            if count:
                (self.phase, self.token, self.columns, self.debt,
                 self.debt_rr, self.fallback_rr) = policy
            self.hold_requests = None
        elif self.hold_requests is None and count:
            self.hold_requests = source_valid

        return Result(
            ready,
            count,
            addr0,
            addr1,
            source_valid == 0 and not was_holding,
        )

    def policy_state(self) -> tuple[object, ...]:
        return (
            self.phase,
            self.token,
            tuple(self.columns),
            tuple(self.debt),
            self.debt_rr,
            self.fallback_rr,
        )
