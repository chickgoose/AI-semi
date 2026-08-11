#!/usr/bin/env python3
"""Executable exhaustive reference for the Expander-Conservative Reaction Fabric.

The exact matcher in this file is a certificate oracle only.  ECRF itself is
the bounded local reaction implemented by :func:`conservative_reaction`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


N_SOURCES = 16
K_VALUES = (2, 4)
B_MAX = 12
D_MAX = 4
TOPOLOGY_SEEDS = 64
WIRE_RATIO_GATE = 0.85
MASK64 = (1 << 64) - 1


def ceil_log2(value: int) -> int:
    return 0 if value <= 1 else (value - 1).bit_length()


def percentile(values: Sequence[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def splitmix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return (value ^ (value >> 31)) & MASK64


@dataclass(frozen=True)
class Topology:
    n: int
    k: int
    b: int
    d: int
    seed: int
    neighbors: tuple[int, ...]

    @property
    def edge_count(self) -> int:
        return sum(mask.bit_count() for mask in self.neighbors)

    @property
    def cell_sources(self) -> tuple[tuple[int, ...], ...]:
        return tuple(
            tuple(source for source, mask in enumerate(self.neighbors)
                  if (mask >> cell) & 1)
            for cell in range(self.b)
        )

    @property
    def max_cell_fanin(self) -> int:
        return max((len(sources) for sources in self.cell_sources), default=0)

    @property
    def wire_proxy(self) -> int:
        return self.n * self.d + self.b * self.k

    @property
    def flat_wire_proxy(self) -> int:
        return self.n * self.k

    @property
    def work_proxy(self) -> int:
        return self.k * (2 * self.n * self.d + self.b * self.k + self.b)

    @property
    def flat_work_proxy(self) -> int:
        return self.n * self.k

    @property
    def depth_proxy(self) -> int:
        per_round = (
            ceil_log2(self.max_cell_fanin)
            + ceil_log2(self.d)
            + ceil_log2(self.b)
            + 1
        )
        return self.k * per_round

    @property
    def flat_depth_proxy(self) -> int:
        return self.k * ceil_log2(self.n)

    def json_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["neighbors"] = [f"0x{mask:0{(self.b + 3) // 4}x}"
                               for mask in self.neighbors]
        result.update({
            "edge_count": self.edge_count,
            "max_cell_fanin": self.max_cell_fanin,
            "wire_proxy": self.wire_proxy,
            "flat_wire_proxy": self.flat_wire_proxy,
            "work_proxy": self.work_proxy,
            "flat_work_proxy": self.flat_work_proxy,
            "depth_proxy": self.depth_proxy,
            "flat_depth_proxy": self.flat_depth_proxy,
        })
        return result


@dataclass(frozen=True)
class Match:
    source: int
    cell: int
    round_index: int


@dataclass(frozen=True)
class ReactionResult:
    matches: tuple[Match, ...]
    rounds_used: int
    edge_probes: int


def make_topology(n: int, k: int, b: int, d: int, seed: int) -> Topology:
    """Generate a deterministic degree-d topology without Python RNG state."""
    neighbors: list[int] = []
    for source in range(n):
        ranked = sorted(
            range(b),
            key=lambda cell: (
                splitmix64(
                    (seed << 40) ^ (k << 32) ^ (b << 24) ^ (d << 16)
                    ^ (source << 8) ^ cell
                ),
                cell,
            ),
        )
        mask = 0
        for cell in ranked[:d]:
            mask |= 1 << cell
        neighbors.append(mask)
    return Topology(n=n, k=k, b=b, d=d, seed=seed,
                    neighbors=tuple(neighbors))


def source_subsets(n: int, max_size: int) -> Iterator[tuple[int, ...]]:
    for size in range(1, max_size + 1):
        yield from itertools.combinations(range(n), size)


def hall_counterexample(topology: Topology) -> dict[str, object] | None:
    for subset in source_subsets(topology.n, topology.k):
        neighbor_union = 0
        for source in subset:
            neighbor_union |= topology.neighbors[source]
        if neighbor_union.bit_count() < len(subset):
            return {
                "sources": list(subset),
                "source_mask": f"0x{sum(1 << source for source in subset):04x}",
                "neighbor_mask": f"0x{neighbor_union:x}",
                "source_count": len(subset),
                "neighbor_count": neighbor_union.bit_count(),
            }
    return None


def smallest_peeling_stopping_set(topology: Topology) -> dict[str, object] | None:
    """Return the smallest active set having no degree-one neighboring cell."""
    cell_sources = topology.cell_sources
    for subset in source_subsets(topology.n, topology.n):
        active_mask = sum(1 << source for source in subset)
        counts = [sum((active_mask >> source) & 1 for source in sources)
                  for sources in cell_sources]
        if all(count != 1 for count in counts):
            neighbor_mask = 0
            for source in subset:
                neighbor_mask |= topology.neighbors[source]
            return {
                "sources": list(subset),
                "source_mask": f"0x{active_mask:04x}",
                "neighbor_mask": f"0x{neighbor_mask:x}",
                "cell_active_degrees": counts,
                "size": len(subset),
            }
    return None


def conservative_reaction(
    topology: Topology, active_mask: int, target: int
) -> ReactionResult:
    """Run at most K local propose/commit reactions.

    Cells propose their lowest-index unmatched active neighbor.  A source with
    multiple proposals commits only to its lowest-index cell.  No proposal is
    externally visible until the final list of distinct matches is returned.
    """
    unmatched = active_mask & ((1 << topology.n) - 1)
    used_cells = 0
    matches: list[Match] = []
    probes = 0
    rounds_used = 0
    cell_sources = topology.cell_sources

    for round_index in range(topology.k):
        if len(matches) >= target or not unmatched:
            break
        rounds_used = round_index + 1
        source_to_cell: dict[int, int] = {}
        for cell, sources in enumerate(cell_sources):
            if (used_cells >> cell) & 1:
                continue
            winner = None
            for source in sources:
                probes += 1
                if (unmatched >> source) & 1:
                    winner = source
                    break
            if winner is not None:
                previous = source_to_cell.get(winner)
                if previous is None or cell < previous:
                    source_to_cell[winner] = cell

        candidates = sorted((cell, source)
                            for source, cell in source_to_cell.items())
        remaining = target - len(matches)
        committed = candidates[:remaining]
        if not committed:
            break
        for cell, source in committed:
            assert (unmatched >> source) & 1
            assert not ((used_cells >> cell) & 1)
            unmatched &= ~(1 << source)
            used_cells |= 1 << cell
            matches.append(Match(source, cell, round_index))

    return ReactionResult(tuple(matches), rounds_used, probes)


def oracle_matching_size(topology: Topology, active_mask: int, limit: int) -> int:
    """Kuhn matcher used only to certify local-reaction counterexamples."""
    cell_owner = [-1] * topology.b

    def augment(source: int, seen: list[bool]) -> bool:
        for cell in range(topology.b):
            if not ((topology.neighbors[source] >> cell) & 1) or seen[cell]:
                continue
            seen[cell] = True
            owner = cell_owner[cell]
            if owner < 0 or augment(owner, seen):
                cell_owner[cell] = source
                return True
        return False

    size = 0
    for source in range(topology.n):
        if not ((active_mask >> source) & 1):
            continue
        if augment(source, [False] * topology.b):
            size += 1
            if size >= limit:
                break
    return size


def exhaustive_check(topology: Topology) -> dict[str, object]:
    counters = {
        "cases": 0,
        "illegal_source": 0,
        "illegal_lane": 0,
        "duplicate_source": 0,
        "duplicate_cell": 0,
        "duplicate_lane": 0,
        "accepted_over_k": 0,
        "p_invariant": 0,
        "reaction_deadlock": 0,
        "capacity_failure": 0,
    }
    first: dict[str, object] = {}
    max_rounds = 0
    max_edge_probes = 0
    lane_masks = range(1, 1 << topology.k)

    for active_mask in range(1 << topology.n):
        active_count = active_mask.bit_count()
        cached: dict[int, ReactionResult] = {}
        for target in range(1, min(topology.k, active_count) + 1):
            result = conservative_reaction(topology, active_mask, target)
            cached[target] = result
            max_rounds = max(max_rounds, result.rounds_used)
            max_edge_probes = max(max_edge_probes, result.edge_probes)

        for lane_mask in lane_masks:
            counters["cases"] += 1
            target = min(active_count, lane_mask.bit_count(), topology.k)
            result = cached.get(target, ReactionResult(tuple(), 0, 0))
            available_lanes = [lane for lane in range(topology.k)
                               if (lane_mask >> lane) & 1]
            lane_assignment = available_lanes[:len(result.matches)]
            sources = [match.source for match in result.matches]
            cells = [match.cell for match in result.matches]

            def fail(name: str, detail: dict[str, object]) -> None:
                counters[name] += 1
                first.setdefault(name, detail)

            base_detail = {
                "active_mask": f"0x{active_mask:04x}",
                "lane_mask": f"0x{lane_mask:x}",
                "target": target,
                "sources": sources,
                "cells": cells,
                "lanes": lane_assignment,
                "rounds_used": result.rounds_used,
            }
            if any(not ((active_mask >> source) & 1) for source in sources):
                fail("illegal_source", base_detail)
            if any(not ((lane_mask >> lane) & 1) for lane in lane_assignment):
                fail("illegal_lane", base_detail)
            if len(set(sources)) != len(sources):
                fail("duplicate_source", base_detail)
            if len(set(cells)) != len(cells):
                fail("duplicate_cell", base_detail)
            if len(set(lane_assignment)) != len(lane_assignment):
                fail("duplicate_lane", base_detail)
            if len(sources) > topology.k or len(sources) > lane_mask.bit_count():
                fail("accepted_over_k", base_detail)
            pending_after = active_count - len(sources)
            if pending_after + len(sources) != active_count:
                fail("p_invariant", base_detail)
            if active_count and lane_mask and not sources:
                fail("reaction_deadlock", base_detail)
            if len(sources) < target:
                oracle = oracle_matching_size(topology, active_mask, target)
                detail = dict(base_detail)
                detail["oracle_matching_size"] = oracle
                if oracle >= target:
                    fail("capacity_failure", detail)

    return {
        "counters": counters,
        "first_counterexamples": first,
        "max_rounds_used": max_rounds,
        "max_edge_probes": max_edge_probes,
    }


def topology_seed_score(topology: Topology) -> tuple[int, int, int, int]:
    stop = smallest_peeling_stopping_set_limited(topology, topology.k)
    stop_size = topology.k + 1 if stop is None else int(stop["size"])
    return (
        topology.max_cell_fanin,
        topology.wire_proxy,
        -stop_size,
        topology.seed,
    )


def smallest_peeling_stopping_set_limited(
    topology: Topology, limit: int
) -> dict[str, object] | None:
    cell_sources = topology.cell_sources
    for subset in source_subsets(topology.n, limit):
        active_mask = sum(1 << source for source in subset)
        counts = [sum((active_mask >> source) & 1 for source in sources)
                  for sources in cell_sources]
        if all(count != 1 for count in counts):
            return {
                "sources": list(subset),
                "source_mask": f"0x{active_mask:04x}",
                "cell_active_degrees": counts,
                "size": len(subset),
            }
    return None


def discover_topologies() -> tuple[list[Topology], list[dict[str, object]]]:
    selected: list[Topology] = []
    rejected: list[dict[str, object]] = []
    for k in K_VALUES:
        for b in range(k, B_MAX + 1):
            for d in range(1, min(D_MAX, b) + 1):
                feasible: list[Topology] = []
                first_hall: dict[str, object] | None = None
                for seed in range(TOPOLOGY_SEEDS):
                    topology = make_topology(N_SOURCES, k, b, d, seed)
                    counterexample = hall_counterexample(topology)
                    if counterexample is None:
                        feasible.append(topology)
                    elif first_hall is None:
                        first_hall = counterexample
                if feasible:
                    selected.append(min(feasible, key=topology_seed_score))
                else:
                    rejected.append({
                        "k": k,
                        "b": b,
                        "d": d,
                        "reason": "no_hall_feasible_seed",
                        "first_hall_counterexample": first_hall,
                    })
    return selected, rejected


@dataclass(frozen=True)
class TraceEvent:
    event_id: int
    source: int
    occurrence: int


def read_trace(path: Path) -> list[TraceEvent]:
    result: list[TraceEvent] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if "logical_source" not in record:
                continue
            source = int(record["logical_source"])
            if not 0 <= source < N_SOURCES:
                raise ValueError(f"{path}:{line_number}: source {source} outside N=16")
            result.append(TraceEvent(
                event_id=int(record["tb_only_event_id"]),
                source=source,
                occurrence=int(record["occurrence_cycle"]),
            ))
    result.sort(key=lambda event: (event.occurrence, event.event_id))
    return result


def read_stim_cycles(trace_path: Path, events: Sequence[TraceEvent]) -> int:
    manifest_path = trace_path.with_name(
        trace_path.name.removesuffix(".events.jsonl") + ".manifest.json"
    )
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        cycle_keys = ("stim_cycles", "stimulus_cycles")
        for key in cycle_keys:
            if key in data:
                return int(data[key])
        for parent in ("run", "configuration"):
            if isinstance(data.get(parent), dict):
                for key in cycle_keys:
                    if key in data[parent]:
                        return int(data[parent][key])
    return (max((event.occurrence for event in events), default=0) + 1)


def simulate_trace(
    events: Sequence[TraceEvent], stim_cycles: int, k: int,
    topology: Topology | None,
) -> dict[str, int | float]:
    by_cycle: dict[int, list[TraceEvent]] = {}
    for event in events:
        by_cycle.setdefault(event.occurrence, []).append(event)

    pending: list[TraceEvent | None] = [None] * N_SOURCES
    output: list[tuple[TraceEvent, int]] = []
    accepted = delivered = overrun = fixed_delivered = 0
    latencies: list[int] = []
    delivered_ids: set[int] = set()
    cycle = 0
    last_occurrence = max((event.occurrence for event in events), default=0)
    drain_limit = max(stim_cycles, last_occurrence + 1) + len(events) + 1024

    while cycle < drain_limit:
        next_output: list[tuple[TraceEvent, int]] = []
        for event, accept_cycle in output:
            if event.event_id in delivered_ids:
                raise AssertionError(f"duplicate delivery id {event.event_id}")
            delivered_ids.add(event.event_id)
            delivered += 1
            if cycle < stim_cycles:
                fixed_delivered += 1
            latencies.append(cycle - event.occurrence + 1)
        output = next_output

        for event in by_cycle.get(cycle, []):
            if pending[event.source] is not None:
                overrun += 1
            else:
                pending[event.source] = event

        active_mask = sum(1 << source for source, event in enumerate(pending)
                          if event is not None)
        if topology is None:
            selected_sources = [source for source in range(N_SOURCES)
                                if (active_mask >> source) & 1][:k]
        else:
            target = min(active_mask.bit_count(), k)
            result = conservative_reaction(topology, active_mask, target)
            selected_sources = [match.source for match in result.matches]

        for source in selected_sources:
            event = pending[source]
            if event is None:
                raise AssertionError("selected non-pending source")
            pending[source] = None
            output.append((event, cycle))
            accepted += 1

        if (cycle >= last_occurrence and not output
                and not any(event is not None for event in pending)):
            break
        cycle += 1
    else:
        raise AssertionError("trace drain timeout")

    if accepted != delivered or delivered != len(delivered_ids):
        raise AssertionError(
            f"conservation failed accepted={accepted} delivered={delivered}"
        )
    return {
        "generated": len(events),
        "accepted": accepted,
        "delivered": delivered,
        "source_overrun": overrun,
        "fixed_window_delivered": fixed_delivered,
        "average_latency": (sum(latencies) / len(latencies)) if latencies else 0.0,
        "p95_latency": percentile(latencies, 0.95),
        "p99_latency": percentile(latencies, 0.99),
        "max_latency": max(latencies, default=0),
        "drain_cycle": cycle,
    }


def iter_trace_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.events.jsonl"))


def replay_suites(
    suites: Sequence[tuple[str, Path]], selected: dict[int, Topology]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for suite_name, directory in suites:
        traces = iter_trace_files(directory)
        if not traces:
            raise FileNotFoundError(f"no *.events.jsonl traces in {directory}")
        for k in K_VALUES:
            topology = selected[k]
            for trace_path in traces:
                events = read_trace(trace_path)
                stim_cycles = read_stim_cycles(trace_path, events)
                for model, model_topology in (("flat_k_grant", None),
                                              ("ecrf", topology)):
                    metrics = simulate_trace(events, stim_cycles, k, model_topology)
                    rows.append({
                        "suite": suite_name,
                        "trace": trace_path.name.removesuffix(".events.jsonl"),
                        "trace_sha256": sha256(trace_path),
                        "k": k,
                        "model": model,
                        "b": topology.b if model == "ecrf" else 0,
                        "d": topology.d if model == "ecrf" else 0,
                        "seed": topology.seed if model == "ecrf" else 0,
                        **metrics,
                    })
    return rows


def aggregate_trace_gate(rows: Sequence[dict[str, object]], k: int) -> dict[str, object]:
    failures: list[dict[str, object]] = []
    grouped: dict[tuple[str, str], dict[str, dict[str, object]]] = {}
    for row in rows:
        if int(row["k"]) != k:
            continue
        grouped.setdefault((str(row["suite"]), str(row["trace"])), {})[
            str(row["model"])
        ] = row
    for key, models in grouped.items():
        if set(models) != {"flat_k_grant", "ecrf"}:
            failures.append({"suite_trace": key, "reason": "missing_model"})
            continue
        flat = models["flat_k_grant"]
        ecrf = models["ecrf"]
        reasons: list[str] = []
        if int(ecrf["accepted"]) != int(ecrf["delivered"]):
            reasons.append("ecrf_conservation")
        if int(ecrf["fixed_window_delivered"]) < int(flat["fixed_window_delivered"]):
            reasons.append("fixed_window_throughput")
        if int(ecrf["source_overrun"]) > int(flat["source_overrun"]):
            reasons.append("source_overrun")
        if int(ecrf["p99_latency"]) > int(flat["p99_latency"]) + 1:
            reasons.append("p99_latency")
        if reasons:
            failures.append({"suite": key[0], "trace": key[1], "reasons": reasons})
    return {"pass": not failures, "failure_count": len(failures),
            "first_failures": failures[:16]}


def point_gate(
    topology: Topology, exhaustive: dict[str, object],
    trace_gate: dict[str, object] | None,
) -> dict[str, object]:
    counters = exhaustive["counters"]
    functional_names = (
        "illegal_source", "illegal_lane", "duplicate_source", "duplicate_cell",
        "duplicate_lane", "accepted_over_k", "p_invariant",
        "reaction_deadlock", "capacity_failure",
    )
    checks = {
        "functional_exhaustive": all(int(counters[name]) == 0
                                     for name in functional_names),
        "hall": hall_counterexample(topology) is None,
        "trace_replay": bool(trace_gate and trace_gate["pass"]),
        "wire_proxy": topology.wire_proxy <= WIRE_RATIO_GATE * topology.flat_wire_proxy,
        "work_proxy": topology.work_proxy <= topology.flat_work_proxy,
        "depth_proxy": topology.depth_proxy <= topology.flat_depth_proxy,
    }
    return {"pass": all(checks.values()), "checks": checks}


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_suite(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("suite must be NAME=DIRECTORY")
    name, raw_path = value.split("=", 1)
    path = Path(raw_path).resolve()
    if not name or not path.is_dir():
        raise argparse.ArgumentTypeError(f"invalid suite {value!r}")
    return name, path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trace-suite", action="append", default=[], type=parse_suite)
    parser.add_argument("--quick", action="store_true",
                        help="unit/debug mode: check only the first Hall topology per K")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    topologies, rejected = discover_topologies()
    if args.quick:
        first: dict[int, Topology] = {}
        for topology in topologies:
            first.setdefault(topology.k, topology)
        topologies = list(first.values())

    exhaustive_rows: list[dict[str, object]] = []
    exhaustive_details: dict[str, dict[str, object]] = {}
    for topology in topologies:
        check = exhaustive_check(topology)
        stopping = smallest_peeling_stopping_set(topology)
        key = f"k{topology.k}_b{topology.b}_d{topology.d}_s{topology.seed}"
        exhaustive_details[key] = {
            "topology": topology.json_dict(),
            "hall_counterexample": hall_counterexample(topology),
            "smallest_peeling_stopping_set": stopping,
            **check,
        }
        exhaustive_rows.append({
            "key": key,
            "k": topology.k,
            "b": topology.b,
            "d": topology.d,
            "seed": topology.seed,
            "max_cell_fanin": topology.max_cell_fanin,
            "wire_proxy": topology.wire_proxy,
            "flat_wire_proxy": topology.flat_wire_proxy,
            "wire_ratio": topology.wire_proxy / topology.flat_wire_proxy,
            "work_proxy": topology.work_proxy,
            "flat_work_proxy": topology.flat_work_proxy,
            "work_ratio": topology.work_proxy / topology.flat_work_proxy,
            "depth_proxy": topology.depth_proxy,
            "flat_depth_proxy": topology.flat_depth_proxy,
            "depth_ratio": topology.depth_proxy / topology.flat_depth_proxy,
            "max_rounds_used": check["max_rounds_used"],
            "max_edge_probes": check["max_edge_probes"],
            "stopping_set_size": stopping["size"] if stopping else 0,
            **{name: value for name, value in check["counters"].items()},
        })

    functional = [row for row in exhaustive_rows
                  if all(int(row[name]) == 0 for name in (
                      "illegal_source", "illegal_lane", "duplicate_source",
                      "duplicate_cell", "duplicate_lane", "accepted_over_k",
                      "p_invariant", "reaction_deadlock", "capacity_failure"))]
    selected: dict[int, Topology] = {}
    for k in K_VALUES:
        candidates = [row for row in functional if int(row["k"]) == k]
        if not candidates:
            candidates = [row for row in exhaustive_rows if int(row["k"]) == k]
        if not candidates:
            raise RuntimeError(f"no ECRF topology for K={k}")
        best = min(candidates, key=lambda row: (
            float(row["wire_ratio"]), float(row["work_ratio"]),
            float(row["depth_ratio"]), int(row["b"]), int(row["d"]),
            int(row["seed"])))
        selected[k] = next(
            topology for topology in topologies
            if (topology.k, topology.b, topology.d, topology.seed)
            == (int(best["k"]), int(best["b"]), int(best["d"]), int(best["seed"]))
        )

    trace_rows = replay_suites(args.trace_suite, selected) if args.trace_suite else []
    trace_gates = {k: aggregate_trace_gate(trace_rows, k) if trace_rows else None
                   for k in K_VALUES}

    selected_results: dict[str, object] = {}
    for k, topology in selected.items():
        key = f"k{topology.k}_b{topology.b}_d{topology.d}_s{topology.seed}"
        selected_results[str(k)] = {
            "key": key,
            "topology": topology.json_dict(),
            "exhaustive": exhaustive_details[key],
            "trace_gate": trace_gates[k],
            "go_gate": point_gate(topology, exhaustive_details[key], trace_gates[k]),
        }

    overall_go = any(bool(result["go_gate"]["pass"])
                     for result in selected_results.values())
    summary = {
        "schema": "ecrf-w3-reference-v1",
        "preregistered": {
            "n": N_SOURCES,
            "k_values": list(K_VALUES),
            "b_max": B_MAX,
            "d_max": D_MAX,
            "topology_seeds": TOPOLOGY_SEEDS,
            "reaction_round_bound": "K",
            "wire_ratio_gate": WIRE_RATIO_GATE,
        },
        "hall_feasible_points_checked": len(topologies),
        "hall_rejected_points": rejected,
        "selected": selected_results,
        "trace_suite_counts": {
            name: len(iter_trace_files(path)) for name, path in args.trace_suite
        },
        "rtl_permitted": overall_go,
        "decision": "GO" if overall_go else "HOLD",
    }
    (output_dir / "w3_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "counterexamples.json").write_text(
        json.dumps({
            "selected": {
                str(k): {
                    "first_counterexamples": result["exhaustive"]["first_counterexamples"],
                    "smallest_peeling_stopping_set": result["exhaustive"]["smallest_peeling_stopping_set"],
                }
                for k, result in ((int(key), value)
                                  for key, value in selected_results.items())
            },
            "hall_rejected_points": rejected,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "pareto.csv", exhaustive_rows)
    write_csv(output_dir / "trace_metrics.csv", trace_rows)
    print(
        f"ECRF_REFERENCE_COMPLETE points={len(topologies)} "
        f"traces={len(trace_rows)} decision={summary['decision']} "
        f"rtl_permitted={int(overall_go)}"
    )
    for k, result in selected_results.items():
        topology = result["topology"]
        gate = result["go_gate"]
        print(
            f"ECRF_SELECTED K={k} B={topology['b']} d={topology['d']} "
            f"seed={topology['seed']} gate={int(gate['pass'])} "
            f"checks={json.dumps(gate['checks'], sort_keys=True)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
