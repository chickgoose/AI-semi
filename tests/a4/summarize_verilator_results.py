#!/usr/bin/env python3
"""Create per-trace RTL metrics and RTL-vs-model deltas from A4 outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def percentile(values: list[int], quantile: float) -> int:
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1] if ordered else 0


def jain(values: list[float]) -> float:
    denominator = len(values) * sum(value * value for value in values)
    return 1.0 if denominator == 0 else sum(values) ** 2 / denominator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument(
        "--model-candidate", default="a4-quadtree-model",
        choices=("a4-quadtree-model", "mock-flat-rr-model"),
    )
    args = parser.parse_args()

    model_rows = {
        row["name"]: row for row in csv.DictReader(args.model.open(encoding="utf-8"))
        if row["candidate"] == args.model_candidate
    }
    manifest = json.loads(
        (Path(__file__).resolve().parents[2] /
         "benchmarks/clean_slate_aer/manifest.neutrality-n16.json").read_text(encoding="utf-8")
    )
    rows: list[dict[str, object]] = []
    comparisons: list[dict[str, object]] = []
    for declared in manifest["runs"]:
        name = declared["name"]
        metadata = json.loads(
            (args.trace_dir / f"{name}.manifest.json").read_text(encoding="utf-8")
        )
        with (args.results / f"{name}.csv").open(newline="", encoding="utf-8") as stream:
            summary = next(csv.DictReader(stream))
        with (args.results / f"{name}.events.csv").open(
            newline="", encoding="utf-8"
        ) as stream:
            events = list(csv.DictReader(stream))
        delivered = [event for event in events if event["event_state"] == "delivered"]
        latencies = [
            int(event["delivery_cycle"]) - int(event["occurrence_cycle"])
            for event in delivered
        ]
        offered = [0] * 16
        accepted = [0] * 16
        for event in events:
            source = int(event["logical_source"])
            offered[source] += 1
            if event["event_state"] == "delivered":
                accepted[source] += 1
        ratios = [accepted[source] / offered[source] for source in range(16) if offered[source]]
        row = {
            "name": name,
            "report_group": metadata["report_group"],
            "workload": declared["workload"],
            "seed": declared["seed"],
            "load": declared["load"],
            "trace_sha256": metadata["trace_sha256"],
            "generated": int(summary["generated"]),
            "source_overrun": int(summary["source_overrun"]),
            "accepted": int(summary["accepted"]),
            "delivered": int(summary["delivered"]),
            "errors": int(summary["errors"]),
            "measurement_event_per_cycle": float(summary["throughput"]),
            "p95_e2e_latency": percentile(latencies, 0.95),
            "p99_e2e_latency": percentile(latencies, 0.99),
            "max_e2e_latency": max(latencies, default=0),
            "max_request_wait": int(summary["max_request_wait"]),
            "demand_normalized_fairness": jain(ratios),
            "min_source_service_ratio": min(ratios, default=1.0),
            "total_cycles": int(summary["total_cycles"]),
        }
        rows.append(row)
        model = model_rows[name]
        comparisons.append({
            "name": name,
            "count_match": (
                int(model["accepted"]) == row["accepted"]
                and int(model["delivered"]) == row["delivered"]
            ),
            "overrun_match": int(model["source_overrun"]) == row["source_overrun"],
            "throughput_delta_rtl_minus_model": (
                row["measurement_event_per_cycle"]
                - float(model["measurement_event_per_cycle"])
            ),
            "p95_latency_delta_rtl_minus_model": (
                row["p95_e2e_latency"] - int(model["p95_e2e_latency"])
            ),
            "p99_latency_delta_rtl_minus_model": (
                row["p99_e2e_latency"] - int(model["p99_e2e_latency"])
            ),
            "max_wait_delta_rtl_minus_model": (
                row["max_request_wait"] - int(model["max_request_wait"])
            ),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with args.comparison.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(comparisons[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(comparisons)

    if any(not row["count_match"] or not row["overrun_match"] for row in comparisons):
        raise SystemExit("RTL/model conservation or overrun mismatch")
    print(f"A4_VERILATOR_SUMMARY_PASS runs={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
