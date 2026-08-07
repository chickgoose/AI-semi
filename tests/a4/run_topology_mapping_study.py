#!/usr/bin/env python3
"""Generate candidate-only traces and quantify A4 static mapping sensitivity."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random

from topology_mapping_model import (
    Event,
    ceil_power_of_four,
    clone_trace,
    named_mapping,
    run_trace,
    tree_levels,
)


def add_event(trace: list[Event], source: int, cycle: int, pair_id: int | None) -> None:
    trace.append(Event(len(trace), source, cycle, pair_id))


def generate_trace(n: int, workload: str, seed: int, stim_cycles: int) -> list[Event]:
    rng = random.Random(seed)
    side = math.isqrt(n)
    trace: list[Event] = []
    pair_id = 0
    if workload == "quadrant_boundary_move":
        assert side * side == n
        y = side // 2 - 1
        centers = list(range(max(0, side // 2 - 2), min(side - 1, side // 2 + 1) + 1))
        for cycle in range(stim_cycles):
            if rng.random() >= 0.78:
                continue
            phase = (cycle // 32) % (2 * len(centers) - 2)
            center = centers[phase if phase < len(centers) else 2 * len(centers) - 2 - phase]
            peer = min(side - 1, center + 1)
            add_event(trace, y * side + center, cycle, pair_id)
            add_event(trace, (y + 1) * side + peer, cycle, pair_id)
            pair_id += 1
    elif workload == "single_quadrant_overload":
        assert side * side == n
        active = [y * side + x for y in range(side // 2) for x in range(side // 2)]
        for cycle in range(stim_cycles):
            if rng.random() >= 0.82:
                continue
            first = active[(2 * cycle) % len(active)]
            second = active[(2 * cycle + 1) % len(active)]
            add_event(trace, first, cycle, pair_id)
            add_event(trace, second, cycle, pair_id)
            pair_id += 1
    elif workload == "all_quadrants_equal":
        assert side * side == n
        quadrants = [
            [y * side + x for y in range(y0, y0 + side // 2)
             for x in range(x0, x0 + side // 2)]
            for y0 in (0, side // 2) for x0 in (0, side // 2)
        ]
        for cycle in range(stim_cycles):
            if rng.random() >= 0.405:
                continue
            for quadrant, sources in enumerate(quadrants):
                add_event(trace, sources[(cycle + quadrant) % len(sources)], cycle, pair_id)
            pair_id += 1
    elif workload == "padded_uniform":
        # Deterministic Bernoulli offered rate of about 1.25 events/cycle.
        probability = 1.25 / n
        for cycle in range(stim_cycles):
            simultaneous: list[int] = []
            for source in range(n):
                if rng.random() < probability:
                    simultaneous.append(source)
            this_pair = pair_id if len(simultaneous) >= 2 else None
            for source in simultaneous:
                add_event(trace, source, cycle, this_pair)
            if this_pair is not None:
                pair_id += 1
    else:
        raise ValueError(workload)
    return trace


def trace_digest(trace: list[Event]) -> str:
    payload = "".join(
        json.dumps({"event_id": event.event_id, "source": event.source,
                    "occurrence_cycle": event.occurrence, "pair_id": event.pair_id},
                   sort_keys=True, separators=(",", ":")) + "\n"
        for event in trace
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def write_trace(path: Path, trace: list[Event]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for event in trace:
            stream.write(json.dumps({
                "event_id": event.event_id,
                "source": event.source,
                "occurrence_cycle": event.occurrence,
                "pair_id": event.pair_id,
            }, sort_keys=True, separators=(",", ":")) + "\n")


def random_mapping(n: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    ports = list(range(ceil_power_of_four(n)))
    rng.shuffle(ports)
    return ports[:n]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).with_name("topology_mapping_manifest.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--named-output", type=Path, required=True)
    parser.add_argument("--bracket-output", type=Path, required=True)
    parser.add_argument("--trace-manifest-output", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, default=Path("/tmp/a4-topology-traces"))
    args = parser.parse_args()
    spec = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.trace_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    trace_rows: list[dict[str, object]] = []

    for run in spec["runs"]:
        n = int(run["sources"])
        trace = generate_trace(n, run["workload"], int(run["seed"]), int(spec["stim_cycles"]))
        digest = trace_digest(trace)
        trace_path = args.trace_dir / f"{run['name']}.jsonl"
        write_trace(trace_path, trace)
        if hashlib.sha256(trace_path.read_bytes()).hexdigest() != digest:
            raise AssertionError(f"trace serialization mismatch: {run['name']}")
        weights = [0] * n
        for event in trace:
            weights[event.source] += 1
        trace_rows.append({
            "name": run["name"], "sources": n, "padded_ports": ceil_power_of_four(n),
            "empty_ports": ceil_power_of_four(n) - n, "workload": run["workload"],
            "seed": run["seed"], "stim_cycles": spec["stim_cycles"],
            "generated": len(trace), "trace_sha256": digest,
        })
        mappings = {
            name: named_mapping(name, n, weights)
            for name in ("identity", "interleaved", "bit_reversed",
                         "placement_best", "placement_worst")
        }
        for sample in range(int(spec["random_mapping_samples"])):
            mappings[f"shuffle_{sample:02d}"] = random_mapping(n, int(run["seed"]) * 100 + sample)
        for mapping_name, mapping in mappings.items():
            metrics = run_trace(n, mapping, clone_trace(trace), int(spec["stim_cycles"]))
            rows.append({
                "name": run["name"], "sources": n,
                "padded_ports": ceil_power_of_four(n), "empty_ports": ceil_power_of_four(n) - n,
                "padding_fraction": (ceil_power_of_four(n) - n) / ceil_power_of_four(n),
                "tree_levels": tree_levels(n),
                "merge_nodes": (ceil_power_of_four(n) - 1) // 3,
                "state_bits": ((ceil_power_of_four(n) - 1) // 3) *
                              (16 + (n - 1).bit_length() + 8 + 3),
                "workload": run["workload"],
                "mapping": mapping_name, "trace_sha256": digest, **metrics,
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    named_rows = [row for row in rows if not str(row["mapping"]).startswith("shuffle_")]
    with args.named_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(named_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(named_rows)
    with args.trace_manifest_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(trace_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(trace_rows)

    bracket_rows: list[dict[str, object]] = []
    bracket_metrics = (
        "overrun", "pair_p99_latency", "max_request_wait", "pair_completion_ratio",
        "level0_link_util_max", "level1_link_util_max", "level2_link_util_max",
        "mapping_wire_span_max", "mapping_wire_span_total",
    )
    for run in spec["runs"]:
        candidates = [row for row in rows if row["name"] == run["name"]]
        bracket: dict[str, object] = {
            "name": run["name"], "sources": run["sources"],
            "workload": run["workload"], "mappings_examined": len(candidates),
        }
        for metric in bracket_metrics:
            low = min(candidates, key=lambda row: (row[metric], row["mapping"]))
            high = max(candidates, key=lambda row: (row[metric], row["mapping"]))
            bracket[f"min_{metric}"] = low[metric]
            bracket[f"min_{metric}_mapping"] = low["mapping"]
            bracket[f"max_{metric}"] = high[metric]
            bracket[f"max_{metric}_mapping"] = high["mapping"]
        bracket_rows.append(bracket)
    with args.bracket_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(bracket_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(bracket_rows)
    print(f"A4_TOPOLOGY_MAPPING_PASS runs={len(spec['runs'])} mappings={len(rows)} "
          f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
