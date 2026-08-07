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


def parse_vcd(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    scopes: list[str] = []
    selected: dict[str, tuple[int, str]] = {}
    clock_identifier: str | None = None
    enables: dict[str, tuple[int, str]] = {}
    previous: dict[str, int] = {}
    toggles = {name: 0 for name in STATE_NAMES}
    enable_values: dict[str, int] = {}
    clock_value = 0
    enable_counts = {
        "clock_samples": 0,
        "membrane_enabled_bit_cycles": 0,
        "homeostasis_enable_cycles": 0,
        "phase_enable_cycles": 0,
    }
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
                        base = reference.split()[0]
                        if base == "clk":
                            clock_identifier = identifier
                        elif "native_candidate" in scopes and base in {
                            "membrane_write_enable",
                            "homeostasis_write_enable",
                            "phase_write_enable",
                        }:
                            enables[identifier] = (int(width), base)
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

    # Re-scan the small set of clock/enable signals.  Keeping this separate
    # avoids coupling state-toggle accounting to VCD declaration order.
    if clock_identifier is not None and enables:
        with path.open(encoding="utf-8", errors="replace") as stream:
            header = True
            for raw_line in stream:
                line = raw_line.strip()
                if header:
                    if line == "$enddefinitions $end":
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
                if any(bit in value_text.lower() for bit in "xz"):
                    continue
                if identifier in enables:
                    width, enable_name = enables[identifier]
                    enable_values[enable_name] = int(value_text, 2) & ((1 << width) - 1)
                elif identifier == clock_identifier:
                    new_clock = int(value_text, 2)
                    if clock_value == 0 and new_clock == 1:
                        enable_counts["clock_samples"] += 1
                        enable_counts["membrane_enabled_bit_cycles"] += enable_values.get(
                            "membrane_write_enable", 0
                        ).bit_count()
                        enable_counts["homeostasis_enable_cycles"] += enable_values.get(
                            "homeostasis_write_enable", 0
                        )
                        enable_counts["phase_enable_cycles"] += enable_values.get(
                            "phase_write_enable", 0
                        )
                    clock_value = new_clock
    if not selected:
        raise ValueError(f"no A3 state variables found in {path}")
    return toggles, enable_counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vcd", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cycles", type=int, required=True)
    parser.add_argument("--delivered", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    toggles, enable_counts = parse_vcd(args.vcd)
    total = sum(toggles.values())
    row: dict[str, object] = {
        "name": args.name,
        "cycles": args.cycles,
        "delivered": args.delivered,
        **{f"{name}_toggles": toggles[name] for name in STATE_NAMES},
        **enable_counts,
        "membrane_write_enable_ratio": (
            enable_counts["membrane_enabled_bit_cycles"] /
            (16 * enable_counts["clock_samples"])
            if enable_counts["clock_samples"] else 0.0
        ),
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
