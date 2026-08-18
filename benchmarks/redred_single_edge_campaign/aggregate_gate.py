#!/usr/bin/env python3
"""Aggregate separately verified REDRED normalized campaign views.

This is a campaign decision gate, not a release gate.  It deliberately emits
no pooled synthetic/public metric and counts every public retiming series as
one public dataset family.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


VIEW_SCHEMA = "redred_single_edge_campaign_normalized_view_v1"
RESULT_SCHEMA = "redred_single_edge_campaign_aggregate_result_v1"
CAMPAIGN_ID = "redred-a2-a3-single-edge-campaign-native-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
CLAIMS_FALSE = {"official": False, "physical": False, "power": False, "release": False}
SLOT_IDENTITIES = {
    "synthetic_v2": {
        "evidence_status": "PASS",
        "source_class": "TEAM_DEFINED_SYNTHETIC",
        "canonical_redred_traffic": True,
        "unit_kind": "SYNTHETIC_TRACE_CAMPAIGN",
    },
    "public_v2": {
        "evidence_status": "PUBLIC_PROJECTED_EXTENSION",
        "source_class": "PUBLIC_PROJECTED_EXTENSION",
        "canonical_redred_traffic": False,
        "unit_kind": "PUBLIC_DATASET_RETIMING_FAMILY",
    },
}


class AggregateGateError(RuntimeError):
    """An input view is malformed, contradictory, or evidence-expanding."""


def strict_equal(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(strict_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AggregateGateError(f"{label} must be an object")
    if set(value) != keys:
        raise AggregateGateError(
            f"{label} keys differ: missing={sorted(keys-set(value))} "
            f"extra={sorted(set(value)-keys)}"
        )
    return value


def load_json(data: bytes, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AggregateGateError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise AggregateGateError(f"{label} contains non-standard JSON constant: {value}")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AggregateGateError(f"cannot decode {label}: {error}") from error


def stable_file(path: Path, label: str) -> tuple[Path, bytes, tuple[int, int, int, int, int]]:
    if ".." in path.parts:
        raise AggregateGateError(f"{label} aliases through '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise AggregateGateError(f"{label} traverses a symlink")
    try:
        resolved = absolute.resolve(strict=True)
        before = resolved.stat()
    except OSError as error:
        raise AggregateGateError(f"{label} is missing") from error
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise AggregateGateError(f"{label} must be one unaliased regular file")
    descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        data = b""
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            data += block
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(opened, key) or
           getattr(opened, key) != getattr(after, key) for key in fields):
        raise AggregateGateError(f"{label} changed while read")
    return resolved, data, tuple(getattr(after, key) for key in fields)


def recheck_file(path: Path, identity: tuple[int, int, int, int, int], label: str) -> None:
    try:
        current = path.stat()
    except OSError as error:
        raise AggregateGateError(f"{label} changed after validation") from error
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if path.is_symlink() or tuple(getattr(current, key) for key in fields) != identity:
        raise AggregateGateError(f"{label} changed after validation")


def sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise AggregateGateError(f"{label} must be lowercase SHA-256")
    return value


def tokens(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise AggregateGateError(f"{label} must be a {'possibly empty ' if allow_empty else ''}list")
    if any(not isinstance(item, str) or not SAFE_TOKEN.fullmatch(item) for item in value):
        raise AggregateGateError(f"{label} contains an unsafe token")
    if len(value) != len(set(value)):
        raise AggregateGateError(f"{label} contains duplicates")
    return value


def validate_candidate(value: Any, candidate: str, slot: str) -> dict[str, Any]:
    row = exact(value, {"role", "semantic_class", "gate_status", "failure_scope", "reason_codes"},
                f"{slot}.candidates.{candidate}")
    expected_role = "PRIMARY" if candidate == "A2" else "SEMANTIC_FALLBACK"
    expected_semantics = (
        "AGGREGATE_WEIGHTED_PERFORMANCE" if candidate == "A2" else "EXACT_SCALAR_PREFIX"
    )
    if row["role"] != expected_role or row["semantic_class"] != expected_semantics:
        raise AggregateGateError(f"{slot}.{candidate} role/semantics differ")
    if row["gate_status"] not in ("PASS", "FAIL", "HOLD"):
        raise AggregateGateError(f"{slot}.{candidate} gate_status differs")
    reasons = tokens(row["reason_codes"], f"{slot}.{candidate}.reason_codes", allow_empty=True)
    expected_scope = {
        "PASS": "NONE", "FAIL": "CANDIDATE_SPECIFIC", "HOLD": "UNRESOLVED",
    }[row["gate_status"]]
    if row["failure_scope"] != expected_scope:
        raise AggregateGateError(f"{slot}.{candidate} failure scope is not fail-closed")
    if (row["gate_status"] == "PASS") != (not reasons):
        raise AggregateGateError(f"{slot}.{candidate} reason code cardinality differs")
    return row


def validate_view(value: Any, slot: str) -> dict[str, Any]:
    view = exact(value, {
        "schema", "slot", "verification", "classification", "campaign_units",
        "shared_gates", "candidates", "claims",
    }, f"{slot} normalized view")
    if view["schema"] != VIEW_SCHEMA or view["slot"] != slot:
        raise AggregateGateError(f"{slot} normalized view identity differs")
    verification = exact(view["verification"], {
        "status", "separately_verified", "adapter_id", "adapter_sha256",
        "source_result_sha256", "source_publication_sha256",
    }, f"{slot}.verification")
    if verification["status"] != "PASS" or verification["separately_verified"] is not True:
        raise AggregateGateError(f"{slot} was not separately verified")
    if not isinstance(verification["adapter_id"], str) or not SAFE_TOKEN.fullmatch(
            verification["adapter_id"]):
        raise AggregateGateError(f"{slot}.verification.adapter_id is unsafe")
    for key in ("adapter_sha256", "source_result_sha256", "source_publication_sha256"):
        sha(verification[key], f"{slot}.verification.{key}")

    classification = exact(view["classification"], {
        "evidence_status", "source_class", "canonical_redred_traffic",
        "official_contest_traffic", "p6_evidence_used",
    }, f"{slot}.classification")
    expected = SLOT_IDENTITIES[slot]
    for key in ("evidence_status", "source_class", "canonical_redred_traffic"):
        if not strict_equal(classification[key], expected[key]):
            raise AggregateGateError(f"{slot}.classification.{key} differs")
    if classification["official_contest_traffic"] is not False \
            or classification["p6_evidence_used"] is not False:
        raise AggregateGateError(f"{slot} expands official or P6 evidence")

    units = exact(view["campaign_units"], {
        "family_id", "unit_kind", "independent_sample_count", "retiming_labels",
        "retimings_are_independent_samples", "pooling_with_other_slots_permitted",
    }, f"{slot}.campaign_units")
    if not isinstance(units["family_id"], str) or not SAFE_TOKEN.fullmatch(units["family_id"]):
        raise AggregateGateError(f"{slot}.campaign_units.family_id is unsafe")
    if units["unit_kind"] != expected["unit_kind"]:
        raise AggregateGateError(f"{slot}.campaign_units.unit_kind differs")
    if type(units["independent_sample_count"]) is not int \
            or units["independent_sample_count"] < 1:
        raise AggregateGateError(f"{slot}.independent_sample_count must be positive")
    labels = tokens(units["retiming_labels"], f"{slot}.retiming_labels", allow_empty=True)
    if units["retimings_are_independent_samples"] is not False \
            or units["pooling_with_other_slots_permitted"] is not False:
        raise AggregateGateError(f"{slot} permits forbidden pooling")
    if slot == "synthetic_v2" and labels:
        raise AggregateGateError("synthetic_v2 must not masquerade as a retiming family")
    if slot == "public_v2" and (not labels or units["independent_sample_count"] != 1):
        raise AggregateGateError(
            "public_v2 retimings must remain one independent public dataset family"
        )

    shared = view["shared_gates"]
    if not isinstance(shared, dict) or not shared:
        raise AggregateGateError(f"{slot}.shared_gates must be a nonempty object")
    for key, status in shared.items():
        if not isinstance(key, str) or not SAFE_TOKEN.fullmatch(key) \
                or status not in ("PASS", "FAIL", "HOLD"):
            raise AggregateGateError(f"{slot}.shared_gates is malformed")
    candidates = exact(view["candidates"], {"A2", "A3"}, f"{slot}.candidates")
    validate_candidate(candidates["A2"], "A2", slot)
    validate_candidate(candidates["A3"], "A3", slot)
    if not strict_equal(view["claims"], CLAIMS_FALSE):
        raise AggregateGateError(f"{slot} expands official/physical/power/release claims")
    return view


def load_view(path: Path, slot: str) -> tuple[dict[str, Any], dict[str, Any], Path,
                                               tuple[int, int, int, int, int]]:
    resolved, data, identity = stable_file(path, f"{slot} normalized view")
    view = validate_view(load_json(data, f"{slot} normalized view"), slot)
    record = {
        "schema": view["schema"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "verification_status": view["verification"]["status"],
        "adapter_id": view["verification"]["adapter_id"],
        "source_result_sha256": view["verification"]["source_result_sha256"],
        "source_publication_sha256": view["verification"]["source_publication_sha256"],
        "family_id": view["campaign_units"]["family_id"],
    }
    return view, record, resolved, identity


def candidate_rollup(views: dict[str, dict[str, Any]], candidate: str) -> dict[str, Any]:
    rows = {slot: view["candidates"][candidate] for slot, view in views.items()}
    statuses = [row["gate_status"] for row in rows.values()]
    if all(status == "PASS" for status in statuses):
        status = "PASS"
    elif any(status == "HOLD" for status in statuses):
        status = "HOLD"
    else:
        status = "FAIL"
    failures = [
        {"slot": slot, "gate_status": row["gate_status"], "reason_codes": row["reason_codes"]}
        for slot, row in rows.items() if row["gate_status"] != "PASS"
    ]
    return {"status": status, "per_view": {slot: row["gate_status"] for slot, row in rows.items()},
            "nonpass": failures}


def decide(views: dict[str, dict[str, Any]], semantic_requirement: str) -> tuple[dict[str, Any], dict[str, Any]]:
    shared_nonpass = [
        {"slot": slot, "gate": gate, "status": status}
        for slot, view in views.items() for gate, status in view["shared_gates"].items()
        if status != "PASS"
    ]
    candidates = {name: candidate_rollup(views, name) for name in ("A2", "A3")}
    if shared_nonpass:
        result = ("HOLD_SHARED_GATE", None, False, None,
                  "shared campaign gate is not PASS; A3 cannot bypass shared failures")
    elif semantic_requirement == "EXACT_SCALAR_PREFIX":
        if candidates["A3"]["status"] == "PASS":
            result = ("A3_FALLBACK", "A3", True, "EXACT_PREFIX_REQUIRED",
                      "exact scalar-prefix semantics explicitly require A3")
        else:
            result = ("HOLD_A3_NOT_QUALIFIED", None, False, None,
                      "exact prefix is required but A3 is not independently PASS")
    elif candidates["A2"]["status"] == "PASS":
        result = ("A2_PRIMARY", "A2", False, None,
                  "A2 primary is PASS in both separately verified views")
    elif candidates["A2"]["status"] == "FAIL" and candidates["A3"]["status"] == "PASS":
        result = ("A3_FALLBACK", "A3", True, "A2_SPECIFIC_GATE_FAILURE",
                  "A2 has a candidate-specific FAIL and A3 independently passes both views")
    else:
        result = ("HOLD_NO_QUALIFIED_CAMPAIGN_RECOMMENDATION", None, False, None,
                  "A2 is not PASS and the exact A3 fallback preconditions are not met")
    status, recommendation, fallback, trigger, reason = result
    decision = {
        "status": status,
        "campaign_recommendation": recommendation,
        "reason": reason,
        "fallback_activated": fallback,
        "fallback_trigger": trigger,
        "final_selected_candidate": None,
        "final_selection_status": "HOLD",
        "final_release_status": "HOLD",
        "release_authority": False,
    }
    gates = {
        "normalized_views_separately_verified": "PASS",
        "shared_campaign_gates": "PASS" if not shared_nonpass else "HOLD",
        "A2_candidate": candidates["A2"]["status"],
        "A3_candidate": candidates["A3"]["status"],
        "official": "HOLD_FALSE_CLAIM",
        "physical": "HOLD_FALSE_CLAIM",
        "power": "HOLD_FALSE_CLAIM",
        "final_release": "HOLD",
    }
    return decision, {"shared_nonpass": shared_nonpass, "candidates": candidates, "gates": gates}


def evaluate(synthetic_path: Path, public_path: Path,
             semantic_requirement: str = "AGGREGATE_WEIGHTED") -> dict[str, Any]:
    synthetic, synthetic_record, synthetic_resolved, synthetic_identity = load_view(
        synthetic_path, "synthetic_v2"
    )
    public, public_record, public_resolved, public_identity = load_view(public_path, "public_v2")
    if synthetic_resolved == public_resolved or synthetic_resolved.samefile(public_resolved):
        raise AggregateGateError("synthetic_v2 and public_v2 views alias the same file")
    views = {"synthetic_v2": synthetic, "public_v2": public}
    decision, state = decide(views, semantic_requirement)
    public_units = public["campaign_units"]
    result = {
        "schema": RESULT_SCHEMA,
        "status": (
            "PASS_SCOPED_CAMPAIGN_RECOMMENDATION"
            if decision["campaign_recommendation"] is not None else "HOLD_CAMPAIGN_RECOMMENDATION"
        ),
        "campaign_id": CAMPAIGN_ID,
        "semantic_requirement": semantic_requirement,
        "input_views": {
            "synthetic_v2": synthetic_record,
            "public_v2": public_record,
        },
        "aggregation": {
            "synthetic_public_pooling": "FORBIDDEN",
            "pooled_totals_emitted": False,
            "public_unit_kind": public_units["unit_kind"],
            "public_independent_sample_count": public_units["independent_sample_count"],
            "public_retiming_labels": public_units["retiming_labels"],
            "public_retimings_counted_as_independent_samples": False,
        },
        "candidate_policy": {
            "primary": "A2",
            "fallback": "A3",
            "A2_semantics": "AGGREGATE_WEIGHTED_PERFORMANCE",
            "A3_semantics": "EXACT_SCALAR_PREFIX",
            "fallback_allowed_triggers": [
                "EXACT_PREFIX_REQUIRED", "A2_SPECIFIC_GATE_FAILURE",
            ],
            "fallback_forbidden_triggers": [
                "SHARED_CAMPAIGN_FAILURE", "SHARED_INTERFACE_FAILURE",
                "SHARED_CDC_RDC_FAILURE", "SHARED_PDK_IO_FAILURE",
            ],
        },
        "candidate_rollup": state["candidates"],
        "shared_nonpass": state["shared_nonpass"],
        "gates": state["gates"],
        "decision": decision,
        "claims": {**CLAIMS_FALSE, "final_candidate_selection": False},
    }
    recheck_file(synthetic_resolved, synthetic_identity, "synthetic_v2 normalized view")
    recheck_file(public_resolved, public_identity, "public_v2 normalized view")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evaluate", nargs="?", choices=("evaluate",))
    parser.add_argument("--synthetic-v2-view", type=Path, required=True)
    parser.add_argument("--public-v2-view", type=Path, required=True)
    parser.add_argument(
        "--semantic-requirement",
        choices=("AGGREGATE_WEIGHTED", "EXACT_SCALAR_PREFIX"),
        default="AGGREGATE_WEIGHTED",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-hold", action="store_true")
    arguments = parser.parse_args()
    try:
        report = evaluate(
            arguments.synthetic_v2_view, arguments.public_v2_view,
            arguments.semantic_requirement,
        )
        payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if arguments.output:
            if arguments.output.exists() or arguments.output.is_symlink():
                raise AggregateGateError("output already exists")
            arguments.output.write_bytes(payload)
        sys.stdout.buffer.write(payload)
        if report["status"] == "PASS_SCOPED_CAMPAIGN_RECOMMENDATION":
            return 0
        return 0 if arguments.allow_hold else 3
    except (AggregateGateError, OSError, ValueError) as error:
        print(f"REDRED_CAMPAIGN_AGGREGATE_FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
