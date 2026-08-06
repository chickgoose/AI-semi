#!/usr/bin/env python3
"""Compute qualified post-route Fmax brackets without architecture assumptions."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TextIO


REQUIRED_COLUMNS = (
    "candidate",
    "period_ns",
    "synthesis_mode",
    "corner",
    "setup_wns_ns",
    "hold_wns_ns",
    "route_ok",
    "unconstrained_paths",
)

OUTPUT_COLUMNS = (
    "candidate",
    "synthesis_mode",
    "corner",
    "status",
    "tested_points",
    "qualified_passes",
    "demonstrated_period_ns",
    "demonstrated_fmax_mhz",
    "first_fail_period_ns",
    "first_fail_fmax_mhz",
    "fmax_bracket_mhz",
    "monotonic",
    "demonstrated_setup_wns_ns",
    "demonstrated_hold_wns_ns",
    "demonstrated_drc",
    "demonstrated_antenna",
)


class InputError(ValueError):
    """Raised when a physical-result CSV cannot be interpreted safely."""


@dataclass(frozen=True)
class Point:
    candidate: str
    period_ns: float
    synthesis_mode: str
    corner: str
    setup_wns_ns: float
    hold_wns_ns: float
    route_ok: bool
    unconstrained_paths: int
    drc_violations: int | None = None
    antenna_violations: int | None = None

    @property
    def fmax_mhz(self) -> float:
        return 1000.0 / self.period_ns

    @property
    def qualified_pass(self) -> bool:
        return (
            self.setup_wns_ns >= 0.0
            and self.hold_wns_ns >= 0.0
            and self.route_ok
            and self.unconstrained_paths == 0
        )


def _finite_float(value: str, column: str, location: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be numeric") from exc
    if not math.isfinite(parsed):
        raise InputError(f"{location}: {column} must be finite")
    return parsed


def _nonnegative_int(value: str, column: str, location: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be an integer") from exc
    if parsed < 0:
        raise InputError(f"{location}: {column} must be nonnegative")
    return parsed


def _optional_count(row: dict[str, str], column: str, location: str) -> int | None:
    value = (row.get(column) or "").strip()
    return _nonnegative_int(value, column, location) if value else None


def _boolean(value: str | None, column: str, location: str) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"1", "true", "yes", "pass"}:
        return True
    if normalized in {"0", "false", "no", "fail"}:
        return False
    raise InputError(f"{location}: {column} must be true/false")


def read_points(paths: Iterable[Path]) -> list[Point]:
    points: list[Point] = []
    for path in paths:
        try:
            stream = path.open(newline="", encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot read {path}: {exc}") from exc
        with stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or ())
            missing = [column for column in REQUIRED_COLUMNS if column not in columns]
            if missing:
                raise InputError(f"{path}: missing columns: {', '.join(missing)}")
            for line_number, row in enumerate(reader, start=2):
                location = f"{path}:{line_number}"
                labels = {
                    column: (row.get(column) or "").strip()
                    for column in ("candidate", "synthesis_mode", "corner")
                }
                for column, value in labels.items():
                    if not value:
                        raise InputError(f"{location}: {column} must not be empty")
                period = _finite_float(row["period_ns"], "period_ns", location)
                if period <= 0.0:
                    raise InputError(f"{location}: period_ns must be positive")
                points.append(
                    Point(
                        **labels,
                        period_ns=period,
                        setup_wns_ns=_finite_float(
                            row["setup_wns_ns"], "setup_wns_ns", location
                        ),
                        hold_wns_ns=_finite_float(
                            row["hold_wns_ns"], "hold_wns_ns", location
                        ),
                        route_ok=_boolean(row["route_ok"], "route_ok", location),
                        unconstrained_paths=_nonnegative_int(
                            row["unconstrained_paths"],
                            "unconstrained_paths",
                            location,
                        ),
                        drc_violations=_optional_count(
                            row, "drc_violations", location
                        ),
                        antenna_violations=_optional_count(
                            row, "antenna_violations", location
                        ),
                    )
                )
    if not points:
        raise InputError("no physical-result rows found")
    return points


def _physical_check(value: int | None) -> str:
    if value is None:
        return "NOT_REPORTED"
    return "CLEAN" if value == 0 else f"VIOLATIONS:{value}"


def _format_frequency(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def summarize(points: Sequence[Point]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[Point]] = {}
    for point in points:
        key = (point.candidate, point.synthesis_mode, point.corner)
        grouped.setdefault(key, []).append(point)

    results: list[dict[str, object]] = []
    for key, group in sorted(grouped.items()):
        periods = [point.period_ns for point in group]
        if len(periods) != len(set(periods)):
            raise InputError(
                "duplicate period in group "
                f"candidate={key[0]}, synthesis_mode={key[1]}, corner={key[2]}"
            )

        ordered = sorted(group, key=lambda point: point.fmax_mhz)
        passes = [point for point in ordered if point.qualified_pass]
        demonstrated = max(passes, key=lambda point: point.fmax_mhz) if passes else None
        higher_fails = (
            [
                point
                for point in ordered
                if not point.qualified_pass
                and demonstrated is not None
                and point.fmax_mhz > demonstrated.fmax_mhz
            ]
            if demonstrated is not None
            else []
        )
        first_fail = (
            min(higher_fails, key=lambda point: point.fmax_mhz)
            if higher_fails
            else None
        )
        monotonic = not any(
            (not lower.qualified_pass)
            and upper.qualified_pass
            and lower.fmax_mhz < upper.fmax_mhz
            for lower in ordered
            for upper in ordered
        )

        if not monotonic:
            status = "NON_MONOTONIC"
        elif demonstrated is None:
            status = "NO_QUALIFIED_PASS"
        elif first_fail is None:
            status = "LOWER_BOUND_ONLY"
        else:
            status = "BRACKETED"

        bracket = ""
        if demonstrated is not None:
            lower = _format_frequency(demonstrated.fmax_mhz)
            bracket = (
                f"[{lower}, {_format_frequency(first_fail.fmax_mhz)})"
                if first_fail is not None
                else f"[{lower}, unbounded)"
            )

        results.append(
            {
                "candidate": key[0],
                "synthesis_mode": key[1],
                "corner": key[2],
                "status": status,
                "tested_points": len(group),
                "qualified_passes": len(passes),
                "demonstrated_period_ns": (
                    demonstrated.period_ns if demonstrated else None
                ),
                "demonstrated_fmax_mhz": (
                    demonstrated.fmax_mhz if demonstrated else None
                ),
                "first_fail_period_ns": first_fail.period_ns if first_fail else None,
                "first_fail_fmax_mhz": first_fail.fmax_mhz if first_fail else None,
                "fmax_bracket_mhz": bracket,
                "monotonic": monotonic,
                "demonstrated_setup_wns_ns": (
                    demonstrated.setup_wns_ns if demonstrated else None
                ),
                "demonstrated_hold_wns_ns": (
                    demonstrated.hold_wns_ns if demonstrated else None
                ),
                "demonstrated_drc": (
                    _physical_check(demonstrated.drc_violations)
                    if demonstrated
                    else "NOT_APPLICABLE"
                ),
                "demonstrated_antenna": (
                    _physical_check(demonstrated.antenna_violations)
                    if demonstrated
                    else "NOT_APPLICABLE"
                ),
            }
        )
    return results


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.9g}"
    return value


def write_csv(results: Sequence[dict[str, object]], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    for result in results:
        writer.writerow({key: _csv_value(result.get(key)) for key in OUTPUT_COLUMNS})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="physical-result CSV")
    parser.add_argument("-o", "--output", type=Path, help="output path (default stdout)")
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        results = summarize(read_points(args.inputs))
    except InputError as exc:
        parser.error(str(exc))

    stream = (
        args.output.open("w", newline="", encoding="utf-8")
        if args.output
        else sys.stdout
    )
    try:
        if args.format == "json":
            json.dump(results, stream, indent=2, sort_keys=True)
            stream.write("\n")
        else:
            write_csv(results, stream)
    finally:
        if args.output:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
