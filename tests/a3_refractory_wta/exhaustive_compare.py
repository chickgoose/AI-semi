#!/usr/bin/env python3
"""Small-N exhaustive checks and policy controls for A3 refractory WTA."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path


def toggles(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@dataclass(frozen=True)
class RefractoryState:
    last_valid: bool = False
    last_winner: int = 0
    refractory: bool = False


class RefractoryWTA:
    def __init__(self, sources: int):
        self.sources = sources
        self.state = RefractoryState()
        self.state_toggles = 0

    def step(self, request: int) -> int | None:
        old = self.state
        alternative = request & ~(1 << old.last_winner) if old.last_valid else request
        escape = old.last_valid and old.refractory and bool(request & (1 << old.last_winner)) and bool(alternative)
        eligible = alternative if escape else request
        winner = next((i for i in range(self.sources) if eligible & (1 << i)), None)
        if winner is None:
            new = RefractoryState(old.last_valid, old.last_winner, False)
        else:
            new = RefractoryState(True, winner, True)
        self.state_toggles += int(old.last_valid != new.last_valid)
        self.state_toggles += toggles(old.last_winner, new.last_winner)
        self.state_toggles += int(old.refractory != new.refractory)
        self.state = new
        return winner


class RoundRobin:
    def __init__(self, sources: int):
        self.sources = sources
        self.pointer = 0
        self.state_toggles = 0

    def step(self, request: int) -> int | None:
        winner = next(
            ((self.pointer + offset) % self.sources for offset in range(self.sources)
             if request & (1 << ((self.pointer + offset) % self.sources))),
            None,
        )
        if winner is not None:
            new = (winner + 1) % self.sources
            self.state_toggles += toggles(self.pointer, new)
            self.pointer = new
        return winner


class FixedPriority:
    def __init__(self, sources: int):
        self.sources = sources
        self.state_toggles = 0

    def step(self, request: int) -> int | None:
        return next((i for i in range(self.sources) if request & (1 << i)), None)


@dataclass(frozen=True)
class ExactState:
    policy: RefractoryState
    pending: int
    output: int


def step_from(state: RefractoryState, request: int, sources: int) -> tuple[RefractoryState, int | None]:
    policy = RefractoryWTA(sources)
    policy.state = state
    winner = policy.step(request)
    return policy.state, winner


def exhaustive_state(depth: int = 8, sources: int = 4) -> dict[str, object]:
    initial = ExactState(RefractoryState(), 0, -1)
    frontier = {initial}
    visited = {initial}
    transitions = 0
    for _ in range(depth):
        following: set[ExactState] = set()
        for state in frontier:
            for occurrence in range(1 << sources):
                transitions += 1
                augmented = state.pending | occurrence
                overrun = (state.pending & occurrence).bit_count()
                policy, winner = step_from(state.policy, augmented, sources)
                pending = augmented if winner is None else augmented & ~(1 << winner)
                new = ExactState(policy, pending, -1 if winner is None else winner)
                assert state.pending.bit_count() + int(state.output >= 0) + occurrence.bit_count() == (
                    int(state.output >= 0) + overrun + pending.bit_count() + int(new.output >= 0)
                )
                assert winner is None or augmented & (1 << winner)
                assert 0 <= policy.last_winner < sources
                following.add(new)
                visited.add(new)
        frontier = following

    maximum_drain = 0
    for state in visited:
        current = state
        cycles = 0
        while current.pending or current.output >= 0:
            assert cycles <= sources + 1
            policy, winner = step_from(current.policy, current.pending, sources)
            pending = current.pending if winner is None else current.pending & ~(1 << winner)
            current = ExactState(policy, pending, -1 if winner is None else winner)
            cycles += 1
        maximum_drain = max(maximum_drain, cycles)
    return {
        "depth": depth,
        "transitions": transitions,
        "reachable_states": len(visited),
        "maximum_drain_cycles": maximum_drain,
        "transport_invariants_pass": True,
    }


def exhaustive_tokens(depth: int = 5, sources: int = 4) -> dict[str, int]:
    transitions = 0
    leaves = 0

    def visit(policy_state: RefractoryState, pending: tuple[int | None, ...],
              output: tuple[int, int] | None, next_id: tuple[int, ...],
              last_delivered: tuple[int, ...], level: int) -> None:
        nonlocal transitions, leaves
        if level == depth:
            leaves += 1
            return
        delivered = list(last_delivered)
        if output is not None:
            source, token = output
            assert token > delivered[source]
            delivered[source] = token
        for occurrence in range(1 << sources):
            transitions += 1
            pending_next = list(pending)
            ids = list(next_id)
            for source in range(sources):
                if occurrence & (1 << source):
                    token = ids[source]
                    ids[source] += 1
                    if pending_next[source] is None:
                        pending_next[source] = token
            request = sum((token is not None) << source for source, token in enumerate(pending_next))
            state, winner = step_from(policy_state, request, sources)
            next_output = None
            if winner is not None:
                next_output = (winner, int(pending_next[winner]))
                pending_next[winner] = None
            live = [(source, token) for source, token in enumerate(pending_next) if token is not None]
            assert next_output is None or next_output not in live
            visit(state, tuple(pending_next), next_output, tuple(ids), tuple(delivered), level + 1)

    visit(RefractoryState(), (None,) * sources, None, (0,) * sources,
          (-1,) * sources, 0)
    return {"depth": depth, "transitions": transitions, "leaf_sequences": leaves}


def rr_non_equivalence(depth: int = 6, sources: int = 4) -> dict[str, object]:
    # State pairs are explored under every direct request mask; a concrete
    # all-request witness is also retained for review.
    frontier = {(RefractoryState(), 0)}
    divergent_transitions = 0
    total = 0
    for _ in range(depth):
        following = set()
        for refractory_state, rr_pointer in frontier:
            for request in range(1 << sources):
                wta_state, wta_winner = step_from(refractory_state, request, sources)
                rr = RoundRobin(sources)
                rr.pointer = rr_pointer
                rr_winner = rr.step(request)
                total += 1
                divergent_transitions += wta_winner != rr_winner
                following.add((wta_state, rr.pointer))
        frontier = following

    wta, rr = RefractoryWTA(sources), RoundRobin(sources)
    witness_wta, witness_rr = [], []
    for _ in range(8):
        witness_wta.append(wta.step((1 << sources) - 1))
        witness_rr.append(rr.step((1 << sources) - 1))
    assert witness_wta != witness_rr
    return {
        "depth": depth,
        "compared_transitions": total,
        "divergent_transitions": divergent_transitions,
        "persistent_wta": witness_wta,
        "persistent_rr": witness_rr,
        "rr_rename": False,
    }


def workload(name: str, cycles: int, sources: int) -> tuple[list[int], int]:
    masks = []
    transition = 0
    for cycle in range(cycles):
        if name == "sparse":
            mask = (1 << ((cycle // 32) % sources)) if cycle % 32 == 0 else 0
        elif name == "persistent_contention":
            mask = (1 << sources) - 1
        elif name == "elephant_mouse":
            mask = 1
            for source in range(1, sources):
                if cycle % (17 + source) == source:
                    mask |= 1 << source
        elif name == "rotating_victim":
            victim = (cycle // 32) % sources
            mask = ((1 << sources) - 1) & ~(1 << victim)
            if cycle % 7 == 0:
                mask |= 1 << victim
        elif name == "rate_step":
            transition = cycles // 4
            if cycle < transition:
                mask = sum((cycle % 16 == 0) << source for source in range(sources))
            else:
                mask = sum((cycle % (1 << min(source, 5)) == 0) << source for source in range(sources))
        else:
            raise ValueError(name)
        masks.append(mask)
    return masks, transition


def jain(values: list[float]) -> float:
    denominator = len(values) * sum(value * value for value in values)
    return 1.0 if denominator == 0 else sum(values) ** 2 / denominator


def simulate(policy_name: str, masks: list[int], sources: int) -> dict[str, object]:
    policy = {
        "refractory_wta": RefractoryWTA(sources),
        "rr": RoundRobin(sources),
        "fixed": FixedPriority(sources),
    }[policy_name]
    pending = 0
    arrival = [-1] * sources
    generated = [0] * sources
    served = [0] * sources
    overrun = [0] * sources
    waits: list[int] = []
    service_history: list[list[int]] = []
    for cycle, occurrence in enumerate(masks):
        for source in range(sources):
            if occurrence & (1 << source):
                generated[source] += 1
                if pending & (1 << source):
                    overrun[source] += 1
                else:
                    pending |= 1 << source
                    arrival[source] = cycle
        winner = policy.step(pending)
        row = [0] * sources
        if winner is not None:
            pending &= ~(1 << winner)
            served[winner] += 1
            row[winner] = 1
            waits.append(cycle - arrival[winner])
            arrival[winner] = -1
        service_history.append(row)
    ratios = [served[i] / generated[i] if generated[i] else 1.0 for i in range(sources)]
    zero_sources = sum(generated[i] > 0 and served[i] == 0 for i in range(sources))
    # Pending requests at the right edge are censored waits, not zero wait.
    for source in range(sources):
        if pending & (1 << source):
            waits.append(len(masks) - arrival[source])
    return {
        "generated": sum(generated),
        "served": sum(served),
        "overrun": sum(overrun),
        "jain": jain(ratios),
        "min_ratio": min(ratios),
        "max_wait": max(waits, default=0),
        "zero_service_sources": zero_sources,
        "state_toggles": policy.state_toggles,
        "toggles_per_cycle": policy.state_toggles / len(masks),
        "history": service_history,
    }


def settling(history: list[list[int]], masks: list[int], transition: int) -> int:
    if transition == 0:
        return 0
    sources = len(history[0])
    width = 32
    for end in range(transition + width, len(history) + 1):
        service = [sum(row[i] for row in history[end - width:end]) for i in range(sources)]
        offered = [sum(bool(mask & (1 << i)) for mask in masks[end - width:end])
                   for i in range(sources)]
        ratios = [service[i] / offered[i] for i in range(sources) if offered[i]]
        if ratios and jain(ratios) >= 0.90:
            return end - transition
    return -1


def comparisons(cycles: int = 512, sources: int = 16) -> list[dict[str, object]]:
    state_bits = {"refractory_wta": math.ceil(math.log2(sources)) + 2,
                  "rr": math.ceil(math.log2(sources)), "fixed": 0}
    depth = {"refractory_wta": math.ceil(math.log2(sources)) + 2,
             "rr": 3 * math.ceil(math.log2(sources)),
             "fixed": math.ceil(math.log2(sources))}
    rows = []
    for name in ("sparse", "persistent_contention", "elephant_mouse", "rotating_victim", "rate_step"):
        masks, transition = workload(name, cycles, sources)
        for policy_name in ("refractory_wta", "rr", "fixed"):
            result = simulate(policy_name, masks, sources)
            rows.append({
                "workload": name,
                "policy": policy_name,
                "generated": result["generated"],
                "served": result["served"],
                "overrun": result["overrun"],
                "jain_demand_normalized": f'{result["jain"]:.6f}',
                "min_source_ratio": f'{result["min_ratio"]:.6f}',
                "max_wait": result["max_wait"],
                "zero_service_sources": result["zero_service_sources"],
                "settling_cycles": settling(result["history"], masks, transition),
                "policy_state_bits": state_bits[policy_name],
                "operator_depth_proxy": depth[policy_name],
                "policy_toggles": result["state_toggles"],
                "toggles_per_cycle": f'{result["toggles_per_cycle"]:.6f}',
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=512)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "state_exhaustive": exhaustive_state(),
        "token_exhaustive": exhaustive_tokens(),
        "rr_non_equivalence": rr_non_equivalence(),
    }
    rows = comparisons(args.cycles)
    persistent = [row for row in rows if row["workload"] == "persistent_contention"]
    salvage = next(row for row in persistent if row["policy"] == "refractory_wta")
    rr = next(row for row in persistent if row["policy"] == "rr")
    assert salvage["zero_service_sources"] == 14
    assert rr["zero_service_sources"] == 0
    with (args.output_dir / "a3r_exhaustive.json").open("w", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
        stream.write("\n")
    with (args.output_dir / "a3r_policy_compare.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        "A3R_EXHAUSTIVE_PASS "
        f"states={evidence['state_exhaustive']['reachable_states']} "
        f"token_paths={evidence['token_exhaustive']['leaf_sequences']} "
        f"rr_divergent={evidence['rr_non_equivalence']['divergent_transitions']} "
        f"persistent_zero_sources={salvage['zero_service_sources']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
