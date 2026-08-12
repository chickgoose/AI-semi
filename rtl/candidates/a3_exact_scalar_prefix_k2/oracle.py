#!/usr/bin/env python3
"""Independent scalar-fold oracle for Exact-Scalar-Prefix-K2.

This file deliberately does not import the W7 Cluster2 model.  It implements
the canonical arbiter2/tree equations, one scalar Fovea transition, the K=2
fold, and the registered atomic-bundle boundary independently of the RTL.
"""

from __future__ import annotations

from dataclasses import dataclass


CENTER_MASK = 0x6
PERIPH_MASK = 0x9


def arb2(req: int, last: int) -> tuple[int, int]:
    req0 = req & 1
    req1 = (req >> 1) & 1
    grant1 = req1 & (int(last == 0) | (1 - req0))
    grant0 = req0 & (1 - grant1)
    grant = grant0 | (grant1 << 1)
    next_last = grant1 if req else last
    return grant, next_last


def arb4(req: int, state: int) -> tuple[int, int]:
    lo_req = req & 0x3
    hi_req = (req >> 2) & 0x3
    lo_grant, lo_next = arb2(lo_req, state & 1)
    hi_grant, hi_next = arb2(hi_req, (state >> 1) & 1)
    group_req = int(bool(lo_req)) | (int(bool(hi_req)) << 1)
    group_grant, top_next = arb2(group_req, (state >> 2) & 1)
    grant = lo_grant if group_grant & 1 else ((hi_grant << 2) if group_grant & 2 else 0)
    next_state = lo_next | (hi_next << 1) | (top_next << 2)
    return grant, next_state


def onehot_index(bits: int) -> int:
    for index in range(4):
        if bits & (1 << index):
            return index
    return 3


def row_requests(req: int) -> int:
    return sum(int(bool(req & (0xF << (row * 4)))) << row for row in range(4))


@dataclass(frozen=True)
class PolicyState:
    round: int = 0
    center: int = 7
    peripheral: int = 7
    column: int = 7


def scalar_step(req: int, state: PolicyState) -> tuple[int | None, PolicyState]:
    """Execute exactly one canonical scalar Fovea address transition."""

    rows = row_requests(req)
    center_available = bool(rows & CENTER_MASK)
    peripheral_available = bool(rows & PERIPH_MASK)
    prefer_center = state.round != 5
    use_center = (prefer_center and center_available) or (
        not prefer_center and not peripheral_available and center_available
    )
    use_peripheral = (not prefer_center and peripheral_available) or (
        prefer_center and not center_available and peripheral_available
    )

    center_req = rows & CENTER_MASK if use_center else 0
    peripheral_req = rows & PERIPH_MASK if use_peripheral else 0
    center_grant, center_next = arb4(center_req, state.center)
    peripheral_grant, peripheral_next = arb4(peripheral_req, state.peripheral)
    row_grant = center_grant if use_center else peripheral_grant if use_peripheral else 0
    if not row_grant:
        return None, PolicyState(state.round, center_next, peripheral_next, state.column)

    row = onehot_index(row_grant)
    columns = (req >> (row * 4)) & 0xF
    column_grant, column_next = arb4(columns, state.column)
    if not column_grant:
        raise AssertionError("canonical row grant lacked a column request")
    column = onehot_index(column_grant)
    round_next = 0 if state.round == 5 else state.round + 1
    return row * 4 + column, PolicyState(
        round_next, center_next, peripheral_next, column_next
    )


def scalar_prefix_k2(req: int, state: PolicyState) -> tuple[tuple[int, ...], PolicyState]:
    """Fold up to two scalar transitions, masking each selected address."""

    remaining = req & 0xFFFF
    grants: list[int] = []
    next_state = state
    for _ in range(2):
        address, after = scalar_step(remaining, next_state)
        if address is None:
            break
        if address in grants:
            raise AssertionError("scalar prefix produced a duplicate address")
        grants.append(address)
        remaining &= ~(1 << address)
        next_state = after
    return tuple(grants), next_state


@dataclass
class AtomicK2Model:
    """Registered atomic output bundle matching the candidate boundary."""

    state: PolicyState = PolicyState()
    grants: tuple[int, ...] = ()
    post_state: PolicyState = PolicyState()

    def reset(self) -> None:
        self.state = PolicyState()
        self.grants = ()
        self.post_state = PolicyState()

    @property
    def fire_mask(self) -> int:
        return sum(1 << address for address in self.grants)

    def step(self, *, rst: bool, ready: bool, pending: int) -> tuple[int, ...]:
        """Apply one rising edge and return the pre-edge committed bundle."""

        fired = self.grants if (self.grants and ready and not rst) else ()
        if rst:
            self.reset()
            return ()
        if self.grants and not ready:
            return ()

        if fired:
            selection_state = self.post_state
            selection_req = pending & ~sum(1 << address for address in fired)
            self.state = self.post_state
        else:
            selection_state = self.state
            selection_req = pending

        self.grants, self.post_state = scalar_prefix_k2(selection_req, selection_state)
        return fired

    def snapshot(self) -> dict[str, int]:
        return {
            "grant_count": len(self.grants),
            "addr0": self.grants[0] if self.grants else 0,
            "addr1": self.grants[1] if len(self.grants) == 2 else 0,
            "round": self.state.round,
            "center": self.state.center,
            "peripheral": self.state.peripheral,
            "column": self.state.column,
        }


def persistent_probe(bundle_commits: int = 120) -> dict[str, object]:
    model = AtomicK2Model()
    model.step(rst=True, ready=False, pending=0xFFFF)
    counts = [0, 0, 0, 0]
    committed = 0
    cycles = 0
    while committed < bundle_commits:
        fired = model.step(rst=False, ready=True, pending=0xFFFF)
        if fired:
            if len(fired) != 2:
                raise AssertionError("persistent K2 bundle was not full")
            for address in fired:
                counts[address // 4] += 1
            committed += 1
        cycles += 1
        if cycles > bundle_commits + 4:
            raise AssertionError("persistent probe failed to fill/commit")
    expected_scale = bundle_commits // 6
    expected = [expected_scale, 5 * expected_scale, 5 * expected_scale, expected_scale]
    if bundle_commits % 6 == 0 and counts != expected:
        raise AssertionError(f"persistent opportunity mismatch {counts} != {expected}")
    return {
        "bundle_commits": committed,
        "address_grants": 2 * committed,
        "row_opportunities_0_1_2_3": counts,
        "cycles_including_initial_fill": cycles,
    }


if __name__ == "__main__":
    print(persistent_probe())
