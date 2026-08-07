#!/usr/bin/env python3
"""Emit auditable N=16/64 structural proxies for A4 and flat RR."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def a4_row(n: int) -> dict[str, int | str]:
    source_width = math.ceil(math.log2(n))
    levels = round(math.log(n, 4))
    nodes = (n - 1) // 3
    edge_distance = sum((n // (4**level)) * (2**level) for level in range(levels))
    channel_bits = 16 + source_width + 8 + 2
    return {
        "candidate": "a4_quadtree",
        "sources": n,
        "source_width": source_width,
        "quadtree_levels": levels,
        "registered_stages": levels,
        "merge_nodes": nodes,
        "state_bits": nodes * (16 + source_width + 8 + 3),
        "merge_fanin": 4,
        "balanced_mux_depth_per_stage": 2,
        "longest_local_wire_grid": 2 ** (levels - 1),
        "control_wire_bit_grid": edge_distance * 2,
        "full_channel_bit_grid": edge_distance * channel_bits,
    }


def flat_row(n: int) -> dict[str, int | str]:
    source_width = math.ceil(math.log2(n))
    side = math.isqrt(n)
    average_span = side // 2
    edge_distance = n * average_span
    return {
        "candidate": "flat_one_slot_rr",
        "sources": n,
        "source_width": source_width,
        "quadtree_levels": 0,
        "registered_stages": 1,
        "merge_nodes": 1,
        "state_bits": 16 + source_width + 1 + source_width,
        "merge_fanin": n,
        "balanced_mux_depth_per_stage": source_width,
        "longest_local_wire_grid": side - 1,
        "control_wire_bit_grid": edge_distance * 2,
        "full_channel_bit_grid": edge_distance * (16 + source_width + 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [row for n in (16, 64) for row in (a4_row(n), flat_row(n))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"A4_SCALING_PASS rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
