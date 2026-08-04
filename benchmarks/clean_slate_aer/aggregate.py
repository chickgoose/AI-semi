#!/usr/bin/env python3
"""Aggregate architecture-neutral clean-slate AER benchmark CSV files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TextIO


REQUIRED_COLUMNS = (
    "test",
    "seed",
    "load_pct",
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "total_cycles",
    "avg_e2e_latency",
    "max_e2e_latency",
    "avg_internal_latency",
    "max_internal_latency",
    "throughput",
    "fairness",
    "max_request_wait",
    "avg_timing_error",
    "max_timing_error",
)

INTEGER_COLUMNS = (
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "total_cycles",
)

FLOAT_COLUMNS = tuple(
    column
    for column in REQUIRED_COLUMNS
    if column not in {"test", "seed", *INTEGER_COLUMNS}
)

SUMMARY_COLUMNS = (
    "test",
    "load_pct",
    "runs",
    "seed_count",
    "stim_cycles",
    "generated",
    "source_overrun",
    "accepted",
    "delivered",
    "errors",
    "delivery_ratio",
    "acceptance_ratio",
    "overrun_ratio",
    "end_to_end_ratio",
    "avg_total_cycles",
    "avg_throughput",
    "worst_throughput",
    "avg_e2e_latency",
    "worst_e2e_latency",
    "avg_internal_latency",
    "worst_internal_latency",
    "avg_request_wait",
    "worst_request_wait",
    "avg_timing_error",
    "worst_timing_error",
    "avg_fairness",
    "worst_fairness",
    "performance_state",
    "knee_load_pct",
    "tail_latency_factor",
    "tail_wait_factor",
    "tail_timing_error_factor",
    "tail_degraded",
    "correctness_issues",
)


class InputError(ValueError):
    """Raised for malformed benchmark input."""


@dataclass(frozen=True)
class Run:
    test: str
    seed: str
    load_pct: float
    stim_cycles: int
    generated: int
    source_overrun: int
    accepted: int
    delivered: int
    errors: int
    total_cycles: int
    avg_e2e_latency: float
    max_e2e_latency: float
    avg_internal_latency: float
    max_internal_latency: float
    throughput: float
    fairness: float
    max_request_wait: float
    avg_timing_error: float
    max_timing_error: float


def _parse_nonnegative_int(value: str, column: str, location: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be an integer") from exc
    if parsed < 0:
        raise InputError(f"{location}: {column} must be nonnegative")
    return parsed


def _parse_finite_float(value: str, column: str, location: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{location}: {column} must be numeric") from exc
    if not math.isfinite(parsed):
        raise InputError(f"{location}: {column} must be finite")
    if parsed < 0:
        raise InputError(f"{location}: {column} must be nonnegative")
    return parsed


def read_runs(paths: Iterable[Path]) -> list[Run]:
    runs: list[Run] = []
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
                test = (row.get("test") or "").strip()
                seed = (row.get("seed") or "").strip()
                if not test:
                    raise InputError(f"{location}: test must not be empty")
                if not seed:
                    raise InputError(f"{location}: seed must not be empty")
                values: dict[str, object] = {"test": test, "seed": seed}
                for column in INTEGER_COLUMNS:
                    values[column] = _parse_nonnegative_int(row[column], column, location)
                for column in FLOAT_COLUMNS:
                    values[column] = _parse_finite_float(row[column], column, location)
                runs.append(Run(**values))
    if not runs:
        raise InputError("no benchmark rows found")
    return runs


def _safe_ratio(numerator: float, denominator: float, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def _weighted_mean(rows: Sequence[Run], attribute: str) -> float:
    weight = sum(row.delivered for row in rows)
    if weight:
        return sum(getattr(row, attribute) * row.delivered for row in rows) / weight
    return statistics.fmean(getattr(row, attribute) for row in rows)


def _correctness_issues(rows: Sequence[Run]) -> list[str]:
    issues: set[str] = set()
    if any(row.errors for row in rows):
        issues.add("scoreboard_errors")
    if any(row.source_overrun > row.generated for row in rows):
        issues.add("overrun_exceeds_generated")
    if any(row.accepted > row.generated - row.source_overrun for row in rows):
        issues.add("accepted_exceeds_retained_arrivals")
    if any(row.delivered > row.accepted for row in rows):
        issues.add("delivered_exceeds_accepted")
    if any(row.delivered < row.accepted for row in rows):
        issues.add("accepted_delivery_mismatch")
    return sorted(issues)


def _aggregate_group(test: str, load_pct: float, rows: Sequence[Run]) -> dict[str, object]:
    generated = sum(row.generated for row in rows)
    overrun = sum(row.source_overrun for row in rows)
    accepted = sum(row.accepted for row in rows)
    delivered = sum(row.delivered for row in rows)
    retained = generated - overrun
    issues = _correctness_issues(rows)
    return {
        "test": test,
        "load_pct": load_pct,
        "runs": len(rows),
        "seed_count": len({row.seed for row in rows}),
        "stim_cycles": sum(row.stim_cycles for row in rows),
        "generated": generated,
        "source_overrun": overrun,
        "accepted": accepted,
        "delivered": delivered,
        "errors": sum(row.errors for row in rows),
        # Delivery isolates post-accept transport correctness. Acceptance uses
        # only arrivals retained by the source queue. Overrun is reported
        # separately so saturation is not mislabeled as functional failure.
        "delivery_ratio": _safe_ratio(delivered, accepted),
        "acceptance_ratio": _safe_ratio(accepted, retained),
        "overrun_ratio": _safe_ratio(overrun, generated, empty=0.0),
        "end_to_end_ratio": _safe_ratio(delivered, generated),
        "avg_total_cycles": statistics.fmean(row.total_cycles for row in rows),
        "avg_throughput": statistics.fmean(row.throughput for row in rows),
        "worst_throughput": min(row.throughput for row in rows),
        "avg_e2e_latency": _weighted_mean(rows, "avg_e2e_latency"),
        "worst_e2e_latency": max(row.max_e2e_latency for row in rows),
        "avg_internal_latency": _weighted_mean(rows, "avg_internal_latency"),
        "worst_internal_latency": max(row.max_internal_latency for row in rows),
        "avg_request_wait": statistics.fmean(row.max_request_wait for row in rows),
        "worst_request_wait": max(row.max_request_wait for row in rows),
        "avg_timing_error": _weighted_mean(rows, "avg_timing_error"),
        "worst_timing_error": max(row.max_timing_error for row in rows),
        "avg_fairness": statistics.fmean(row.fairness for row in rows),
        "worst_fairness": min(row.fairness for row in rows),
        "performance_state": "CORRECTNESS_FAIL" if issues else "PASS",
        "knee_load_pct": None,
        "tail_latency_factor": None,
        "tail_wait_factor": None,
        "tail_timing_error_factor": None,
        "tail_degraded": False,
        "correctness_issues": ";".join(issues),
    }


def _factor(value: float, reference: float) -> float | None:
    if reference > 0:
        return value / reference
    return None


def aggregate_runs(
    runs: Sequence[Run],
    *,
    acceptance_floor: float = 0.99,
    overrun_ceiling: float = 0.01,
    tail_factor: float = 1.5,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    grouped: dict[tuple[str, float], list[Run]] = {}
    for run in runs:
        grouped.setdefault((run.test, run.load_pct), []).append(run)
    summaries = [
        _aggregate_group(test, load_pct, rows)
        for (test, load_pct), rows in sorted(grouped.items())
    ]

    test_reports: list[dict[str, object]] = []
    tests = sorted({str(summary["test"]) for summary in summaries})
    for test in tests:
        sweep = [summary for summary in summaries if summary["test"] == test]
        knee_index: int | None = None
        for index, summary in enumerate(sweep):
            if summary["performance_state"] == "CORRECTNESS_FAIL":
                continue
            if (
                float(summary["acceptance_ratio"]) < acceptance_floor
                or float(summary["overrun_ratio"]) > overrun_ceiling
            ):
                knee_index = index
                break

        knee_load = sweep[knee_index]["load_pct"] if knee_index is not None else None
        reference = None
        if knee_index is not None:
            reference = next(
                (
                    item
                    for item in reversed(sweep[:knee_index])
                    if item["performance_state"] != "CORRECTNESS_FAIL"
                ),
                None,
            )
        for index, summary in enumerate(sweep):
            summary["knee_load_pct"] = knee_load
            if summary["performance_state"] == "CORRECTNESS_FAIL":
                continue
            if knee_index is not None and index >= knee_index:
                summary["performance_state"] = "SATURATED"
                if reference is not None:
                    summary["tail_latency_factor"] = _factor(
                        float(summary["avg_e2e_latency"]),
                        float(reference["avg_e2e_latency"]),
                    )
                    summary["tail_wait_factor"] = _factor(
                        float(summary["avg_request_wait"]),
                        float(reference["avg_request_wait"]),
                    )
                    summary["tail_timing_error_factor"] = _factor(
                        float(summary["avg_timing_error"]),
                        float(reference["avg_timing_error"]),
                    )
                    factors = (
                        summary["tail_latency_factor"],
                        summary["tail_wait_factor"],
                        summary["tail_timing_error_factor"],
                    )
                    summary["tail_degraded"] = any(
                        factor is not None and float(factor) >= tail_factor
                        for factor in factors
                    )

        test_reports.append(
            {
                "test": test,
                "knee_load_pct": knee_load,
                "correctness": (
                    "FAIL"
                    if any(item["performance_state"] == "CORRECTNESS_FAIL" for item in sweep)
                    else "PASS"
                ),
                "tail_degraded": any(bool(item["tail_degraded"]) for item in sweep),
                "loads": len(sweep),
                "runs": sum(int(item["runs"]) for item in sweep),
                "seeds": max(int(item["seed_count"]) for item in sweep),
            }
        )
    return summaries, test_reports


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.9g}"
    return value


def write_csv(summaries: Sequence[dict[str, object]], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for summary in summaries:
        writer.writerow({key: _csv_value(summary.get(key)) for key in SUMMARY_COLUMNS})


def write_json(
    summaries: Sequence[dict[str, object]],
    tests: Sequence[dict[str, object]],
    stream: TextIO,
    *,
    acceptance_floor: float,
    overrun_ceiling: float,
    tail_factor: float,
) -> None:
    payload = {
        "policy": {
            "acceptance_floor": acceptance_floor,
            "overrun_ceiling": overrun_ceiling,
            "tail_factor": tail_factor,
        },
        "tests": list(tests),
        "loads": list(summaries),
    }
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")


def _unit_interval(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="input benchmark CSV files")
    parser.add_argument("-o", "--output", type=Path, help="output path (default: stdout)")
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--acceptance-floor", type=_unit_interval, default=0.99)
    parser.add_argument("--overrun-ceiling", type=_unit_interval, default=0.01)
    parser.add_argument("--tail-factor", type=_positive_float, default=1.5)
    parser.add_argument(
        "--fail-on-correctness",
        action="store_true",
        help="return status 2 if any grouped load has correctness errors",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        runs = read_runs(args.inputs)
        summaries, tests = aggregate_runs(
            runs,
            acceptance_floor=args.acceptance_floor,
            overrun_ceiling=args.overrun_ceiling,
            tail_factor=args.tail_factor,
        )
    except InputError as exc:
        parser.error(str(exc))

    if args.output:
        stream = args.output.open("w", newline="", encoding="utf-8")
    else:
        stream = sys.stdout
    try:
        if args.format == "json":
            write_json(
                summaries,
                tests,
                stream,
                acceptance_floor=args.acceptance_floor,
                overrun_ceiling=args.overrun_ceiling,
                tail_factor=args.tail_factor,
            )
        else:
            write_csv(summaries, stream)
    finally:
        if args.output:
            stream.close()

    if args.fail_on_correctness and any(test["correctness"] == "FAIL" for test in tests):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
