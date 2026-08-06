#!/usr/bin/env python3
"""Validate native AER candidate capabilities before running workloads."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TextIO


CORE_CAPABILITIES = (
    "sink_always_ready",
    "address_event_correctness",
    "occurrence_to_delivery_latency",
    "loss_duplicate_phantom",
    "fairness",
)

OPTIONAL_CAPABILITIES = (
    "output_backpressure",
    "polarity_event_type",
    "multi_lane_retirement",
)

KNOWN_CAPABILITIES = CORE_CAPABILITIES + OPTIONAL_CAPABILITIES

DECISION_RUN = "RUN"
DECISION_SKIP = "SKIP_UNSUPPORTED"
DECISION_HARD_FAIL = "HARD_FAIL_CORE_UNSUPPORTED"

OUTPUT_COLUMNS = (
    "candidate",
    "workload",
    "suite",
    "decision",
    "unsupported_core",
    "unsupported_optional",
    "reason",
)


class ContractError(ValueError):
    """Raised for an invalid capability profile or workload contract."""


@dataclass(frozen=True)
class Capability:
    supported: bool
    reason: str


@dataclass(frozen=True)
class Profile:
    candidate: str
    capabilities: dict[str, Capability]


@dataclass(frozen=True)
class Workload:
    name: str
    suite: str
    required_capabilities: tuple[str, ...]


def _read_json(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path}: invalid JSON: {exc}") from exc


def _object(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(f"{location} must be an object")
    return value


def _nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{location} must be a non-empty string")
    return value.strip()


def _schema_version(document: dict[str, object], location: str) -> None:
    if document.get("schema_version") != 1:
        raise ContractError(f"{location}.schema_version must be 1")


def load_profile(path: Path) -> Profile:
    document = _object(_read_json(path), str(path))
    _schema_version(document, str(path))
    candidate = _nonempty_string(document.get("candidate"), f"{path}.candidate")
    raw_capabilities = _object(document.get("capabilities"), f"{path}.capabilities")

    missing = [name for name in KNOWN_CAPABILITIES if name not in raw_capabilities]
    unknown = sorted(set(raw_capabilities) - set(KNOWN_CAPABILITIES))
    if missing:
        raise ContractError(f"{path}: undeclared capabilities: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{path}: unknown capabilities: {', '.join(unknown)}")

    capabilities: dict[str, Capability] = {}
    for name in KNOWN_CAPABILITIES:
        location = f"{path}.capabilities.{name}"
        entry = _object(raw_capabilities[name], location)
        supported = entry.get("supported")
        if not isinstance(supported, bool):
            raise ContractError(f"{location}.supported must be boolean")
        reason_value = entry.get("reason", "")
        if not isinstance(reason_value, str):
            raise ContractError(f"{location}.reason must be a string")
        reason = reason_value.strip()
        if not supported and not reason:
            raise ContractError(
                f"{location}.reason is required when capability is unsupported"
            )
        capabilities[name] = Capability(supported=supported, reason=reason)
    return Profile(candidate=candidate, capabilities=capabilities)


def load_workloads(path: Path) -> list[Workload]:
    document = _object(_read_json(path), str(path))
    _schema_version(document, str(path))
    raw_workloads = document.get("workloads")
    if not isinstance(raw_workloads, list) or not raw_workloads:
        raise ContractError(f"{path}.workloads must be a non-empty array")

    workloads: list[Workload] = []
    seen_names: set[str] = set()
    for index, raw_workload in enumerate(raw_workloads):
        location = f"{path}.workloads[{index}]"
        entry = _object(raw_workload, location)
        name = _nonempty_string(entry.get("name"), f"{location}.name")
        if name in seen_names:
            raise ContractError(f"{path}: duplicate workload name: {name}")
        seen_names.add(name)
        suite = _nonempty_string(entry.get("suite"), f"{location}.suite")
        if suite not in {"core", "optional"}:
            raise ContractError(f"{location}.suite must be core or optional")
        raw_required = entry.get("required_capabilities")
        if not isinstance(raw_required, list) or not raw_required:
            raise ContractError(
                f"{location}.required_capabilities must be a non-empty array"
            )
        required = tuple(
            _nonempty_string(value, f"{location}.required_capabilities")
            for value in raw_required
        )
        if len(required) != len(set(required)):
            raise ContractError(f"{location}: duplicate required capability")
        unknown = sorted(set(required) - set(KNOWN_CAPABILITIES))
        if unknown:
            raise ContractError(
                f"{location}: unknown capabilities: {', '.join(unknown)}"
            )
        optional_required = set(required) & set(OPTIONAL_CAPABILITIES)
        if suite == "core" and optional_required:
            raise ContractError(
                f"{location}: core workload requires optional capability: "
                f"{', '.join(sorted(optional_required))}"
            )
        if suite == "optional" and not optional_required:
            raise ContractError(
                f"{location}: optional workload must require an optional capability"
            )
        workloads.append(
            Workload(
                name=name,
                suite=suite,
                required_capabilities=required,
            )
        )
    covered_core = {
        capability
        for workload in workloads
        if workload.suite == "core"
        for capability in workload.required_capabilities
        if capability in CORE_CAPABILITIES
    }
    missing_core = [name for name in CORE_CAPABILITIES if name not in covered_core]
    if missing_core:
        raise ContractError(
            f"{path}: core suite does not cover: {', '.join(missing_core)}"
        )
    return workloads


def evaluate(profile: Profile, workloads: Sequence[Workload]) -> list[dict[str, str]]:
    decisions: list[dict[str, str]] = []
    for workload in workloads:
        unsupported = [
            name
            for name in workload.required_capabilities
            if not profile.capabilities[name].supported
        ]
        unsupported_core = [name for name in unsupported if name in CORE_CAPABILITIES]
        unsupported_optional = [
            name for name in unsupported if name in OPTIONAL_CAPABILITIES
        ]
        if unsupported_core:
            decision = DECISION_HARD_FAIL
        elif unsupported_optional:
            decision = DECISION_SKIP
        else:
            decision = DECISION_RUN

        reasons = [
            f"{name}: {profile.capabilities[name].reason}" for name in unsupported
        ]
        decisions.append(
            {
                "candidate": profile.candidate,
                "workload": workload.name,
                "suite": workload.suite,
                "decision": decision,
                "unsupported_core": ";".join(unsupported_core),
                "unsupported_optional": ";".join(unsupported_optional),
                "reason": " | ".join(reasons),
            }
        )
    return decisions


def write_csv(decisions: Sequence[dict[str, str]], stream: TextIO) -> None:
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
    writer.writeheader()
    writer.writerows(decisions)


def write_json(decisions: Sequence[dict[str, str]], stream: TextIO) -> None:
    counts = {
        decision: sum(row["decision"] == decision for row in decisions)
        for decision in (DECISION_RUN, DECISION_SKIP, DECISION_HARD_FAIL)
    }
    payload = {"summary": counts, "workloads": list(decisions)}
    json.dump(payload, stream, indent=2, sort_keys=True)
    stream.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--workloads", required=True, type=Path)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("-o", "--output", type=Path, help="output path (default stdout)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        decisions = evaluate(load_profile(args.profile), load_workloads(args.workloads))
    except ContractError as exc:
        print(f"capability contract error: {exc}", file=sys.stderr)
        return 3

    try:
        stream = (
            args.output.open("w", newline="", encoding="utf-8")
            if args.output
            else sys.stdout
        )
    except OSError as exc:
        print(f"cannot open output: {exc}", file=sys.stderr)
        return 3
    try:
        try:
            if args.format == "json":
                write_json(decisions, stream)
            else:
                write_csv(decisions, stream)
        except OSError as exc:
            print(f"cannot write output: {exc}", file=sys.stderr)
            return 3
    finally:
        if args.output:
            stream.close()
    return (
        2
        if any(row["decision"] == DECISION_HARD_FAIL for row in decisions)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
