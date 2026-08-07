#!/usr/bin/env python3
"""Collect A8 scaling aggregate outputs and structural state-bit counts."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


ARCHITECTURES = ("rr", "exact", "b1", "b2", "b4", "b8")
SOURCE_COUNTS = (16, 32, 64)
FIELDS = (
    "architecture", "source_count", "state_bits", "age_width_bits",
    "bucket_cycles", "epoch_count", "sparse_p99", "simultaneous_max_wait",
    "uniform_1p25_throughput", "uniform_1p25_overrun",
    "uniform_1p25_max_wait", "uniform_1p25_p99",
    "uniform_1p25_zero_service", "uniform_1p25_dn_fairness",
    "rotating_throughput", "rotating_overrun", "rotating_max_wait",
    "rotating_p99", "rotating_zero_service", "rotating_dn_fairness",
    "timing_throughput", "timing_overrun", "timing_max_wait",
    "timing_e2e_p99", "timing_pair_p99", "timing_zero_service",
    "correctness_issues",
)


def state_accounting(architecture: str, sources: int) -> dict[str, int]:
    source_width = math.ceil(math.log2(sources))
    output_bits = 1 + 16 + source_width
    if architecture == "rr":
        return {"state_bits": source_width + output_bits,
                "age_width_bits": 0, "bucket_cycles": 0, "epoch_count": 0}
    if architecture == "exact":
        age_width = math.ceil(math.log2(2 * sources))
        bits = sources + sources * age_width + source_width + output_bits
        return {"state_bits": bits, "age_width_bits": age_width,
                "bucket_cycles": 0, "epoch_count": 0}
    bucket_cycles = int(architecture[1:])
    epoch_count = 2 * sources // bucket_cycles
    epoch_width = math.ceil(math.log2(epoch_count))
    phase_width = max(1, math.ceil(math.log2(bucket_cycles)))
    bits = (sources + sources * epoch_width + epoch_width + phase_width
            + source_width + output_bits)
    return {"state_bits": bits, "age_width_bits": epoch_width,
            "bucket_cycles": bucket_cycles, "epoch_count": epoch_count}


def row_by(rows: list[dict[str, str]], test: str, load: str | None = None) -> dict[str, str]:
    matches = [row for row in rows if row["test"] == test and
               (load is None or row["load_pct"] == load)]
    if len(matches) != 1:
        raise ValueError(f"expected one row for test={test} load={load}, got {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output_rows: list[dict[str, object]] = []

    for sources in SOURCE_COUNTS:
        for architecture in ARCHITECTURES:
            result_dir = args.result_root / f"{architecture}-n{sources}"
            with (result_dir / "aggregate.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            timing = json.loads((result_dir / "timing-pair.json").read_text(encoding="utf-8"))
            sparse = row_by(rows, f"n{sources}_sparse")
            simultaneous = row_by(rows, f"n{sources}_simultaneous")
            uniform = row_by(rows, "uniform", "125")
            rotating = row_by(rows, f"n{sources}_rotating_victim")
            timing_row = row_by(rows, f"n{sources}_timing_pair")
            structural = state_accounting(architecture, sources)
            output_rows.append({
                "architecture": architecture,
                "source_count": sources,
                **structural,
                "sparse_p99": sparse["p99_e2e_latency_cycles"],
                "simultaneous_max_wait": simultaneous["worst_request_wait"],
                "uniform_1p25_throughput": uniform["avg_throughput"],
                "uniform_1p25_overrun": uniform["overrun_ratio"],
                "uniform_1p25_max_wait": uniform["worst_request_wait"],
                "uniform_1p25_p99": uniform["p99_e2e_latency_cycles"],
                "uniform_1p25_zero_service": uniform["zero_demand_service_source_window_ratio"],
                "uniform_1p25_dn_fairness": uniform["demand_normalized_delivery_fairness"],
                "rotating_throughput": rotating["avg_throughput"],
                "rotating_overrun": rotating["overrun_ratio"],
                "rotating_max_wait": rotating["worst_request_wait"],
                "rotating_p99": rotating["p99_e2e_latency_cycles"],
                "rotating_zero_service": rotating["zero_demand_service_source_window_ratio"],
                "rotating_dn_fairness": rotating["demand_normalized_delivery_fairness"],
                "timing_throughput": timing_row["avg_throughput"],
                "timing_overrun": timing_row["overrun_ratio"],
                "timing_max_wait": timing_row["worst_request_wait"],
                "timing_e2e_p99": timing_row["p99_e2e_latency_cycles"],
                "timing_pair_p99": timing["p99_pair_timing_error_cycles"],
                "timing_zero_service": timing_row["zero_demand_service_source_window_ratio"],
                "correctness_issues": sum(
                    int(row["correctness_issues"] or "0") for row in rows
                ),
            })

    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"A8_MATRIX_SUMMARY_PASS rows={len(output_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
