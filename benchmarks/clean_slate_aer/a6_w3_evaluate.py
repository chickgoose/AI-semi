#!/usr/bin/env python3
"""Compare and gate the A6 W3 Elias--Fano monotone-dequeue transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from a6_w3_elias_fano import (
    CodecError,
    EncodedBatch,
    Event,
    address_width,
    count_width,
    encode_batch,
)


LINK_WIDTH = 2
PHYSICAL_PINS = 5  # two data, two valid-count, one ready
WINDOWS = (0, 1, 2, 4)
OFFICIAL_CAP22_MANIFEST_SHA256 = (
    "99a8bbd329eeb8d232209263a5624d197c701fcbc0aff76ba44241a87be98c62"
)
EXPECTED_CAP22_RUN_COUNT = 22


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_provenance(
    manifest_path: Path,
    generator_path: Path,
    registry_path: Path,
    trace_dir: Path | None,
    *,
    require_capacity22: bool = True,
) -> dict[str, object]:
    """Validate immutable generation inputs and outputs before any metrics."""

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CodecError(f"cannot read provenance contract: {error}") from error
    if registry.get("schema_version") != 1 or registry.get("suite") != "capacity22":
        raise CodecError("invalid A6 W3 capacity22 registry schema")

    generator_contract = registry.get("generator", {})
    generator_sha256 = sha256_file(generator_path)
    if generator_sha256 != generator_contract.get("sha256"):
        raise CodecError("generator byte SHA does not match canonical registry")
    generator_source = generator_path.read_text(encoding="utf-8")
    version_match = re.search(
        r'^GENERATOR_VERSION\s*=\s*["\']([^"\']+)["\']',
        generator_source,
        re.MULTILINE,
    )
    if not version_match or version_match.group(1) != generator_contract.get("version"):
        raise CodecError("generator version does not match canonical registry")

    manifest_contract = registry.get("manifest", {})
    manifest_sha256 = sha256_file(manifest_path)
    if manifest_sha256 != manifest_contract.get("sha256"):
        raise CodecError("manifest byte SHA does not match canonical registry")
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise CodecError("capacity22 manifest runs must be a list")
    run_names = [run.get("name") if isinstance(run, dict) else None for run in runs]
    canonical_names = manifest_contract.get("run_names")
    if run_names != canonical_names or len(run_names) != len(set(run_names)):
        raise CodecError("manifest ordered run set does not match canonical registry")
    if require_capacity22 and len(run_names) != EXPECTED_CAP22_RUN_COUNT:
        raise CodecError("canonical capacity22 registry must contain exactly 22 runs")
    trace_contracts = registry.get("traces")
    if not isinstance(trace_contracts, dict) or set(trace_contracts) != set(run_names):
        raise CodecError("registry trace set does not match canonical manifest run set")

    trace_records = []
    if trace_dir is not None:
        for name in run_names:
            contract = trace_contracts[name]
            trace_path = trace_dir / f"{name}.events.jsonl"
            metadata_path = trace_dir / f"{name}.manifest.json"
            if not trace_path.is_file() or trace_path.stat().st_size == 0:
                raise CodecError(f"missing or empty canonical trace: {trace_path}")
            if not metadata_path.is_file() or metadata_path.stat().st_size == 0:
                raise CodecError(f"missing or empty trace metadata: {metadata_path}")
            trace_sha256 = sha256_file(trace_path)
            metadata_sha256 = sha256_file(metadata_path)
            if trace_sha256 != contract.get("sha256"):
                raise CodecError(f"{name}: trace SHA does not match canonical registry")
            if metadata_sha256 != contract.get("metadata_sha256"):
                raise CodecError(f"{name}: metadata SHA does not match canonical registry")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("generator_version") != generator_contract.get("version"):
                raise CodecError(f"{name}: generated metadata version mismatch")
            if metadata.get("trace_sha256") != trace_sha256:
                raise CodecError(f"{name}: metadata trace SHA mismatch")
            if metadata.get("event_count") != contract.get("event_count"):
                raise CodecError(f"{name}: canonical event count mismatch")
            if metadata.get("run", {}).get("name") != name:
                raise CodecError(f"{name}: metadata run identity mismatch")
            trace_records.append({
                "name": name,
                "sha256": trace_sha256,
                "metadata_sha256": metadata_sha256,
                "generated_events": contract["event_count"],
            })
    return {
        "registry": str(registry_path.resolve()),
        "registry_sha256": sha256_file(registry_path),
        "generator": str(generator_path.resolve()),
        "generator_sha256": generator_sha256,
        "generator_version": generator_contract["version"],
        "manifest_sha256": manifest_sha256,
        "run_names": run_names,
        "trace_records": trace_records,
    }


def nearest_rank(values: Sequence[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered) / 100))
    return ordered[rank - 1]


def _weighted_average(items: Iterable[tuple[int, float]]) -> float:
    numerator = denominator = 0.0
    for weight, value in items:
        numerator += weight * value
        denominator += weight
    return numerator / denominator if denominator else 0.0


def comparison_point(num_sources: int, max_batch: int, k: int) -> dict[str, object]:
    aw = address_width(num_sources)
    cw = count_width(max_batch)
    if not 0 <= k <= max_batch or k > num_sources:
        raise ValueError("k outside comparison range")
    if k == 0:
        ef_bits = 1 + cw
        return {
            "num_sources": num_sources,
            "k": 0,
            "raw": {"payload_bits": 0, "framing_bits": 0, "cycles": 0,
                    "bits_per_event": None, "decoder_work": 0},
            "bitmap": {"payload_bits": num_sources, "framing_bits": 1,
                       "cycles": 1 + math.ceil(num_sources / LINK_WIDTH),
                       "bits_per_event": None, "decoder_work": num_sources},
            "enumerative": {"payload_bits": 0, "framing_bits": 1 + cw,
                            "cycles": 1 + math.ceil(cw / LINK_WIDTH),
                            "bits_per_event": None, "decoder_work": 0},
            "elias_fano": {"mean_payload_bits": 0.0, "min_payload_bits": 0,
                           "max_payload_bits": 0, "framing_bits": 1 + cw,
                           "mean_selected_bits": float(ef_bits),
                           "mean_selected_cycles": float(1 + math.ceil(cw / LINK_WIDTH)),
                           "bits_per_event": None, "escape_ratio": 0.0,
                           "mean_decoder_work": 0.0},
        }

    combinations = math.comb(num_sources, k)
    lw = max(0, (num_sources // k).bit_length() - 1)
    raw_payload = k * aw
    raw_cycles = math.ceil(raw_payload / LINK_WIDTH)
    enum_payload = math.ceil(math.log2(combinations)) if combinations > 1 else 0
    bitmap_cycles = 1 + math.ceil(num_sources / LINK_WIDTH)
    enum_cycles = 1 + math.ceil((cw + enum_payload) / LINK_WIDTH)

    weighted: list[tuple[int, dict[str, float]]] = []
    for maximum in range(k - 1, num_sources):
        multiplicity = math.comb(maximum, k - 1)
        high_bits = (maximum >> lw) + k
        payload = high_bits + k * lw
        framed_bits = 1 + cw + payload
        ef_cycles = 1 + math.ceil((cw + payload) / LINK_WIDTH)
        selected_ef = ef_cycles < raw_cycles
        weighted.append((multiplicity, {
            "payload": float(payload),
            "selected_bits": float(framed_bits if selected_ef else raw_payload),
            "selected_cycles": float(ef_cycles if selected_ef else raw_cycles),
            "escape": 0.0 if selected_ef else 1.0,
            "decoder_work": float(high_bits + 2 * k if selected_ef else k),
        }))
    if sum(weight for weight, _ in weighted) != combinations:
        raise AssertionError("maximum-order-statistic accounting failed")

    mean_payload = _weighted_average(
        (weight, row["payload"]) for weight, row in weighted)
    mean_bits = _weighted_average(
        (weight, row["selected_bits"]) for weight, row in weighted)
    mean_cycles = _weighted_average(
        (weight, row["selected_cycles"]) for weight, row in weighted)
    escape_ratio = _weighted_average(
        (weight, row["escape"]) for weight, row in weighted)
    decoder_work = _weighted_average(
        (weight, row["decoder_work"]) for weight, row in weighted)
    payload_extremes = [
        ((maximum >> lw) + k + k * lw)
        for maximum in range(k - 1, num_sources)
    ]
    return {
        "num_sources": num_sources,
        "k": k,
        "raw": {
            "payload_bits": raw_payload, "framing_bits": 0,
            "cycles": raw_cycles, "bits_per_event": raw_payload / k,
            "events_per_pin_cycle": k / (PHYSICAL_PINS * raw_cycles),
            "decoder_work": k,
        },
        "bitmap": {
            "payload_bits": num_sources, "framing_bits": 1,
            "cycles": bitmap_cycles, "bits_per_event": (num_sources + 1) / k,
            "events_per_pin_cycle": k / (PHYSICAL_PINS * bitmap_cycles),
            "decoder_work": num_sources + k,
        },
        "enumerative": {
            "payload_bits": enum_payload, "framing_bits": 1 + cw,
            "cycles": enum_cycles, "bits_per_event": (1 + cw + enum_payload) / k,
            "events_per_pin_cycle": k / (PHYSICAL_PINS * enum_cycles),
            "decoder_work": num_sources + k,
        },
        "elias_fano": {
            "low_width": lw,
            "mean_payload_bits": mean_payload,
            "min_payload_bits": min(payload_extremes),
            "max_payload_bits": max(payload_extremes),
            "framing_bits": 1 + cw,
            "mean_selected_bits": mean_bits,
            "mean_selected_cycles": mean_cycles,
            "bits_per_event": mean_bits / k,
            "events_per_pin_cycle": k / (PHYSICAL_PINS * mean_cycles),
            "escape_ratio": escape_ratio,
            "mean_decoder_work": decoder_work,
        },
    }


def sweep(max_batch: int) -> dict[str, object]:
    points = []
    for num_sources in (16, 64):
        for k in range(0, min(max_batch, num_sources) + 1):
            points.append(comparison_point(num_sources, max_batch, k))
    return {
        "schema_version": 1,
        "format": "a6_w3_elias_fano_monotone_dequeue",
        "link": {"data_width": LINK_WIDTH, "physical_pins": PHYSICAL_PINS},
        "max_batch": max_batch,
        "points": points,
    }


@dataclass(frozen=True)
class SimulationResult:
    mode: str
    window_cycles: int
    generated: int
    accepted: int
    overrun: int
    delivered: int
    delivered_in_window: int
    stim_cycles: int
    drain_cycle: int
    link_bits: int
    link_transfer_cycles: int
    link_busy_cycles: int
    ef_batches: int
    raw_batches: int
    average_batch_wait: float
    max_batch_wait: int
    average_latency: float
    p50_latency: int
    p95_latency: int
    p99_latency: int
    max_latency: int
    decoder_work: int
    latencies: tuple[int, ...]

    @property
    def throughput(self) -> float:
        return self.delivered_in_window / self.stim_cycles

    @property
    def drained_throughput(self) -> float:
        return self.delivered / max(1, self.drain_cycle + 1)

    @property
    def bits_per_event(self) -> float:
        return self.link_bits / self.delivered if self.delivered else 0.0

    @property
    def events_per_pin_cycle(self) -> float:
        denominator = PHYSICAL_PINS * self.link_busy_cycles
        return self.delivered / denominator if denominator else 0.0

    @property
    def events_per_elapsed_pin_cycle(self) -> float:
        return self.delivered / (PHYSICAL_PINS * max(1, self.drain_cycle + 1))

    def public(self) -> dict[str, object]:
        row = asdict(self)
        row.pop("latencies")
        row.update({
            "throughput": self.throughput,
            "drained_throughput": self.drained_throughput,
            "bits_per_event": self.bits_per_event,
            "events_per_pin_cycle": self.events_per_pin_cycle,
            "events_per_elapsed_pin_cycle": self.events_per_elapsed_pin_cycle,
        })
        return row


def simulate(
    events: Sequence[Event],
    *,
    stim_cycles: int,
    num_sources: int,
    max_batch: int,
    window_cycles: int,
    codec: bool,
) -> SimulationResult:
    arrivals: dict[int, list[Event]] = defaultdict(list)
    for event in events:
        if not 0 <= event.occurrence_cycle < stim_cycles:
            raise CodecError("event occurrence outside stimulus window")
        if not 0 <= event.source < num_sources:
            raise CodecError("event source outside configured universe")
        arrivals[event.occurrence_cycle].append(event)
    pending: dict[int, Event] = {}
    tx_queue: list[tuple[tuple[Event, ...], EncodedBatch, int]] = []
    rx_queue: list[Event] = []
    rx_capacity = 2 * max_batch
    active: tuple[tuple[Event, ...], EncodedBatch, int] | None = None
    active_beat = 0
    accepted = overrun = delivered = delivered_in_window = 0
    link_bits = link_transfer_cycles = link_busy_cycles = 0
    ef_batches = raw_batches = decoder_work = 0
    waits: list[int] = []
    latencies: list[int] = []
    delivered_sequences: list[int] = []
    accepted_sequences: list[int] = []
    cycle = 0
    limit = stim_cycles + max(10000, len(events) * 32)

    while cycle < limit:
        for event in arrivals.get(cycle, []):
            if event.source in pending:
                overrun += 1
            else:
                pending[event.source] = event

        tx_banks_at_cycle_start = len(tx_queue) + int(active is not None)

        # RTL retirement observes FIFO state from the start of the edge.  A
        # frame completing on this edge becomes visible only after this pop.
        if rx_queue:
            event = rx_queue.pop(0)
            delivered += 1
            delivered_sequences.append(event.sequence)
            latency = cycle - event.occurrence_cycle
            latencies.append(latency)
            if cycle < stim_cycles:
                delivered_in_window += 1

        if active is None and tx_queue:
            active = tx_queue.pop(0)
            active_beat = 0

        if active is not None:
            link_busy_cycles += 1
            batch_events, frame, work = active
            free_slots = rx_capacity - len(rx_queue)
            # This exactly follows committed RTL: an EF marker reserves the
            # worst-case K slots, subsequent frame beats cannot stall, while
            # each raw beat requires one currently free FIFO entry.
            link_ready = (
                free_slots >= max_batch
                if frame.mode == "elias_fano" and active_beat == 0
                else (True if frame.mode == "elias_fano" else free_slots >= 1)
            )
            if link_ready:
                beat = frame.beats[active_beat]
                link_bits += beat.count
                link_transfer_cycles += 1
                active_beat += 1
                if frame.mode == "raw":
                    beats_per_event = address_width(num_sources) // LINK_WIDTH
                    if active_beat % beats_per_event == 0:
                        rx_queue.append(
                            batch_events[active_beat // beats_per_event - 1]
                        )
                elif active_beat == frame.link_cycles:
                    # The RTL buffers all high/low components and performs one
                    # push_count=frame_count update on the terminal beat.
                    rx_queue.extend(batch_events)

                if active_beat == frame.link_cycles:
                    decoder_work += work
                    ef_batches += int(frame.mode == "elias_fano")
                    raw_batches += int(frame.mode == "raw")
                    active = None
                    active_beat = 0

        # Exactly two TX batch banks total: the actively serialized bank counts
        # against capacity, rather than being a hidden third bank.
        can_capture = tx_banks_at_cycle_start < 2
        if can_capture and pending:
            oldest = min(event.occurrence_cycle for event in pending.values())
            eligible_sources = sorted(
                source for source, event in pending.items()
                if event.occurrence_cycle <= oldest + window_cycles
            )
            ready = (
                len(eligible_sources) >= max_batch
                or cycle - oldest >= window_cycles
                or cycle >= stim_cycles
            )
            if ready:
                selected_sources = eligible_sources[:max_batch]
                selected = tuple(pending.pop(source) for source in selected_sources)
                # The source-monotone order is the arbitration order and wire order.
                selected = tuple(sorted(selected, key=lambda event: event.source))
                if selected and (
                    max(event.occurrence_cycle for event in selected)
                    - min(event.occurrence_cycle for event in selected)
                    > window_cycles
                ):
                    raise AssertionError("batch exceeded its occurrence window")
                frame = encode_batch(
                    tuple(event.source for event in selected),
                    num_sources=num_sources,
                    max_batch=max_batch,
                    force_mode=None if codec else "raw",
                )
                waits.extend(cycle - event.occurrence_cycle for event in selected)
                accepted_sequences.extend(event.sequence for event in selected)
                accepted += len(selected)
                work = (
                    frame.high_bits + 2 * len(selected)
                    if frame.mode == "elias_fano"
                    else len(selected)
                )
                tx_queue.append((selected, frame, work))

        done = (
            cycle >= stim_cycles and not pending and not tx_queue
            and active is None and not rx_queue
        )
        if done:
            break
        cycle += 1
    else:
        raise RuntimeError("transport simulation failed to drain")

    if accepted + overrun != len(events):
        raise AssertionError("generation/acceptance conservation failed")
    if accepted != delivered or accepted_sequences != delivered_sequences:
        raise AssertionError("accepted event sequence was not retired exactly")
    return SimulationResult(
        mode="elias_fano" if codec else "raw",
        window_cycles=window_cycles,
        generated=len(events), accepted=accepted, overrun=overrun,
        delivered=delivered, delivered_in_window=delivered_in_window,
        stim_cycles=stim_cycles, drain_cycle=cycle,
        link_bits=link_bits, link_transfer_cycles=link_transfer_cycles,
        link_busy_cycles=link_busy_cycles,
        ef_batches=ef_batches, raw_batches=raw_batches,
        average_batch_wait=sum(waits) / len(waits) if waits else 0.0,
        max_batch_wait=max(waits, default=0),
        average_latency=sum(latencies) / len(latencies) if latencies else 0.0,
        p50_latency=nearest_rank(latencies, 50),
        p95_latency=nearest_rank(latencies, 95),
        p99_latency=nearest_rank(latencies, 99),
        max_latency=max(latencies, default=0),
        decoder_work=decoder_work,
        latencies=tuple(latencies),
    )


def load_trace(path: Path, *, num_sources: int) -> list[Event]:
    events = []
    identities: set[int] = set()
    source_cycles: set[tuple[int, int]] = set()
    with path.open(encoding="utf-8") as handle:
        for sequence, line in enumerate(handle):
            raw = json.loads(line)
            identity = raw["tb_only_event_id"]
            source = raw["logical_source"]
            occurrence = raw["occurrence_cycle"]
            if identity in identities:
                raise CodecError(f"{path}: duplicate TB-only identity")
            if (source, occurrence) in source_cycles:
                raise CodecError(f"{path}: duplicate source occurrence in one cycle")
            if not 0 <= source < num_sources:
                raise CodecError(f"{path}: source outside configured universe")
            identities.add(identity)
            source_cycles.add((source, occurrence))
            events.append(Event(occurrence, sequence, source))
    return events


def evaluate_cap22(
    manifest_path: Path,
    trace_dir: Path,
    max_batch: int,
    *,
    generator_path: Path | None = None,
    registry_path: Path | None = None,
) -> dict[str, object]:
    manifest_sha256 = sha256_file(manifest_path)
    if manifest_sha256 != OFFICIAL_CAP22_MANIFEST_SHA256:
        raise CodecError("cap22 manifest digest is not the frozen official contract")
    if generator_path is None or registry_path is None:
        raise CodecError("cap22 requires explicit generator and canonical registry")
    provenance = validate_provenance(
        manifest_path, generator_path, registry_path, trace_dir)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs = manifest.get("runs")
    if not isinstance(runs, list) or len(runs) != 22:
        raise CodecError("cap22 evaluation requires the official 22-run manifest")
    results = []
    trace_provenance = provenance["trace_records"]
    for run in runs:
        width = run["geometry"]["width"]
        height = run["geometry"]["height"]
        num_sources = width * height
        if num_sources != 16:
            raise CodecError("cap22 must use the frozen N=16 geometry")
        trace = trace_dir / f"{run['name']}.events.jsonl"
        if not trace.is_file() or trace.stat().st_size == 0:
            raise CodecError(f"missing or empty generated trace: {trace}")
        events = load_trace(trace, num_sources=num_sources)
        if len(events) != next(
            row["generated_events"] for row in trace_provenance
            if row["name"] == run["name"]
        ):
            raise CodecError(f"{run['name']}: parsed event count mismatch")
        for window in WINDOWS:
            raw = simulate(
                events, stim_cycles=run["stim_cycles"], num_sources=num_sources,
                max_batch=max_batch, window_cycles=window, codec=False,
            )
            codec = simulate(
                events, stim_cycles=run["stim_cycles"], num_sources=num_sources,
                max_batch=max_batch, window_cycles=window, codec=True,
            )
            results.append({
                "name": run["name"], "workload": run["workload"],
                "window_cycles": window, "raw": raw.public(),
                "codec": codec.public(),
                "latency_non_regression": codec.p95_latency <= raw.p95_latency,
            })

    gates = []
    raw_zero_by_name = {
        row["name"]: row["raw"] for row in results if row["window_cycles"] == 0
    }
    for window in WINDOWS:
        rows = [row for row in results if row["window_cycles"] == window]
        # Every bounded-window codec is compared with the natural no-wait raw
        # reference.  Comparing window four only with a deliberately delayed
        # window-four raw transport would hide batching latency.
        raw_rows = [raw_zero_by_name[row["name"]] for row in rows]
        raw_delivered = sum(row["delivered"] for row in raw_rows)
        codec_delivered = sum(row["codec"]["delivered"] for row in rows)
        raw_window = sum(row["delivered_in_window"] for row in raw_rows)
        codec_window = sum(row["codec"]["delivered_in_window"] for row in rows)
        raw_overrun = sum(row["overrun"] for row in raw_rows)
        codec_overrun = sum(row["codec"]["overrun"] for row in rows)
        raw_stim_cycles = sum(row["stim_cycles"] for row in raw_rows)
        codec_stim_cycles = sum(row["codec"]["stim_cycles"] for row in rows)
        raw_elapsed_cycles = sum(row["drain_cycle"] + 1 for row in raw_rows)
        codec_elapsed_cycles = sum(
            row["codec"]["drain_cycle"] + 1 for row in rows)
        raw_link_cycles = sum(row["link_busy_cycles"] for row in raw_rows)
        codec_link_cycles = sum(row["codec"]["link_busy_cycles"] for row in rows)
        raw_pin = raw_delivered / (PHYSICAL_PINS * raw_link_cycles)
        codec_pin = codec_delivered / (PHYSICAL_PINS * codec_link_cycles)
        latency_failures = [
            row["name"] for row in rows
            if row["codec"]["p95_latency"]
            > raw_zero_by_name[row["name"]]["p95_latency"]
        ]
        passed = (
            codec_window >= raw_window
            and codec_overrun <= raw_overrun
            and codec_pin > raw_pin
            and not latency_failures
        )
        gates.append({
            "window_cycles": window,
            "raw_delivered": raw_delivered,
            "codec_delivered": codec_delivered,
            "raw_delivered_in_window": raw_window,
            "codec_delivered_in_window": codec_window,
            "raw_overrun": raw_overrun,
            "codec_overrun": codec_overrun,
            "raw_events_per_pin_cycle": raw_pin,
            "codec_events_per_pin_cycle": codec_pin,
            "raw_events_per_cycle": raw_window / raw_stim_cycles,
            "codec_events_per_cycle": codec_window / codec_stim_cycles,
            "raw_events_per_elapsed_pin_cycle": (
                raw_delivered / (PHYSICAL_PINS * raw_elapsed_cycles)),
            "codec_events_per_elapsed_pin_cycle": (
                codec_delivered / (PHYSICAL_PINS * codec_elapsed_cycles)),
            "latency_regression_runs": latency_failures,
            "gate_pass": passed,
        })
    passing = [gate for gate in gates if gate["gate_pass"]]
    selected = max(
        passing,
        key=lambda gate: (gate["codec_delivered_in_window"],
                          gate["codec_events_per_pin_cycle"]),
        default=None,
    )
    return {
        "schema_version": 2,
        "format": "a6_w3_elias_fano_monotone_dequeue",
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "provenance": {
            key: value for key, value in provenance.items()
            if key != "trace_records"
        },
        "suite": "capacity22",
        "run_count": len(runs),
        "max_batch": max_batch,
        "windows": list(WINDOWS),
        "cycle_contract": {
            "ef_visibility": "all k entries become visible after terminal beat",
            "ef_marker_admission": "requires MAX_BATCH free RX slots including same-edge pop",
            "tx_batch_banks_total": 2,
            "retirement_width": 1,
            "prior_ac6c0b8_gate": "superseded_model_rtl_mismatch"
        },
        "trace_provenance": trace_provenance,
        "results": results,
        "gates": gates,
        "decision": "GO_RTL" if selected else "HOLD_LATENCY_OR_LINK_GATE",
        "selected_gate": selected,
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sweep_parser = subparsers.add_parser("sweep")
    sweep_parser.add_argument("--max-batch", type=int, default=16)
    sweep_parser.add_argument("--output", type=Path, required=True)
    cap_parser = subparsers.add_parser("cap22")
    cap_parser.add_argument("--manifest", type=Path, required=True)
    cap_parser.add_argument("--trace-dir", type=Path, required=True)
    cap_parser.add_argument("--generator", type=Path, required=True)
    cap_parser.add_argument("--registry", type=Path, required=True)
    cap_parser.add_argument("--max-batch", type=int, default=16)
    cap_parser.add_argument("--output", type=Path, required=True)
    cap_parser.add_argument("--require-go", action="store_true")
    verify_parser = subparsers.add_parser("verify-contract")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--generator", type=Path, required=True)
    verify_parser.add_argument("--registry", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "sweep":
        report = sweep(args.max_batch)
    elif args.command == "verify-contract":
        report = validate_provenance(
            args.manifest, args.generator, args.registry, None)
        report["status"] = "contract_verified"
    else:
        report = evaluate_cap22(
            args.manifest, args.trace_dir, args.max_batch,
            generator_path=args.generator, registry_path=args.registry,
        )
    write_json(args.output, report)
    print(f"A6_W3_REPORT output={args.output} decision={report.get('decision', 'n/a')}")
    if args.command == "cap22" and args.require_go and report.get("decision") != "GO_RTL":
        print(
            "A6_W3_REQUIRE_GO_FAIL diagnostic_json="
            f"{args.output} decision={report.get('decision')}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
