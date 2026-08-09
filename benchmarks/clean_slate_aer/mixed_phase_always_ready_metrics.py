#!/usr/bin/env python3
"""Analyze a provenance-bound mixed-phase, always-ready AER result.

The analyzer deliberately consumes the generated per-run manifest rather than
accepting phase boundaries on the command line.  The workload itself freezes
the 4x4/4096 phase schedule, while the manifest names and hashes the trace.
Source overrun is reported as capacity loss; malformed identity accounting,
incomplete drain, and right-censored events fail closed.
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
GENERATOR_VERSION = "4.0"
OFFICIAL_SEED = 4001
OFFICIAL_UNIFORM_PROBABILITY = Decimal("0.125")
OFFICIAL_HOT_PROBABILITY = Decimal("0.8")
PHASE_NAMES = (
    "u_bernoulli",
    "u_smooth",
    "s_persistent",
    "s_rotating",
    "h_a",
    "h_b",
    "h_a_replay",
)
FROZEN_PHASE_BOUNDS = (
    ("u_bernoulli", 0, 640),
    ("u_smooth", 640, 1280),
    ("s_persistent", 1280, 1536),
    ("s_rotating", 1536, 1792),
    ("h_a", 1792, 2560),
    ("h_b", 2560, 3328),
    ("h_a_replay", 3328, 4096),
)
PAIR_SPECS = (
    ("uniform_temporal", "u_bernoulli", "u_smooth"),
    ("sustained_temporal", "s_persistent", "s_rotating"),
    ("spatial_b_vs_a", "h_b", "h_a"),
    ("spatial_replay_vs_a", "h_a_replay", "h_a"),
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


def _frozen_phases() -> list[Phase]:
    return [Phase(name, start, end) for name, start, end in FROZEN_PHASE_BOUNDS]


def _validate_manifest(manifest_path: Path) -> tuple[dict[str, Any], Path, list[Phase], int, int, int]:
    metadata = _read_json(manifest_path)
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise MixedPhaseMetricError("generated manifest schema_version must be 1")
    if metadata.get("generator_version") != GENERATOR_VERSION:
        raise MixedPhaseMetricError(
            f"generator_version must be frozen {GENERATOR_VERSION}"
        )
    run = metadata.get("run")
    if not isinstance(run, dict) or run.get("workload") != WORKLOAD:
        raise MixedPhaseMetricError(f"run manifest must describe {WORKLOAD}")
    if run.get("seed") != OFFICIAL_SEED:
        raise MixedPhaseMetricError(f"mixed phase seed must be {OFFICIAL_SEED}")
    stim_cycles = _integer(run.get("stim_cycles"), "run.stim_cycles", minimum=1)
    geometry = run.get("geometry")
    if not isinstance(geometry, dict):
        raise MixedPhaseMetricError("run.geometry must be an object")
    width = _integer(geometry.get("width"), "run.geometry.width", minimum=1)
    height = _integer(geometry.get("height"), "run.geometry.height", minimum=1)
    if (width, height, stim_cycles) != (4, 4, 4096):
        raise MixedPhaseMetricError("mixed phase requires frozen 4x4 geometry and 4096 cycles")
    if run.get("sink") != {"mode": "always"}:
        raise MixedPhaseMetricError("mixed-phase analysis requires sink.mode=always")
    if metadata.get("generation_contract") != "trace_is_fully_generated_before_any_DUT_ready_is_observed":
        raise MixedPhaseMetricError("open-loop generation contract is missing")
    if metadata.get("event_identity_mode") != "address_only":
        raise MixedPhaseMetricError("event_identity_mode must be address_only")
    if metadata.get("dut_address_fields") != ["logical_source"]:
        raise MixedPhaseMetricError("logical_source must be the address-only DUT field")
    if metadata.get("dut_payload_fields") != []:
        raise MixedPhaseMetricError("address-only AER must not declare DUT payload fields")
    if metadata.get("dut_sideband_fields") != ["logical_source"]:
        raise MixedPhaseMetricError("logical_source must be the only DUT sideband identity")
    trace_metadata = metadata.get("trace_metadata_fields")
    if trace_metadata != ["x", "y", "polarity", "event_type"]:
        raise MixedPhaseMetricError("coordinate annotations must remain trace metadata")
    parameters = run.get("parameters")
    if not isinstance(parameters, dict) or "fixed_polarity" not in parameters or "fixed_event_type" not in parameters:
        raise MixedPhaseMetricError("address-only run must freeze polarity and event_type")
    try:
        uniform_probability = Decimal(str(parameters["uniform_source_probability"]))
        hot_probability = Decimal(str(parameters["hot_probability"]))
    except (KeyError, InvalidOperation) as exc:
        raise MixedPhaseMetricError("mixed phase probability parameters are missing") from exc
    if uniform_probability != OFFICIAL_UNIFORM_PROBABILITY:
        raise MixedPhaseMetricError("uniform_source_probability must be frozen 0.125")
    if hot_probability != OFFICIAL_HOT_PROBABILITY:
        raise MixedPhaseMetricError("hot_probability must be frozen 0.8")
    if parameters["fixed_polarity"] != 1 or parameters["fixed_event_type"] != "spike":
        raise MixedPhaseMetricError("mixed phase annotations must be fixed polarity=1 spike")
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
    phases = _frozen_phases()
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


def _logical_permutation(metadata: dict[str, Any], source_count: int) -> list[int]:
    permutation = metadata.get("logical_source_permutation")
    if (not isinstance(permutation, list) or len(permutation) != source_count
            or any(isinstance(value, bool) or not isinstance(value, int) for value in permutation)
            or sorted(permutation) != list(range(source_count))):
        raise MixedPhaseMetricError("logical_source_permutation must be a 16-source bijection")
    declaration = metadata["run"]["parameters"].get("source_permutation", "identity")
    if not isinstance(declaration, str) or declaration not in {"identity", "bit_reverse"}:
        raise MixedPhaseMetricError("mixed phase mapping must be identity or bit_reverse")
    expected = list(range(source_count))
    if declaration == "bit_reverse":
        expected = [int(f"{source:04b}"[::-1], 2) for source in range(source_count)]
    if permutation != expected:
        raise MixedPhaseMetricError("logical_source_permutation disagrees with declared mapping")
    return permutation


def _validate_matched_trace(
    grouped: dict[str, list[dict[str, Any]]], phases: list[Phase], source_count: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    by_name = {phase.name: phase for phase in phases}
    permutation = _logical_permutation(metadata, source_count)
    inverse_permutation = {physical: logical for logical, physical in enumerate(permutation)}
    u_left, u_right = grouped["u_bernoulli"], grouped["u_smooth"]
    if len(u_left) != len(u_right) or _source_histogram(u_left, source_count) != _source_histogram(u_right, source_count):
        raise MixedPhaseMetricError("uniform pair does not match event count/source histogram")
    s_left, s_right = grouped["s_persistent"], grouped["s_rotating"]
    if (len(s_left) != len(s_right)
            or _source_histogram(s_left, source_count) != _source_histogram(s_right, source_count)
            or _fan_in_histogram(s_left, by_name["s_persistent"]) != _fan_in_histogram(s_right, by_name["s_rotating"])):
        raise MixedPhaseMetricError("sustained pair does not match count/source/fan-in histograms")
    expected_histogram = [64] * source_count
    if (_source_histogram(s_left, source_count) != expected_histogram
            or _source_histogram(s_right, source_count) != expected_histogram):
        raise MixedPhaseMetricError("sustained phases must offer exactly 64 events per source")
    for phase_name in ("s_persistent", "s_rotating"):
        phase = by_name[phase_name]
        per_cycle = collections.defaultdict(set)
        for row in grouped[phase_name]:
            relative = int(row["occurrence_cycle"]) - phase.start
            per_cycle[relative].add(inverse_permutation[int(row["logical_source"])])
        for relative in range(phase.cycles):
            if phase_name == "s_persistent":
                column = relative // 64
            else:
                column = relative % 4
            expected = {column + 4 * row for row in range(4)}
            if per_cycle[relative] != expected:
                raise MixedPhaseMetricError(
                    f"{phase_name} violates frozen column dwell/rotation at cycle {relative}"
                )
    map_a = [5, 6, 9, 10] + [source for source in range(16) if source not in {5, 6, 9, 10}]
    map_b = [0, 5, 10, 15] + [source for source in range(16) if source not in {0, 5, 10, 15}]
    inverse_a = {source: rank for rank, source in enumerate(map_a)}
    inverse_b = {source: rank for rank, source in enumerate(map_b)}

    def hotspot_rank_stream(name: str, inverse_map: dict[int, int]) -> list[tuple[int, int]]:
        phase = by_name[name]
        return [
            (int(row["occurrence_cycle"]) - phase.start,
             inverse_map[inverse_permutation[int(row["logical_source"])]])
            for row in grouped[name]
        ]

    hot_streams = (
        hotspot_rank_stream("h_a", inverse_a),
        hotspot_rank_stream("h_b", inverse_b),
        hotspot_rank_stream("h_a_replay", inverse_a),
    )
    if not hot_streams[0] == hot_streams[1] == hot_streams[2]:
        raise MixedPhaseMetricError("H-A/B/A-replay canonical rank streams do not match")
    for phase_name in ("h_a", "h_b", "h_a_replay"):
        if _fan_in_histogram(grouped[phase_name], by_name[phase_name]) != {2: 768}:
            raise MixedPhaseMetricError(f"{phase_name} must offer exactly two events per cycle")
    if _relative_stream(grouped["h_a"], by_name["h_a"], "logical_source") != _relative_stream(
        grouped["h_a_replay"], by_name["h_a_replay"], "logical_source"
    ):
        raise MixedPhaseMetricError("H-A-replay does not exactly replay H-A physical identity")
    return {
        "status": "pass",
        "uniform_exact_event_count_and_source_histogram": True,
        "sustained_exact_event_source_and_fan_in_histograms": True,
        "sustained_frozen_dwell_and_rotation": True,
        "hotspot_derived_rank_stream": True,
        "hotspot_a_replay_exact_physical_replay": True,
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


def _validate_summary(
    summary_path: Path | None, events: list[aggregate.Event], metadata: dict[str, Any]
) -> dict[str, Any]:
    if summary_path is None:
        return {
            "status": "not_provided",
            "correctness_qualified": False,
            "scoreboard_errors": None,
            "conservation_validated": False,
        }
    runs = aggregate.read_runs([summary_path])
    if len(runs) != 1:
        raise MixedPhaseMetricError("summary CSV must contain exactly one run")
    summary = runs[0]
    first = events[0]
    if (summary.candidate, summary.test, summary.seed, summary.load_pct) != first.run_key:
        raise MixedPhaseMetricError("summary and event CSV run provenance do not match")
    generated = len(events)
    overrun = sum(event.event_state == "source_overrun" for event in events)
    accepted = sum(event.accept_cycle is not None for event in events)
    delivered = sum(event.delivery_cycle is not None for event in events)
    if (summary.generated, summary.source_overrun, summary.accepted, summary.delivered) != (
        generated, overrun, accepted, delivered
    ):
        raise MixedPhaseMetricError("summary counters do not match per-event accounting")
    if generated != overrun + accepted or accepted != delivered:
        raise MixedPhaseMetricError("complete-run generation/transport conservation failed")
    if summary.stim_cycles != metadata["run"]["stim_cycles"]:
        raise MixedPhaseMetricError("summary stim_cycles does not match run manifest")
    return {
        "status": "qualified_fail" if summary.errors else "qualified_pass",
        "correctness_qualified": True,
        "scoreboard_errors": summary.errors,
        "conservation_validated": True,
        "generated_equals_overrun_plus_accepted": True,
        "accepted_equals_delivered": True,
    }


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
        phase_origin_drain = max(0, last_delivery - phase.end + 1)
        backlog_at_end = backlog_by_cycle[phase.end - 1]
        backlog_recovery = 0
        if backlog_at_end:
            next_zero = next(
                (cycle for cycle in range(phase.end, len(backlog_by_cycle))
                 if backlog_by_cycle[cycle] == 0),
                None,
            )
            backlog_recovery = None if next_zero is None else next_zero - phase.end + 1
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
            "backlog_at_end": backlog_at_end,
            "backlog_recovery_to_zero_cycles": backlog_recovery,
            "phase_origin_last_delivery_after_boundary_cycles": phase_origin_drain,
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
            "backlog_recovery_cycles_delta": _subtract(
                left["backlog_recovery_to_zero_cycles"],
                right["backlog_recovery_to_zero_cycles"],
            ),
        })
    return result


def analyze(
    manifest_path: Path, event_path: Path, summary_path: Path | None = None
) -> dict[str, Any]:
    metadata, trace_path, phases, stim_cycles, width, height = _validate_manifest(manifest_path)
    trace = _read_trace(trace_path)
    grouped = _validate_trace(trace, metadata, phases, stim_cycles, width, height)
    matching = _validate_matched_trace(grouped, phases, width * height, metadata)
    events = aggregate.read_events([event_path])
    by_id, offset, observation_end = _validate_events(
        events, trace, metadata, stim_cycles, width * height
    )
    phase_rows = _phase_metrics(phases, grouped, by_id, offset, observation_end)
    summary_evidence = _validate_summary(summary_path, events, metadata)
    total_overrun = sum(row["source_overrun"] for row in phase_rows)
    correctness_failure = bool(summary_evidence["scoreboard_errors"])
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
        "summary_evidence": summary_evidence,
        "classification": {
            "analysis_status": (
                "correctness_failure" if correctness_failure
                else "capacity_loss_unqualified"
                if total_overrun and not summary_evidence["correctness_qualified"]
                else "correctness_not_qualified"
                if not summary_evidence["correctness_qualified"]
                else "capacity_loss" if total_overrun else "pass"
            ),
            "correctness_status": (
                summary_evidence["status"]
                if summary_evidence["correctness_qualified"]
                else "not_qualified"
            ),
            "correctness_scope": (
                "common summary errors plus exact event conservation"
                if summary_evidence["correctness_qualified"]
                else "not qualified without common summary error/conservation counters"
            ),
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
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--require-qualified", action="store_true",
        help="return 1 unless common summary correctness qualification passes",
    )
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.run_manifest, args.events, args.summary)
    except (MixedPhaseMetricError, aggregate.InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if args.output:
            diagnostic = {
                "classification": {
                    "analysis_status": "input_failure",
                    "correctness_status": "not_qualified",
                },
                "error": str(exc),
            }
            args.output.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.require_qualified:
        classification = result["classification"]
        if (classification["correctness_status"] != "qualified_pass"
                or classification["analysis_status"] == "correctness_failure"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
