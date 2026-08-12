#!/usr/bin/env python3
"""Generate deterministic directed lockstep vectors and semantic summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from model import PairedCorticalColumnK2  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mirror(mask: int) -> int:
    result = 0
    for source in range(16):
        if mask & (1 << source):
            row, column = divmod(source, 4)
            result |= 1 << (4 * row + (3 - column))
    return result


def cases() -> dict[str, list[tuple[bool, int, bool]]]:
    reset = [(False, 0, False), (False, 0, True)]
    all_rows = reset + [(True, 0xFFFF, True) for _ in range(60)]
    all_rows += [(True, 0, True) for _ in range(4)]

    sparse = list(reset)
    sparse += [(True, 1 << source, True) for source in (0, 5, 10, 15, 3, 6, 9, 12)]
    sparse += [(True, 0, True) for _ in range(4)]

    hotspot = list(reset)
    hotspot += [(True, 0x00F0, True) for _ in range(12)]
    hotspot += [(True, 0xF00F, True) for _ in range(18)]
    hotspot += [(True, 0xFFFF, True) for _ in range(24)]
    hotspot += [(True, 0, True) for _ in range(8)]

    base_masks = [0x1248, 0x8421, 0x0660, 0x9009, 0x6996, 0x0F0F]
    mirror_case = list(reset)
    for mask in base_masks * 5:
        mirror_case.append((True, mask, True))
        mirror_case.append((True, mirror(mask), True))
    mirror_case += [(True, 0, True) for _ in range(8)]

    stall = list(reset)
    for cycle in range(80):
        ready = (False, False, False, True, False, True, False, True)[cycle % 8]
        stall.append((True, 0xFFFF, ready))
    stall += [(True, 0, True) for _ in range(8)]

    reset_stress = list(reset)
    reset_stress += [(True, 0xFFFF, False) for _ in range(5)]
    reset_stress += [(False, 0, False) for _ in range(3)]
    reset_stress += [(True, 0x8421, True) for _ in range(20)]
    reset_stress += [(True, 0, True) for _ in range(8)]

    reset_live = [
        (False, 0xFFFF, True),
        (False, 0xFFFF, False),
        (False, 0x8421, True),
        (True, 0, True),
        (True, 0xFFFF, True),
        (True, 0, True),
        (True, 0, True),
    ]
    return {
        "all_rows": all_rows,
        "sparse": sparse,
        "hotspot": hotspot,
        "mirror": mirror_case,
        "stall": stall,
        "reset": reset_stress,
        "reset_live": reset_live,
    }


def generate(output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError(f"refusing to reuse output: {output}")
    output.mkdir(parents=True)
    documents: dict[str, object] = {}
    for name, stimuli in cases().items():
        model = PairedCorticalColumnK2()
        lines: list[str] = []
        row_grants = [0, 0, 0, 0]
        accepted = 0
        offered = 0
        for rst_n, valid, ready in stimuli:
            result = model.step(valid, ready, rst_n)
            for source in range(16):
                if result.source_ready & (1 << source):
                    row_grants[source >> 2] += 1
                    accepted += 1
            offered += result.grant_count
            lines.append(
                f"{int(rst_n)} {valid:04x} {int(ready)} {result.source_ready:04x} "
                f"{result.grant_count:x} {result.grant_addr0:x} "
                f"{result.grant_addr1:x} {int(result.drain_idle)}"
            )
        path = output / f"{name}.vectors"
        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        documents[name] = {
            "cycles": len(stimuli),
            "accepted": accepted,
            "offered_lane_cycles": offered,
            "row_grants": row_grants,
            "vector_sha256": sha256(path),
        }
    if documents["all_rows"]["row_grants"] != [10, 50, 50, 10]:
        raise AssertionError(f"persistent weight failure: {documents['all_rows']}")
    report = {"schema": "a4_pcck2_directed_vectors_v1", "cases": documents}
    (output / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = generate(args.output)
    print(f"A4_PCCK2_VECTOR_PASS cases={len(report['cases'])} persistent=10,50,50,10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
