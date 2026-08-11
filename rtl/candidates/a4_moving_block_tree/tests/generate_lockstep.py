#!/usr/bin/env python3
"""Generate deterministic adversarial vectors with Python-model expectations."""

from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from model import MovingBlockTreeModel  # noqa: E402


def arrivals(cycle: int) -> list[int]:
    if cycle < 2:
        return []
    if cycle < 66:  # repeated B16/global fan-in
        return list(range(16)) if (cycle - 2) % 8 == 0 else []
    if cycle < 130:  # branch-merge collisions
        return [0, 1, 8, 9] if cycle % 2 == 0 else [2, 3, 10, 11]
    if cycle < 226:  # long-stall injection shock
        return list(range(16)) if cycle % 3 == 0 else []
    if cycle < 354:  # recovery, no reset
        return [cycle % 16] if cycle % 11 == 0 else []
    if cycle < 610:  # mixed dense/sparse phases, no reset
        phase = (cycle - 354) // 32
        if phase % 2 == 0:
            return [source for source in range(16) if (source + cycle) % 3 == 0]
        return [cycle % 16] if cycle % 7 == 0 else []
    return []


def sink_ready(cycle: int) -> bool:
    if 146 <= cycle < 202:
        return False
    if 370 <= cycle < 410:
        return cycle % 9 == 0
    return (cycle % 13) not in (5, 6, 7)


def generate(path: pathlib.Path, cycles: int = 760, max_advance: int = 2) -> None:
    model = MovingBlockTreeModel(16, max_advance)
    pending: list[int | None] = [None] * 16
    sequence = [0] * 16
    lines: list[str] = []
    for cycle in range(cycles):
        rst_n = cycle >= 2
        for source in arrivals(cycle):
            if pending[source] is None:
                sequence[source] += 1
                pending[source] = (source << 24) | sequence[source]
        valid = [item is not None for item in pending]
        payload = [item or 0 for item in pending]
        ready = sink_ready(cycle)
        result = model.step(valid, payload, ready, rst_n=rst_n)
        valid_mask = sum(int(bit) << index for index, bit in enumerate(valid))
        ready_mask = sum(
            int(bit) << index for index, bit in enumerate(result.source_ready)
        )
        fields = [str(int(rst_n)), f"{valid_mask:04x}", str(int(ready))]
        fields.extend(f"{value:08x}" for value in payload)
        fields.extend(
            [
                f"{ready_mask:04x}",
                str(int(result.retire_valid)),
                f"{result.retire_source:x}",
                f"{result.retire_payload:08x}",
            ]
        )
        lines.append(" ".join(fields))
        for source, accepted in enumerate(result.source_ready):
            if accepted:
                pending[source] = None
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--cycles", type=int, default=760)
    parser.add_argument("--max-advance", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    generate(args.output, args.cycles, args.max_advance)


if __name__ == "__main__":
    main()
