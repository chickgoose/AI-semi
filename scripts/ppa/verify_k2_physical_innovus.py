#!/usr/bin/env python3
"""Fail-closed qualification gate for one W2 Innovus output directory."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Sequence


COMMAND_SENTINEL = b"W2_INNOVUS_COMMANDS_COMPLETE\n"
CLEAN_SENTINEL = b"W2_INNOVUS_FLOW_CLEAN\n"

TIMING_REPORTS = (
    "setup_timing.rpt",
    "hold_timing.rpt",
    "recovery_timing.rpt",
    "removal_timing.rpt",
)
ZERO_COUNT_REPORTS = {
    "check_place_post_place.rpt": "placement_violations",
    "check_place_post_route.rpt": "placement_violations",
    "connectivity.rpt": "connectivity_violations",
    "pg_connectivity.rpt": "pg_connectivity_violations",
    "pg_connectivity_post_route.rpt": "pg_connectivity_violations",
    "drc.rpt": "drc_violations",
    "antenna.rpt": "antenna_violations",
    "check_timing.rpt": "unconstrained_paths",
    "check_design_pre_place.rpt": "unresolved_references",
    "check_design_post_route.rpt": "unresolved_references",
}
OTHER_REPORTS = ("area.rpt", "power.rpt", "route.rpt")
BAD_LOG = re.compile(
    r"(?:^|\s)(?:ERROR|FATAL)(?::|\s)|\*\*(?:ERROR|FATAL):|"
    r"SEG(?:MENTATION)?\s+FAULT|INTERRUPT|W2_INNOVUS_FLOW_FATAL",
    re.IGNORECASE | re.MULTILINE,
)
SLACK = re.compile(
    r"^\s*slack(?:\s*\([^)]*\))?\s*[:=]?\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COUNT_LINE = re.compile(r"^([a-z][a-z0-9_]*)=([0-9]+)$")
NATIVE_COUNT_PATTERNS = {
    "placement_violations": (
        r"(?:total\s+(?:number\s+of\s+)?)?(?:place(?:ment)?)[^\n:]*"
        r"violations?\s*[:=]\s*([0-9]+)",
    ),
    "connectivity_violations": (
        r"(?:total\s+(?:number\s+of\s+)?)?connectivity\s+violations?\s*[:=]\s*([0-9]+)",
        r"(?:total\s+(?:number\s+of\s+)?)?connectivity\s+errors?\s*[:=]\s*([0-9]+)",
    ),
    "pg_connectivity_violations": (
        r"(?:total\s+(?:number\s+of\s+)?)?(?:special|pg)\s+connectivity\s+violations?\s*[:=]\s*([0-9]+)",
        r"(?:total\s+(?:number\s+of\s+)?)?(?:special|pg)\s+connectivity\s+errors?\s*[:=]\s*([0-9]+)",
    ),
    "drc_violations": (
        r"(?:total\s+(?:number\s+of\s+)?)?(?:drc\s+)?violations?\s*[:=]\s*([0-9]+)",
    ),
    "antenna_violations": (
        r"(?:total\s+(?:number\s+of\s+)?)?(?:nets?\s+with\s+)?antenna\s+violations?\s*[:=]\s*([0-9]+)",
    ),
    "unconstrained_paths": (
        r"(?:total\s+(?:number\s+of\s+)?)?unconstrained\s+paths?\s*[:=]\s*([0-9]+)",
    ),
    "unresolved_references": (
        r"(?:total\s+(?:number\s+of\s+)?)?unresolved\s+references?\s*[:=]\s*([0-9]+)",
    ),
}


class QualificationError(ValueError):
    pass


def _regular(path: Path) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise QualificationError(f"missing artifact: {path}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise QualificationError(f"artifact is not a regular non-symlink file: {path}")
    data = path.read_bytes()
    if not data:
        raise QualificationError(f"artifact is empty: {path}")
    return data


def _text(path: Path) -> str:
    try:
        return _regular(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QualificationError(f"artifact is not UTF-8: {path}") from exc


def _require_nonempty_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise QualificationError(f"missing artifact directory: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise QualificationError(f"artifact directory is invalid: {path}")
    if not any(path.iterdir()):
        raise QualificationError(f"artifact directory is empty: {path}")


def _timing_slack(path: Path) -> float:
    values = []
    for token in SLACK.findall(_text(path)):
        value = float(token)
        if not math.isfinite(value):
            raise QualificationError(f"non-finite timing slack: {path}")
        values.append(value)
    if not values:
        raise QualificationError(f"timing report has no recognized slack path: {path}")
    return min(values)


def _canonical_counts(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in _text(path).splitlines():
        match = COUNT_LINE.fullmatch(line)
        if match:
            key, token = match.groups()
            if key in result:
                raise QualificationError(f"duplicate canonical count {key}: {path}")
            result[key] = int(token)
    return result


def _require_zero(path: Path, key: str) -> None:
    text = _text(path)
    counts = _canonical_counts(path)
    values = [counts[key]] if key in counts else []
    for pattern in NATIVE_COUNT_PATTERNS[key]:
        values.extend(
            int(token) for token in re.findall(pattern, text, re.IGNORECASE)
        )
    if not values:
        raise QualificationError(f"report lacks recognized {key} count: {path}")
    if any(value != 0 for value in values):
        raise QualificationError(f"{key} is nonzero ({max(values)}): {path}")


def validate(run_dir: Path, top: str) -> dict[str, float]:
    if not top or "/" in top or top in {".", ".."}:
        raise QualificationError("top must be one simple module name")
    marker = run_dir / "status" / "COMMANDS_COMPLETE"
    if _regular(marker) != COMMAND_SENTINEL:
        raise QualificationError("Innovus command-completion sentinel mismatch")
    if (run_dir / "status" / "COMMANDS_FAILED").exists():
        raise QualificationError("Innovus failure sentinel is present")
    if (run_dir / "status" / "FLOW_CLEAN").exists():
        raise QualificationError("FLOW_CLEAN already exists")

    log = _text(run_dir / "tool.log")
    if BAD_LOG.search(log):
        raise QualificationError("Innovus log contains an error/interruption marker")

    reports = run_dir / "reports"
    slacks = {
        name.removesuffix("_timing.rpt"): _timing_slack(reports / name)
        for name in TIMING_REPORTS
    }
    for check, value in slacks.items():
        if value < 0.0:
            raise QualificationError(f"{check} WNS is negative ({value})")
    for name, key in ZERO_COUNT_REPORTS.items():
        _require_zero(reports / name, key)
    for name in OTHER_REPORTS:
        _regular(reports / name)
    route_text = _text(reports / "route.rpt")
    route = _canonical_counts(reports / "route.rpt")
    native_route_complete = re.search(
        r"detailed\s+route(?:ing)?\s+(?:is\s+)?completed", route_text,
        re.IGNORECASE,
    )
    if route.get("detailed_route_completed") != 1 and not native_route_complete:
        raise QualificationError("detailed route is not canonically complete")

    _regular(run_dir / "netlist" / f"{top}.postroute.v")
    _require_nonempty_directory(run_dir / "database")
    return slacks


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--top", required=True)
    parser.add_argument("--write-clean-marker", action="store_true")
    args = parser.parse_args(argv)
    try:
        slacks = validate(args.run_dir.resolve(), args.top)
        if args.write_clean_marker:
            _write_exclusive(args.run_dir / "status" / "FLOW_CLEAN", CLEAN_SENTINEL)
    except (OSError, QualificationError) as exc:
        print(f"W2_INNOVUS_NOT_CLEAN: {exc}", file=sys.stderr)
        return 2
    values = " ".join(f"{name}_wns={value:g}" for name, value in sorted(slacks.items()))
    print(f"W2_INNOVUS_FLOW_CLEAN {values}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
