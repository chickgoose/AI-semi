#!/usr/bin/env python3
"""Count bit transitions in A3 policy/output state as a power proxy."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


STATE_NAMES = ("membrane", "homeostasis", "phase", "retire_valid", "retire_event", "retire_source")


def category(scopes: list[str], reference: str) -> str | None:
    if "native_candidate" not in scopes:
        return None
    base = reference.split()[0]
    if base.startswith("membrane["):
        return "membrane"
    if base in STATE_NAMES:
        return base
    return None


def parse_vcd(path: Path) -> dict[str, int]:
    scopes: list[str] = []
    selected: dict[str, tuple[int, str]] = {}
    previous: dict[str, int] = {}
    toggles = {name: 0 for name in STATE_NAMES}
    var_pattern = re.compile(r"\$var\s+\S+\s+(\d+)\s+(\S+)\s+(.+?)\s+\$end")
    header = True
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if header:
                if line.startswith("$scope"):
                    scopes.append(line.split()[2])
                elif line.startswith("$upscope"):
                    scopes.pop()
                elif line.startswith("$var"):
                    match = var_pattern.fullmatch(line)
                    if match:
                        width, identifier, reference = match.groups()
                        state_category = category(scopes, reference)
                        if state_category is not None:
                            selected[identifier] = (int(width), state_category)
                elif line == "$enddefinitions $end":
                    header = False
                continue

            if not line or line[0] in "#$":
                continue
            if line[0] in "bBrR":
                parts = line[1:].split()
                if len(parts) != 2:
                    continue
                value_text, identifier = parts
            else:
                value_text, identifier = line[0], line[1:]
            if identifier not in selected or any(bit in value_text.lower() for bit in "xz"):
                continue
            width, state_category = selected[identifier]
            base = 2 if set(value_text) <= {"0", "1"} else 16
            value = int(value_text, base) & ((1 << width) - 1)
            if identifier in previous:
                toggles[state_category] += (previous[identifier] ^ value).bit_count()
            previous[identifier] = value
    if not selected:
        raise ValueError(f"no A3 state variables found in {path}")
    return toggles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcd", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cycles", type=int, required=True)
    parser.add_argument("--delivered", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    toggles = parse_vcd(args.vcd)
    total = sum(toggles.values())
    row: dict[str, object] = {
        "name": args.name,
        "cycles": args.cycles,
        "delivered": args.delivered,
        **{f"{name}_toggles": toggles[name] for name in STATE_NAMES},
        "total_state_toggles": total,
        "toggles_per_cycle": total / args.cycles if args.cycles else 0.0,
        "toggles_per_delivered": total / args.delivered if args.delivered else 0.0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
