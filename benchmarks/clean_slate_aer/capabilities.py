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
    "source_count",
    "native_protocol",
    "native_retire_lanes",
    "decision",
    "unsupported_core",
    "unsupported_optional",
    "unsupported_profile",
    "reason",
)


class ContractError(ValueError):
    """Raised for an invalid capability profile or workload contract."""


@dataclass(frozen=True)
class Capability:
    supported: bool
    reason: str


@dataclass(frozen=True)
class SourceCount:
    kind: str
    value: int | None
    minimum: int | None
    maximum: int | None

    def supports(self, requested: int) -> bool:
        if self.kind == "fixed":
            return requested == self.value
        return requested >= int(self.minimum) and (
            self.maximum is None or requested <= self.maximum
        )

    def describe(self) -> str:
        if self.kind == "fixed":
            return f"fixed at {self.value}"
        upper = "unbounded" if self.maximum is None else str(self.maximum)
        return f"parameterized range [{self.minimum}, {upper}]"


@dataclass(frozen=True)
class NativeInterface:
    protocol: str
    source_count: SourceCount
    source_observable: bool
    retire_lanes: int


@dataclass(frozen=True)
class Profile:
    candidate: str
    capabilities: dict[str, Capability]
    native_interface: NativeInterface


@dataclass(frozen=True)
class Workload:
    name: str
    suite: str
    source_count: int
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


def _positive_int(value: object, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(f"{location} must be a positive integer")
    return value


def _load_native_interface(
    value: object, location: str, capabilities: dict[str, Capability]
) -> NativeInterface:
    native = _object(value, location)
    protocol = _nonempty_string(native.get("protocol"), f"{location}.protocol")
    source_observable = native.get("source_observable")
    if not isinstance(source_observable, bool):
        raise ContractError(f"{location}.source_observable must be boolean")
    retire_lanes = _positive_int(native.get("retire_lanes"), f"{location}.retire_lanes")

    count_location = f"{location}.source_count"
    count = _object(native.get("source_count"), count_location)
    kind = _nonempty_string(count.get("kind"), f"{count_location}.kind")
    if kind == "fixed":
        source_count = SourceCount(
            kind=kind,
            value=_positive_int(count.get("value"), f"{count_location}.value"),
            minimum=None,
            maximum=None,
        )
    elif kind == "parameterized":
        minimum = _positive_int(count.get("minimum"), f"{count_location}.minimum")
        maximum_value = count.get("maximum")
        maximum = (
            _positive_int(maximum_value, f"{count_location}.maximum")
            if maximum_value is not None
            else None
        )
        if maximum is not None and maximum < minimum:
            raise ContractError(f"{count_location}.maximum must be >= minimum")
        source_count = SourceCount(
            kind=kind,
            value=None,
            minimum=minimum,
            maximum=maximum,
        )
    else:
        raise ContractError(f"{count_location}.kind must be fixed or parameterized")

    if capabilities["fairness"].supported and not source_observable:
        raise ContractError(
            f"{location}: fairness support requires source_observable=true"
        )
    if capabilities["multi_lane_retirement"].supported != (retire_lanes > 1):
        raise ContractError(
            f"{location}: multi_lane_retirement must match retire_lanes > 1"
        )
    return NativeInterface(
        protocol=protocol,
        source_count=source_count,
        source_observable=source_observable,
        retire_lanes=retire_lanes,
    )


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
    native_interface = _load_native_interface(
        document.get("native_interface"),
        f"{path}.native_interface",
        capabilities,
    )
    return Profile(
        candidate=candidate,
        capabilities=capabilities,
        native_interface=native_interface,
    )


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
        source_count = _positive_int(
            entry.get("source_count"), f"{location}.source_count"
        )
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
                source_count=source_count,
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
        source_count_supported = profile.native_interface.source_count.supports(
            workload.source_count
        )
        unsupported_profile = "" if source_count_supported else "source_count"
        if unsupported_core:
            decision = DECISION_HARD_FAIL
        elif unsupported_optional or unsupported_profile:
            decision = DECISION_SKIP
        else:
            decision = DECISION_RUN

        reasons = [
            f"{name}: {profile.capabilities[name].reason}" for name in unsupported
        ]
        if unsupported_profile:
            reasons.append(
                f"source_count: workload requires {workload.source_count}; "
                f"native profile is "
                f"{profile.native_interface.source_count.describe()}"
            )
        decisions.append(
            {
                "candidate": profile.candidate,
                "workload": workload.name,
                "suite": workload.suite,
                "source_count": str(workload.source_count),
                "native_protocol": profile.native_interface.protocol,
                "native_retire_lanes": str(profile.native_interface.retire_lanes),
                "decision": decision,
                "unsupported_core": ";".join(unsupported_core),
                "unsupported_optional": ";".join(unsupported_optional),
                "unsupported_profile": unsupported_profile,
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
    parser.add_argument(
        "--profile",
        required=True,
        action="append",
        type=Path,
        help="candidate profile; repeat to compare candidates",
    )
    parser.add_argument("--workloads", required=True, type=Path)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("-o", "--output", type=Path, help="output path (default stdout)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profiles = [load_profile(path) for path in args.profile]
        candidates = [profile.candidate for profile in profiles]
        if len(candidates) != len(set(candidates)):
            raise ContractError("duplicate candidate profile")
        workloads = load_workloads(args.workloads)
        decisions = [
            decision
            for profile in profiles
            for decision in evaluate(profile, workloads)
        ]
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
