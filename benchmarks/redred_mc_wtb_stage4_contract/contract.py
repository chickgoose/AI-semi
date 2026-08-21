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
EXPECTED_CANONICAL_SHA256 = "50201b521af2df69c76566cc7a2685395d38c7cd04e2b0c47025024a01cd574d"
DEFAULT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "MC_WTB_STAGE4_COMPARISON_CONTRACT_20260821.json"
)


_EXPECTED_CONTRACT = {
    "schema": CONTRACT_SCHEMA,
    "status": "frozen_before_arm_scoring",
    "checkpoint_commit": "7791e21a8415b0b5dd2c76cc6ea83698c7427d71",
    "development_only": True,
    "registry": {
        "window_count": 24,
        "query_event_count": 8914,
        "sha256": "19df5788d3300ef9e6169165ed1dc68806a08f4e4af73eb4a52aebc9b642f62f",
        "forbidden_interval_ns": [43320750000, 43322000000],
    },
    "timing": {
        "clock_period_ps": 6500,
        "same_edge_pose_visible_to_event": False,
        "event_lanes": 2,
        "transform_pipeline_cycles": 1,
        "event_record_bits": 102,
        "pose_packet_bits": 192,
        "buffer_entries": 1024,
        "dataset_pose_arrival_assumption": "arrival_equals_recorded_timestamp",
    },
    "arms": {
        "zoh_freshness": {"max_pose_age_ns": 1000000},
        "delayed_exact": {"deadline_ns": 6000000, "ordered": True},
        "causal_cav": {
            "max_horizon_ns": 5000000,
            "max_horizon_pose_intervals": 1,
            "zoh_fallback_max_age_ns": 1000000,
        },
        "supplied_pose_1khz": {
            "cadence_ns": 1000000,
            "commit_delay_cycles": 1,
            "max_pose_age_ns": 1000000,
            "counterfactual_upstream_interface": True,
        },
    },
    "go_to_rtl": {
        "minimum_r_all_fraction": 0.01,
        "minimum_positive_windows": 18,
        "minimum_enable_rate": 0.10,
        "maximum_quality_waste_rate": 0.50,
        "maximum_operational_waste_rate": 0.01,
        "maximum_accepted_event_loss": 0,
        "maximum_added_p99_latency_ns": 1000000,
        "maximum_buffer_entries": 1024,
        "maximum_pose_bandwidth_bits_per_second": 250000,
        "maximum_incremental_state_bits": 131072,
    },
    "score_blind_decision_receipt_required": True,
    "score_all_arms_once": True,
    "holdout_claim_authorized": False,
    "rtl_or_ppa_claim_authorized": False,
    "novelty_claim_authorized": False,
}


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


def _strict_match(actual: Any, expected: Any, where: str = "$") -> None:
    if type(actual) is not type(expected):
        raise ContractError("%s has the wrong JSON type" % where)
    if isinstance(expected, dict):
        actual_keys = set(actual)
        expected_keys = set(expected)
        if actual_keys != expected_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
            raise ContractError(
                "%s keys differ; missing=%r extra=%r" % (where, missing, extra)
            )
        for key in sorted(expected):
            _strict_match(actual[key], expected[key], "%s.%s" % (where, key))
        return
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise ContractError("%s has the wrong list length" % where)
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            _strict_match(actual_item, expected_item, "%s[%d]" % (where, index))
        return
    if actual != expected:
        raise ContractError("%s differs from the frozen value" % where)


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
    _strict_match(document, _EXPECTED_CONTRACT)
    canonical = canonical_json_bytes(document)
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != EXPECTED_CANONICAL_SHA256:
        raise ContractError("comparison contract canonical hash differs")
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
