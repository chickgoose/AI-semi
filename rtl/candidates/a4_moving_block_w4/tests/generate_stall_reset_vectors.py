#!/usr/bin/env python3
"""Generate bounded stalled/reset adversarial vectors for W4 exact lockstep."""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys


HERE = pathlib.Path(__file__).resolve().parent
W4 = HERE.parent
ROOT = W4.parents[2]
W3 = ROOT / "rtl/candidates/a4_moving_block_tree"
sys.path.insert(0, str(W3))

from model import MovingBlockTreeModel  # noqa: E402


SCENARIOS = {
    "long_root_stall": 16,
    "no_reset_shock": 16,
    "random_ready_midstream_reset": 16,
    "bounded_n64": 64,
}


def reset_active(name: str, cycle: int) -> bool:
    if cycle < 2:
        return True
    if name == "random_ready_midstream_reset" and 257 <= cycle < 260:
        return True
    if name == "bounded_n64" and 333 <= cycle < 335:
        return True
    return False


def arrivals(name: str, n: int, cycle: int, seed: int) -> list[int]:
    if name == "long_root_stall":
        if cycle < 150 and cycle % 3 == 0:
            return list(range(n))
        return [cycle % n] if 190 <= cycle < 290 and cycle % 7 == 0 else []
    if name == "no_reset_shock":
        if cycle < 80:
            return [cycle % n] if cycle % 5 == 0 else []
        if cycle < 250:
            return list(range(n)) if cycle % 4 == 0 else []
        if cycle < 410:
            return [source for source in range(n) if (source + cycle) % 4 == 0]
        return [cycle % n] if cycle < 500 and cycle % 13 == 0 else []
    if name == "random_ready_midstream_reset":
        if cycle >= 580:
            return []
        rng = random.Random(seed ^ (cycle * 0x9E3779B1))
        return [source for source in range(n) if rng.random() < 0.23]
    if name == "bounded_n64":
        if cycle >= 620:
            return []
        if cycle % 11 == 0:
            return list(range(n))
        rng = random.Random(seed ^ (cycle * 0x45D9F3B))
        return [source for source in range(n) if rng.random() < 0.075]
    raise ValueError(name)


def sink_ready(name: str, cycle: int, seed: int) -> bool:
    if name == "long_root_stall":
        return not (24 <= cycle < 184)
    if name == "no_reset_shock":
        if 118 <= cycle < 188:
            return False
        return cycle % 17 not in (4, 5, 6)
    if name == "random_ready_midstream_reset":
        if 92 <= cycle < 132 or 401 <= cycle < 438:
            return False
        return random.Random(seed + cycle * 131).random() < 0.63
    if name == "bounded_n64":
        if 104 <= cycle < 196 or 470 <= cycle < 510:
            return False
        return random.Random(seed + cycle * 313).random() < 0.71
    raise ValueError(name)


def stimulus_end(name: str) -> int:
    return {
        "long_root_stall": 290,
        "no_reset_shock": 500,
        "random_ready_midstream_reset": 580,
        "bounded_n64": 620,
    }[name]


def generate(name: str, output: pathlib.Path, seed: int = 44004) -> dict[str, int | str]:
    n = SCENARIOS[name]
    model = MovingBlockTreeModel(n, 2)
    pending: list[int | None] = [None] * n
    sequence = [0] * n
    accepted_queue: list[list[int]] = [[] for _ in range(n)]
    accepted = 0
    retired = 0
    reset_discarded = 0
    reset_cycles = 0
    max_outstanding = 0
    held_stall = 0
    max_held_stall = 0
    lines: list[str] = []
    idle_after_drain = False
    mask_digits = (n + 3) // 4

    for cycle in range(stimulus_end(name) + 5000):
        rst_n = not reset_active(name, cycle)
        if not rst_n:
            reset_cycles += 1
            reset_discarded += sum(len(queue) for queue in accepted_queue)
            accepted_queue = [[] for _ in range(n)]
            pending = [None] * n
        else:
            for source in arrivals(name, n, cycle, seed):
                if pending[source] is None:
                    sequence[source] += 1
                    pending[source] = (source << 24) | sequence[source]

        valid = [item is not None for item in pending]
        payload = [item if item is not None else 0 for item in pending]
        ready = True if cycle >= stimulus_end(name) else sink_ready(name, cycle, seed)
        result = model.step(valid, payload, ready, rst_n=rst_n)
        valid_mask = sum(int(bit) << source for source, bit in enumerate(valid))
        ready_mask = sum(
            int(bit) << source for source, bit in enumerate(result.source_ready)
        )
        fields = [str(int(rst_n)), f"{valid_mask:0{mask_digits}x}", str(int(ready))]
        fields.extend(f"{value:08x}" for value in payload)
        fields.extend(
            [
                f"{ready_mask:0{mask_digits}x}",
                str(int(result.retire_valid)),
                f"{result.retire_source:x}",
                f"{result.retire_payload:08x}",
            ]
        )
        lines.append(" ".join(fields))

        if rst_n:
            if result.retired is not None:
                source = result.retired.source
                if not accepted_queue[source]:
                    raise AssertionError("phantom/duplicate retire in generator")
                expected = accepted_queue[source].pop(0)
                if result.retired.payload != expected:
                    raise AssertionError("source order failure in generator")
                retired += 1
            for source, did_accept in enumerate(result.source_ready):
                if did_accept:
                    accepted_queue[source].append(payload[source])
                    pending[source] = None
                    accepted += 1
        if result.retire_valid and not ready and rst_n:
            held_stall += 1
            max_held_stall = max(max_held_stall, held_stall)
        else:
            held_stall = 0
        max_outstanding = max(
            max_outstanding, sum(len(queue) for queue in accepted_queue)
        )

        drained = (
            cycle >= stimulus_end(name)
            and not any(item is not None for item in pending)
            and model.occupancy() == 0
            and not result.retire_valid
        )
        if drained:
            idle_after_drain = True
            break
    else:
        raise AssertionError(f"{name}: bounded drain timeout")

    if not idle_after_drain or any(accepted_queue):
        raise AssertionError(f"{name}: conservation/drain failure")
    if accepted != retired + reset_discarded:
        raise AssertionError(f"{name}: accepted/retired/reset conservation failure")
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {
        "name": name,
        "sources": n,
        "seed": seed,
        "cycles": len(lines),
        "accepted": accepted,
        "retired": retired,
        "reset_discarded": reset_discarded,
        "reset_cycles": reset_cycles,
        "max_outstanding": max_outstanding,
        "max_continuous_valid_root_stall": max_held_stall,
        "drained": "PASS",
        "conservation_and_source_order": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--index", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() or args.index.exists():
        raise SystemExit("output collision")
    args.output_dir.mkdir(parents=True)
    records = []
    for name in SCENARIOS:
        vector = args.output_dir / f"{name}.vectors.txt"
        record = generate(name, vector)
        record["vector_file"] = vector.name
        records.append(record)
    args.index.parent.mkdir(parents=True, exist_ok=True)
    with args.index.open("x", encoding="utf-8") as stream:
        json.dump({"schema_version": 1, "scenarios": records}, stream, indent=2)
        stream.write("\n")
    print("A4_W4_STALL_RESET_VECTOR_PASS scenarios=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
