#!/usr/bin/env python3
"""Frozen-trace A3 fixed-point parameter and state-toggle Pareto sweep."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path


TRACE_NAMES = (
    "core_sparse_identity",
    "core_simultaneous_identity",
    "uniform_l0p90_s2001",
    "uniform_l1p25_s2001",
    "uniform_l2p00_s2001",
    "shape_b16",
    "moving_hotspot_single_s3301",
    "rotating_victim_identity",
    "rotating_victim_affine",
    "phase_transition_s3501",
    "elephant_mouse_identity",
)
HIGH_LOAD_NAMES = {
    "uniform_l1p25_s2001",
    "uniform_l2p00_s2001",
    "rotating_victim_identity",
    "rotating_victim_affine",
    "phase_transition_s3501",
}


@dataclass(frozen=True)
class Parameters:
    urgency_width: int
    threshold_base: int
    threshold_shift: int
    leak: int
    inhibit_high: int
    gain_high: int
    sources: int = 16
    home_width: int = 4

    @property
    def gain_low(self) -> int:
        return self.gain_high + 1

    @property
    def inhibit_low(self) -> int:
        return max(0, self.inhibit_high - 1)

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
    def legal(self) -> bool:
        return self.progress > 0 and self.threshold_max <= self.urgency_max

    @property
    def bound(self) -> int:
        return math.ceil(self.threshold_max / self.progress) + self.sources

    @property
    def policy_state_bits(self) -> int:
        return self.sources * self.urgency_width + self.home_width + 4

    @property
    def candidate_state_bits(self) -> int:
        return self.policy_state_bits + 1 + 4 + 16

    @property
    def identifier(self) -> str:
        return (
            f"u{self.urgency_width}_tb{self.threshold_base}_ts{self.threshold_shift}_"
            f"l{self.leak}_ih{self.inhibit_high}_gh{self.gain_high}"
        )


@dataclass(frozen=True)
class Trace:
    name: str
    stim_cycles: int
    arrivals: tuple[tuple[int, ...], ...]
    offered: tuple[int, ...]


def load_trace(trace_dir: Path, name: str, sources: int = 16) -> Trace:
    manifest = json.loads((trace_dir / f"{name}.manifest.json").read_text())
    stim_cycles = int(manifest["run"]["stim_cycles"])
    arrivals: list[list[int]] = [[] for _ in range(stim_cycles)]
    offered = [0] * sources
    with (trace_dir / f"{name}.events.jsonl").open() as stream:
        for line in stream:
            event = json.loads(line)
            cycle = int(event["occurrence_cycle"])
            source = int(event["logical_source"])
            arrivals[cycle].append(source)
            offered[source] += 1
    return Trace(
        name=name,
        stim_cycles=stim_cycles,
        arrivals=tuple(tuple(row) for row in arrivals),
        offered=tuple(offered),
    )


def hamming(old: int, new: int) -> int:
    return (old ^ new).bit_count()


def jain(values: list[float]) -> float:
    if not values:
        return 1.0
    denominator = len(values) * sum(value * value for value in values)
    return (sum(values) ** 2) / denominator if denominator else 1.0


def simulate(parameters: Parameters, trace: Trace) -> dict[str, float | int | str]:
    n = parameters.sources
    membrane = [0] * n
    home = 0
    phase = 0
    pending = [False] * n
    pending_since = [0] * n
    accepted = [0] * n
    overrun = 0
    max_wait = 0
    output_valid = False
    output_source = 0
    measurement_delivered = 0
    membrane_toggles = 0
    home_toggles = 0
    phase_toggles = 0
    retire_toggles = 0

    def step(cycle: int) -> None:
        nonlocal home, phase, output_valid, output_source
        nonlocal max_wait, membrane_toggles, home_toggles, phase_toggles
        nonlocal retire_toggles
        active = sum(pending)
        high = home >= (1 << (parameters.home_width - 1))
        gain = parameters.gain_high if high else parameters.gain_low
        inhibit = parameters.inhibit_high if high else parameters.inhibit_low
        threshold = parameters.threshold_base + (home << parameters.threshold_shift)
        order = [(phase + offset) % n for offset in range(n)]
        protected = [
            source for source in order if pending[source] and membrane[source] >= threshold
        ]
        if protected:
            winner = protected[0]
        else:
            candidates = [source for source in order if pending[source]]
            winner = max(candidates, key=lambda source: membrane[source]) if candidates else None

        next_home = home
        if active > 4:
            next_home = min(parameters.home_max, home + 1)
        elif active < 2:
            next_home = max(0, home - 1)
        home_toggles += hamming(home, next_home)
        home = next_home

        next_membrane = membrane.copy()
        for source in range(n):
            if source == winner:
                next_membrane[source] = 0
            elif pending[source]:
                delta = gain - parameters.leak - (inhibit if winner is not None else 0)
                next_membrane[source] = min(
                    parameters.urgency_max, max(0, membrane[source] + delta)
                )
            else:
                next_membrane[source] = max(0, membrane[source] - parameters.leak)
            membrane_toggles += hamming(membrane[source], next_membrane[source])
        membrane[:] = next_membrane

        next_output_valid = winner is not None
        next_output_source = output_source if winner is None else winner
        retire_toggles += int(output_valid != next_output_valid)
        if winner is not None:
            retire_toggles += hamming(output_source, next_output_source) * 2
            next_phase = (winner + 1) % n
            phase_toggles += hamming(phase, next_phase)
            phase = next_phase
            pending[winner] = False
            accepted[winner] += 1
            max_wait = max(max_wait, cycle - pending_since[winner])
        output_valid = next_output_valid
        output_source = next_output_source

    for cycle in range(trace.stim_cycles):
        if output_valid:
            measurement_delivered += 1
        for source in trace.arrivals[cycle]:
            if pending[source]:
                overrun += 1
            else:
                pending[source] = True
                pending_since[source] = cycle
        step(cycle)

    drain_cycle = trace.stim_cycles
    while any(pending) or output_valid:
        step(drain_cycle)
        drain_cycle += 1
        if drain_cycle > trace.stim_cycles + 1024:
            raise RuntimeError(f"drain failed for {parameters.identifier} {trace.name}")
    for _ in range(8):
        step(drain_cycle)
        drain_cycle += 1

    ratios = [
        accepted[source] / trace.offered[source]
        for source in range(n)
        if trace.offered[source]
    ]
    policy_toggles = membrane_toggles + home_toggles + phase_toggles
    total_toggles = policy_toggles + retire_toggles
    if sum(accepted) + overrun != sum(trace.offered):
        raise AssertionError(
            f"conservation failed for {parameters.identifier} {trace.name}"
        )
    if max_wait > parameters.bound:
        raise AssertionError(
            f"bound failed for {parameters.identifier} {trace.name}: "
            f"wait={max_wait} bound={parameters.bound}"
        )
    return {
        "name": trace.name,
        "generated": sum(trace.offered),
        "accepted": sum(accepted),
        "overrun": overrun,
        "measurement_delivered": measurement_delivered,
        "throughput": measurement_delivered / trace.stim_cycles,
        "max_wait": max_wait,
        "fairness": jain(ratios),
        "min_source_ratio": min(ratios) if ratios else 1.0,
        "membrane_toggles": membrane_toggles,
        "home_toggles": home_toggles,
        "phase_toggles": phase_toggles,
        "policy_toggles": policy_toggles,
        "total_state_toggles": total_toggles,
        "stim_cycles": trace.stim_cycles,
    }


def dominates(left: dict[str, object], right: dict[str, object]) -> bool:
    lower = ("high_toggles_per_cycle", "total_overrun", "worst_max_wait", "policy_state_bits")
    higher = ("min_fairness", "min_source_ratio")
    no_worse = all(float(left[key]) <= float(right[key]) for key in lower) and all(
        float(left[key]) >= float(right[key]) for key in higher
    )
    strictly = any(float(left[key]) < float(right[key]) for key in lower) or any(
        float(left[key]) > float(right[key]) for key in higher
    )
    return no_worse and strictly


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pareto-output", type=Path, required=True)
    parser.add_argument("--rtl-aggregate", type=Path)
    parser.add_argument("--rtl-id")
    args = parser.parse_args()
    traces = [load_trace(args.trace_dir, name) for name in TRACE_NAMES]

    rows: list[dict[str, object]] = []
    rejected = 0
    for values in itertools.product(
        (5, 6, 7), (4, 8, 12), (0, 1), (0, 1, 2), (1, 2, 3), (4, 5, 6)
    ):
        parameters = Parameters(*values)
        if not parameters.legal:
            rejected += 1
            continue
        results = [simulate(parameters, trace) for trace in traces]
        high = [result for result in results if result["name"] in HIGH_LOAD_NAMES]
        high_cycles = sum(int(result["stim_cycles"]) for result in high)
        row: dict[str, object] = {
            "id": parameters.identifier,
            "urgency_width": parameters.urgency_width,
            "threshold_base": parameters.threshold_base,
            "threshold_shift": parameters.threshold_shift,
            "leak": parameters.leak,
            "inhibit_high": parameters.inhibit_high,
            "inhibit_low": parameters.inhibit_low,
            "gain_high": parameters.gain_high,
            "gain_low": parameters.gain_low,
            "progress": parameters.progress,
            "analytical_bound": parameters.bound,
            "policy_state_bits": parameters.policy_state_bits,
            "candidate_state_bits": parameters.candidate_state_bits,
            "total_overrun": sum(int(result["overrun"]) for result in results),
            "worst_max_wait": max(int(result["max_wait"]) for result in results),
            "min_fairness": min(float(result["fairness"]) for result in results),
            "min_source_ratio": min(float(result["min_source_ratio"]) for result in results),
            "high_toggles_per_cycle": sum(
                int(result["total_state_toggles"]) for result in high
            ) / high_cycles,
            "high_membrane_toggles_per_cycle": sum(
                int(result["membrane_toggles"]) for result in high
            ) / high_cycles,
            "all_toggles_per_cycle": sum(
                int(result["total_state_toggles"]) for result in results
            ) / sum(int(result["stim_cycles"]) for result in results),
            "default": int(parameters == Parameters(6, 8, 1, 1, 2, 5)),
        }
        rows.append(row)

    for row in rows:
        row["pareto"] = int(not any(dominates(other, row) for other in rows if other is not row))
    fieldnames = list(rows[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    pareto_rows = [row for row in rows if row["pareto"]]
    with args.pareto_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(pareto_rows)

    default = next(row for row in rows if row["default"])
    lower_bounded = [
        row
        for row in rows
        if float(row["high_toggles_per_cycle"]) < float(default["high_toggles_per_cycle"])
        and int(row["analytical_bound"]) <= int(default["analytical_bound"])
    ]
    # Equal net progress can make several parameter tuples dynamically
    # identical.  Break exact toggle ties by proximity to the proven default,
    # avoiding a zero-leak stale-state choice when an equivalent leaky point
    # exists.
    best = min(
        lower_bounded,
        key=lambda row: (
            float(row["high_toggles_per_cycle"]),
            abs(int(row["leak"]) - 1),
            abs(int(row["inhibit_high"]) - 2),
            abs(int(row["gain_high"]) - 5),
        ),
    )
    rtl_crosscheck = "SKIP"
    if args.rtl_aggregate is not None:
        if not args.rtl_id:
            parser.error("--rtl-id is required with --rtl-aggregate")
        model_row = next((row for row in rows if row["id"] == args.rtl_id), None)
        if model_row is None:
            parser.error(f"--rtl-id is not in the legal sweep: {args.rtl_id}")
        with args.rtl_aggregate.open() as stream:
            rtl_rows = [
                row for row in csv.DictReader(stream) if row["test"] in TRACE_NAMES
            ]
        rtl_metrics = {
            "total_overrun": sum(int(row["source_overrun"]) for row in rtl_rows),
            "worst_max_wait": max(int(float(row["worst_request_wait"])) for row in rtl_rows),
            "min_fairness": min(
                float(row["demand_normalized_delivery_fairness"]) for row in rtl_rows
            ),
            "min_source_ratio": min(
                float(row["min_source_delivery_ratio"]) for row in rtl_rows
            ),
        }
        for key, rtl_value in rtl_metrics.items():
            model_value = float(model_row[key])
            if not math.isclose(model_value, float(rtl_value), rel_tol=0.0, abs_tol=1e-6):
                raise AssertionError(
                    f"RTL/model mismatch id={args.rtl_id} metric={key} "
                    f"model={model_value} rtl={rtl_value}"
                )
        rtl_crosscheck = "PASS"
    print(
        f"A3_PARETO_SWEEP legal={len(rows)} rejected={rejected} pareto={len(pareto_rows)} "
        f"default={default['id']} default_toggle={default['high_toggles_per_cycle']:.6f} "
        f"best_bounded={best['id']} best_toggle={best['high_toggles_per_cycle']:.6f} "
        f"rtl_crosscheck={rtl_crosscheck}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
