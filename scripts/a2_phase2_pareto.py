#!/usr/bin/env python3
"""Candidate-private A2 parameter and adversarial Pareto model.

The model follows the normalized one-entry source latch and one-lane retirement
boundary. It does not replace RTL correctness; tests/a2/run_parameter_sweep.sh
is the independent synthesizable-RTL gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Event:
    event_id: int
    source: int
    occurrence: int
    sparse_probe: bool = False


@dataclass(frozen=True)
class Config:
    sources: int
    banks: int
    depth: int
    enter: int
    exit: int
    dwell: int

    @property
    def name(self) -> str:
        return (
            f"n{self.sources}_b{self.banks}_d{self.depth}_"
            f"e{self.enter}_x{self.exit}_q{self.dwell}"
        )


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct * len(ordered)) - 1)]


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def rotate(valid: Iterable[int], base: int, sources: int) -> list[int]:
    valid_set = set(valid)
    return [source for offset in range(sources)
            if (source := (base + offset) % sources) in valid_set]


def workloads(sources: int, banks: int) -> dict[str, tuple[int, dict[int, list[tuple[int, bool]]]]]:
    result: dict[str, tuple[int, dict[int, list[tuple[int, bool]]]]] = {}

    sparse: dict[int, list[tuple[int, bool]]] = defaultdict(list)
    for cycle in range(8, 256, 8):
        sparse[cycle].append(((cycle // 8) % sources, True))
    result["sparse"] = (320, sparse)

    # These source/time streams remain identical across the bank-count sweep.
    # A modulo-4 congruence class adversarially collides in the four-bank
    # fixed-hash reference and also maps to one bank for the two-bank case.
    del banks
    adversarial_mod = 4
    width = min(8, sources // adversarial_mod)
    hotspot: dict[int, list[tuple[int, bool]]] = defaultdict(list)
    spread: dict[int, list[tuple[int, bool]]] = defaultdict(list)
    for epoch in (24, 152, 280):
        for burst in range(5):
            cycle = epoch + burst * 3
            for index in range(width):
                hotspot[cycle].append(((index * adversarial_mod) % sources, False))
                spread[cycle].append((index % sources, False))
    result["hotspot_fixed"] = (448, hotspot)
    result["hotspot_spread"] = (448, spread)

    recurrence: dict[int, list[tuple[int, bool]]] = defaultdict(list)
    group = min(sources, 6)
    for epoch in (24, 152, 280):
        for burst in range(12):
            cycle = epoch + burst * 2
            base = (epoch + burst) % sources
            for index in range(group):
                recurrence[cycle].append(((base + index * adversarial_mod) % sources, False))
    result["recurrence"] = (448, recurrence)

    for span in (1, 2, 4):
        oscillating: dict[int, list[tuple[int, bool]]] = defaultdict(list)
        for cycle in range(24, 280):
            high = ((cycle - 24) // span) % 2 == 1
            # Alternate exactly around the one-event/cycle service rate:
            # two simultaneous arrivals in the high phase and no arrivals in
            # the low phase.  This drains between phases instead of turning
            # the purported oscillation test into a permanently overloaded
            # burst test.
            count = min(sources, 2) if high else 0
            for index in range(count):
                oscillating[cycle].append(((cycle + index * 3) % sources, False))
        for cycle in range(352, 448, 8):
            oscillating[cycle].append(((cycle // 8) % sources, True))
        result[f"oscillate_{span}"] = (512, oscillating)
    return result


class Simulator:
    def __init__(self, model: str, config: Config):
        self.model = model
        self.cfg = config
        self.pending: list[Event | None] = [None] * config.sources
        self.pending_conflict = [False] * config.sources
        self.queue: list[Event] = []
        self.bank_queues: list[list[tuple[int, Event]]] = [
            [] for _ in range(config.banks)
        ]
        self.rotate_base = 0
        self.next_ticket = 0
        self.read_ticket = 0
        self.accepted = 0
        self.delivered = 0
        self.overrun = 0
        self.bank_conflict_overrun = 0
        self.bank_conflict_reject = 0
        self.latencies: list[int] = []
        self.sparse_latencies: list[int] = []
        self.toggle_proxy = 0
        self.last_write = [0] * config.banks
        self.last_read = [0] * config.banks
        self.last_state: dict[str, int] = {}
        self.mode = False
        self.quiet = 0
        self.previous_count = 0
        self.mode_transitions = 0
        self.naive_mode = False
        self.naive_transitions = 0
        self.max_occupancy = 0
        self.fixed_window_delivered = 0
        self.stim_cycles = 0
        self.burst_mode_cycles = 0
        self.last_mode_exit_cycle = -1

    def payload(self, event: Event) -> int:
        return (event.source << 16) | (event.event_id & 0xFFFF)

    def deliver(self, event: Event, cycle: int, bank: int = 0) -> None:
        self.delivered += 1
        if cycle < self.stim_cycles:
            self.fixed_window_delivered += 1
        latency = cycle - event.occurrence + 1
        self.latencies.append(latency)
        if event.sparse_probe:
            self.sparse_latencies.append(latency)
        word = self.payload(event)
        self.toggle_proxy += hamming(self.last_read[bank], word) + 1
        self.last_read[bank] = word

    def accept(self, event: Event, bank: int) -> None:
        self.accepted += 1
        self.pending[event.source] = None
        self.pending_conflict[event.source] = False
        word = self.payload(event)
        self.toggle_proxy += hamming(self.last_write[bank], word) + 1
        self.last_write[bank] = word

    def state_toggle(self, name: str, value: int) -> None:
        self.toggle_proxy += hamming(self.last_state.get(name, 0), value)
        self.last_state[name] = value

    def update_control(self, cycle: int, occupancy_before: int,
                       valid_count: int) -> None:
        # Deliberately fragile one-threshold controller used only as the
        # preregistered oscillation baseline.
        naive = valid_count >= 2
        if naive != self.naive_mode:
            self.naive_transitions += 1
        self.naive_mode = naive

        old_mode = self.mode
        if not self.mode:
            self.quiet = 0
            if (valid_count >= 2 or occupancy_before >= self.cfg.enter or
                    occupancy_before > self.previous_count):
                self.mode = True
        elif (occupancy_before <= self.cfg.exit and
              occupancy_before <= self.previous_count and valid_count < 2):
            self.quiet += 1
            if self.quiet >= self.cfg.dwell:
                self.mode = False
                self.quiet = 0
        else:
            self.quiet = 0
        if self.mode != old_mode:
            self.mode_transitions += 1
            if not self.mode:
                self.last_mode_exit_cycle = cycle

    def step(self, cycle: int) -> None:
        valid = [source for source, event in enumerate(self.pending) if event]
        ordered = rotate(valid, self.rotate_base, self.cfg.sources)
        occupancy_before = len(self.queue) + sum(map(len, self.bank_queues))
        if self.model == "a2":
            self.update_control(cycle, occupancy_before, len(valid))
            if self.mode:
                self.burst_mode_cycles += 1

        if self.model == "flat_rr":
            if self.queue:
                self.deliver(self.queue.pop(0), cycle)
            if ordered and not self.queue:
                event = self.pending[ordered[0]]
                assert event is not None
                self.accept(event, 0)
                self.queue.append(event)
                self.rotate_base = (event.source + 1) % self.cfg.sources
        elif self.model in {"a2", "always_buffered"}:
            had_queue = bool(self.queue)
            if had_queue:
                self.deliver(self.queue.pop(0), cycle,
                             self.read_ticket % self.cfg.banks)
                self.read_ticket += 1
            direct: Event | None = None
            if self.model == "a2" and not had_queue and ordered:
                direct = self.pending[ordered.pop(0)]
                assert direct is not None
                self.accepted += 1
                self.pending[direct.source] = None
                self.pending_conflict[direct.source] = False
                self.delivered += 1
                if cycle < self.stim_cycles:
                    self.fixed_window_delivered += 1
                latency = cycle - direct.occurrence + 1
                self.latencies.append(latency)
                if direct.sparse_probe:
                    self.sparse_latencies.append(latency)
                self.rotate_base = (direct.source + 1) % self.cfg.sources
            free = self.cfg.depth - len(self.queue)
            wide = (self.model == "always_buffered" or self.mode or
                    len(valid) >= 2 or occupancy_before > self.previous_count or
                    occupancy_before >= self.cfg.enter)
            limit = min(self.cfg.banks if wide else 1, free)
            if self.model == "a2" and direct is None and not had_queue:
                limit = 0
            chosen = ordered[:limit]
            for lane, source in enumerate(chosen):
                event = self.pending[source]
                assert event is not None
                bank = (self.next_ticket + lane) % self.cfg.banks
                self.accept(event, bank)
                self.queue.append(event)
            if chosen:
                self.next_ticket += len(chosen)
                self.rotate_base = (chosen[-1] + 1) % self.cfg.sources
        elif self.model == "fixed_hash":
            heads = [(queue[0][0], bank) for bank, queue in enumerate(self.bank_queues)
                     if queue]
            if heads:
                _, bank = min(heads)
                _, event = self.bank_queues[bank].pop(0)
                self.deliver(event, cycle, bank)
            accepted_banks: set[int] = set()
            chosen: list[int] = []
            bank_depth = self.cfg.depth // self.cfg.banks
            for source in ordered:
                bank = source % self.cfg.banks
                if bank in accepted_banks or len(self.bank_queues[bank]) >= bank_depth:
                    if (bank in accepted_banks and
                            sum(len(queue) for queue in self.bank_queues) < self.cfg.depth):
                        self.bank_conflict_reject += 1
                        self.pending_conflict[source] = True
                    continue
                event = self.pending[source]
                assert event is not None
                self.accept(event, bank)
                self.bank_queues[bank].append((self.next_ticket, event))
                self.next_ticket += 1
                accepted_banks.add(bank)
                chosen.append(source)
            if chosen:
                self.rotate_base = (chosen[-1] + 1) % self.cfg.sources
        else:
            raise ValueError(self.model)
        occupancy = len(self.queue) + sum(map(len, self.bank_queues))
        self.max_occupancy = max(self.max_occupancy, occupancy)
        self.state_toggle("rotate", self.rotate_base)
        self.state_toggle("occupancy", occupancy)
        if self.model in {"a2", "always_buffered"}:
            self.state_toggle("write_pointer", self.next_ticket % self.cfg.depth)
            self.state_toggle("read_pointer", self.read_ticket % self.cfg.depth)
        if self.model == "a2":
            self.state_toggle("previous_count", self.previous_count)
            self.state_toggle("quiet", self.quiet)
            self.state_toggle("mode", int(self.mode))
        elif self.model == "fixed_hash":
            self.state_toggle("ticket", self.next_ticket)
            for bank, queue in enumerate(self.bank_queues):
                self.state_toggle(f"bank_count_{bank}", len(queue))
        self.previous_count = occupancy_before

    def run(self, stim_cycles: int,
            occurrences: dict[int, list[tuple[int, bool]]]) -> dict[str, float | int | str]:
        self.stim_cycles = stim_cycles
        next_id = 0
        cycle = 0
        for cycle in range(stim_cycles):
            for source, sparse_probe in occurrences.get(cycle, []):
                if self.pending[source] is not None:
                    self.overrun += 1
                    if self.pending_conflict[source]:
                        self.bank_conflict_overrun += 1
                else:
                    self.pending[source] = Event(next_id, source, cycle, sparse_probe)
                    next_id += 1
            self.step(cycle)
        while (any(self.pending) or self.queue or any(self.bank_queues)) and cycle < stim_cycles + 8192:
            cycle += 1
            self.step(cycle)
        if any(self.pending) or self.queue or any(self.bank_queues):
            raise RuntimeError(f"drain timeout {self.model} {self.cfg.name}")
        if self.accepted != self.delivered:
            raise RuntimeError(f"conservation failure {self.model} {self.cfg.name}")
        return {
            "model": self.model,
            "config": self.cfg.name,
            "sources": self.cfg.sources,
            "banks": self.cfg.banks,
            "depth": self.cfg.depth,
            "enter": self.cfg.enter,
            "exit": self.cfg.exit,
            "dwell": self.cfg.dwell,
            "generated": next_id + self.overrun,
            "overrun": self.overrun,
            "accepted": self.accepted,
            "delivered": self.delivered,
            "fixed_window_delivered": self.fixed_window_delivered,
            "fixed_window_throughput": self.fixed_window_delivered / stim_cycles,
            "bank_conflict_reject": self.bank_conflict_reject,
            "bank_conflict_overrun": self.bank_conflict_overrun,
            "p95": percentile(self.latencies, 0.95),
            "p99": percentile(self.latencies, 0.99),
            "sparse_p95": percentile(self.sparse_latencies, 0.95),
            "sparse_p99": percentile(self.sparse_latencies, 0.99),
            "mode_transitions": self.mode_transitions,
            "naive_transitions": self.naive_transitions,
            "burst_mode_cycles": self.burst_mode_cycles,
            "last_mode_exit_cycle": self.last_mode_exit_cycle,
            "max_occupancy": self.max_occupancy,
            "toggle_per_event": self.toggle_proxy / max(1, self.delivered),
            "drain_cycles": cycle + 1 - stim_cycles,
        }


def proxy(config: Config, model: str) -> tuple[int, int, int]:
    source_width = math.ceil(math.log2(config.sources))
    ptr_width = math.ceil(math.log2(config.depth))
    count_width = math.ceil(math.log2(config.depth + 1))
    quiet_width = max(1, math.ceil(math.log2(config.dwell + 1)))
    if model == "flat_rr":
        state = 1 + 16 + source_width + source_width
        cell = state + 2 * config.sources + source_width
        depth = math.ceil(math.log2(config.sources)) + 2
    else:
        state = (config.depth * (16 + source_width) + 2 * ptr_width +
                 2 * count_width + source_width + quiet_width + 1)
        if model == "always_buffered":
            state -= count_width + quiet_width + 1
        cell = state + 4 * config.sources + 12 * config.banks + 8 * count_width
        depth = (math.ceil(math.log2(config.sources)) +
                 math.ceil(math.log2(config.banks + 1)) + 2)
    return state, cell, depth


def dominated(point: dict[str, float | int], others: list[dict[str, float | int]]) -> bool:
    keys = ("overrun_score", "p99_score", "toggle_score", "state_bits", "depth_proxy")
    for other in others:
        if other is point:
            continue
        no_worse = all(float(other[key]) <= float(point[key]) for key in keys)
        better = any(float(other[key]) < float(point[key]) for key in keys)
        if no_worse and better:
            return True
    return False


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    structural_rows: list[dict[str, object]] = []
    scores: dict[int, list[dict[str, float | int]]] = defaultdict(list)

    for sources in (16, 32, 64):
        for banks in (1, 2, 4):
            for depth in (4, 8, 16):
                if banks > depth or depth % banks:
                    continue
                cfg = Config(sources, banks, depth, depth // 2, 1, 3)
                per_config: list[dict[str, object]] = []
                for workload, (stim_cycles, trace) in workloads(sources, banks).items():
                    for model in ("a2", "flat_rr", "always_buffered", "fixed_hash"):
                        row = Simulator(model, cfg).run(stim_cycles, trace)
                        row["phase_recovery_cycles"] = (
                            max(0, int(row["last_mode_exit_cycle"]) - 279)
                            if workload.startswith("oscillate") and model == "a2"
                            else 0
                        )
                        state, cell, depth_value = proxy(cfg, model)
                        row.update({"workload": workload, "state_bits": state,
                                    "cell_proxy": cell, "depth_proxy": depth_value})
                        structural_rows.append(row)
                        per_config.append(row)
                a2 = [row for row in per_config if row["model"] == "a2"]
                scores[sources].append({
                    "sources": sources, "banks": banks, "depth": depth,
                    "overrun_score": sum(int(row["overrun"]) for row in a2
                                         if row["workload"] in {"hotspot_fixed", "recurrence"}),
                    "p99_score": max(int(row["p99"]) for row in a2),
                    "toggle_score": sum(float(row["toggle_per_event"]) for row in a2) / len(a2),
                    "state_bits": proxy(cfg, "a2")[0],
                    "depth_proxy": proxy(cfg, "a2")[2],
                })

    nondominated: list[dict[str, float | int]] = []
    for points in scores.values():
        nondominated.extend(point for point in points if not dominated(point, points))

    control_rows: list[dict[str, object]] = []
    for point in nondominated:
        sources = int(point["sources"])
        banks = int(point["banks"])
        depth = int(point["depth"])
        enters = sorted({max(1, depth // 4), depth // 2, 3 * depth // 4})
        exits = sorted({0, 1, depth // 4})
        selected_workloads = workloads(sources, banks)
        for enter in enters:
            for exit_level in exits:
                if exit_level >= enter:
                    continue
                for dwell in (1, 3, 7):
                    cfg = Config(sources, banks, depth, enter, exit_level, dwell)
                    for workload in ("sparse", "oscillate_1", "oscillate_2", "oscillate_4"):
                        stim_cycles, trace = selected_workloads[workload]
                        row = Simulator("a2", cfg).run(stim_cycles, trace)
                        row["phase_recovery_cycles"] = (
                            max(0, int(row["last_mode_exit_cycle"]) - 279)
                            if workload.startswith("oscillate") else 0
                        )
                        state, cell, depth_value = proxy(cfg, "a2")
                        row.update({"workload": workload, "state_bits": state,
                                    "cell_proxy": cell, "depth_proxy": depth_value})
                        control_rows.append(row)

    nondominated_keys = {
        (int(point["sources"]), int(point["banks"]), int(point["depth"]))
        for point in nondominated
    }
    best_reduction: dict[int, int] = {}
    for sources in (16, 32, 64):
        reductions: list[int] = []
        for banks in (1, 2, 4):
            for depth in (4, 8, 16):
                selected = [
                    row for row in structural_rows
                    if int(row["sources"]) == sources and int(row["banks"]) == banks
                    and int(row["depth"]) == depth
                    and row["workload"] in {"hotspot_fixed", "recurrence"}
                ]
                a2_overrun = sum(int(row["overrun"]) for row in selected
                                 if row["model"] == "a2")
                flat_overrun = sum(int(row["overrun"]) for row in selected
                                   if row["model"] == "flat_rr")
                reductions.append(flat_overrun - a2_overrun)
        best_reduction[sources] = max(reductions)

    families: list[dict[str, object]] = []
    for banks in (1, 2, 4):
        for depth in (4, 8, 16):
            per_n: list[dict[str, object]] = []
            controls_ok = True
            for sources in (16, 32, 64):
                def rows_for(model: str, workload: str) -> list[dict[str, object]]:
                    return [
                        row for row in structural_rows
                        if int(row["sources"]) == sources
                        and int(row["banks"]) == banks
                        and int(row["depth"]) == depth
                        and row["model"] == model and row["workload"] == workload
                    ]

                a2_pressure = sum(int(rows_for("a2", workload)[0]["overrun"])
                                  for workload in ("hotspot_fixed", "recurrence"))
                flat_pressure = sum(int(rows_for("flat_rr", workload)[0]["overrun"])
                                    for workload in ("hotspot_fixed", "recurrence"))
                reduction = flat_pressure - a2_pressure
                sparse_a2 = rows_for("a2", "sparse")[0]
                sparse_always = rows_for("always_buffered", "sparse")[0]
                ratio = float(sparse_a2["toggle_per_event"]) / max(
                    float(sparse_always["toggle_per_event"]), 1e-12)

                candidates: dict[str, list[dict[str, object]]] = defaultdict(list)
                for row in control_rows:
                    if (int(row["sources"]), int(row["banks"]), int(row["depth"])) == (
                            sources, banks, depth):
                        candidates[str(row["config"])].append(row)
                passing_controls: list[tuple[tuple[float, ...], dict[str, object]]] = []
                for rows in candidates.values():
                    oscillating = [row for row in rows
                                   if str(row["workload"]).startswith("oscillate")]
                    sparse = next(row for row in rows if row["workload"] == "sparse")
                    if (all(2 * int(row["mode_transitions"]) <=
                            int(row["naive_transitions"]) for row in oscillating)
                            and int(sparse["sparse_p95"]) == 1
                            and int(sparse["sparse_p99"]) == 1):
                        representative = rows[0]
                        rank = (
                            max(int(row["p99"]) for row in oscillating),
                            sum(float(row["toggle_per_event"]) for row in rows) / len(rows),
                            int(representative["state_bits"]),
                            int(representative["cell_proxy"]),
                            int(representative["depth_proxy"]),
                            int(representative["enter"]),
                            int(representative["exit"]),
                            int(representative["dwell"]),
                        )
                        passing_controls.append((rank, representative))
                control = min(passing_controls, default=None, key=lambda item: item[0])
                controls_ok &= control is not None
                per_n.append({
                    "sources": sources,
                    "overrun_a2": a2_pressure,
                    "overrun_flat": flat_pressure,
                    "overrun_reduction": reduction,
                    "overrun_reduction_fraction": reduction / max(1, flat_pressure),
                    "retains_90pct_best": reduction >= 0.9 * best_reduction[sources],
                    "sparse_p95": int(sparse_a2["sparse_p95"]),
                    "sparse_p99": int(sparse_a2["sparse_p99"]),
                    "sparse_toggle_ratio_vs_always": ratio,
                    "nondominated": (sources, banks, depth) in nondominated_keys,
                    "selected_control": None if control is None else {
                        "enter": int(control[1]["enter"]),
                        "exit": int(control[1]["exit"]),
                        "dwell": int(control[1]["dwell"]),
                    },
                })
            hard_gates = {
                "sparse_latency": all(row["sparse_p95"] == 1 and row["sparse_p99"] == 1
                                      for row in per_n),
                "zero_a2_bank_conflict": all(
                    int(row["bank_conflict_reject"]) == 0
                    for row in structural_rows
                    if row["model"] == "a2" and int(row["banks"]) == banks
                    and int(row["depth"]) == depth
                ),
                "absorption": (all(int(row["overrun_reduction"]) > 0 for row in per_n)
                               and sum(float(row["overrun_reduction_fraction"]) >= 0.10
                                       for row in per_n) >= 2),
                "oscillation_hysteresis": controls_ok,
                "sparse_toggle": all(float(row["sparse_toggle_ratio_vs_always"]) <= 0.60
                                     for row in per_n),
                "nondominated": all(bool(row["nondominated"]) for row in per_n),
            }
            families.append({
                "banks": banks,
                "depth": depth,
                "per_n": per_n,
                "hard_gates": hard_gates,
                "model_shortlisted": all(hard_gates.values()),
                "retains_90pct_best_all_n": all(bool(row["retains_90pct_best"])
                                                 for row in per_n),
            })

    soft_candidates = [family for family in families
                       if family["model_shortlisted"]
                       and family["retains_90pct_best_all_n"]]
    selected_family = min(soft_candidates,
                          key=lambda family: (int(family["depth"]), int(family["banks"])),
                          default=None)
    fixed_hash_conflicts = sum(
        int(row["bank_conflict_reject"]) for row in structural_rows
        if row["model"] == "fixed_hash" and row["workload"] == "hotspot_fixed"
    )
    decision = {
        "best_overrun_reduction_by_n": best_reduction,
        "families": families,
        "fixed_hash_hotspot_conflict_rejects": fixed_hash_conflicts,
        "selected_family": selected_family,
        "rtl_gate_external_to_model": True,
    }

    write_csv(args.output_dir / "structural.csv", structural_rows)
    write_csv(args.output_dir / "control.csv", control_rows)
    with (args.output_dir / "nondominated.json").open("w", encoding="utf-8") as handle:
        json.dump(nondominated, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with (args.output_dir / "decision.json").open("w", encoding="utf-8") as handle:
        json.dump(decision, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"A2_PHASE2_MODEL_PASS structural_rows={len(structural_rows)} "
        f"control_rows={len(control_rows)} nondominated={len(nondominated)} "
        f"output={args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
