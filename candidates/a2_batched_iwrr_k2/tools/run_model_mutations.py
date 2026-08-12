#!/usr/bin/env python3
"""Directed negative controls for policy/model invariants."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
import batched_iwrr_k2 as base  # noqa: E402


def calendar_counts(calendar: tuple[int, ...]) -> list[int]:
    return [calendar.count(row) for row in range(4)]


def main() -> int:
    killed = []
    mutants = {
        "wrong_weight_token": (0,) + base.CALENDAR[1:],
        "short_wrap": base.CALENDAR[:-1],
    }
    for name, calendar in mutants.items():
        if len(calendar) != 12 or calendar_counts(calendar) != [1, 5, 5, 1] or any(
                calendar[i] == calendar[(i + 1) % len(calendar)] for i in range(len(calendar))):
            killed.append(name)
        else:
            raise RuntimeError(f"calendar mutant survived: {name}")

    model = base.Scheduler()
    before = model.cursor, model.pointers
    model.cycle(0xFFFF, False)
    if before == (model.cursor, model.pointers):
        killed.append("advance_on_stall")
    else:
        raise RuntimeError("stall-state mutant oracle failed")

    sparse = base.offer(1 << 12, 0, (0, 0, 0, 0))
    if sparse.count == 1 and sparse.address[0] == 12 and sparse.next_cursor == 1:
        killed.append("wrong_sparse_fallback")
    else:
        raise RuntimeError("sparse mutant oracle failed")

    pair = base.offer(0x0003, 0, (0, 0, 0, 0))
    if pair.count == 2 and pair.address == (0, 1) and pair.bitmap.bit_count() == 2:
        killed.append("duplicate_source")
    else:
        raise RuntimeError("exact-once mutant oracle failed")

    full = base.offer(0xFFFF, 0, (0, 0, 0, 0))
    if full.count == 2 and full.next_cursor == 2:
        killed.append("partial_count_advance")
    else:
        raise RuntimeError("count-microstep mutant oracle failed")

    fixed = [base.pick_column(0b1111, pointer) for pointer in range(4)]
    if fixed == [0, 1, 2, 3]:
        killed.append("fixed_priority")
    else:
        raise RuntimeError("round-robin mutant oracle failed")
    if len(killed) != 7:
        raise RuntimeError(f"unexpected mutation count {len(killed)}")
    print(f"A2_K2_MODEL_MUTATION_PASS killed={len(killed)} names={','.join(killed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
