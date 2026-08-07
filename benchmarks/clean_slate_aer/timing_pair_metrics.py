#!/usr/bin/env python3
"""Measure exact cross-source A/B timing distortion using TB-only relation IDs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

import aggregate


class TimingMetricError(ValueError):
    """Raised when timing-pair trace and result provenance disagree."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TimingMetricError(f"cannot read {path}: {exc}") from exc


def _read_trace(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise TimingMetricError(f"cannot read trace {path}: {exc}") from exc


def _percentile(values: list[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered) / 100) - 1]


def analyze(trace_path: Path, manifest_path: Path, event_path: Path) -> dict[str, Any]:
    metadata = _read_json(manifest_path)
    run = metadata.get("run", {})
    if run.get("workload") != "timing_pair":
        raise TimingMetricError("run manifest must describe timing_pair")
    if metadata.get("trace_file") != trace_path.name:
        raise TimingMetricError("trace filename does not match run manifest")
    if hashlib.sha256(trace_path.read_bytes()).hexdigest() != metadata.get("trace_sha256"):
        raise TimingMetricError("trace SHA256 does not match run manifest")
    trace = _read_trace(trace_path)
    events = aggregate.read_events([event_path])
    event_by_id = {event.tb_only_event_id: event for event in events}
    if len(event_by_id) != len(events) or set(event_by_id) != set(range(len(trace))):
        raise TimingMetricError("event result IDs do not match trace")

    relations: dict[int, dict[str, tuple[dict[str, Any], aggregate.Event]]] = {}
    for event_id, row in enumerate(trace):
        relation_id = row.get("relation_id")
        relation_role = row.get("relation_role")
        if relation_id is None and relation_role is None:
            continue
        if not isinstance(relation_id, int) or relation_role not in {"a", "b"}:
            raise TimingMetricError(f"event {event_id} has invalid timing relation")
        members = relations.setdefault(relation_id, {})
        if relation_role in members:
            raise TimingMetricError(f"relation {relation_id} repeats role {relation_role}")
        members[relation_role] = (row, event_by_id[event_id])
    if not relations or any(set(members) != {"a", "b"} for members in relations.values()):
        raise TimingMetricError("each timing relation must contain exactly A and B")

    errors: list[int] = []
    dropped = 0
    censored = 0
    for members in relations.values():
        row_a, event_a = members["a"]
        row_b, event_b = members["b"]
        if "source_overrun" in {event_a.event_state, event_b.event_state}:
            dropped += 1
        elif event_a.delivery_cycle is None or event_b.delivery_cycle is None:
            censored += 1
        else:
            input_gap = int(row_b["occurrence_cycle"]) - int(row_a["occurrence_cycle"])
            output_gap = event_b.delivery_cycle - event_a.delivery_cycle
            errors.append(abs(output_gap - input_gap))

    first = events[0] if events else None
    return {
        "candidate": first.candidate if first else "unspecified",
        "test": first.test if first else metadata.get("report_group", run.get("name")),
        "seed": first.seed if first else str(run.get("seed", "")),
        "trace_sha256": metadata.get("trace_sha256"),
        "pair_count": len(relations),
        "evaluable_pairs": len(errors),
        "dropped_pairs": dropped,
        "censored_pairs": censored,
        "mean_pair_timing_error_cycles": statistics.fmean(errors) if errors else None,
        "p95_pair_timing_error_cycles": _percentile(errors, 95),
        "p99_pair_timing_error_cycles": _percentile(errors, 99),
        "max_pair_timing_error_cycles": max(errors) if errors else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.trace, args.run_manifest, args.events)
    except (TimingMetricError, aggregate.InputError) as exc:
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
