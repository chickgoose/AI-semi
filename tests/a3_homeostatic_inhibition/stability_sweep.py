#!/usr/bin/env python3
"""Deterministic A3 parameter sweep and persistent-victim counterexample search."""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Parameters:
    urgency_width: int
    gain_high: int
    inhibit_high: int
    threshold_base: int
    threshold_shift: int
    leak: int = 1
    home_width: int = 4
    sources: int = 16

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
    def progress(self) -> int:
        return self.gain_high - self.leak - self.inhibit_high

    @property
    def bound(self) -> int | None:
        if self.progress <= 0 or self.threshold_max > self.urgency_max:
            return None
        return math.ceil(self.threshold_max / self.progress) + self.sources


class Model:
    def __init__(self, parameters: Parameters):
        self.p = parameters
        self.membrane = [0] * parameters.sources
        self.home = 0
        self.phase = 0

    def step(self, requests: list[bool]) -> int | None:
        active = sum(requests)
        high = self.home >= (1 << (self.p.home_width - 1))
        gain = self.p.gain_high if high else self.p.gain_high + 1
        inhibit = self.p.inhibit_high if high else max(0, self.p.inhibit_high - 1)
        threshold = self.p.threshold_base + (self.home << self.p.threshold_shift)
        order = [(self.phase + offset) % self.p.sources for offset in range(self.p.sources)]
        protected = [index for index in order if requests[index] and self.membrane[index] >= threshold]
        if protected:
            winner = protected[0]
        else:
            candidates = [index for index in order if requests[index]]
            winner = max(candidates, key=lambda index: self.membrane[index]) if candidates else None

        if active > 4:
            self.home = min(self.p.home_max, self.home + 1)
        elif active < 2:
            self.home = max(0, self.home - 1)

        for index in range(self.p.sources):
            if index == winner:
                self.membrane[index] = 0
            elif requests[index]:
                delta = gain - self.p.leak - (inhibit if winner is not None else 0)
                self.membrane[index] = min(self.p.urgency_max, max(0, self.membrane[index] + delta))
            else:
                self.membrane[index] = max(0, self.membrane[index] - self.p.leak)
        if winner is not None:
            self.phase = (winner + 1) % self.p.sources
        return winner


def requests_for(pattern: str, cycle: int, rng: random.Random, sources: int) -> list[bool]:
    if pattern == "fanin":
        return [True] * sources
    if pattern == "elephant_mouse":
        request = [False] * sources
        request[0] = True
        request[1] = True
        return request
    if pattern == "moving_hotspot":
        request = [False] * sources
        request[0] = True
        request[1 + ((cycle // 64) % (sources - 1))] = True
        return request
    if pattern == "random_adversary":
        return [True] + [rng.random() < 0.72 for _ in range(sources - 1)]
    raise ValueError(pattern)


def run_case(parameters: Parameters, pattern: str, cycles: int, seed: int) -> tuple[int, int, int]:
    model = Model(parameters)
    rng = random.Random(seed)
    last_victim_service = 0
    max_victim_wait = 0
    victim_services = 0
    for cycle in range(1, cycles + 1):
        request = requests_for(pattern, cycle, rng, parameters.sources)
        winner = model.step(request)
        if winner == 0:
            max_victim_wait = max(max_victim_wait, cycle - last_victim_service)
            last_victim_service = cycle
            victim_services += 1
        max_victim_wait = max(max_victim_wait, cycle - last_victim_service)
    return max_victim_wait, victim_services, model.home


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=4096)
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    failures = 0
    patterns = ("fanin", "elephant_mouse", "moving_hotspot", "random_adversary")
    for width in (5, 6, 7):
        for gain_high in (4, 5, 6):
            for inhibit_high in (1, 2):
                for threshold_base in (4, 8, 12):
                    for threshold_shift in (0, 1):
                        parameters = Parameters(
                            urgency_width=width,
                            gain_high=gain_high,
                            inhibit_high=inhibit_high,
                            threshold_base=threshold_base,
                            threshold_shift=threshold_shift,
                        )
                        legal = parameters.bound is not None
                        for pattern in patterns:
                            wait, services, final_home = run_case(
                                parameters, pattern, args.cycles, seed=0xA300 + width
                            )
                            bound_ok = legal and wait <= int(parameters.bound)
                            if legal and not bound_ok:
                                failures += 1
                            rows.append(
                                {
                                    "urgency_width": width,
                                    "gain_high": gain_high,
                                    "inhibit_high": inhibit_high,
                                    "threshold_base": threshold_base,
                                    "threshold_shift": threshold_shift,
                                    "progress": parameters.progress,
                                    "threshold_max": parameters.threshold_max,
                                    "legal": int(legal),
                                    "analytical_bound": "" if parameters.bound is None else parameters.bound,
                                    "pattern": pattern,
                                    "cycles": args.cycles,
                                    "victim_services": services,
                                    "max_victim_wait": wait,
                                    "final_homeostasis": final_home,
                                    "bound_ok": int(bound_ok),
                                }
                            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    legal_rows = sum(int(row["legal"]) for row in rows)
    rejected_rows = len(rows) - legal_rows
    print(
        f"A3_STABILITY_SWEEP rows={len(rows)} legal_rows={legal_rows} "
        f"rejected_rows={rejected_rows} counterexamples={failures} output={args.output}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
