#!/usr/bin/env python3
"""Collect one-row-per-architecture metrics from the official N=16 46 traces."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


RESULTS = {
    "rr": Path("/tmp/a8-rr-mock-regression"),
    "exact": Path("/tmp/a8-official-exact-regression"),
    "b1": Path("/tmp/a8-age-calendar-wheel-b1-regression"),
    "b2": Path("/tmp/a8-official-b2-regression"),
    "b4": Path("/tmp/a8-age-calendar-wheel-regression"),
    "b8": Path("/tmp/a8-official-b8-regression"),
}


def state_bits(architecture: str, sources: int = 16) -> int:
    source_width = math.ceil(math.log2(sources))
    output_bits = 1 + 16 + source_width
    if architecture == "rr":
        return source_width + output_bits
    if architecture == "exact":
        age_width = math.ceil(math.log2(2 * sources))
        return sources + sources * age_width + source_width + output_bits
    bucket_cycles = int(architecture[1:])
    epoch_count = 2 * sources // bucket_cycles
    epoch_width = math.ceil(math.log2(epoch_count))
    phase_width = max(1, math.ceil(math.log2(bucket_cycles)))
    return (sources + sources * epoch_width + epoch_width + phase_width
            + source_width + output_bits)


def select(rows: list[dict[str, str]], test: str, load: str | None = None) -> dict[str, str]:
    matches = [row for row in rows if row["test"] == test and
               (load is None or row["load_pct"] == load)]
    if len(matches) != 1:
        raise ValueError(f"expected one {test}/{load} row, found {len(matches)}")
    return matches[0]


def number(row: dict[str, str], field: str) -> float:
    value = row[field]
    return float(value) if value else math.nan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output_rows: list[dict[str, object]] = []

    for architecture, result_dir in RESULTS.items():
        with (result_dir / "aggregate.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        timing = [json.loads((result_dir / f"timing_pair_s{seed}.timing.json").read_text(
            encoding="utf-8")) for seed in (3901, 3902)]
        sparse = select(rows, "core_sparse_identity")
        uniform = select(rows, "uniform", "125")
        rotating = select(rows, "rotating_victim_identity")
        elephant = select(rows, "elephant_mouse_identity")
        phase = select(rows, "phase_transition")
        retrigger = select(rows, "retrigger_identity")
        timing_row = select(rows, "timing_pair")
        hotspot_rows = [row for row in rows if row["test"].startswith("moving_hotspot")]

        output_rows.append({
            "architecture": architecture,
            "state_bits": state_bits(architecture),
            "correctness_issues": sum(int(row["correctness_issues"] or 0) for row in rows),
            "sparse_p99": sparse["p99_e2e_latency_cycles"],
            "uniform_125_max_wait": uniform["worst_request_wait"],
            "uniform_125_p99": uniform["p99_e2e_latency_cycles"],
            "uniform_125_overrun": uniform["overrun_ratio"],
            "uniform_125_zero_service": uniform["zero_demand_service_source_window_ratio"],
            "uniform_125_dn_fairness": uniform["demand_normalized_delivery_fairness"],
            "rotating_max_wait": rotating["worst_request_wait"],
            "rotating_p99": rotating["p99_e2e_latency_cycles"],
            "rotating_overrun": rotating["overrun_ratio"],
            "rotating_zero_service": rotating["zero_demand_service_source_window_ratio"],
            "rotating_dn_fairness": rotating["demand_normalized_delivery_fairness"],
            "elephant_max_wait": elephant["worst_request_wait"],
            "phase_max_wait": phase["worst_request_wait"],
            "retrigger_max_wait": retrigger["worst_request_wait"],
            "hotspot_max_wait": max(number(row, "worst_request_wait") for row in hotspot_rows),
            "timing_e2e_p99": timing_row["p99_e2e_latency_cycles"],
            "timing_pair_p95_worst_seed": max(item["p95_pair_timing_error_cycles"] for item in timing),
            "timing_pair_p99_worst_seed": max(item["p99_pair_timing_error_cycles"] for item in timing),
            "timing_overrun": timing_row["overrun_ratio"],
            "timing_zero_service": timing_row["zero_demand_service_source_window_ratio"],
            "overall_max_wait": max(number(row, "worst_request_wait") for row in rows),
            "overall_max_p99": max(number(row, "p99_e2e_latency_cycles") for row in rows),
            "overall_max_overrun": max(number(row, "overrun_ratio") for row in rows),
            "overall_max_zero_service": max(
                number(row, "zero_demand_service_source_window_ratio") for row in rows),
            "overall_min_dn_fairness": min(
                number(row, "demand_normalized_delivery_fairness") for row in rows),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"A8_OFFICIAL_SUMMARY_PASS rows={len(output_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
