#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from batched_iwrr_k2 import Scheduler  # noqa: E402


def ptr_pack(pointers: tuple[int, int, int, int]) -> int:
    return sum(value << (2 * row) for row, value in enumerate(pointers))


def xorshift64(value: int) -> int:
    value ^= value << 13
    value ^= value >> 7
    value ^= value << 17
    return value & ((1 << 64) - 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cycles", type=int, default=20000)
    args = parser.parse_args()
    model = Scheduler()
    rng = 0xA2B17E12C0FFEE01
    lines = []
    for cycle in range(args.cycles):
        rng = xorshift64(rng)
        req = (rng >> 11) & 0xFFFF
        ready = bool((rng >> 3) & 1)
        reset = cycle in {0, 1, 97, 4096, 16383}
        if 200 <= cycle < 224:
            req, ready = 0xFFFF, True
        elif 300 <= cycle < 312:
            req, ready = 0xFFFF, False
        elif 500 <= cycle < 516:
            req, ready = 1 << ((cycle - 500) & 15), True
        pre_phase, pre_ptrs = model.phase, model.pointers
        if reset:
            valid = (False, False)
            address = (0, 0)
            bitmap = 0
            model.reset()
        else:
            result = model.cycle(req, ready)
            valid, address, bitmap = result.valid, result.address, result.bitmap
        valid_bits = int(valid[0]) | (int(valid[1]) << 1)
        lines.append(
            f"{int(reset)} {int(ready)} {req:04x} {valid_bits:x} "
            f"{address[0]:x} {address[1]:x} {bitmap:04x} "
            f"{pre_phase:x} {ptr_pack(pre_ptrs):02x}\n"
        )
    args.output.write_text("".join(lines), encoding="ascii")
    print(f"A2_K2_LOCKSTEP_VECTORS_PASS cycles={args.cycles} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
