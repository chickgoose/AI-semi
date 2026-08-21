"""Frozen, score-blind Stage-4 comparison-contract validation.

This module reads protocol and registry metadata only.  It does not load event
arms, losses, scores, or holdout results.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


class ContractError(ValueError):
    """The frozen comparison contract or registry is invalid."""


CONTRACT_SCHEMA = "redred.mc_wtb.stage4_comparison_contract/v1"
EXPECTED_CANONICAL_SHA256 = "d145ef342654069c442361f386d6b60fe2abff36d8fb7fa655dc1c0066921eba"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT_PATH = (
    _REPOSITORY_ROOT
    / "docs"
    / "MC_WTB_STAGE4_COMPARISON_CONTRACT_20260821.json"
)


def _validate_json_domain(value: Any, where: str = "$") -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError("%s contains a non-finite number" % where)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_domain(item, "%s[%d]" % (where, index))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ContractError("%s contains a non-string object key" % where)
            _validate_json_domain(item, "%s.%s" % (where, key))
        return
    raise ContractError("%s contains a non-JSON value" % where)


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic ASCII JSON with sorted keys and one final newline."""

    _validate_json_domain(value)
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("value is not canonical-JSON serializable") from exc
    return (payload + "\n").encode("ascii")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _object_without_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON object key: %s" % key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ContractError("non-finite JSON number: %s" % value)


@dataclass(frozen=True)
class ComparisonContract:
    source_path: Path
    canonical_document: bytes
    canonical_sha256: str

    def as_dict(self) -> Dict[str, Any]:
        return json.loads(self.canonical_document.decode("ascii"))

    @property
    def registry(self) -> Mapping[str, Any]:
        return self.as_dict()["registry"]

    @property
    def timing(self) -> Mapping[str, Any]:
        return self.as_dict()["timing"]

    @property
    def arms(self) -> Mapping[str, Any]:
        return self.as_dict()["arms"]


@dataclass(frozen=True)
class RegistryValidation:
    window_count: int
    canonical_sha256: str
    forbidden_interval_ns: Tuple[int, int]


def load_comparison_contract(path: Path = DEFAULT_CONTRACT_PATH) -> ComparisonContract:
    """Load and validate the exact frozen Stage-4 comparison contract."""

    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ContractError("cannot read comparison contract: %s" % source) from exc
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractError("comparison contract is not strict UTF-8") from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except ContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError("comparison contract is not valid JSON") from exc
    _validate_json_domain(document)
    if not isinstance(document, dict) or document.get("schema") != CONTRACT_SCHEMA:
        raise ContractError("comparison contract schema differs")
    canonical = canonical_json_bytes(document)
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != EXPECTED_CANONICAL_SHA256:
        raise ContractError("comparison contract canonical hash differs")
    normative_relative = document.get("normative_markdown")
    normative_digest = document.get("normative_markdown_sha256")
    if normative_relative != "docs/MC_WTB_STAGE4_COMPARISON_CONTRACT_20260821.md":
        raise ContractError("normative Markdown path differs")
    if type(normative_digest) is not str or len(normative_digest) != 64:
        raise ContractError("normative Markdown hash is invalid")
    normative_path = _REPOSITORY_ROOT / normative_relative
    try:
        actual_normative_digest = hashlib.sha256(normative_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ContractError("cannot read normative Markdown") from exc
    if actual_normative_digest != normative_digest:
        raise ContractError("normative Markdown hash differs")
    return ComparisonContract(source, canonical, digest)


_REGISTRY_KEYS = {
    "window_id",
    "warmup_start_ns_inclusive",
    "query_start_ns_inclusive",
    "query_end_ns_exclusive",
}


def _strict_nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError("%s must be a non-negative integer" % where)
    return value


def validate_registry(
    contract: ComparisonContract, rows: Sequence[Mapping[str, Any]]
) -> RegistryValidation:
    """Validate registry identity, ordering, intervals, and blacklist exclusion."""

    if not isinstance(contract, ComparisonContract):
        raise ContractError("contract must be a validated ComparisonContract")
    registry_contract = contract.registry
    expected_count = registry_contract["window_count"]
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise ContractError("registry must be an ordered sequence")
    if len(rows) != expected_count:
        raise ContractError("registry window count differs from frozen contract")
    forbidden_values = registry_contract["forbidden_interval_ns"]
    forbidden = (int(forbidden_values[0]), int(forbidden_values[1]))
    if forbidden[0] >= forbidden[1]:
        raise ContractError("forbidden interval is invalid")

    normalized = []  # type: List[Dict[str, Any]]
    seen_ids = set()
    previous_end = None
    for index, row in enumerate(rows):
        where = "registry[%d]" % index
        if not isinstance(row, Mapping):
            raise ContractError("%s must be an object" % where)
        if set(row) != _REGISTRY_KEYS:
            raise ContractError("%s has unexpected fields" % where)
        window_id = row["window_id"]
        if type(window_id) is not str or not window_id:
            raise ContractError("%s.window_id must be a non-empty string" % where)
        if window_id in seen_ids:
            raise ContractError("registry window IDs are duplicated")
        seen_ids.add(window_id)
        warmup_start = _strict_nonnegative_int(
            row["warmup_start_ns_inclusive"], where + ".warmup_start_ns_inclusive"
        )
        query_start = _strict_nonnegative_int(
            row["query_start_ns_inclusive"], where + ".query_start_ns_inclusive"
        )
        query_end = _strict_nonnegative_int(
            row["query_end_ns_exclusive"], where + ".query_end_ns_exclusive"
        )
        if not warmup_start < query_start < query_end:
            raise ContractError("%s interval is invalid" % where)
        if previous_end is not None and warmup_start < previous_end:
            raise ContractError("registry windows overlap or are reordered")
        previous_end = query_end
        if not (query_end <= forbidden[0] or warmup_start >= forbidden[1]):
            raise ContractError("registry overlaps the forbidden interval")
        normalized.append(
            {
                "window_id": window_id,
                "warmup_start_ns_inclusive": warmup_start,
                "query_start_ns_inclusive": query_start,
                "query_end_ns_exclusive": query_end,
            }
        )

    digest = canonical_sha256(normalized)
    if digest != registry_contract["sha256"]:
        raise ContractError("registry canonical hash differs from frozen contract")
    return RegistryValidation(len(normalized), digest, forbidden)


def validate_existing_registry(contract: ComparisonContract) -> RegistryValidation:
    """Validate the existing development registry without loading event data."""

    from benchmarks.redred_mc_wtb_causal_reference.development import (
        CONSUMED_BLACKLIST,
        window_registry,
    )

    forbidden = tuple(contract.registry["forbidden_interval_ns"])
    if tuple(CONSUMED_BLACKLIST) != forbidden:
        raise ContractError("existing registry blacklist differs from frozen contract")
    return validate_registry(contract, window_registry())
