#!/usr/bin/env python3
"""Join A4 and flat frozen-RTL metrics without collapsing workload identity."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


METRICS = (
    "source_overrun",
    "measurement_event_per_cycle",
    "p95_e2e_latency",
    "p99_e2e_latency",
    "max_request_wait",
    "demand_normalized_fairness",
    "min_source_service_ratio",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--a4", type=Path, required=True)
    parser.add_argument("--flat", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    a4 = {row["name"]: row for row in csv.DictReader(args.a4.open(encoding="utf-8"))}
    flat = {row["name"]: row for row in csv.DictReader(args.flat.open(encoding="utf-8"))}
    if set(a4) != set(flat):
        raise SystemExit("A4/flat trace identities differ")
    rows = []
    for name in a4:
        row: dict[str, object] = {
            "name": name,
            "workload": a4[name]["workload"],
            "seed": a4[name]["seed"],
            "load": a4[name]["load"],
            "trace_sha256": a4[name]["trace_sha256"],
        }
        for metric in METRICS:
            a4_value = float(a4[name][metric])
            flat_value = float(flat[name][metric])
            row[f"a4_{metric}"] = a4[name][metric]
            row[f"flat_{metric}"] = flat[name][metric]
            row[f"delta_{metric}"] = a4_value - flat_value
        rows.append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"A4_RTL_COMPARISON_PASS runs={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
