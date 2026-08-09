#!/usr/bin/env python3
"""Analyze a provenance-bound mixed-phase, always-ready AER result.

The analyzer deliberately consumes the generated per-run manifest rather than
accepting phase boundaries on the command line.  The manifest names and hashes
the trace, and its phase provenance is bound to that same hash.  Source overrun
is reported as capacity loss; malformed identity accounting, incomplete drain,
and right-censored events fail closed.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import aggregate


WORKLOAD = "mixed_phase_always_ready"
PHASE_NAMES = (
    "u_bernoulli",
    "u_smooth",
    "s_persistent",
    "s_rotating",
    "h_a",
    "h_b",
    "h_a_return",
)
PAIR_SPECS = (
    ("uniform_temporal", "u_bernoulli", "u_smooth"),
    ("sustained_temporal", "s_persistent", "s_rotating"),
    ("spatial_b_vs_a", "h_b", "h_a"),
    ("spatial_return_vs_a", "h_a_return", "h_a"),
)


class MixedPhaseMetricError(ValueError):
    """Raised when provenance or complete event accounting cannot be proven."""


@dataclass(frozen=True)
class Phase:
    name: str
    start: int
    end: int

    @property
    def cycles(self) -> int:
        return self.end - self.start


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MixedPhaseMetricError(f"cannot read {path}: {exc}") from exc


def _read_trace(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise MixedPhaseMetricError(f"cannot read trace {path}: {exc}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise MixedPhaseMetricError("every trace row must be a JSON object")
    return rows


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MixedPhaseMetricError(f"{label} must be an integer >= {minimum}")
    return value


def _percentile(values: Iterable[int], percentile: int) -> int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def _phase_provenance(metadata: dict[str, Any], stim_cycles: int) -> list[Phase]:
    provenance = metadata.get("phase_provenance")
    if not isinstance(provenance, dict) or provenance.get("schema_version") != 1:
        raise MixedPhaseMetricError("phase_provenance schema_version must be 1")
    if provenance.get("boundary_basis") != "trace_occurrence_cycle":
        raise MixedPhaseMetricError("phase boundaries must use trace_occurrence_cycle")
    if provenance.get("trace_sha256") != metadata.get("trace_sha256"):
        raise MixedPhaseMetricError("phase provenance is not bound to the trace SHA256")
    if provenance.get("generator_version") != metadata.get("generator_version"):
        raise MixedPhaseMetricError("phase provenance generator version mismatch")
    raw_phases = provenance.get("phases")
    if not isinstance(raw_phases, list) or len(raw_phases) != len(PHASE_NAMES):
        raise MixedPhaseMetricError("phase provenance must contain the seven frozen phases")
    phases: list[Phase] = []
    cursor = 0
    for index, raw in enumerate(raw_phases):
        if not isinstance(raw, dict) or raw.get("name") != PHASE_NAMES[index]:
            raise MixedPhaseMetricError("phase names/order do not match the frozen mixed format")
        start = _integer(raw.get("start_cycle"), f"{PHASE_NAMES[index]}.start_cycle")
        end = _integer(
            raw.get("end_cycle_exclusive"),
            f"{PHASE_NAMES[index]}.end_cycle_exclusive",
            minimum=1,
        )
        if start != cursor or end <= start:
            raise MixedPhaseMetricError("phase boundaries must be positive and gap-free")
        phases.append(Phase(PHASE_NAMES[index], start, end))
        cursor = end
    if cursor != stim_cycles:
        raise MixedPhaseMetricError("phase boundaries must cover exactly stim_cycles")
    if phases[0].cycles != phases[1].cycles:
        raise MixedPhaseMetricError("uniform matched phases must have equal duration")
    if phases[2].cycles != phases[3].cycles:
        raise MixedPhaseMetricError("sustained matched phases must have equal duration")
    if len({phase.cycles for phase in phases[4:]}) != 1:
        raise MixedPhaseMetricError("H-A/B/A-return phases must have equal duration")
    return phases


def _validate_manifest(manifest_path: Path) -> tuple[dict[str, Any], Path, list[Phase], int, int, int]:
    metadata = _read_json(manifest_path)
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise MixedPhaseMetricError("generated manifest schema_version must be 1")
    run = metadata.get("run")
    if not isinstance(run, dict) or run.get("workload") != WORKLOAD:
        raise MixedPhaseMetricError(f"run manifest must describe {WORKLOAD}")
    stim_cycles = _integer(run.get("stim_cycles"), "run.stim_cycles", minimum=1)
    geometry = run.get("geometry")
    if not isinstance(geometry, dict):
        raise MixedPhaseMetricError("run.geometry must be an object")
    width = _integer(geometry.get("width"), "run.geometry.width", minimum=1)
    height = _integer(geometry.get("height"), "run.geometry.height", minimum=1)
    if run.get("sink") != {"mode": "always"}:
        raise MixedPhaseMetricError("mixed-phase analysis requires sink.mode=always")
    if metadata.get("generation_contract") != "trace_is_fully_generated_before_any_DUT_ready_is_observed":
        raise MixedPhaseMetricError("open-loop generation contract is missing")
    if metadata.get("event_identity_mode") != "address_only":
        raise MixedPhaseMetricError("event_identity_mode must be address_only")
    if metadata.get("dut_payload_fields") != ["x", "y", "polarity", "event_type"]:
        raise MixedPhaseMetricError("DUT payload fields do not match address-only AER semantics")
    if metadata.get("dut_sideband_fields") != ["logical_source"]:
        raise MixedPhaseMetricError("logical_source must be the only DUT sideband identity")
    tb_only = metadata.get("tb_only_fields")
    if not isinstance(tb_only, list) or "canonical_rank" not in tb_only:
        raise MixedPhaseMetricError("canonical_rank must be declared TB-only")
    parameters = run.get("parameters")
    if not isinstance(parameters, dict) or "fixed_polarity" not in parameters or "fixed_event_type" not in parameters:
        raise MixedPhaseMetricError("address-only run must freeze polarity and event_type")
    trace_name = metadata.get("trace_file")
    if not isinstance(trace_name, str) or not trace_name or Path(trace_name).name != trace_name:
        raise MixedPhaseMetricError("trace_file must be a local filename")
    trace_path = manifest_path.parent / trace_name
    try:
        trace_digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MixedPhaseMetricError(f"cannot read trace {trace_path}: {exc}") from exc
    if trace_digest != metadata.get("trace_sha256"):
        raise MixedPhaseMetricError("trace SHA256 does not match generated manifest")
    phases = _phase_provenance(metadata, stim_cycles)
    return metadata, trace_path, phases, stim_cycles, width, height


def _phase_for_cycle(phases: list[Phase], cycle: int) -> Phase:
    for phase in phases:
        if phase.start <= cycle < phase.end:
            return phase
    raise MixedPhaseMetricError(f"trace occurrence cycle {cycle} is outside phase provenance")


def _validate_trace(
    trace: list[dict[str, Any]], metadata: dict[str, Any], phases: list[Phase],
    stim_cycles: int, width: int, height: int,
) -> dict[str, list[dict[str, Any]]]:
    if metadata.get("event_count") != len(trace):
        raise MixedPhaseMetricError("generated manifest event_count does not match trace")
    try:
        declared_load = Decimal(str(metadata["declared_mean_load"]))
        run_load = Decimal(str(metadata["run"]["load"]))
        actual_load = Decimal(str(metadata["actual_mean_load"]))
    except (KeyError, InvalidOperation) as exc:
        raise MixedPhaseMetricError("generated manifest mean-load provenance is missing") from exc
    if declared_load != run_load:
        raise MixedPhaseMetricError("declared_mean_load does not match run.load")
    if actual_load != Decimal(len(trace)) / Decimal(stim_cycles):
        raise MixedPhaseMetricError("actual_mean_load does not match trace event count")
    parameters = metadata["run"]["parameters"]
    source_count = width * height
    grouped = {phase.name: [] for phase in phases}
    prior_cycle = -1
    source_cycles: set[tuple[int, int]] = set()
    required = {"occurrence_cycle", "tb_only_event_id", "logical_source", "x", "y", "polarity", "event_type"}
    for expected_id, row in enumerate(trace):
        if not required <= set(row):
            raise MixedPhaseMetricError(f"trace event {expected_id} is missing address-only fields")
        if row.get("tb_only_event_id") != expected_id:
            raise MixedPhaseMetricError("trace event IDs must be contiguous")
        cycle = _integer(row.get("occurrence_cycle"), f"event {expected_id} occurrence_cycle")
        source = _integer(row.get("logical_source"), f"event {expected_id} logical_source")
        x = _integer(row.get("x"), f"event {expected_id} x")
        y = _integer(row.get("y"), f"event {expected_id} y")
        if cycle >= stim_cycles or cycle < prior_cycle:
            raise MixedPhaseMetricError("trace cycles must be sorted and inside stim_cycles")
        if source >= source_count or x >= width or y >= height or source != y * width + x:
            raise MixedPhaseMetricError(f"event {expected_id} source/coordinate identity mismatch")
        if row.get("polarity") != parameters["fixed_polarity"] or row.get("event_type") != parameters["fixed_event_type"]:
            raise MixedPhaseMetricError(f"event {expected_id} violates frozen address-only fields")
        if (cycle, source) in source_cycles:
            raise MixedPhaseMetricError("trace has multiple occurrences for one source in one cycle")
        source_cycles.add((cycle, source))
        prior_cycle = cycle
        phase = _phase_for_cycle(phases, cycle)
        if phase.name in {"h_a", "h_b", "h_a_return"}:
            rank = row.get("canonical_rank")
            if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank < source_count:
                raise MixedPhaseMetricError("hotspot events require a valid TB-only canonical_rank")
        grouped[phase.name].append(row)
    return grouped


def _source_histogram(rows: Iterable[dict[str, Any]], source_count: int) -> list[int]:
    histogram = [0] * source_count
    for row in rows:
        histogram[int(row["logical_source"])] += 1
    return histogram


def _fan_in_histogram(rows: Iterable[dict[str, Any]], phase: Phase) -> dict[int, int]:
    per_cycle = collections.Counter(int(row["occurrence_cycle"]) - phase.start for row in rows)
    return dict(collections.Counter(per_cycle.get(cycle, 0) for cycle in range(phase.cycles)))


def _relative_stream(rows: Iterable[dict[str, Any]], phase: Phase, field: str) -> list[tuple[int, int]]:
    return sorted((int(row["occurrence_cycle"]) - phase.start, int(row[field])) for row in rows)


def _validate_matched_trace(
    grouped: dict[str, list[dict[str, Any]]], phases: list[Phase], source_count: int,
) -> dict[str, Any]:
    by_name = {phase.name: phase for phase in phases}
    u_left, u_right = grouped["u_bernoulli"], grouped["u_smooth"]
    if len(u_left) != len(u_right) or _source_histogram(u_left, source_count) != _source_histogram(u_right, source_count):
        raise MixedPhaseMetricError("uniform pair does not match event count/source histogram")
    s_left, s_right = grouped["s_persistent"], grouped["s_rotating"]
    if (len(s_left) != len(s_right)
            or _source_histogram(s_left, source_count) != _source_histogram(s_right, source_count)
            or _fan_in_histogram(s_left, by_name["s_persistent"]) != _fan_in_histogram(s_right, by_name["s_rotating"])):
        raise MixedPhaseMetricError("sustained pair does not match count/source/fan-in histograms")
    hot_streams = {
        name: _relative_stream(grouped[name], by_name[name], "canonical_rank")
        for name in ("h_a", "h_b", "h_a_return")
    }
    if len({tuple(stream) for stream in hot_streams.values()}) != 1:
        raise MixedPhaseMetricError("H-A/B/A-return canonical rank streams do not match")
    if _relative_stream(grouped["h_a"], by_name["h_a"], "logical_source") != _relative_stream(
        grouped["h_a_return"], by_name["h_a_return"], "logical_source"
    ):
        raise MixedPhaseMetricError("H-A-return does not exactly replay H-A physical identity")
    return {
        "status": "pass",
        "uniform_exact_event_count_and_source_histogram": True,
        "sustained_exact_event_source_and_fan_in_histograms": True,
        "hotspot_exact_canonical_rank_stream": True,
        "hotspot_a_return_exact_physical_replay": True,
    }


def _validate_events(
    events: list[aggregate.Event], trace: list[dict[str, Any]], metadata: dict[str, Any],
    stim_cycles: int, source_count: int,
) -> tuple[dict[int, aggregate.Event], int, int]:
    if len(events) != len(trace):
        raise MixedPhaseMetricError("event CSV row count does not match generated trace")
    by_id = {event.tb_only_event_id: event for event in events}
    if len(by_id) != len(events) or set(by_id) != set(range(len(trace))):
        raise MixedPhaseMetricError("event result IDs do not match generated trace")
    run_keys = {event.run_key for event in events}
    if len(run_keys) != 1:
        raise MixedPhaseMetricError("event CSV must contain exactly one run")
    run = metadata["run"]
    expected_test = metadata.get("report_group", run.get("name"))
    first = events[0] if events else None
    if first is not None and (first.test != expected_test or first.seed != str(run.get("seed"))):
        raise MixedPhaseMetricError("event CSV test/seed provenance does not match run manifest")
    try:
        expected_load_pct = float(Decimal(str(run["load"])) * 100)
    except (KeyError, InvalidOperation) as exc:
        raise MixedPhaseMetricError("run.load is not valid decimal provenance") from exc
    if first is not None and not math.isclose(first.load_pct, expected_load_pct, abs_tol=1e-9):
        raise MixedPhaseMetricError("event CSV load_pct does not match run manifest")
    offsets: set[int] = set()
    observation_ends: set[int] = set()
    censored = 0
    for event_id, row in enumerate(trace):
        event = by_id[event_id]
        if event.logical_source != row["logical_source"] or event.source_count != source_count:
            raise MixedPhaseMetricError(f"event {event_id} CSV source identity mismatch")
        offsets.add(event.occurrence_cycle - int(row["occurrence_cycle"]))
        observation_ends.add(event.observation_end_cycle)
        expected_deadline = row.get("deadline")
        if expected_deadline is None:
            if event.deadline_cycle is not None:
                raise MixedPhaseMetricError(f"event {event_id} has an undeclared deadline")
        elif event.deadline_cycle is None:
            raise MixedPhaseMetricError(f"event {event_id} is missing its trace deadline")
        if event.event_state in {"pending", "accepted"}:
            censored += 1
    if len(offsets) != 1:
        raise MixedPhaseMetricError("TB occurrence offset is not constant")
    if len(observation_ends) != 1:
        raise MixedPhaseMetricError("observation_end_cycle must be constant")
    offset = offsets.pop()
    observation_end = observation_ends.pop()
    for event_id, row in enumerate(trace):
        event = by_id[event_id]
        if row.get("deadline") is not None and event.deadline_cycle != int(row["deadline"]) + offset:
            raise MixedPhaseMetricError(f"event {event_id} deadline offset mismatch")
    if censored:
        raise MixedPhaseMetricError(f"event data are right-censored ({censored} pending/accepted events)")
    per_source: dict[int, list[aggregate.Event]] = collections.defaultdict(list)
    for event_id in range(len(trace)):
        event = by_id[event_id]
        if event.event_state != "source_overrun":
            per_source[event.logical_source].append(event)
    for source, source_events in per_source.items():
        accept_cycles = [event.accept_cycle for event in source_events]
        delivery_cycles = [event.delivery_cycle for event in source_events]
        if accept_cycles != sorted(accept_cycles) or delivery_cycles != sorted(delivery_cycles):
            raise MixedPhaseMetricError(f"source {source} result violates source-local order")
    if offset < 0 or observation_end < offset + stim_cycles - 1:
        raise MixedPhaseMetricError("observation window does not cover the complete stimulus")
    return by_id, offset, observation_end


def _service_metrics(events: list[aggregate.Event], active_sources: set[int]) -> dict[str, Any]:
    deliveries: dict[int, list[int]] = collections.defaultdict(list)
    for event in events:
        if event.delivery_cycle is not None:
            deliveries[event.logical_source].append(event.delivery_cycle)
    gaps: list[int] = []
    for source_deliveries in deliveries.values():
        ordered = sorted(source_deliveries)
        gaps.extend(right - left for left, right in zip(ordered, ordered[1:]))
    return {
        "active_sources": len(active_sources),
        "delivered_sources": len(deliveries),
        "unobserved_active_sources": len(active_sources - set(deliveries)),
        "samples": len(gaps),
        "p95_cycles": _percentile(gaps, 95),
        "p99_cycles": _percentile(gaps, 99),
        "max_cycles": max(gaps) if gaps else None,
    }


def _phase_metrics(
    phases: list[Phase], grouped_trace: dict[str, list[dict[str, Any]]],
    by_id: dict[int, aggregate.Event], offset: int, observation_end: int,
) -> list[dict[str, Any]]:
    all_events = list(by_id.values())
    max_normalized = max(observation_end - offset, phases[-1].end - 1)
    backlog_delta = [0] * (max_normalized + 2)
    for row in (row for rows in grouped_trace.values() for row in rows):
        event = by_id[int(row["tb_only_event_id"])]
        if event.event_state == "source_overrun":
            continue
        occurrence = int(row["occurrence_cycle"])
        delivery = event.delivery_cycle
        if delivery is None:  # guarded by fail-closed validation
            raise MixedPhaseMetricError("retained event has no delivery cycle")
        backlog_delta[occurrence] += 1
        backlog_delta[delivery - offset] -= 1
    backlog = 0
    backlog_by_cycle: list[int] = []
    for delta in backlog_delta:
        backlog += delta
        if backlog < 0:
            raise MixedPhaseMetricError("delivery accounting makes backlog negative")
        backlog_by_cycle.append(backlog)

    result: list[dict[str, Any]] = []
    for phase in phases:
        rows = grouped_trace[phase.name]
        events = [by_id[int(row["tb_only_event_id"])] for row in rows]
        overrun = sum(event.event_state == "source_overrun" for event in events)
        accepted = sum(event.accept_cycle is not None for event in events)
        delivered = [event for event in events if event.delivery_cycle is not None]
        latencies = [event.delivery_cycle - event.occurrence_cycle for event in delivered]
        delivered_in_window = sum(
            event.delivery_cycle is not None
            and phase.start <= event.delivery_cycle - offset < phase.end
            for event in all_events
        )
        last_delivery = max((event.delivery_cycle - offset for event in delivered), default=phase.end - 1)
        recovery = max(0, last_delivery - phase.end + 1)
        result.append({
            "phase": phase.name,
            "start_cycle": phase.start,
            "end_cycle_exclusive": phase.end,
            "cycles": phase.cycles,
            "generated": len(events),
            "source_overrun": overrun,
            "accepted": accepted,
            "delivered": len(delivered),
            "offered_events_per_cycle": len(events) / phase.cycles,
            "accepted_events_per_cycle": accepted / phase.cycles,
            "delivered_by_occurrence_events_per_cycle": len(delivered) / phase.cycles,
            "delivered_in_window": delivered_in_window,
            "retire_throughput_events_per_cycle": delivered_in_window / phase.cycles,
            "capacity_loss_ratio": overrun / len(events) if events else 0.0,
            "latency_cycles": {
                "samples": len(latencies),
                "mean": statistics.fmean(latencies) if latencies else None,
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "p99": _percentile(latencies, 99),
                "max": max(latencies) if latencies else None,
            },
            "service_gap_cycles": _service_metrics(
                events, {int(row["logical_source"]) for row in rows}
            ),
            "backlog_at_start": backlog_by_cycle[phase.start - 1] if phase.start else 0,
            "backlog_peak": max(backlog_by_cycle[phase.start:phase.end], default=0),
            "backlog_at_end": backlog_by_cycle[phase.end - 1],
            "recovery_to_zero_after_phase_cycles": recovery,
            "recovered_before_next_phase": recovery == 0,
        })
    if backlog_by_cycle[-1] != 0:
        raise MixedPhaseMetricError("complete event data do not recover backlog to zero")
    return result


def _subtract(left: Any, right: Any) -> float | int | None:
    if left is None or right is None:
        return None
    return left - right


def _matched_deltas(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_phase = {row["phase"]: row for row in rows}
    result = []
    for name, left_name, right_name in PAIR_SPECS:
        left, right = by_phase[left_name], by_phase[right_name]
        result.append({
            "pair": name,
            "left_phase": left_name,
            "right_phase": right_name,
            "sign_convention": "left_minus_right",
            "generated_delta": left["generated"] - right["generated"],
            "capacity_loss_events_delta": left["source_overrun"] - right["source_overrun"],
            "capacity_loss_ratio_delta": left["capacity_loss_ratio"] - right["capacity_loss_ratio"],
            "retire_throughput_delta": left["retire_throughput_events_per_cycle"] - right["retire_throughput_events_per_cycle"],
            "p95_latency_cycles_delta": _subtract(left["latency_cycles"]["p95"], right["latency_cycles"]["p95"]),
            "p99_latency_cycles_delta": _subtract(left["latency_cycles"]["p99"], right["latency_cycles"]["p99"]),
            "max_service_gap_cycles_delta": _subtract(left["service_gap_cycles"]["max_cycles"], right["service_gap_cycles"]["max_cycles"]),
            "backlog_peak_delta": left["backlog_peak"] - right["backlog_peak"],
            "recovery_cycles_delta": left["recovery_to_zero_after_phase_cycles"] - right["recovery_to_zero_after_phase_cycles"],
        })
    return result


def analyze(manifest_path: Path, event_path: Path) -> dict[str, Any]:
    metadata, trace_path, phases, stim_cycles, width, height = _validate_manifest(manifest_path)
    trace = _read_trace(trace_path)
    grouped = _validate_trace(trace, metadata, phases, stim_cycles, width, height)
    matching = _validate_matched_trace(grouped, phases, width * height)
    events = aggregate.read_events([event_path])
    by_id, offset, observation_end = _validate_events(
        events, trace, metadata, stim_cycles, width * height
    )
    phase_rows = _phase_metrics(phases, grouped, by_id, offset, observation_end)
    total_overrun = sum(row["source_overrun"] for row in phase_rows)
    first = events[0] if events else None
    return {
        "schema_version": 1,
        "candidate": first.candidate if first else "unspecified",
        "test": first.test if first else metadata.get("report_group", metadata["run"]["name"]),
        "seed": first.seed if first else str(metadata["run"].get("seed", "")),
        "trace_sha256": metadata["trace_sha256"],
        "event_identity_mode": "address_only",
        "sink_mode": "always",
        "tb_cycle_offset": offset,
        "observation_end_cycle": observation_end,
        "provenance_validation": {
            "status": "pass",
            "trace_sha256": True,
            "phase_boundaries": True,
            "address_only_identity": True,
            "source_local_order": True,
            "complete_uncensored_event_accounting": True,
        },
        "matched_trace_validation": matching,
        "classification": {
            "analysis_status": "capacity_loss" if total_overrun else "pass",
            "correctness_status": "pass",
            "correctness_scope": "generated-event identity/accounting; source_overrun is excluded",
            "capacity_status": "loss_observed" if total_overrun else "lossless",
            "capacity_loss_events": total_overrun,
            "capacity_loss_ratio": total_overrun / len(trace) if trace else 0.0,
            "censored_events": 0,
        },
        "phases": phase_rows,
        "matched_pair_deltas": _matched_deltas(phase_rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.run_manifest, args.events)
    except (MixedPhaseMetricError, aggregate.InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
