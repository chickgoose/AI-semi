#!/usr/bin/env python3
"""Candidate-neutral scalar-policy oracle and shared K2 utilities."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


VECTOR_SCHEMA = "a5_k2_vector_bundle_v1"
EVIDENCE_SCHEMA = "a5_k2_candidate_evidence_v1"
RESULT_SCHEMA = "a5_k2_evaluation_v1"
SOURCE_COUNT = 16
RETIRE_LANES = 2
ROW_WHEEL = (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3)


class ContractError(ValueError):
    """An input artifact violates the frozen evaluator contract."""


@dataclass(frozen=True)
class PolicyState:
    wheel_pos: int = 0
    column_rr: tuple[int, int, int, int] = (0, 0, 0, 0)

    def document(self) -> dict[str, Any]:
        return {"wheel_pos": self.wheel_pos, "column_rr": list(self.column_rr)}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True) + "\n").encode("ascii")


def object_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read JSON {path}: {error}") from error


def row_for_source(source: int) -> int:
    if not 0 <= source < SOURCE_COUNT:
        raise ContractError(f"source outside N16 contract: {source}")
    return source // 4


def scalar_select(pending_sources: Iterable[int], state: PolicyState) -> tuple[int, int] | None:
    """Return (source, selected wheel index), without changing state.

    Empty wheel entries are searched but not consumed independently.  The
    state advances only when the selected event commits.
    """
    pending = set(pending_sources)
    if not pending:
        return None
    for wheel_offset in range(len(ROW_WHEEL)):
        wheel_index = (state.wheel_pos + wheel_offset) % len(ROW_WHEEL)
        row = ROW_WHEEL[wheel_index]
        base = row * 4
        for column_offset in range(4):
            column = (state.column_rr[row] + column_offset) % 4
            source = base + column
            if source in pending:
                return source, wheel_index
    raise AssertionError("nonempty N16 pending set produced no scalar winner")


def scalar_commit(state: PolicyState, source: int, wheel_index: int) -> PolicyState:
    row = row_for_source(source)
    if ROW_WHEEL[wheel_index] != row:
        raise ContractError("selected source row does not match selected wheel token")
    columns = list(state.column_rr)
    columns[row] = ((source % 4) + 1) % 4
    return PolicyState((wheel_index + 1) % len(ROW_WHEEL), tuple(columns))


def fold_prefix(pending_sources: Iterable[int], state: PolicyState,
                width: int = RETIRE_LANES) -> tuple[list[int], PolicyState]:
    pending = set(pending_sources)
    grants: list[int] = []
    current = state
    for _ in range(width):
        selected = scalar_select(pending, current)
        if selected is None:
            break
        source, wheel_index = selected
        grants.append(source)
        pending.remove(source)
        current = scalar_commit(current, source, wheel_index)
    return grants, current


def advance_actual(state: PolicyState, source: int) -> PolicyState:
    """Advance state after an observed grant, including a non-oracle mutant."""
    row = row_for_source(source)
    for offset in range(len(ROW_WHEEL)):
        index = (state.wheel_pos + offset) % len(ROW_WHEEL)
        if ROW_WHEEL[index] == row:
            return scalar_commit(state, source, index)
    raise AssertionError("every row occurs in the wheel")


def event_id(run: str, cycle: int, source: int) -> str:
    return f"{run}:c{cycle}:s{source}"


def percentile(values: list[int], percent: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = ((len(ordered) * percent) + 99) // 100 - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def latency_summary(values: list[int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": (sum(values) / len(values)) if values else None,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else None,
    }


def run_sha(run: dict[str, Any]) -> str:
    copy = dict(run)
    copy.pop("run_sha256", None)
    return object_sha256(copy)


def validate_vector_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("schema") != VECTOR_SCHEMA:
        raise ContractError(f"vector schema must be {VECTOR_SCHEMA}")
    if bundle.get("source_count") != SOURCE_COUNT or bundle.get("retire_lanes") != RETIRE_LANES:
        raise ContractError("vectors must use N16/K2")
    policy = bundle.get("oracle_policy")
    if not isinstance(policy, dict) or policy.get("row_wheel") != list(ROW_WHEEL):
        raise ContractError("vector oracle policy differs from frozen row wheel")
    if policy.get("row_for_source") != "source_div_4" or policy.get("column_rule") != "round_robin":
        raise ContractError("vector source/column policy mismatch")
    runs = bundle.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ContractError("vector bundle requires runs")
    names: set[str] = set()
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("name"), str):
            raise ContractError("invalid vector run")
        if run["name"] in names:
            raise ContractError(f"duplicate vector run {run['name']}")
        names.add(run["name"])
        cycles = run.get("cycles")
        if not isinstance(cycles, list) or not cycles:
            raise ContractError(f"{run['name']} has no cycles")
        seen_ids: set[str] = set()
        for index, cycle in enumerate(cycles):
            if not isinstance(cycle, dict) or cycle.get("cycle") != index:
                raise ContractError(f"{run['name']} cycle sequence mismatch at {index}")
            if not isinstance(cycle.get("reset_n"), bool):
                raise ContractError(f"{run['name']} reset_n must be boolean")
            ready = cycle.get("retire_ready")
            if not isinstance(ready, list) or len(ready) != RETIRE_LANES or not all(
                    isinstance(item, bool) for item in ready):
                raise ContractError(f"{run['name']} retire_ready must contain two booleans")
            occurrences = cycle.get("occurrences")
            if not isinstance(occurrences, list):
                raise ContractError(f"{run['name']} occurrences must be an array")
            if not cycle["reset_n"] and occurrences:
                raise ContractError(f"{run['name']} cycle {index}: occurrences during reset are forbidden")
            sources: set[int] = set()
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    raise ContractError(f"{run['name']} malformed occurrence")
                source = occurrence.get("source")
                identifier = occurrence.get("event_id")
                if not isinstance(source, int) or not 0 <= source < SOURCE_COUNT:
                    raise ContractError(f"{run['name']} occurrence source outside N16")
                if source in sources:
                    raise ContractError(f"{run['name']} duplicate source occurrence at cycle {index}")
                if not isinstance(identifier, str) or not identifier or identifier in seen_ids:
                    raise ContractError(f"{run['name']} duplicate/invalid event_id {identifier}")
                sources.add(source)
                seen_ids.add(identifier)
        if run.get("run_sha256") != run_sha(run):
            raise ContractError(f"{run['name']} run SHA mismatch")
    expected = bundle.get("bundle_sha256")
    copy = dict(bundle)
    copy.pop("bundle_sha256", None)
    if expected != object_sha256(copy):
        raise ContractError("vector bundle SHA mismatch")
    return bundle
