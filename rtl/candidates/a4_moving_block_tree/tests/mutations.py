"""Deliberately broken models used to prove the frozen falsifiers fire."""

from __future__ import annotations

from model import CycleResult, Event, MovingBlockTreeModel


class MutatedMovingBlockTreeModel(MovingBlockTreeModel):
    MUTATIONS = {
        "retire_refill_clear_after_write",
        "two_microstep_double_inject",
        "stalled_root_clearance_leak",
        "dual_child_single_parent",
        "no_reset_shock_recovery",
    }

    def __init__(self, mutation: str):
        if mutation not in self.MUTATIONS:
            raise ValueError(f"unknown mutation: {mutation}")
        super().__init__(16, 2)
        self.mutation = mutation
        self.deadlocked = False

    def step(self, source_valid, source_payload, retire_ready, rst_n=True):
        if not rst_n:
            self.deadlocked = False
            return super().step(source_valid, source_payload, retire_ready, rst_n=False)
        if self.deadlocked:
            root = self.nodes[0]
            self.cycle += 1
            return CycleResult(
                (False,) * self.num_sources,
                root is not None,
                root.source if root is not None else 0,
                root.payload if root is not None else 0,
                None,
                (),
            )

        collision_keys = []
        if self.mutation == "dual_child_single_parent":
            for parent in range(self.first_leaf):
                left = self.nodes[2 * parent + 1]
                right = self.nodes[2 * parent + 2]
                if self.nodes[parent] is None and left is not None and right is not None:
                    collision_keys = [
                        (left.source, left.payload, left.accepted_cycle),
                        (right.source, right.payload, right.accepted_cycle),
                    ]
                    break

        result = super().step(source_valid, source_payload, retire_ready, rst_n=True)

        if self.mutation == "retire_refill_clear_after_write":
            if result.retired is not None and self.nodes[0] is not None:
                self.nodes[0] = None

        elif self.mutation == "two_microstep_double_inject":
            for source, accepted in enumerate(result.source_ready):
                if not accepted:
                    continue
                original = next(
                    (
                        item
                        for item in self.nodes
                        if item is not None
                        and item.source == source
                        and item.payload == int(source_payload[source])
                    ),
                    None,
                )
                empty = next(
                    (index for index, item in enumerate(self.nodes) if item is None),
                    None,
                )
                if original is not None and empty is not None:
                    self.nodes[empty] = Event(
                        original.source, original.payload, original.accepted_cycle
                    )
                    break

        elif self.mutation == "stalled_root_clearance_leak":
            if result.retire_valid and not retire_ready:
                replacement = next(
                    (
                        index
                        for index in range(1, self.total_nodes)
                        if self.nodes[index] is not None
                    ),
                    None,
                )
                if replacement is not None:
                    self.nodes[0] = self.nodes[replacement]
                    self.nodes[replacement] = None

        elif self.mutation == "dual_child_single_parent" and collision_keys:
            # A faulty merge acknowledges both contenders but stores only one.
            victim = collision_keys[1]
            for index, item in enumerate(self.nodes):
                if item is not None and (
                    item.source, item.payload, item.accepted_cycle
                ) == victim:
                    self.nodes[index] = None
                    break

        elif self.mutation == "no_reset_shock_recovery":
            if self.occupancy() >= self.total_nodes - 2:
                self.deadlocked = True

        return result


def exercise_mutation(name: str) -> None:
    """Run one directed scenario; surviving a mutation is a test failure."""

    model = MutatedMovingBlockTreeModel(name)
    pending: list[int | None] = [None] * 16
    sequence = [0] * 16
    accepted: set[tuple[int, int]] = set()
    previous_stall: tuple[int, int] | None = None

    for cycle in range(700):
        injecting = cycle < 180
        if name == "no_reset_shock_recovery":
            injecting = cycle < 260
        if injecting:
            for source in range(16):
                if pending[source] is None and (
                    cycle < 40 or (cycle + source) % 3 == 0
                ):
                    sequence[source] += 1
                    pending[source] = (source << 24) | sequence[source]

        sink_ready = not (
            name == "stalled_root_clearance_leak" and 20 <= cycle < 90
        )
        valid = [item is not None for item in pending]
        payload = [item or 0 for item in pending]
        result = model.step(valid, payload, sink_ready)

        if previous_stall is not None:
            current = (
                result.retire_source,
                result.retire_payload,
            ) if result.retire_valid else None
            if current != previous_stall:
                raise AssertionError("stall stability detector fired")
        previous_stall = (
            (result.retire_source, result.retire_payload)
            if result.retire_valid and not sink_ready
            else None
        )

        for source, did_accept in enumerate(result.source_ready):
            if did_accept:
                key = (source, payload[source])
                if key in accepted:
                    raise AssertionError("duplicate acceptance detector fired")
                accepted.add(key)
                pending[source] = None
        if result.retired is not None:
            key = (result.retired.source, result.retired.payload)
            if key not in accepted:
                raise AssertionError("phantom/duplicate retire detector fired")
            accepted.remove(key)

        if cycle >= 260 and not any(pending) and model.occupancy() == 0:
            if accepted:
                raise AssertionError("loss detector fired")
            raise AssertionError(f"mutation escaped all detectors: {name}")

    if accepted or model.occupancy() or any(pending):
        if model.deadlocked:
            raise AssertionError("deadlock/drain-timeout detector fired")
        raise AssertionError("loss/drain-timeout detector fired")
    raise AssertionError(f"mutation escaped all detectors: {name}")
