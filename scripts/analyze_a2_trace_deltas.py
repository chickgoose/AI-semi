#!/usr/bin/env python3
"""Compare A2 and reference aggregate CSVs without copying raw results."""

import argparse
import csv
from pathlib import Path


FIELDS = (
    "test",
    "source_overrun",
    "avg_throughput",
    "p95_e2e_latency_cycles",
    "p99_e2e_latency_cycles",
    "worst_request_wait",
)


def load(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = {row["test"]: row for row in csv.DictReader(handle)}
    if not rows:
        raise ValueError(f"no aggregate rows in {path}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a2", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    a2_rows = load(args.a2)
    ref_rows = load(args.reference)
    if set(a2_rows) != set(ref_rows):
        raise ValueError("A2/reference trace sets differ")

    writer = csv.writer(__import__("sys").stdout, lineterminator="\n")
    writer.writerow(
        (
            "trace",
            "overrun_delta_a2_minus_ref",
            "throughput_delta_a2_minus_ref",
            "p95_delta_cycles_a2_minus_ref",
            "p99_delta_cycles_a2_minus_ref",
            "a2_worst_wait",
            "ref_worst_wait",
            "classification",
        )
    )
    for name in sorted(a2_rows):
        a2 = a2_rows[name]
        ref = ref_rows[name]
        overrun_delta = int(a2["source_overrun"]) - int(ref["source_overrun"])
        throughput_delta = float(a2["avg_throughput"]) - float(ref["avg_throughput"])
        p95_delta = float(a2["p95_e2e_latency_cycles"]) - float(
            ref["p95_e2e_latency_cycles"]
        )
        p99_delta = float(a2["p99_e2e_latency_cycles"]) - float(
            ref["p99_e2e_latency_cycles"]
        )
        if p95_delta < 0 and overrun_delta <= 0:
            classification = "LATENCY_GAIN"
        elif p95_delta > 0 and overrun_delta < 0:
            classification = "CAPACITY_GAIN_TAIL_LOSS"
        elif p95_delta > 0:
            classification = "TAIL_LOSS"
        elif overrun_delta < 0 or throughput_delta > 0:
            classification = "CAPACITY_GAIN"
        else:
            classification = "TIE"
        writer.writerow(
            (
                name,
                overrun_delta,
                f"{throughput_delta:.6f}",
                f"{p95_delta:.0f}",
                f"{p99_delta:.0f}",
                a2["worst_request_wait"],
                ref["worst_request_wait"],
                classification,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
