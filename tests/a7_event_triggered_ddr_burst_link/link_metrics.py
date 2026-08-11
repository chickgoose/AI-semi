#!/usr/bin/env python3
"""Deterministic pin/edge/toggle/capacity proxies for frozen N=16 links."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from typing import Iterable, TextIO


@dataclass(frozen=True)
class LinkMetric:
    name: str
    pins: int
    clock_edges_per_event: int
    mean_total_toggles_per_event: float
    max_events_per_core_cycle: float


def _bit(value: int, index: int) -> int:
    return (value >> index) & 1


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _mean_toggles(kind: str) -> float:
    totals = []
    for previous in range(16):
        for current in range(16):
            if kind == "parallel4":
                data_toggles = _hamming(previous, current)
                clock_edges = 2
            elif kind == "ddr2":
                previous_last_symbol = (previous >> 2) & 0x3
                low_symbol = current & 0x3
                high_symbol = (current >> 2) & 0x3
                data_toggles = _hamming(previous_last_symbol, low_symbol)
                data_toggles += _hamming(low_symbol, high_symbol)
                clock_edges = 2
            elif kind == "serial1":
                bits = [_bit(current, index) for index in range(4)]
                sequence = [_bit(previous, 3), *bits]
                data_toggles = sum(a != b for a, b in zip(sequence, sequence[1:]))
                clock_edges = 4
            else:
                raise ValueError(f"unknown link kind: {kind}")
            totals.append(data_toggles + clock_edges)
    return sum(totals) / len(totals)


def metrics(link_ratio: int) -> list[LinkMetric]:
    if link_ratio <= 0:
        raise ValueError("link_ratio must be positive")
    return [
        LinkMetric("parallel4", 5, 2, _mean_toggles("parallel4"), float(link_ratio)),
        LinkMetric("ddr2", 3, 2, _mean_toggles("ddr2"), float(link_ratio)),
        LinkMetric("serial1", 2, 4, _mean_toggles("serial1"), link_ratio / 2.0),
    ]


def write_csv(rows: Iterable[LinkMetric], stream: TextIO) -> None:
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "link",
            "pins",
            "clock_edges_per_event",
            "mean_total_toggles_per_event",
            "max_logical_events_per_core_cycle_proxy",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.name,
                row.pins,
                row.clock_edges_per_event,
                f"{row.mean_total_toggles_per_event:.3f}",
                f"{row.max_events_per_core_cycle:.3f}",
            ]
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--link-ratio", type=int, required=True)
    args = parser.parse_args()
    write_csv(metrics(args.link_ratio), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
