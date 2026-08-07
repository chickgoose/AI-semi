#!/usr/bin/env python3
"""Reduced-state and adversarial control validation for the A3 arbiter.

This is a candidate-only, dependency-free executable specification.  It models
the source one-entry latches and registered retire slot as well as the policy,
so the exhaustive transition checks cover transport conservation too.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path


def bit_toggles(before: int, after: int) -> int:
    return (before ^ after).bit_count()


@dataclass(frozen=True)
class Parameters:
    sources: int = 16
    urgency_width: int = 6
    home_width: int = 4
    leak: int = 1
    gain_low: int = 6
    gain_high: int = 5
    inhibit_low: int = 1
    inhibit_high: int = 2
    home_low_active: int = 2
    home_high_active: int = 4
    threshold_base: int = 8
    threshold_shift: int = 1

    @property
    def urgency_max(self) -> int:
        return (1 << self.urgency_width) - 1

    @property
    def home_max(self) -> int:
        return (1 << self.home_width) - 1

    @property
    def threshold_max(self) -> int:
        return self.threshold_base + (self.home_max << self.threshold_shift)

    @property
    def progress_min(self) -> int:
        return min(
            self.gain_low - self.leak - self.inhibit_low,
            self.gain_high - self.leak - self.inhibit_high,
        )

    @property
    def service_bound(self) -> int | None:
        if self.progress_min <= 0 or self.threshold_max > self.urgency_max:
            return None
        return math.ceil(self.threshold_max / self.progress_min) + self.sources


@dataclass(frozen=True)
class A3State:
    membrane: tuple[int, ...]
    home: int
    phase: int


class A3Policy:
    def __init__(self, p: Parameters):
        self.p = p
        self.state = A3State((0,) * p.sources, 0, 0)
        self.toggles = 0

    def clone_from(self, state: A3State) -> "A3Policy":
        other = A3Policy(self.p)
        other.state = state
        return other

    def step(self, request_mask: int) -> int | None:
        p, old = self.p, self.state
        active = request_mask.bit_count()
        high = bool(old.home & (1 << (p.home_width - 1)))
        gain = p.gain_high if high else p.gain_low
        inhibit = p.inhibit_high if high else p.inhibit_low
        threshold = p.threshold_base + (old.home << p.threshold_shift)
        order = tuple((old.phase + offset) % p.sources for offset in range(p.sources))
        winner = next(
            (i for i in order if request_mask & (1 << i) and old.membrane[i] >= threshold),
            None,
        )
        if winner is None:
            candidates = [i for i in order if request_mask & (1 << i)]
            if candidates:
                maximum = max(old.membrane[i] for i in candidates)
                winner = next(i for i in candidates if old.membrane[i] == maximum)

        home = old.home
        if active > p.home_high_active:
            home = min(p.home_max, home + 1)
        elif active < p.home_low_active:
            home = max(0, home - 1)

        membrane = list(old.membrane)
        for i in range(p.sources):
            if i == winner:
                membrane[i] = 0
            elif request_mask & (1 << i):
                delta = gain - p.leak - (inhibit if winner is not None else 0)
                membrane[i] = min(p.urgency_max, max(0, membrane[i] + delta))
            else:
                membrane[i] = max(0, membrane[i] - p.leak)
        phase = old.phase if winner is None else (winner + 1) % p.sources
        new = A3State(tuple(membrane), home, phase)
        self.toggles += sum(bit_toggles(a, b) for a, b in zip(old.membrane, new.membrane))
        self.toggles += bit_toggles(old.home, new.home) + bit_toggles(old.phase, new.phase)
        self.state = new
        return winner


class RRPolicy:
    def __init__(self, sources: int):
        self.sources = sources
        self.pointer = 0
        self.toggles = 0

    def step(self, request_mask: int) -> int | None:
        winner = next(
            (i for offset in range(self.sources) if request_mask & (1 << (i := (self.pointer + offset) % self.sources))),
            None,
        )
        if winner is not None:
            new_pointer = (winner + 1) % self.sources
            self.toggles += bit_toggles(self.pointer, new_pointer)
            self.pointer = new_pointer
        return winner


class FixedPolicy:
    def __init__(self, sources: int):
        self.sources = sources
        self.toggles = 0

    def step(self, request_mask: int) -> int | None:
        return next((i for i in range(self.sources) if request_mask & (1 << i)), None)


@dataclass(frozen=True)
class TransportState:
    control: A3State
    pending: int
    output: int  # -1 means empty; otherwise the source of the prior-cycle grant


def exhaustive_transitions(p: Parameters, depth: int) -> dict[str, int]:
    """Enumerate all occurrence masks with state merging and check invariants."""
    initial = TransportState(A3State((0,) * p.sources, 0, 0), 0, -1)
    frontier = {initial}
    visited = {initial}
    transitions = 0
    saturation_hits = 0
    for _ in range(depth):
        following: set[TransportState] = set()
        for state in frontier:
            for occurrence in range(1 << p.sources):
                transitions += 1
                old_outstanding = state.pending.bit_count() + int(state.output >= 0)
                overrun = (occurrence & state.pending).bit_count()
                augmented = state.pending | occurrence
                policy = A3Policy(p).clone_from(state.control)
                winner = policy.step(augmented)
                pending = augmented if winner is None else augmented & ~(1 << winner)
                new = TransportState(policy.state, pending, -1 if winner is None else winner)
                delivered = int(state.output >= 0)
                generated = occurrence.bit_count()
                new_outstanding = pending.bit_count() + int(new.output >= 0)
                assert old_outstanding + generated == delivered + overrun + new_outstanding
                assert winner is None or (augmented & (1 << winner))
                assert all(0 <= value <= p.urgency_max for value in new.control.membrane)
                assert 0 <= new.control.home <= p.home_max
                assert 0 <= new.control.phase < p.sources
                saturation_hits += sum(value in (0, p.urgency_max) for value in new.control.membrane)
                following.add(new)
                visited.add(new)
        frontier = following

    # Any reachable bounded state must drain losslessly with no new arrivals.
    maximum_drain = 0
    for state in visited:
        current = state
        drain = 0
        while current.pending or current.output >= 0:
            assert drain <= p.sources + 1
            policy = A3Policy(p).clone_from(current.control)
            winner = policy.step(current.pending)
            pending = current.pending if winner is None else current.pending & ~(1 << winner)
            current = TransportState(policy.state, pending, -1 if winner is None else winner)
            drain += 1
        maximum_drain = max(maximum_drain, drain)
    return {
        "depth": depth,
        "transitions": transitions,
        "reachable_states": len(visited),
        "frontier_states": len(frontier),
        "saturation_boundary_observations": saturation_hits,
        "maximum_drain_cycles": maximum_drain,
    }


def exhaustive_token_order(p: Parameters, depth: int = 5) -> dict[str, int]:
    """Enumerate 16**depth paths with token IDs to check duplicate/order."""
    transitions = 0
    leaves = 0

    def visit(
        control: A3State,
        pending: tuple[int | None, ...],
        output: tuple[int, int] | None,
        next_generated: tuple[int, ...],
        last_delivered: tuple[int, ...],
        level: int,
    ) -> None:
        nonlocal transitions, leaves
        if level == depth:
            leaves += 1
            return
        delivered = list(last_delivered)
        if output is not None:
            source, sequence = output
            assert sequence > delivered[source], "duplicate or source-local reorder"
            delivered[source] = sequence
        for occurrence in range(1 << p.sources):
            transitions += 1
            next_ids = list(next_generated)
            pending_ids = list(pending)
            for source in range(p.sources):
                if occurrence & (1 << source):
                    sequence = next_ids[source]
                    next_ids[source] += 1
                    if pending_ids[source] is None:
                        pending_ids[source] = sequence
            request_mask = sum((token is not None) << source for source, token in enumerate(pending_ids))
            policy = A3Policy(p).clone_from(control)
            winner = policy.step(request_mask)
            new_output = None
            if winner is not None:
                assert pending_ids[winner] is not None
                new_output = (winner, int(pending_ids[winner]))
                pending_ids[winner] = None
            # A token has exactly one location: pending or registered output.
            live = [(source, token) for source, token in enumerate(pending_ids) if token is not None]
            if new_output is not None:
                assert new_output not in live
            visit(
                policy.state,
                tuple(pending_ids),
                new_output,
                tuple(next_ids),
                tuple(delivered),
                level + 1,
            )

    visit(A3State((0,) * p.sources, 0, 0), (None,) * p.sources, None,
          (0,) * p.sources, (-1,) * p.sources, 0)
    return {"depth": depth, "transitions": transitions, "leaf_sequences": leaves}


def exhaustive_persistent_victim(p: Parameters, victim: int = 0) -> dict[str, int]:
    """Explore every refill choice for non-victims until the analytical bound."""
    assert p.service_bound is not None
    # Include reset-like and hostile boundary seeds.  This is deliberately
    # stronger than starting the victim alone at phase zero (which wins at
    # once and says little about starvation).
    levels = (0, min(p.urgency_max, p.threshold_max - 1),
              min(p.urgency_max, p.threshold_max), p.urgency_max)
    frontier = {
        (A3State(tuple(membrane), home, phase), (1 << p.sources) - 1)
        for membrane in product(levels, repeat=p.sources)
        for home in (0, p.home_max)
        for phase in range(p.sources)
    }
    seed_states = len(frontier)
    transitions = 0
    latest_service = 0
    for wait in range(1, p.service_bound + 1):
        following: set[tuple[A3State, int]] = set()
        for control, pending in frontier:
            for refill_compact in range(1 << (p.sources - 1)):
                refill = 0
                bit = 0
                for source in range(p.sources):
                    if source != victim:
                        refill |= ((refill_compact >> bit) & 1) << source
                        bit += 1
                augmented = pending | refill
                policy = A3Policy(p).clone_from(control)
                winner = policy.step(augmented)
                transitions += 1
                if winner == victim:
                    latest_service = wait
                    continue
                new_pending = augmented if winner is None else augmented & ~(1 << winner)
                assert new_pending & (1 << victim)
                following.add((policy.state, new_pending))
        frontier = following
        if not frontier:
            break
    assert not frontier, "persistent victim survived the analytical service bound"
    return {
        "victim": victim,
        "analytical_bound": int(p.service_bound),
        "latest_exhaustive_service": latest_service,
        "transitions": transitions,
        "hostile_seed_states": seed_states,
    }


def workload(name: str, cycles: int, sources: int = 16) -> tuple[list[int], list[int]]:
    masks: list[int] = []
    transitions: list[int] = []
    for cycle in range(cycles):
        if name == "asymmetric_rate_step":
            transitions = [128]
            periods = ([16] * sources) if cycle < 128 else [1 << min(i, 5) for i in range(sources)]
            mask = sum((cycle % period == 0) << i for i, period in enumerate(periods))
        elif name == "rotating_burst":
            transitions = list(range(32, cycles, 32))
            hot = (cycle // 32) % sources
            mask = 1 << hot
            mask |= (cycle % 3 == 0) << ((hot + 1) % sources)
            mask |= (cycle % 7 == 0) << ((hot + 2) % sources)
        elif name == "correlated_oscillation":
            # Eight synchronized overload cycles followed by 24 quiet cycles:
            # long enough to drain all N=16 latches and expose H decay.
            transitions = sorted(list(range(8, cycles, 32)) + list(range(32, cycles, 32)))
            mask = (1 << sources) - 1 if cycle % 32 < 8 else 0
        elif name == "one_source_recovery":
            transitions = [128]
            mask = (1 << sources) - 1 if cycle < 128 else ((cycle % 4 == 0) << 0)
        else:
            raise ValueError(name)
        masks.append(mask)
    return masks, transitions


def jain(values: list[float]) -> float:
    total = sum(values)
    square = sum(value * value for value in values)
    return 1.0 if square == 0 else total * total / (len(values) * square)


def settling_time(generated_history: list[list[int]], service_history: list[list[int]], transition: int) -> int:
    """First post-step 32-cycle window with demand-normalized Jain >= .90."""
    width, consecutive = 32, 8
    stable = 0
    for end in range(transition + width, len(generated_history) + 1):
        sources = len(generated_history[0])
        offered = [sum(row[i] for row in generated_history[end - width : end]) for i in range(sources)]
        served = [sum(row[i] for row in service_history[end - width : end]) for i in range(sources)]
        ratios = [served[i] / offered[i] for i in range(sources) if offered[i]]
        if ratios and jain(ratios) >= 0.90:
            stable += 1
            if stable >= consecutive:
                return end - consecutive + 1 - transition
        else:
            stable = 0
    return -1


def simulate(policy_name: str, masks: list[int], p: Parameters) -> dict[str, object]:
    policy = {"a3": A3Policy(p), "rr": RRPolicy(p.sources), "fixed": FixedPolicy(p.sources)}[policy_name]
    pending = 0
    arrival_cycle = [-1] * p.sources
    generated = [0] * p.sources
    overrun = [0] * p.sources
    served = [0] * p.sources
    waits: list[int] = []
    generated_history: list[list[int]] = []
    service_history: list[list[int]] = []
    home_history: list[int] = []
    for cycle, occurrence in enumerate(masks):
        generated_row = [0] * p.sources
        service_row = [0] * p.sources
        for source in range(p.sources):
            if occurrence & (1 << source):
                generated[source] += 1
                generated_row[source] = 1
                if pending & (1 << source):
                    overrun[source] += 1
                else:
                    pending |= 1 << source
                    arrival_cycle[source] = cycle
        winner = policy.step(pending)
        if winner is not None:
            assert pending & (1 << winner)
            pending &= ~(1 << winner)
            served[winner] += 1
            service_row[winner] = 1
            waits.append(cycle - arrival_cycle[winner])
            arrival_cycle[winner] = -1
        generated_history.append(generated_row)
        service_history.append(service_row)
        home_history.append(policy.state.home if isinstance(policy, A3Policy) else 0)
    ratios = [served[i] / generated[i] if generated[i] else 1.0 for i in range(p.sources)]
    return {
        "generated": sum(generated),
        "served": sum(served),
        "overrun": sum(overrun),
        "jain_demand_normalized": jain(ratios),
        "max_wait": max(waits, default=0),
        "policy_state_toggles": policy.toggles,
        "toggles_per_cycle": policy.toggles / len(masks),
        "generated_history": generated_history,
        "service_history": service_history,
        "home_history": home_history,
    }


def scenario_comparison(p: Parameters, cycles: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in ("asymmetric_rate_step", "rotating_burst", "correlated_oscillation", "one_source_recovery"):
        masks, transitions = workload(name, cycles, p.sources)
        for policy_name in ("a3", "rr", "fixed"):
            result = simulate(policy_name, masks, p)
            # Do not call a transition at the end of the trace "unsettled" when
            # a complete observation window does not exist (right censoring).
            evaluable = [t for t in transitions if t + 40 <= cycles]
            settle_samples = [settling_time(result["generated_history"], result["service_history"], t) for t in evaluable]
            finite = [value for value in settle_samples if value >= 0]
            control_recovery = ""
            if name == "one_source_recovery" and policy_name == "a3":
                history = result["home_history"]
                control_recovery = next((i - 128 for i in range(128, cycles) if history[i] == 0), -1)
            rows.append(
                {
                    "workload": name,
                    "policy": policy_name,
                    "cycles": cycles,
                    "generated": result["generated"],
                    "served": result["served"],
                    "overrun": result["overrun"],
                    "jain_demand_normalized": f'{result["jain_demand_normalized"]:.6f}',
                    "max_wait": result["max_wait"],
                    "settling_max_cycles": max(finite, default=-1),
                    "unsettled_steps": sum(value < 0 for value in settle_samples),
                    "home_recovery_cycles": control_recovery,
                    "policy_state_toggles": result["policy_state_toggles"],
                    "toggles_per_cycle": f'{result["toggles_per_cycle"]:.6f}',
                }
            )
    return rows


def parameter_regions(default: Parameters) -> list[dict[str, object]]:
    cases = [
        ("default", default),
        ("zero_progress", replace(default, gain_high=3)),
        ("negative_progress", replace(default, gain_high=2)),
        ("unreachable_threshold", replace(default, urgency_width=5)),
        ("no_leak", replace(default, leak=0)),
        ("excess_feedback_slope", replace(default, threshold_shift=2)),
    ]
    rows = []
    masks, _ = workload("correlated_oscillation", 512, default.sources)
    for name, p in cases:
        result = simulate("a3", masks, p)
        home = result["home_history"]
        nonzero_directions = []
        for i in range(1, len(home)):
            delta = home[i] - home[i - 1]
            if delta:
                nonzero_directions.append(1 if delta > 0 else -1)
        rows.append(
            {
                "case": name,
                "progress_min": p.progress_min,
                "threshold_max": p.threshold_max,
                "urgency_max": p.urgency_max,
                "bound": "" if p.service_bound is None else p.service_bound,
                "legal_rtl": int(p.progress_min > 0 and p.threshold_max <= p.urgency_max),
                "home_min": min(home),
                "home_max": max(home),
                "home_direction_changes": sum(
                    nonzero_directions[i] != nonzero_directions[i - 1]
                    for i in range(1, len(nonzero_directions))
                ),
                "max_wait": result["max_wait"],
                "toggles_per_cycle": f'{result["toggles_per_cycle"]:.6f}',
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=8)
    parser.add_argument("--cycles", type=int, default=512)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    default = Parameters()
    assert default.progress_min == 2
    assert default.threshold_max == 38
    assert default.service_bound == 35
    n4 = replace(default, sources=4)
    n4_scaled_feedback = replace(n4, home_low_active=1, home_high_active=2)
    exhaustive = exhaustive_transitions(n4, args.depth)
    scaled_exhaustive = exhaustive_transitions(n4_scaled_feedback, args.depth)
    token_order = exhaustive_token_order(n4)
    starvation = exhaustive_persistent_victim(n4)
    comparison = scenario_comparison(default, args.cycles)
    regions = parameter_regions(default)

    with (args.output_dir / "a3_n4_exhaustive.json").open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "invariants": {
                    "generated_equals_delivered_plus_overrun_plus_outstanding": True,
                    "no_grant_without_pending_token": True,
                    "single_retire_copy_per_grant": True,
                    "source_local_order_preserved_by_one_entry_latch": True,
                    "membrane_and_home_saturation_ranges": True,
                },
                "transport_default_thresholds": exhaustive,
                "transport_n4_scaled_feedback_thresholds": scaled_exhaustive,
                "token_identity_bounded_sequences": token_order,
                "starvation": starvation,
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    for filename, rows in (("a3_control_comparison.csv", comparison), ("a3_parameter_regions.csv", regions)):
        with (args.output_dir / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    assert all(int(row["max_wait"]) <= default.service_bound for row in comparison if row["policy"] == "a3")
    print(
        "A3_CONTROL_STABILITY PASS "
        f"states={exhaustive['reachable_states']} scaled_states={scaled_exhaustive['reachable_states']} "
        f"transitions={exhaustive['transitions'] + scaled_exhaustive['transitions']} "
        f"token_paths={token_order['leaf_sequences']} "
        f"victim_latest={starvation['latest_exhaustive_service']}/{n4.service_bound} "
        f"rows={len(comparison)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
