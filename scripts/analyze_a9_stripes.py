#!/usr/bin/env python3
"""Report fixed A9 source-to-stripe load without remapping it."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="directory of *.events.csv")
    parser.add_argument("--lanes", type=int, required=True)
    parser.add_argument("--sources", type=int, default=16)
    parser.add_argument("-o", "--output", type=Path)
    return parser.parse_args()


def coefficient_of_variation(values: list[int]) -> float:
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def main() -> int:
    args = parse_args()
    if args.lanes <= 0 or args.sources % args.lanes:
        raise SystemExit("lanes must be positive and evenly divide sources")
    depth = args.sources // args.lanes
    rows: list[dict[str, object]] = []

    for event_path in sorted(args.results.glob("*.events.csv")):
        offered = [0] * args.lanes
        delivered = [0] * args.lanes
        overrun = [0] * args.lanes
        with event_path.open(newline="", encoding="utf-8") as stream:
            for event in csv.DictReader(stream):
                source = int(event["logical_source"])
                lane = source // depth
                offered[lane] += 1
                if event["event_state"] == "delivered":
                    delivered[lane] += 1
                elif event["event_state"] == "source_overrun":
                    overrun[lane] += 1

        nonzero = [value for value in offered if value]
        max_min = (max(nonzero) / min(nonzero)) if nonzero else 1.0
        for lane in range(args.lanes):
            rows.append(
                {
                    "trace": event_path.name.removesuffix(".events.csv"),
                    "lane": lane,
                    "offered": offered[lane],
                    "delivered": delivered[lane],
                    "source_overrun": overrun[lane],
                    "delivery_ratio": (
                        delivered[lane] / offered[lane] if offered[lane] else 1.0
                    ),
                    "trace_offered_max_min_nonzero": max_min,
                    "trace_offered_cv": coefficient_of_variation(offered),
                }
            )

    fieldnames = list(rows[0]) if rows else [
        "trace", "lane", "offered", "delivered", "source_overrun",
        "delivery_ratio", "trace_offered_max_min_nonzero", "trace_offered_cv",
    ]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        stream = args.output.open("w", newline="", encoding="utf-8")
    else:
        import sys
        stream = sys.stdout
    try:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
