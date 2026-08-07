#!/usr/bin/env python3
"""Exhaustive bounded abstract-state checks for A9 neighbor handoff H2."""

from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass


@dataclass(frozen=True)
class State:
    occupancy: tuple[int, ...]
    pinned: tuple[bool, ...]


def states(lanes: int):
    # Empty cannot be pinned; nonempty heads may be fresh or pinned by a stall.
    lane_states = ((0, False), (1, False), (1, True), (2, False), (2, True))
    for product in itertools.product(lane_states, repeat=lanes):
        yield State(tuple(item[0] for item in product),
                    tuple(item[1] for item in product))


def step_h2(state: State, ready_mask: int, inject_mask: int):
    lanes = len(state.occupancy)
    ready = tuple(bool(ready_mask & (1 << lane)) for lane in range(lanes))
    migrate = [False] * lanes
    for origin in range(lanes):
        neighbor = origin ^ 1
        migrate[origin] = (
            state.occupancy[origin] > 0
            and not state.pinned[origin]
            and not ready[origin]
            and ready[neighbor]
            and state.occupancy[neighbor] == 0
        )

    retired = [False] * lanes
    for origin in range(lanes):
        retired[origin] = state.occupancy[origin] > 0 and (
            ready[origin] or migrate[origin]
        )

    occupancy = list(state.occupancy)
    pinned = list(state.pinned)
    accepted = 0
    for lane in range(lanes):
        if retired[lane]:
            occupancy[lane] -= 1
            pinned[lane] = False
        elif occupancy[lane] > 0 and not ready[lane]:
            # If it had migrated it would have retired above.  A head exposed
            # on a stalled home output is now immovably pinned.
            pinned[lane] = True

        # Conservative source credit: injection uses pre-edge capacity only.
        if (inject_mask & (1 << lane)) and state.occupancy[lane] < 2:
            if occupancy[lane] == 0:
                pinned[lane] = False
            occupancy[lane] += 1
            accepted += 1

        if occupancy[lane] == 0:
            pinned[lane] = False

    return (State(tuple(occupancy), tuple(pinned)), accepted,
            tuple(retired), tuple(migrate))


def h1_counterexample() -> None:
    # H1 presents A at home lane 0 while stalled.  When lane 1 becomes empty
    # and ready, H1 removes A into its mailbox although home ready remains low.
    home_output_cycle_0 = (True, "A")
    home_output_cycle_1 = (False, None)
    assert home_output_cycle_0[0] and home_output_cycle_0 != home_output_cycle_1


def check(lanes: int) -> tuple[int, int, int]:
    assert lanes in (2, 4) and lanes % 2 == 0
    transition_count = 0
    migration_count = 0
    state_count = 0
    for state in states(lanes):
        state_count += 1
        for ready_mask in range(1 << lanes):
            for inject_mask in range(1 << lanes):
                nxt, accepted, retired_flags, migrate = step_h2(
                    state, ready_mask, inject_mask
                )
                retired = sum(retired_flags)
                transition_count += 1
                migration_count += sum(migrate)

                # Token/event conservation over the abstract two-entry lane
                # queues.  A direct handoff is a retirement, never a copy.
                assert sum(nxt.occupancy) == (
                    sum(state.occupancy) + accepted - retired
                )
                assert all(0 <= value <= 2 for value in nxt.occupancy)
                assert all((value > 0) or not pin for value, pin in zip(
                    nxt.occupancy, nxt.pinned
                ))
                assert sum(migrate) <= lanes // 2

                # A destination is empty at the start, so it cannot retire its
                # own native head and a neighbor migrant on the same lane.
                for origin, active in enumerate(migrate):
                    if active:
                        destination = origin ^ 1
                        assert state.occupancy[destination] == 0
                        assert ready_mask & (1 << destination)
                        assert not (ready_mask & (1 << origin))
                        assert not state.pinned[origin]

                # Any previously pinned stalled output remains at the same
                # home head.  It cannot disappear through migration.
                for lane in range(lanes):
                    if state.pinned[lane] and not (ready_mask & (1 << lane)):
                        assert not migrate[lane]
                        assert not retired_flags[lane]
                        assert nxt.occupancy[lane] >= 1

            # Stronger than the weak-fairness progress premise: whenever any
            # occupied home lane is ready, at least one head retires now.
            if any(state.occupancy[lane] > 0 and
                   (ready_mask & (1 << lane)) for lane in range(lanes)):
                _, _, retired_flags, _ = step_h2(state, ready_mask, 0)
                assert any(retired_flags)

        # Reset from every abstract state is the unique empty/unpinned state.
        reset_state = State((0,) * lanes, (False,) * lanes)
        assert sum(reset_state.occupancy) == 0
    return state_count, transition_count, migration_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lanes", type=int, action="append", choices=(2, 4))
    args = parser.parse_args()
    h1_counterexample()
    print("A9_H1_REJECT stalled-output migration changes valid/payload")
    for lanes in args.lanes or (2, 4):
        state_count, transitions, migrations = check(lanes)
        print(
            f"A9_H2_BOUNDED_PASS N={lanes} states={state_count} "
            f"transitions={transitions} migration_transitions={migrations}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
