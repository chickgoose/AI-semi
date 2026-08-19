#!/usr/bin/env python3
"""Recompute the scalar-free A2/A3 diagnostic Pareto and policy decision."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "redred-diagnostic-candidate-selection-contract-v1"
MAXIMUM = "DIAGNOSTIC_RECOMMENDATION_RELEASE_HOLD"
OBJECTIVES = [
    {"metric": "source_overrun", "direction": "min"},
    {"metric": "fixed_window_events_per_cycle", "direction": "max"},
    {"metric": "occurrence_to_accept_max", "direction": "min"},
    {"metric": "accept_to_retire_max", "direction": "min"},
    {"metric": "setup_wns_ns", "direction": "max"},
    {"metric": "hold_wns_ns", "direction": "max"},
    {"metric": "area_um2", "direction": "min"},
    {"metric": "total_power_mw", "direction": "min"},
]


class ContractError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root is not an object: {path}")
    return value


def safe_path(root: Path, value: Any, where: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContractError(f"unsafe path at {where}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ContractError(f"unsafe path at {where}")
    return root / pure


def validate_contract(contract: dict[str, Any]) -> None:
    expected_keys = {"schema", "contract_id", "inputs", "hard_gates",
                     "pareto_objectives", "policy", "maximum_decision"}
    if set(contract) != expected_keys:
        raise ContractError("contract keys differ")
    if contract["schema"] != SCHEMA or contract["contract_id"] != \
            "REDRED_A2_PRIMARY_A3_EXACT_PREFIX_FALLBACK_DIAGNOSTIC":
        raise ContractError("contract identity differs")
    if contract["pareto_objectives"] != OBJECTIVES:
        raise ContractError("Pareto objectives/directions differ")
    expected_policy = {
        "default": "AGGREGATE_WEIGHTED_PRIMARY_A2_IF_NONDOMINATED",
        "exact_prefix_required": "A3_IF_ALL_A3_GATES_PASS",
        "shared_hold_or_fail": "NO_OFFICIAL_CANDIDATE",
        "scalar_score": "FORBIDDEN",
    }
    if contract["policy"] != expected_policy:
        raise ContractError("selection policy differs")
    if contract["maximum_decision"] != MAXIMUM:
        raise ContractError("contract illegally raises the release ceiling")
    expected_gates = {
        "accepted_event_exact_once": "PASS_REQUIRED",
        "source_single_edge_cdc_rdc": "PASS_REQUIRED",
        "mapped_postroute_structural_cdc_rdc": "PASS_DIAGNOSTIC_REQUIRED",
        "setup_hold_drc_connectivity": "PASS_DIAGNOSTIC_REQUIRED",
        "organizer_constraints": "HOLD",
        "authenticated_controlled_producer": "HOLD",
        "freshness_authority": "HOLD",
    }
    if contract["hard_gates"] != expected_gates:
        raise ContractError("hard-gate policy differs")


def verify_input_pins(root: Path, contract: dict[str, Any]) -> dict[str, dict[str, Path]]:
    if set(contract["inputs"]) != {"digital", "physical"}:
        raise ContractError("input domains differ")
    result: dict[str, dict[str, Path]] = {}
    for domain, entries in contract["inputs"].items():
        if set(entries) != {"contract", "binding", "verifier"}:
            raise ContractError(f"{domain} input inventory differs")
        result[domain] = {}
        for role, entry in entries.items():
            if set(entry) != {"path", "sha256"}:
                raise ContractError(f"{domain}.{role} pin keys differ")
            path = safe_path(root, entry["path"], f"inputs.{domain}.{role}")
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ContractError(f"cannot read pinned input {path}: {exc}") from exc
            if sha256(data) != entry["sha256"]:
                raise ContractError(f"pinned input hash differs: {domain}.{role}")
            result[domain][role] = path
    return result


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def collect_receipts(root: Path, paths: dict[str, dict[str, Path]]) -> tuple[dict[str, Any], dict[str, Any]]:
    digital_module = load_module("redred_selection_digital", paths["digital"]["verifier"])
    physical_module = load_module("redred_selection_physical", paths["physical"]["verifier"])
    try:
        digital = digital_module.verify(root, paths["digital"]["contract"],
                                         paths["digital"]["binding"])
        physical = physical_module.verify(paths["physical"]["contract"])
    except Exception as exc:  # Verifier-specific ContractError types are dynamically loaded.
        raise ContractError(f"upstream verifier failed: {exc}") from exc
    return digital, physical


def build_rows(digital: dict[str, Any], physical: dict[str, Any]) -> dict[str, dict[str, float | int]]:
    if digital.get("digital_rtl_diagnostic_status") != "PASS" \
            or digital.get("accepted_event_exact_once") is not True \
            or digital.get("team_diagnostic_selection_eligible") is not True:
        raise ContractError("digital diagnostic hard gate is not PASS")
    if physical.get("source_cdc_status") != "PASS" \
            or physical.get("mapped_cdc_rdc_diagnostic_status") != "PASS":
        raise ContractError("CDC/RDC diagnostic hard gate is not PASS")
    if digital.get("producer_authenticated") is not False \
            or digital.get("controlled_freshness_verified") is not False \
            or physical.get("producer_authenticated") is not False \
            or physical.get("freshness_verified") is not False:
        raise ContractError("diagnostic evidence illegally claims authority/freshness")
    rows: dict[str, dict[str, float | int]] = {}
    for candidate in ("a2", "a3"):
        d = digital["metrics"][candidate]
        p = physical["candidates"][candidate]["physical"]
        if any(p[key] != 0 for key in ("setup_violations", "hold_violations", "drc_violations",
                                        "antenna_violations", "connectivity_problems",
                                        "pg_connectivity_problems")):
            raise ContractError(f"{candidate} physical hard gate failed")
        row = {
            "source_overrun": d["source_overrun"],
            "fixed_window_events_per_cycle": d["fixed_window_events_per_cycle"],
            "occurrence_to_accept_max": d["occurrence_to_accept_max"],
            "accept_to_retire_max": d["accept_to_retire_max"],
            "setup_wns_ns": p["setup_wns_ns"],
            "hold_wns_ns": p["hold_wns_ns"],
            "area_um2": p["area_um2"],
            "total_power_mw": p["total_power_mw"],
        }
        for key, value in row.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not math.isfinite(value):
                raise ContractError(f"invalid metric {candidate}.{key}")
        rows[candidate.upper()] = row
    return rows


def dominates(left: dict[str, float | int], right: dict[str, float | int],
              objectives: list[dict[str, str]] = OBJECTIVES) -> bool:
    weak = True
    strict = False
    for objective in objectives:
        metric, direction = objective["metric"], objective["direction"]
        a, b = left[metric], right[metric]
        better_or_equal = a <= b if direction == "min" else a >= b
        strictly_better = a < b if direction == "min" else a > b
        weak = weak and better_or_equal
        strict = strict or strictly_better
    return weak and strict


def compute_receipt(root: Path, contract: dict[str, Any],
                    digital: dict[str, Any] | None = None,
                    physical: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_contract(contract)
    paths = verify_input_pins(root, contract)
    if digital is None or physical is None:
        digital, physical = collect_receipts(root, paths)
    rows = build_rows(digital, physical)
    front = [candidate for candidate in ("A2", "A3")
             if not any(other != candidate and dominates(rows[other], rows[candidate])
                        for other in rows)]
    if front != ["A2", "A3"]:
        raise ContractError(f"unexpected diagnostic Pareto front: {front}")
    return {
        "schema": "redred-diagnostic-candidate-selection-receipt-v1",
        "diagnostic_ingest_status": "PASS_SAME_SNAPSHOT_DIAGNOSTIC_ONLY",
        "correctness_status": "PASS_DIAGNOSTIC",
        "physical_status": "PASS_DIAGNOSTIC",
        "rows": rows,
        "diagnostic_pareto_status": "COMPUTED_NONAUTHORITATIVE_SCALAR_FREE",
        "diagnostic_pareto_front": front,
        "a2_dominates_a3": dominates(rows["A2"], rows["A3"]),
        "a3_dominates_a2": dominates(rows["A3"], rows["A2"]),
        "conditional_default_candidate": "A2",
        "conditional_default_reason": "PREDECLARED_AGGREGATE_WEIGHTED_PRIMARY_ON_NONDOMINATED_FRONT",
        "conditional_exact_prefix_candidate": "A3",
        "authoritative_policy_status": "HOLD_MISSING_ORGANIZER_AND_PRODUCER_AUTHORITY",
        "official_selected_candidate": None,
        "selection_authority": False,
        "release_authority": False,
        "official_score_winner": False,
        "maximum_decision": MAXIMUM,
        "decision": "DIAGNOSTIC_A2_RECOMMENDED_A3_EXACT_PREFIX_FALLBACK_RELEASE_HOLD",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        receipt = compute_receipt(root, load_json(Path(__file__).resolve().parent / "contract.json"))
    except (ContractError, OSError) as exc:
        print(f"REDRED_DIAGNOSTIC_CANDIDATE_SELECTION_FAIL: {exc}")
        return 1
    print(json.dumps(receipt, sort_keys=True, indent=2))
    print("REDRED_DIAGNOSTIC_CANDIDATE_SELECTION_PASS team=A2 fallback=A3 official=NONE release=HOLD")
    return 0


if __name__ == "__main__":
    sys.exit(main())

