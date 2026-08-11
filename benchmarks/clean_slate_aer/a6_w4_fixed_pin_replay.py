#!/usr/bin/env python3
"""A6 W4 fixed-pin/full-endpoint replay of the A7 2-bit DDR link."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


class ReplayError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    occurrence_cycle: int
    sequence: int
    address: int


@dataclass(frozen=True)
class LinkSpec:
    name: str
    data_pins: int
    framing_pins: int
    periods_per_event: int
    forwarded_edges_per_event: int
    fixed_rtl_state_bits: int
    fixed_state_breakdown: dict[str, int]
    internal_clock_edges_per_period: int
    icg_input_edges_per_period: int
    missing_physical_primitives: dict[str, int]
    data_idle_behavior: str

    @property
    def pins(self) -> int:
        return self.data_pins + self.framing_pins


LINKS = {
    "parallel4": LinkSpec(
        "parallel4", 4, 1, 1, 2, 10,
        {"tx_address": 4, "tx_strobe_enable": 1,
         "rx_address": 4, "rx_retire_toggle": 1},
        2, 2,
        {"characterized_icg_or_glitch_free_strobe_gate": 1,
         "forwarded_strobe_output_buffer": 1},
        "hold last four-bit address",
    ),
    "ddr2": LinkSpec(
        "ddr2", 2, 1, 1, 2, 12,
        {"tx_event_addr_q": 4, "tx_frame_enable_q": 1,
         "rx_low_symbol_q": 2, "rx_retire_addr_o": 4,
         "rx_retire_toggle_o": 1},
        4, 2,
        {"characterized_icg": 1, "oddr_data_cells": 2,
         "iddr_data_cells": 2, "forwarded_clock_output_buffer": 1},
        "A7 RTL alternates retained low/high symbols every ref-clock period",
    ),
    "serial1": LinkSpec(
        "serial1", 1, 1, 2, 4, 16,
        {"tx_shift_address": 4, "tx_frame_enable": 1, "tx_symbol_phase": 1,
         "rx_partial_bits": 3, "rx_edge_count": 2, "rx_address": 4,
         "rx_retire_toggle": 1},
        4, 2,
        {"characterized_icg": 1, "oddr_data_cells": 1,
         "iddr_data_cells": 1, "forwarded_clock_output_buffer": 1},
        "hold last serialized bit",
    ),
}


@dataclass(frozen=True)
class ReplayResult:
    suite: str
    run: str
    link: str
    link_ratio: int
    stim_cycles: int
    generated: int
    delivered: int
    delivered_in_stimulus: int
    elapsed_core_cycles: float
    link_periods: int
    max_pending_before_launch: int
    required_fifo_depth: int
    fifo_payload_bits: int
    fifo_pointer_control_bits: int
    modeled_storage_control_state_bits_lower_bound: int
    physical_data_toggles: int
    forwarded_framing_edges: int
    physical_link_toggles: int
    internal_clock_source_edges: int
    icg_input_edges: int
    mean_latency_core_cycles: float
    p95_latency_core_cycles: float
    max_latency_core_cycles: float
    no_cross_core_backlog_schedule_compatible: bool
    sequence_exact: bool

    def public(self, spec: LinkSpec) -> dict[str, object]:
        row = asdict(self)
        row.update({
            "pins": spec.pins,
            "data_pins": spec.data_pins,
            "framing_pins": spec.framing_pins,
            "forwarded_edges_per_event": self.forwarded_framing_edges / self.delivered,
            "physical_data_toggles_per_event": self.physical_data_toggles / self.delivered,
            "physical_link_toggles_per_event": self.physical_link_toggles / self.delivered,
            "internal_clock_source_edges_per_event": self.internal_clock_source_edges / self.delivered,
            "icg_input_edges_per_event": self.icg_input_edges / self.delivered,
            "events_per_pin_cycle": self.delivered / (spec.pins * self.link_periods),
            "events_per_core_cycle_during_stimulus": (
                self.delivered_in_stimulus / self.stim_cycles),
            "drained_events_per_core_cycle": self.delivered / self.elapsed_core_cycles,
            "max_logical_events_per_core_cycle_proxy": (
                self.link_ratio / spec.periods_per_event),
        })
        return row


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nearest_rank(values: Sequence[float], percentile: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(1, math.ceil(len(ordered) * percentile / 100)) - 1
    return ordered[index]


def fifo_state_bits(depth: int) -> tuple[int, int]:
    if depth <= 0:
        return 0, 0
    pointer_width = max(1, math.ceil(math.log2(depth)))
    count_width = max(1, math.ceil(math.log2(depth + 1)))
    return 4 * depth, 2 * pointer_width + count_width


def load_events(path: Path, expected_sha256: str) -> list[Event]:
    if sha256_file(path) != expected_sha256:
        raise ReplayError(f"trace SHA mismatch: {path}")
    events = []
    identities = set()
    previous_key = (-1, -1)
    with path.open(encoding="utf-8") as stream:
        for sequence, line in enumerate(stream):
            raw = json.loads(line)
            identity = raw["tb_only_event_id"]
            event = Event(raw["occurrence_cycle"], sequence, raw["logical_source"])
            if identity in identities:
                raise ReplayError(f"duplicate event identity: {path}")
            if not 0 <= event.address < 16:
                raise ReplayError(f"non-N16 address: {path}")
            key = (event.occurrence_cycle, event.sequence)
            if key < previous_key:
                raise ReplayError(f"noncanonical event order: {path}")
            identities.add(identity)
            previous_key = key
            events.append(event)
    return events


def _toggle_width(previous: int, current: int, width: int) -> int:
    return ((previous ^ current) & ((1 << width) - 1)).bit_count()


def replay(
    events: Sequence[Event], *, suite: str, run: str, stim_cycles: int,
    spec: LinkSpec, link_ratio: int,
) -> ReplayResult:
    if link_ratio <= 0:
        raise ValueError("link_ratio must be positive")
    arrivals: dict[int, list[Event]] = defaultdict(list)
    for event in events:
        if not 0 <= event.occurrence_cycle < stim_cycles:
            raise ReplayError("event lies outside stimulus")
        arrivals[event.occurrence_cycle].append(event)

    queue: list[Event] = []
    active: Event | None = None
    active_phase = 0
    delivered_events: list[Event] = []
    latencies: list[float] = []
    data_state = 0
    retained_address = 0
    data_toggles = framing_edges = 0
    internal_clock_edges = icg_edges = 0
    max_pending = max_fifo = 0
    delivered_in_stimulus = 0
    total_stim_periods = stim_cycles * link_ratio
    period = 0
    native_compatible = True
    native_period_debt = 0

    while period < total_stim_periods or queue or active is not None:
        core_cycle = period // link_ratio
        if period % link_ratio == 0 and core_cycle < stim_cycles:
            incoming = arrivals.get(core_cycle, [])
            queue.extend(incoming)
            max_pending = max(max_pending, len(queue) + int(active is not None))
            # Native A7 has no queue. This debt test is the minimum serializer
            # schedule needed to consume the same-cycle arrivals losslessly.
            native_period_debt += len(incoming) * spec.periods_per_event
        if period % link_ratio == 0:
            native_period_debt = max(0, native_period_debt - link_ratio)
            if native_period_debt > 0:
                native_compatible = False

        if active is None and queue:
            active = queue.pop(0)
            active_phase = 0
        max_fifo = max(max_fifo, len(queue))

        internal_clock_edges += spec.internal_clock_edges_per_period
        icg_edges += spec.icg_input_edges_per_period

        if spec.name == "parallel4":
            if active is not None:
                data_toggles += _toggle_width(data_state, active.address, 4)
                data_state = active.address
                framing_edges += 2
        elif spec.name == "ddr2":
            if active is not None:
                retained_address = active.address
            low = retained_address & 0x3
            high = (retained_address >> 2) & 0x3
            data_toggles += _toggle_width(data_state, low, 2)
            data_toggles += _toggle_width(low, high, 2)
            data_state = high
            if active is not None:
                framing_edges += 2
        elif spec.name == "serial1":
            if active is not None:
                first = (active.address >> (2 * active_phase)) & 1
                second = (active.address >> (2 * active_phase + 1)) & 1
                data_toggles += data_state != first
                data_toggles += first != second
                data_state = second
                framing_edges += 2
        else:  # pragma: no cover
            raise AssertionError("unknown link")

        if active is not None:
            active_phase += 1
            if active_phase == spec.periods_per_event:
                delivered_events.append(active)
                completion = (period + 1) / link_ratio
                latencies.append(completion - active.occurrence_cycle)
                if period < total_stim_periods:
                    delivered_in_stimulus += 1
                active = None
                active_phase = 0
        period += 1

    if [event.sequence for event in delivered_events] != [event.sequence for event in events]:
        raise AssertionError("link replay changed the exact event sequence")
    payload_bits, pointer_bits = fifo_state_bits(max_fifo)
    return ReplayResult(
        suite=suite, run=run, link=spec.name, link_ratio=link_ratio,
        stim_cycles=stim_cycles, generated=len(events), delivered=len(delivered_events),
        delivered_in_stimulus=delivered_in_stimulus,
        elapsed_core_cycles=period / link_ratio, link_periods=period,
        max_pending_before_launch=max_pending, required_fifo_depth=max_fifo,
        fifo_payload_bits=payload_bits, fifo_pointer_control_bits=pointer_bits,
        modeled_storage_control_state_bits_lower_bound=(
            spec.fixed_rtl_state_bits + payload_bits + pointer_bits),
        physical_data_toggles=data_toggles, forwarded_framing_edges=framing_edges,
        physical_link_toggles=data_toggles + framing_edges,
        internal_clock_source_edges=internal_clock_edges, icg_input_edges=icg_edges,
        mean_latency_core_cycles=statistics.fmean(latencies) if latencies else 0.0,
        p95_latency_core_cycles=_nearest_rank(latencies, 95),
        max_latency_core_cycles=max(latencies, default=0.0),
        no_cross_core_backlog_schedule_compatible=native_compatible,
        sequence_exact=True,
    )


def validate_inputs(
    registry_path: Path, generator_path: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    bound_a7_commit = "31947a71ddfcf678f6cd593954df34b27806a63d"
    if (registry.get("schema_version") != 1
            or registry.get("a7_commit") != bound_a7_commit):
        raise ReplayError("invalid W4 registry")
    if sha256_file(generator_path) != registry["generator"]["sha256"]:
        raise ReplayError("generator SHA mismatch")
    version_line = next(
        (line for line in generator_path.read_text(encoding="utf-8").splitlines()
         if line.startswith("GENERATOR_VERSION")), "")
    if f'"{registry["generator"]["version"]}"' not in version_line:
        raise ReplayError("generator version mismatch")
    for relative, expected in registry["a7_sources"].items():
        content = subprocess.check_output(
            ["git", "-C", str(a7_repo), "show",
             f"{bound_a7_commit}:{relative}"])
        if hashlib.sha256(content).hexdigest() != expected:
            raise ReplayError(f"A7 bound source mismatch: {relative}")

    manifests: dict[str, object] = {}
    runs_by_suite: dict[str, list[dict[str, object]]] = {}
    for suite, (manifest_path, trace_dir) in suite_inputs.items():
        contract = registry["suites"][suite]
        if sha256_file(manifest_path) != contract["manifest_sha256"]:
            raise ReplayError(f"{suite}: manifest SHA mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        names = [run["name"] for run in manifest["runs"]]
        if names != contract["run_names"] or set(names) != set(contract["traces"]):
            raise ReplayError(f"{suite}: run set mismatch")
        for name in names:
            trace = trace_dir / f"{name}.events.jsonl"
            if not trace.is_file() or sha256_file(trace) != contract["traces"][name]["sha256"]:
                raise ReplayError(f"{suite}/{name}: trace SHA mismatch")
        manifests[suite] = manifest
        runs_by_suite[suite] = manifest["runs"]
    return registry, runs_by_suite


def aggregate(rows: Iterable[dict[str, object]], specs: dict[str, LinkSpec]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, int], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(row["suite"], row["link"], row["link_ratio"])].append(row)
    output = []
    for (suite, link, ratio), group in sorted(grouped.items()):
        spec = specs[link]
        events = sum(row["delivered"] for row in group)
        periods = sum(row["link_periods"] for row in group)
        stim_cycles = sum(row["stim_cycles"] for row in group)
        in_stim = sum(row["delivered_in_stimulus"] for row in group)
        elapsed = sum(row["elapsed_core_cycles"] for row in group)
        data_toggles = sum(row["physical_data_toggles"] for row in group)
        framing_edges = sum(row["forwarded_framing_edges"] for row in group)
        internal_edges = sum(row["internal_clock_source_edges"] for row in group)
        icg_edges = sum(row["icg_input_edges"] for row in group)
        fifo_depth = max(row["required_fifo_depth"] for row in group)
        payload_bits, pointer_bits = fifo_state_bits(fifo_depth)
        output.append({
            "suite": suite, "link": link, "link_ratio": ratio,
            "runs": len(group), "events": events, "pins": spec.pins,
            "data_pins": spec.data_pins, "framing_pins": spec.framing_pins,
            "forwarded_edges_per_event": framing_edges / events,
            "physical_data_toggles_per_event": data_toggles / events,
            "physical_link_toggles_per_event": (data_toggles + framing_edges) / events,
            "events_per_pin_cycle": events / (spec.pins * periods),
            "events_per_core_cycle_during_stimulus": in_stim / stim_cycles,
            "drained_events_per_core_cycle": events / elapsed,
            "event_weighted_mean_latency_core_cycles": sum(
                row["mean_latency_core_cycles"] * row["delivered"] for row in group
            ) / events,
            "worst_run_p95_latency_core_cycles": max(
                row["p95_latency_core_cycles"] for row in group),
            "worst_event_latency_core_cycles": max(
                row["max_latency_core_cycles"] for row in group),
            "max_logical_events_per_core_cycle_proxy": ratio / spec.periods_per_event,
            "suite_required_fifo_depth": fifo_depth,
            "fifo_payload_bits": payload_bits,
            "fifo_pointer_control_bits": pointer_bits,
            "modeled_storage_control_state_bits_lower_bound": (
                spec.fixed_rtl_state_bits + payload_bits + pointer_bits),
            "fixed_link_state_bits": spec.fixed_rtl_state_bits,
            "internal_clock_source_edges_per_event": internal_edges / events,
            "icg_input_edges_per_event": icg_edges / events,
            "no_cross_core_backlog_schedule_compatible_runs": sum(
                bool(row["no_cross_core_backlog_schedule_compatible"])
                for row in group),
            "sequence_exact_runs": sum(bool(row["sequence_exact"]) for row in group),
            "missing_physical_primitives": spec.missing_physical_primitives,
            "collector_sorter_control_cell_cost": "unknown_not_free",
            "clock_tree_icg_ddr_cell_power_area_timing": "unknown_not_free",
        })
    return output


def evaluate(
    registry_path: Path, generator_path: Path, a7_repo: Path,
    suite_inputs: dict[str, tuple[Path, Path]],
) -> dict[str, object]:
    registry, runs_by_suite = validate_inputs(
        registry_path, generator_path, a7_repo, suite_inputs)
    rows = []
    for suite, runs in runs_by_suite.items():
        trace_dir = suite_inputs[suite][1]
        for run in runs:
            events = load_events(
                trace_dir / f"{run['name']}.events.jsonl",
                registry["suites"][suite]["traces"][run["name"]]["sha256"],
            )
            for ratio in (1, 2, 4):
                for spec in LINKS.values():
                    result = replay(
                        events, suite=suite, run=run["name"],
                        stim_cycles=run["stim_cycles"], spec=spec,
                        link_ratio=ratio,
                    )
                    rows.append(result.public(spec))
    return {
        "schema_version": 1,
        "candidate": "a6_w4_a7_event_triggered_ddr_fixed_pin_audit",
        "a7_bound_commit": registry["a7_commit"],
        "registry": str(registry_path.resolve()),
        "registry_sha256": sha256_file(registry_path),
        "clock_ratio_contract": {
            "R": "link reference periods per core cycle",
            "parallel4_periods_per_event": 1,
            "ddr2_periods_per_event": 1,
            "serial1_periods_per_event": 2,
            "ratios": [1, 2, 4],
        },
        "link_specs": {name: asdict(spec) | {"pins": spec.pins}
                       for name, spec in LINKS.items()},
        "suite_summary": aggregate(rows, LINKS),
        "runs": rows,
        "decision": "HOLD_PHYSICAL_AND_FULL_ENDPOINT_PPA",
        "decision_reasons": [
            "A7 native RTL has no ingress queue; same-cycle multiplicity needs external collection, and both run sets contain traces that exceed even a no-cross-core-backlog schedule at R=4",
            "lossless replay charges required FIFO state but collector/sorter/control cell cost is unknown",
            "clock tree, characterized ICG, forwarded clock buffer, and ODDR/IDDR costs are not implemented",
            "DDR2 data pins toggle during idle under the bound A7 RTL",
        ],
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--generator", type=Path, required=True)
    parser.add_argument("--a7-repo", type=Path, required=True)
    parser.add_argument("--full-manifest", type=Path, required=True)
    parser.add_argument("--full-trace-dir", type=Path, required=True)
    parser.add_argument("--cap-manifest", type=Path, required=True)
    parser.add_argument("--cap-trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(
        args.registry, args.generator, args.a7_repo,
        {"full50": (args.full_manifest, args.full_trace_dir),
         "capacity22": (args.cap_manifest, args.cap_trace_dir)},
    )
    write_json(args.output, report)
    print(f"A6_W4_REPORT output={args.output} decision={report['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
