#!/usr/bin/env python3
"""Verify REDRED policy and pinned bounded claims, never emit evidence/release PASS."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import sys
from typing import Any, Iterable, Mapping


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_CONTRACT = HERE / "active_goal.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_PATH_PARTS = {"tmp", "latest"}


class PolicyError(ValueError):
    """The policy is malformed, incomplete, contradictory, or unbound."""


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_regular(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise PolicyError(f"not a regular non-symlink file: {path}")
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise PolicyError(f"cannot read {path}: {exc}") from exc
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if identity(before) != identity(after):
        raise PolicyError(f"file changed while read: {path}")
    return payload


def load_contract(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(
            read_regular(path), object_pairs_hook=_duplicate_safe_object
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"invalid contract JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise PolicyError("contract root must be an object")
    return document


def exact_object(value: Any, path: str, keys: Iterable[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{path} must be an object")
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        raise PolicyError(
            f"{path} keys mismatch: missing={sorted(expected - actual)} "
            f"unknown={sorted(actual - expected)}"
        )
    return value


def expect(value: Any, expected: Any, path: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise PolicyError(f"{path} structured value mismatch")


def require_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PolicyError(f"{path} must be a nonempty array")
    if any(not isinstance(item, str) or not item for item in value):
        raise PolicyError(f"{path} must contain nonempty strings")
    if len(value) != len(set(value)):
        raise PolicyError(f"{path} contains duplicates")
    return value


def validate_repo_path(value: Any, path: str, *, must_exist: bool) -> Path:
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{path} must be a repository-relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise PolicyError(f"{path} is not a normalized repository-relative path")
    if any(part.casefold() in FORBIDDEN_PATH_PARTS for part in pure.parts):
        raise PolicyError(f"{path} contains forbidden mutable component")
    candidate = ROOT.joinpath(*pure.parts)
    if must_exist:
        resolved_root = ROOT.resolve(strict=True)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise PolicyError(f"{path} does not resolve inside repository") from exc
        return resolved
    return candidate


def validate_sha(value: Any, path: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise PolicyError(f"{path} must be a lowercase SHA-256")
    return value


def verify_local_artifact(row: Any, path: str, extra_keys: Iterable[str] = ()) -> bytes:
    keys = {"path", "sha256", *extra_keys}
    item = exact_object(row, path, keys)
    resolved = validate_repo_path(item["path"], f"{path}.path", must_exist=True)
    expected = validate_sha(item["sha256"], f"{path}.sha256")
    payload = read_regular(resolved)
    if digest(payload) != expected:
        raise PolicyError(f"{path} digest mismatch")
    return payload


def git_blob(commit: Any, repo_path: Any, path: str) -> bytes:
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise PolicyError(f"{path}.commit must be a full commit hash")
    validate_repo_path(repo_path, f"{path}.path", must_exist=False)
    result = subprocess.run(
        ["git", "show", f"{commit}:{repo_path}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise PolicyError(f"{path} Git object is unavailable")
    return result.stdout


def git_text(arguments: list[str], path: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=ROOT, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise PolicyError(f"{path} Git object query failed")
    try:
        return result.stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise PolicyError(f"{path} Git object query is not ASCII") from exc


def compact_canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def parse_json_object(payload: bytes, path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload, object_pairs_hook=_duplicate_safe_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"{path} is not valid duplicate-safe JSON") from exc
    if not isinstance(value, dict):
        raise PolicyError(f"{path} JSON root must be an object")
    return value


def verify_git_artifact(row: Any, path: str) -> bytes:
    item = exact_object(row, path, {"commit", "path", "sha256"})
    payload = git_blob(item["commit"], item["path"], path)
    if digest(payload) != validate_sha(item["sha256"], f"{path}.sha256"):
        raise PolicyError(f"{path} digest mismatch")
    return payload


EXPECTED_GOAL = {
    "primary_candidate": "A2",
    "semantic_fallback": "A3",
    "selected_release_interface": "PARALLEL_FALLBACK",
    "selected_release_interface_status": "IMPLEMENTED_RELEASE_HELD",
    "score_threshold_defined": False,
    "mandatory_scope": [
        "COMPLETE_ENDPOINT_CORRECTNESS",
        "CANONICAL_DIGITAL_EVIDENCE",
        "SELECTED_INTERFACE_DIGITAL_PHYSICAL_POWER",
        "POST_ROUTE_STANDARD_CELL_REFERENCE",
        "FINAL_CDC_RDC",
        "PDK_ENDPOINT_IO_RULES",
    ],
    "stretch_scope": [
        "COORDINATE_SCENARIO_MODEL",
        "COORDINATE_TRANSFORM_RTL",
        "MOTION_ESTIMATION",
    ],
    "research_only_noncandidates": ["A4"],
}


EXPECTED_ENDPOINT = {
    "boundary_id": "IMPLEMENTED_SINGLE_EDGE_SOURCE_PENDING_ACCEPT_THROUGH_RETIRE",
    "active_interface_profile": "PARALLEL_FALLBACK_SINGLE_EDGE_IMPLEMENTED",
    "clock_reset_contract": {
        "primary_clock_port": "clk_i",
        "active_edge": "POSEDGE",
        "clock_domain_count": 1,
        "generated_clocks_allowed": False,
        "gated_clocks_allowed": False,
        "forwarded_clocks_allowed": False,
        "reset_port": "rst_i",
        "reset_polarity": "ACTIVE_HIGH",
        "reset_assertion": "SYNCHRONOUS",
        "reset_deassertion": "SYNCHRONOUS",
        "reset_assertion_precondition": "CLEAN_DRAIN_IDLE_PRE_EDGE",
    },
    "request": {
        "signal": "source_pending_i[15:0]",
        "state_model": "ONE_ENTRY_PENDING_LATCH_PER_SOURCE",
    },
    "acceptance": {
        "signal": "source_accept_o[15:0]",
        "sample_edge": "PRIMARY_CLOCK_POSEDGE",
        "condition": "PENDING_PRE_EDGE_AND_ACCEPT_PRE_EDGE",
    },
    "retirement": {
        "valid_signal": "retire_valid_o[1:0]",
        "address_signals": ["retire_addr0_o[3:0]", "retire_addr1_o[3:0]"],
        "sample_edge": "PRIMARY_CLOCK_POSEDGE",
        "delivered_alias": "retired",
    },
    "included_components": [
        "SCHEDULER_POLICY_STATE",
        "ADMISSION_CONTROL",
        "ELASTIC_BUFFERING",
        "LINK_LAUNCH_CONTROL",
        "LINK_TX",
        "STANDARD_CELL_LINK_WIRES",
        "LINK_RX",
        "RETIRE_OBSERVER",
        "DRAIN_ERROR_LOGIC",
    ],
    "excluded_components": [
        "EVENT_GENERATION_BEFORE_PENDING",
        "TESTBENCH_STORAGE_ARBITRATION_RETRY",
        "PAD_CELLS",
        "PACKAGE",
        "CHANNEL",
        "COORDINATE_TRANSFORM",
        "MOTION_ESTIMATION",
        "VISUALIZATION",
    ],
    "all_functional_state_charged": True,
    "coordinate_inside_endpoint_ppa": False,
    "top_port_scope": {
        "kind": "IMPLEMENTED_SINGLE_EDGE_STANDARD_CELL_LOGIC_PORTS",
        "input_roles": [
            "PRIMARY_CLOCK",
            "SYNCHRONOUS_ACTIVE_HIGH_RESET",
            "LINK_ENABLE",
            "SOURCE_PENDING_16",
        ],
        "output_roles": [
            "SOURCE_ACCEPT_16",
            "ACCEPT_COUNT_2",
            "ACCEPT_ADDRESS_2X4",
            "SINGLE_EDGE_LINK_CELL_9",
            "RETIRE_VALID_2",
            "RETIRE_ADDRESS_2X4",
            "DRAIN_IDLE",
            "PROTOCOL_ERROR",
        ],
        "pads_included": False,
        "package_included": False,
        "channel_included": False,
    },
}


EXPECTED_CANDIDATES = {
    "A2": {
        "role": "PRIMARY",
        "architecture": "BATCHED_IWRR_K2",
        "semantic_class": "PERSISTENT_ALL_FOUR_ROWS_WEIGHTED_OPPORTUNITY",
        "row_weights": [1, 5, 5, 1],
        "persistent_rows": [0, 1, 2, 3],
        "calendar_period_opportunities": 12,
        "calendar_phase_persistent": True,
        "empty_slot_behavior": "SPARSE_FALLBACK_TO_ELIGIBLE_ROW",
        "service_debt_model": "NONE",
        "debt_catch_up": False,
        "exact_scalar_prefix": False,
        "future_trace_equivalence": False,
    },
    "A3": {
        "role": "SEMANTIC_FALLBACK",
        "architecture": "TWO_STEP_SCALAR_SELECTOR_K2",
        "semantic_class": "EXACT_TWO_SELECTION_PREFIX_OF_HELD_PENDING_SNAPSHOT",
        "row_weights": [1, 5, 5, 1],
        "microsteps": 2,
        "snapshot_held_across_microsteps": True,
        "future_arrivals_visible_to_held_snapshot": False,
        "future_trace_equivalence": False,
        "activation_triggers": [
            "EXACT_PREFIX_REQUIRED",
            "A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3",
        ],
        "forbidden_activation_triggers": [
            "SHARED_INTERFACE_FAILURE",
            "SHARED_EVIDENCE_FAILURE",
            "SHARED_CDC_RDC_FAILURE",
            "SHARED_PDK_IO_FAILURE",
        ],
    },
    "A4": {
        "role": "RESEARCH_ONLY",
        "release_candidate": False,
        "ranking_eligible": False,
    },
}


EXPECTED_INTERFACES = {
    "selection": {
        "decision": "SINGLE_EDGE_SELECTED_RELEASE_HELD",
        "selected": "PARALLEL_FALLBACK",
        "selection_rule": "ONLY_IMPLEMENTED_SINGLE_EDGE_INTERFACE_IS_RELEASE_ELIGIBLE",
        "cross_interface_evidence_borrowing": False,
    },
    "P6": {
        "role": "HISTORICAL_RESEARCH_REFERENCE_ONLY",
        "competition_release_status": "SUPERSEDED_NONCURRENT",
        "cell_transfer": {
            "cell_bits": 10,
            "data_wires": 5,
            "forwarded_clock_wires": 1,
            "physical_wires_total": 6,
            "low_half": {
                "clock_level": "LOW",
                "launch_edge": "RISING",
                "payload": "CELL_BITS_4_TO_0",
            },
            "high_half": {
                "clock_level": "HIGH",
                "launch_edge": "FALLING",
                "payload": "CELL_BITS_9_TO_5",
            },
            "receiver_commit_edge": "FALLING",
            "multi_edge_transfer": True,
        },
        "forwarded_clock_exception": {
            "intentional": True,
            "signal": "link_clk_o",
            "allowed_unconstrained_endpoint_count": 1,
            "data_endpoint_exceptions_allowed": 0,
            "scope": "STANDARD_CELL_TOP_OUTPUT_ONLY",
        },
        "standard_cell_reference": {
            "status": "PASS_WITH_CLAIM_LIMIT",
            "period_ns": 6.5,
            "claim_limit": "STANDARD_CELL_LOGIC_PORTS_ONLY_NO_PAD_PACKAGE_CHANNEL",
            "evidence_dependency": "INHERITED_6P5_STANDARD_CELL_REFERENCE",
        },
        "competition_multi_edge_legality": {
            "status": "HOLD",
            "hold_id": "H_P6_MULTI_EDGE_LEGALITY",
        },
        "real_pad_package_channel": {
            "status": "UNPROVEN",
            "hold_id": "H_P6_REAL_PAD_PHY",
        },
        "vectorless_power": {
            "status": "HOLD",
            "hold_id": "H_P6_VECTORLESS_POWER",
        },
    },
    "PARALLEL_FALLBACK": {
        "role": "ONLY_RELEASE_ELIGIBLE_IMPLEMENTED_INTERFACE",
        "competition_release_status": "HOLD_INCOMPLETE_MAPPED_PHYSICAL_POWER_AND_SELECTION",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "integrated_digital_evidence": "PASS_BOUNDED_ACTUAL_RTL_SYNTHETIC_AND_PUBLIC_PROJECTED",
        "source_cdc_rdc": "PASS_SYNCHRONOUS_INPUT_SCOPE",
        "source_structure_pdk_legality": "PASS_SOURCE_ONLY",
        "mapped_pdk_legality": "HOLD",
        "organizer_pdk_legality": "HOLD",
        "post_route_evidence": "HOLD_NO_REAL_PNR_OR_POST_ROUTE_TIMING",
        "vectorless_power_evidence": "HOLD_NO_REAL_MAPPED_VECTORLESS_POWER",
        "hold_id": "H_PARALLEL_REAL_EVIDENCE",
        "may_borrow_p6_physical_evidence": False,
        "disallowed_borrowed_dependencies": [
            "INHERITED_6P5_STANDARD_CELL_REFERENCE",
            "P6_VECTORLESS_POWER",
            "P6_PAD_PACKAGE_CHANNEL",
        ],
    },
}


EXPECTED_EQUATIONS = [
    {
        "scope": "EACH_COMPLETED_TRACE",
        "lhs": "generated",
        "operator": "EQUALS_SUM",
        "rhs": ["source_overrun", "accepted"],
    },
    {
        "scope": "EACH_HARD_CORRECT_DRAINED_TRACE",
        "lhs": "accepted",
        "operator": "EQUALS",
        "rhs": ["retired"],
    },
    {
        "scope": "EACH_TRACE",
        "lhs": "delivered",
        "operator": "EQUALS",
        "rhs": ["retired"],
    },
]


def verify_goal_boundary_candidates(document: Mapping[str, Any]) -> None:
    expect(document["goal_policy"], EXPECTED_GOAL, "goal_policy")
    expect(document["endpoint_boundary"], EXPECTED_ENDPOINT, "endpoint_boundary")
    expect(document["candidate_semantics"], EXPECTED_CANDIDATES, "candidate_semantics")
    expect(document["interfaces"], EXPECTED_INTERFACES, "interfaces")


def verify_bounded_current_evidence(document: Mapping[str, Any]) -> None:
    evidence = exact_object(
        document["bounded_current_evidence"],
        "bounded_current_evidence",
        {
            "policy",
            "single_edge_actual_rtl_synthetic",
            "public_uzh_projected_actual_rtl",
            "single_edge_source_cdc_rdc",
            "single_edge_source_structure_pdk",
            "single_edge_physical",
            "single_edge_vectorless",
            "final_a2_a3_selection_readiness",
            "known_motion_supplied_rotation_synthetic_demo",
        },
    )
    expect(
        evidence["policy"],
        {
            "git_objects_rehashed": True,
            "selected_claim_fields_checked": True,
            "campaigns_reexecuted_by_policy_verifier": False,
            "policy_pass_is_evidence_pass": False,
            "policy_pass_is_release_pass": False,
        },
        "bounded_current_evidence.policy",
    )

    synthetic = exact_object(
        evidence["single_edge_actual_rtl_synthetic"],
        "bounded_current_evidence.single_edge_actual_rtl_synthetic",
        {
            "status", "claim_scope", "release_status", "selection_status",
            "source_commit", "integrated_rtl_commit", "publication_commit",
            "p6_evidence_used", "dataset_scope", "artifacts",
            "execution_accounting", "full50_aggregate",
        },
    )
    expect(
        {key: synthetic[key] for key in (
            "status", "claim_scope", "release_status", "selection_status",
            "source_commit", "integrated_rtl_commit", "publication_commit",
            "p6_evidence_used", "dataset_scope",
        )},
        {
            "status": "PASS",
            "claim_scope": "HARDENED_SINGLE_EDGE_ACTUAL_RTL_SYNTHETIC_SEMANTICS",
            "release_status": "HOLD",
            "selection_status": "HOLD",
            "source_commit": "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b",
            "integrated_rtl_commit": "a0a4eb38632245db8ff5937ea5b6c6e3f3839246",
            "publication_commit": "72491e45a35e6883bd4ee65d5c30409c108ab190",
            "p6_evidence_used": False,
            "dataset_scope": "TEAM_SYNTHETIC_FULL50_NONOFFICIAL",
        },
        "single-edge synthetic claim boundary",
    )
    expect(
        synthetic["execution_accounting"],
        {
            "full50_actual_rtl_executions": 100,
            "reset_actual_rtl_executions": 2,
            "mutation_activation_actual_rtl_executions": 2,
            "mutation_actual_rtl_executions": 8,
            "receipt_only_executions": 0,
        },
        "single-edge synthetic execution accounting",
    )
    expected_totals = {
        "A2": {"generated": 106416, "source_overrun": 2370, "accepted": 104046, "retired": 104046},
        "A3": {"generated": 106416, "source_overrun": 12771, "accepted": 93645, "retired": 93645},
    }
    expect(synthetic["full50_aggregate"], expected_totals, "single-edge synthetic aggregate")
    synthetic_artifacts = exact_object(synthetic["artifacts"], "single-edge synthetic artifacts", {"result", "pins"})
    result = parse_json_object(
        verify_git_artifact(synthetic_artifacts["result"], "single-edge synthetic result"),
        "single-edge synthetic result",
    )
    pins = parse_json_object(
        verify_git_artifact(synthetic_artifacts["pins"], "single-edge synthetic pins"),
        "single-edge synthetic pins",
    )
    expect(result.get("schema"), "a23_full_single_edge_replay_result_v1", "single-edge result schema")
    expect(result.get("status"), "PASS", "single-edge result status")
    expect(result.get("qualification"), {"CDC_RDC": "HOLD", "physical": "HOLD", "power": "HOLD", "single_edge_digital_RTL": "GO"}, "single-edge result qualification")
    actual_execution = result.get("execution_accounting", {})
    for contract_key, result_key in {
        "full50_actual_rtl_executions": "full50_actual_RTL_executions",
        "reset_actual_rtl_executions": "reset_actual_RTL_executions",
        "mutation_activation_actual_rtl_executions": "mutation_activation_actual_RTL_executions",
        "mutation_actual_rtl_executions": "mutation_actual_RTL_executions",
        "receipt_only_executions": "receipt_only_executions",
    }.items():
        expect(actual_execution.get(result_key), synthetic["execution_accounting"][contract_key], f"single-edge result {result_key}")
    for owner, contract_owner in (("a2", "A2"), ("a3", "A3")):
        totals = result.get("owners", {}).get(owner, {}).get("full50", {}).get("aggregate", {}).get("totals")
        expect(
            {key: totals.get(key) for key in ("generated", "source_overrun", "accepted", "retired")} if isinstance(totals, dict) else totals,
            expected_totals[contract_owner],
            f"single-edge result {owner} aggregate",
        )
    expect(pins.get("integration_state"), "LOCKED_ACTUAL_SINGLE_EDGE_RTL", "single-edge pins integration state")
    expect(
        pins.get("rtl_provenance", {}).get("source_commit"), synthetic["source_commit"],
        "single-edge pins source commit",
    )
    expect(
        pins.get("rtl_provenance", {}).get("integration_commit"), synthetic["integrated_rtl_commit"],
        "single-edge pins integration commit",
    )

    public = exact_object(
        evidence["public_uzh_projected_actual_rtl"],
        "bounded_current_evidence.public_uzh_projected_actual_rtl",
        {
            "status", "claim_scope", "release_status", "selection_status",
            "canonical_redred_traffic", "official_redred_traffic", "p6_evidence_used",
            "publication_commit", "artifacts", "dataset_accounting",
        },
    )
    expect(
        {key: public[key] for key in (
            "status", "claim_scope", "release_status", "selection_status",
            "canonical_redred_traffic", "official_redred_traffic", "p6_evidence_used",
            "publication_commit",
        )},
        {
            "status": "PASS",
            "claim_scope": "PUBLIC_UZH_PROJECTED_EXTENSION_ACTUAL_SINGLE_EDGE_RTL",
            "release_status": "HOLD",
            "selection_status": "HOLD",
            "canonical_redred_traffic": False,
            "official_redred_traffic": False,
            "p6_evidence_used": False,
            "publication_commit": "f30fec14572d9efb58a98d8f61dd22604a91446b",
        },
        "public projected claim boundary",
    )
    expect(
        public["dataset_accounting"],
        {
            "unique_projected_window_events": 1100,
            "retiming_scenarios": ["1x", "64x", "256x"],
            "retimings_are_independent_unique_samples": False,
            "projected_actual_rtl_executions": 6,
            "receipt_only_executions": 0,
        },
        "public projected dataset accounting",
    )
    public_artifacts = exact_object(public["artifacts"], "public projected artifacts", {"publication", "result", "pins", "export_bundle"})
    publication = parse_json_object(verify_git_artifact(public_artifacts["publication"], "public projected publication"), "public projected publication")
    public_result = parse_json_object(verify_git_artifact(public_artifacts["result"], "public projected result"), "public projected result")
    public_pins = parse_json_object(verify_git_artifact(public_artifacts["pins"], "public projected pins"), "public projected pins")
    export_payload = verify_git_artifact(public_artifacts["export_bundle"], "public projected export bundle")
    for name, row in (("publication", publication), ("result", public_result), ("pins", public_pins)):
        expect(row.get("release_status"), "HOLD", f"public projected {name} release")
        expect(row.get("selection_status"), "HOLD", f"public projected {name} selection")
        expect(row.get("canonical_redred_traffic"), False, f"public projected {name} canonical status")
        expect(row.get("official_redred_traffic"), False, f"public projected {name} official status")
        expect(row.get("p6_evidence_used"), False, f"public projected {name} P6 status")
    expect(publication.get("result_sha256"), public_artifacts["result"]["sha256"], "public publication result binding")
    expect(publication.get("export_bundle_sha256"), digest(export_payload), "public publication export binding")
    expect(public_result.get("evidence_class"), "PUBLIC_DATASET_PROJECTED_ACTUAL_SINGLE_EDGE_RTL", "public result evidence class")
    expect(public_result.get("execution_accounting", {}).get("projected_actual_RTL_executions"), 6, "public result executions")
    expect(public_result.get("execution_accounting", {}).get("receipt_only_executions"), 0, "public result receipt-only executions")
    expect(public_pins.get("identity_accounting"), {"unique_projected_window_events": 1100, "scenario_retimings": 3, "pooled_3300_unique_events": False}, "public pins identity accounting")

    cdc = exact_object(
        evidence["single_edge_source_cdc_rdc"],
        "bounded_current_evidence.single_edge_source_cdc_rdc",
        {
            "status", "claim_scope", "external_input_scope", "reset_scope",
            "source_commit", "integrated_rtl_commit", "publication_commit",
            "mapped_cdc_rdc_status", "final_selected_interface_status", "artifacts",
        },
    )
    expect(
        {key: cdc[key] for key in cdc if key != "artifacts"},
        {
            "status": "PASS",
            "claim_scope": "SOURCE_AND_ELABORATED_SINGLE_POSEDGE_CLOCK_STRUCTURE",
            "external_input_scope": "BOUND_SYNCHRONOUS_TO_PRIMARY_CLOCK_ASSUMPTION",
            "reset_scope": "SYNCHRONOUS_ASSERT_SYNCHRONOUS_DEASSERT_DRAIN_BEFORE_ASSERT",
            "source_commit": "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b",
            "integrated_rtl_commit": "a0a4eb38632245db8ff5937ea5b6c6e3f3839246",
            "publication_commit": "9d1dced49d3fceabf812d2ba2275c8d4c02eef13",
            "mapped_cdc_rdc_status": "HOLD",
            "final_selected_interface_status": "HOLD",
        },
        "source CDC/RDC claim boundary",
    )
    cdc_artifacts = exact_object(cdc["artifacts"], "source CDC/RDC artifacts", {"contract", "source_binding", "verifier"})
    cdc_contract = parse_json_object(verify_git_artifact(cdc_artifacts["contract"], "source CDC/RDC contract"), "source CDC/RDC contract")
    verify_git_artifact(cdc_artifacts["source_binding"], "source CDC/RDC binding")
    verify_git_artifact(cdc_artifacts["verifier"], "source CDC/RDC verifier")
    expect(cdc_contract.get("a2_source_set", {}).get("repository_commit"), cdc["source_commit"], "CDC source commit")
    expect(cdc_contract.get("a2_source_set", {}).get("integration_commit"), cdc["integrated_rtl_commit"], "CDC integration commit")
    expect(cdc_contract.get("policy", {}).get("external_input_domain"), cdc["external_input_scope"], "CDC synchronous-input scope")
    expect(cdc_contract.get("policy", {}).get("reset"), cdc["reset_scope"], "CDC reset scope")

    pdk = exact_object(
        evidence["single_edge_source_structure_pdk"],
        "bounded_current_evidence.single_edge_source_structure_pdk",
        {
            "status", "claim_scope", "source_commit", "integrated_rtl_commit",
            "publication_commit", "mapped_legality_status",
            "organizer_legality_status", "release_status", "artifacts",
        },
    )
    expect(
        {key: pdk[key] for key in pdk if key != "artifacts"},
        {
            "status": "PASS",
            "claim_scope": "RTL_SOURCE_STRUCTURE_ONLY_NOT_MAPPED_NOT_ORGANIZER_APPROVAL",
            "source_commit": "6fc5e167918fa4c54786c9a3abb5f60ecd8b991b",
            "integrated_rtl_commit": "a0a4eb38632245db8ff5937ea5b6c6e3f3839246",
            "publication_commit": "dd1d30cfcb84aa1b760b8026af2807a11d84940b",
            "mapped_legality_status": "HOLD",
            "organizer_legality_status": "HOLD",
            "release_status": "HOLD",
        },
        "source PDK claim boundary",
    )
    pdk_artifacts = exact_object(pdk["artifacts"], "source PDK artifacts", {"matrix", "verifier"})
    matrix = parse_json_object(verify_git_artifact(pdk_artifacts["matrix"], "source PDK matrix"), "source PDK matrix")
    verify_git_artifact(pdk_artifacts["verifier"], "source PDK verifier")
    audited = matrix.get("audited_rtl", {})
    expect(audited.get("source_commit"), pdk["source_commit"], "PDK source commit")
    expect(audited.get("integrated_commit"), pdk["integrated_rtl_commit"], "PDK integration commit")
    expect(audited.get("source_structure_status"), "PASS", "PDK source structure status")
    expect(audited.get("mapped_structure_status"), "HOLD", "PDK mapped structure status")
    expect(audited.get("organizer_approval_status"), "HOLD", "PDK organizer status")

    physical = exact_object(
        evidence["single_edge_physical"],
        "bounded_current_evidence.single_edge_physical",
        {"status", "claim_scope", "real_pnr_status", "post_route_timing_status", "constraint_authority_status", "contract"},
    )
    expect(
        {key: physical[key] for key in physical if key != "contract"},
        {
            "status": "HOLD",
            "claim_scope": "SOURCE_BOUND_STATIC_FLOW_SCAFFOLD_ONLY",
            "real_pnr_status": "HOLD",
            "post_route_timing_status": "HOLD",
            "constraint_authority_status": "HOLD_UNCONFIRMED_TEAM_PLACEHOLDER",
        },
        "single-edge physical claim boundary",
    )
    physical_contract = parse_json_object(verify_git_artifact(physical["contract"], "single-edge physical contract"), "single-edge physical contract")
    expect(physical["contract"]["commit"],
           "15593a72d68867641196992dd31bd00ef5dacaac",
           "single-edge physical contract commit")
    expect(physical["contract"]["sha256"],
           "6e0e8bb0381419bbb556561314f7bea774c4e131fddf904517baf13ae4232544",
           "single-edge physical contract digest")
    expect(physical_contract.get("status"), "STATIC_READY_CANDIDATE_PHYSICAL_HOLD", "physical contract status")
    expect(physical_contract.get("constraints", {}).get("authority_status"), "UNCONFIRMED_TEAM_PLACEHOLDER", "physical constraint authority")
    expect(physical_contract.get("qualification", {}).get("candidate_physical_go_possible"), False, "physical GO eligibility")

    vectorless = exact_object(
        evidence["single_edge_vectorless"],
        "bounded_current_evidence.single_edge_vectorless",
        {"status", "claim_scope", "real_mapped_vectorless_status", "release_comparison_eligible", "publication_commit", "artifacts"},
    )
    expect(
        {key: vectorless[key] for key in vectorless if key != "artifacts"},
        {
            "status": "HOLD",
            "claim_scope": "DIAGNOSTIC_ONLY_PLACEHOLDER_IO_NO_CONTROLLED_PRODUCER",
            "real_mapped_vectorless_status": "HOLD",
            "release_comparison_eligible": False,
            "publication_commit": "dd1d30cfcb84aa1b760b8026af2807a11d84940b",
        },
        "single-edge vectorless claim boundary",
    )
    vectorless_artifacts = exact_object(vectorless["artifacts"], "single-edge vectorless artifacts", {"contract", "source_manifests"})
    vectorless_contract = parse_json_object(verify_git_artifact(vectorless_artifacts["contract"], "single-edge vectorless contract"), "single-edge vectorless contract")
    verify_git_artifact(vectorless_artifacts["source_manifests"], "single-edge vectorless source manifests")
    expect(vectorless_contract.get("status"), "DIAGNOSTIC_ONLY_PLACEHOLDER_IO_NO_CONTROLLED_PRODUCER", "vectorless contract status")
    expect(vectorless_contract.get("constraint_authority", {}).get("comparison_ready_eligible"), False, "vectorless comparison eligibility")

    selection = exact_object(
        evidence["final_a2_a3_selection_readiness"],
        "bounded_current_evidence.final_a2_a3_selection_readiness",
        {
            "status", "claim_scope", "publication_commit",
            "selected_candidate", "selection_authority", "release_authority",
            "missing_gate_count", "artifacts",
        },
    )
    expect(
        {key: selection[key] for key in selection if key != "artifacts"},
        {
            "status": "HOLD",
            "claim_scope":
                "CURRENT_SINGLE_EDGE_A2_A3_FINAL_SELECTION_READINESS_ONLY",
            "publication_commit": "49a6e28b5cf521bef1b48feb9e1d45074e9f3bb1",
            "selected_candidate": None,
            "selection_authority": False,
            "release_authority": False,
            "missing_gate_count": 12,
        },
        "final-selection readiness claim boundary",
    )
    selection_artifacts = exact_object(
        selection["artifacts"], "final-selection readiness artifacts",
        {"contract", "verifier"})
    selection_contract = parse_json_object(
        verify_git_artifact(selection_artifacts["contract"],
                            "final-selection readiness contract"),
        "final-selection readiness contract")
    verify_git_artifact(selection_artifacts["verifier"],
                        "final-selection readiness verifier")
    expect(selection_contract.get("schema"),
           "redred_final_a2_a3_selection_contract_v1",
           "final-selection readiness schema")
    expect(selection_contract.get("status"),
           "HOLD_MISSING_AUTHENTICATED_MATCHED_PHYSICAL_POWER_PDK_CDC_EVIDENCE",
           "final-selection readiness status")
    expect(selection_contract.get("authority"), {
        "policy_engine_only": True,
        "current_selection_authority": False,
        "release_authority": False,
        "official_score_authority": False,
        "caller_supplied_evidence_allowed": False,
    }, "final-selection readiness authority")
    expect(selection_contract.get("candidate_policy", {}).get(
        "p6_or_multi_edge_evidence_allowed"), False,
        "final-selection readiness P6 boundary")
    expect(selection_contract.get("candidate_policy", {}).get(
        "score_formula_defined"), False,
        "final-selection readiness score boundary")
    expect(selection_contract.get("current_decision"), {
        "selection_status": "HOLD",
        "selected_candidate": None,
        "campaign_recommendation": "A2",
        "fallback_trigger": None,
        "missing_gate_ids": [
            "ORGANIZER_CELL_CLOCK_IO_RULES",
            "OFFICIAL_CONSTRAINTS_CORNERS",
            "CONTROLLED_PRODUCER_FRESHNESS",
            "MATCHED_A2_A3_COHORT",
            "A2:MAPPED_PDK_LEGALITY",
            "A2:POST_ROUTE_TIMING_AREA",
            "A2:VECTORLESS_POWER",
            "A2:FINAL_CDC_RDC",
            "A3:MAPPED_PDK_LEGALITY",
            "A3:POST_ROUTE_TIMING_AREA",
            "A3:VECTORLESS_POWER",
            "A3:FINAL_CDC_RDC",
        ],
        "failed_gate_ids": [],
        "final_selection_authority": False,
        "release_authority": False,
        "official_score_winner": False,
    }, "final-selection readiness current decision")

    motion = exact_object(
        evidence["known_motion_supplied_rotation_synthetic_demo"],
        "bounded_current_evidence.known_motion_supplied_rotation_synthetic_demo",
        {
            "status", "claim_scope", "evidence_class", "inside_endpoint_ppa",
            "canonical_coordinate_status", "coordinate_rtl_status", "release_status",
            "publication_commit", "artifacts",
        },
    )
    expect(
        {key: motion[key] for key in motion if key != "artifacts"},
        {
            "status": "PASS",
            "claim_scope": "KNOWN_MOTION_SUPPLIED_ROTATION_SYNTHETIC_DEMO_ONLY",
            "evidence_class": "SYNTHETIC_DEMO",
            "inside_endpoint_ppa": False,
            "canonical_coordinate_status": "HOLD",
            "coordinate_rtl_status": "HOLD",
            "release_status": "HOLD",
            "publication_commit": "78eb019c56f2aab4b844c0fe925a5f2252fca256",
        },
        "known-motion claim boundary",
    )
    motion_artifacts = motion["artifacts"]
    expected_motion_paths = [
        "demos/known_motion_coordinate/README.md",
        "demos/known_motion_coordinate/__init__.py",
        "demos/known_motion_coordinate/cli.py",
        "demos/known_motion_coordinate/model.py",
        "demos/known_motion_coordinate/fixtures/intrinsics.json",
        "demos/known_motion_coordinate/fixtures/poses.jsonl",
        "demos/known_motion_coordinate/fixtures/retired_events.jsonl",
        "tests/known_motion_coordinate/test_coordinate.py",
    ]
    if not isinstance(motion_artifacts, list) or len(motion_artifacts) != 8:
        raise PolicyError("known-motion artifacts must bind exact eight-object executable closure")
    paths = [row.get("path") if isinstance(row, dict) else None for row in motion_artifacts]
    expect(paths, expected_motion_paths, "known-motion artifact paths")
    if len(set(paths)) != len(paths):
        raise PolicyError("known-motion artifact paths contain duplicates")
    for index, row in enumerate(motion_artifacts):
        expect(row.get("commit"), motion["publication_commit"],
               f"known-motion artifact[{index}] publication commit")
        verify_git_artifact(row, f"known-motion artifact[{index}]")


def load_trace_registry(row: Mapping[str, Any]) -> dict[str, Any]:
    exact_object(
        row,
        "canonical_digital_dependency.trace_registry",
        {"path", "sha256", "source_commit", "generator_version"},
    )
    payload = verify_local_artifact(
        row,
        "canonical_digital_dependency.trace_registry",
        {"source_commit", "generator_version"},
    )
    # The byte-pinned registry contains constants only. Parse first to reject
    # executable statements outside assignments/imports before evaluating it.
    tree = ast.parse(payload.decode("utf-8", errors="strict"))
    allowed = (ast.Assign, ast.AnnAssign, ast.Expr, ast.ImportFrom)
    if any(not isinstance(node, allowed) for node in tree.body):
        raise PolicyError("trace registry contains executable top-level statements")
    namespace = runpy.run_path(str(validate_repo_path(row["path"], "trace registry path", must_exist=True)))
    if namespace.get("SOURCE_COMMIT") != row["source_commit"]:
        raise PolicyError("trace registry source commit mismatch")
    if namespace.get("GENERATOR_VERSION") != row["generator_version"]:
        raise PolicyError("trace registry generator version mismatch")
    traces = namespace.get("TRACE_SHA256")
    full50 = namespace.get("FULL50")
    capacity22 = namespace.get("CAPACITY22")
    if not isinstance(traces, dict) or not isinstance(full50, tuple) or not isinstance(capacity22, tuple):
        raise PolicyError("trace registry constants are malformed")
    if any(not isinstance(name, str) or not SHA256_RE.fullmatch(value)
           for name, value in traces.items()):
        raise PolicyError("trace registry name/SHA mapping is malformed")
    return {"traces": traces, "full50": list(full50), "capacity22": list(capacity22)}


def verify_suite(
    name: str,
    suite: Any,
    registry_names: list[str],
    trace_sha: Mapping[str, str],
) -> None:
    common = {"manifest_path", "manifest_sha256", "member_count", "members", "execution_accounting"}
    if name == "capacity22":
        common.add("membership_relation")
    row = exact_object(suite, f"canonical_digital_dependency.suites.{name}", common)
    manifest_path = validate_repo_path(
        row["manifest_path"], f"canonical_digital_dependency.suites.{name}.manifest_path",
        must_exist=True,
    )
    expected_sha = validate_sha(
        row["manifest_sha256"], f"canonical_digital_dependency.suites.{name}.manifest_sha256"
    )
    payload = read_regular(manifest_path)
    if digest(payload) != expected_sha:
        raise PolicyError(f"{name} manifest digest mismatch")
    try:
        manifest = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{name} manifest invalid JSON") from exc
    members = require_string_list(row["members"], f"canonical_digital_dependency.suites.{name}.members")
    if row["member_count"] != len(members) or members != registry_names:
        raise PolicyError(f"{name} structured membership mismatch")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("runs"), list):
        raise PolicyError(f"{name} manifest runs missing")
    manifest_names = [item.get("name") for item in manifest["runs"] if isinstance(item, dict)]
    if manifest_names != members or len(manifest_names) != len(manifest["runs"]):
        raise PolicyError(f"{name} manifest membership differs from contract")
    if any(member not in trace_sha for member in members):
        raise PolicyError(f"{name} member lacks trace SHA")

    if name == "full50":
        expect(
            row["execution_accounting"],
            {
                "required_member_executions_per_candidate": 50,
                "independent_runs_contributed_to_union": 50,
            },
            "full50.execution_accounting",
        )
    else:
        expect(
            row["membership_relation"],
            {
                "relation": "EXACT_SUBSET",
                "superset": "full50",
                "independent_additional_samples": False,
            },
            "capacity22.membership_relation",
        )
        expect(
            row["execution_accounting"],
            {
                "required_member_rows_per_candidate": 22,
                "additional_independent_runs": 0,
                "independent_runs_contributed_to_union": 0,
            },
            "capacity22.execution_accounting",
        )


def verify_canonical_dependency(document: Mapping[str, Any]) -> None:
    dependency = exact_object(
        document["canonical_digital_dependency"],
        "canonical_digital_dependency",
        {
            "dependency_id",
            "status",
            "release_status",
            "policy_verifier_validates_results",
            "policy_verifier_executes_campaign",
            "native_pipeline_publication",
            "generic_campaign_v3_status",
            "trace_registry",
            "harness_bindings",
            "suites",
            "release_candidate_set",
            "qualified_evidence_fields",
            "results_embedded_in_policy",
        },
    )
    expected_scalars = {
        "dependency_id": "TEAM_REDRED_CANONICAL_DIGITAL_CAMPAIGN_V3",
        "status": "PASS_SCOPED_NATIVE_CAMPAIGN",
        "release_status": "HOLD_PHYSICAL_POWER_PDK_CDC_AND_FINAL_SELECTION",
        "policy_verifier_validates_results": True,
        "policy_verifier_executes_campaign": False,
        "generic_campaign_v3_status":
            "HOLD_SCHEMA_INCOMPATIBLE_UNBOUND_SUPERSEDED_BY_NATIVE_SUCCESSOR",
        "results_embedded_in_policy": False,
    }
    for key, value in expected_scalars.items():
        expect(dependency[key], value, f"canonical_digital_dependency.{key}")

    campaign_reference = exact_object(
        dependency["native_pipeline_publication"],
        "canonical_digital_dependency.native_pipeline_publication",
        {"commit", "path", "sha256", "reference_is_execution_evidence"},
    )
    expect(
        campaign_reference["reference_is_execution_evidence"],
        True,
        "native campaign publication evidence status",
    )
    expect(campaign_reference["commit"],
           "ccc6064a2f28f0d0476ff4cb08b25a028cb47392",
           "native campaign publication commit")
    expect(campaign_reference["path"],
           "benchmarks/redred_single_edge_campaign/native_pipeline_publication.json",
           "native campaign publication path")
    blob = git_blob(
        campaign_reference["commit"],
        campaign_reference["path"],
        "native campaign publication",
    )
    if digest(blob) != validate_sha(
        campaign_reference["sha256"], "native campaign publication.sha256"
    ):
        raise PolicyError("native campaign publication digest mismatch")
    publication = parse_json_object(blob, "native campaign publication")
    expect(publication.get("schema"),
           "redred_single_edge_native_pipeline_publication_v1",
           "native campaign publication schema")
    expect(publication.get("status"), "PASS_SCOPED_NATIVE_CAMPAIGN_PIPELINE",
           "native campaign publication status")
    expect(publication.get("noncircular_provenance"), True,
           "native campaign noncircular provenance")
    expect(publication.get("campaign_decision"), {
        "aggregate_status": "A2_PRIMARY", "campaign_recommendation": "A2",
        "final_selected_candidate": None, "final_selection_status": "HOLD",
        "release_status": "HOLD",
    }, "native campaign decision boundary")
    expect(publication.get("claim_boundary"), {
        "final_selection": "HOLD", "official": False, "physical": False,
        "power": False, "release": False,
    }, "native campaign claim boundary")

    code = exact_object(publication.get("code"), "native campaign code",
                        {"commit", "tree", "inventory"})
    payload = exact_object(publication.get("payload"), "native campaign payload",
                           {"commit", "tree", "result"})
    for label, row in (("code", code), ("payload", payload)):
        if not isinstance(row["commit"], str) or not COMMIT_RE.fullmatch(row["commit"]):
            raise PolicyError(f"native campaign {label} commit is invalid")
        expect(git_text(["rev-parse", f'{row["commit"]}^{{tree}}'],
                        f"native campaign {label} tree"), row["tree"],
               f"native campaign {label} tree")
        absent = subprocess.run(
            ["git", "cat-file", "-e",
             f'{row["commit"]}:{campaign_reference["path"]}'], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if absent.returncode == 0:
            raise PolicyError(f"native campaign publication is circular through {label}")

    inventory = code["inventory"]
    if not isinstance(inventory, list) or len(inventory) != 10:
        raise PolicyError("native campaign code inventory must contain ten artifacts")
    inventory_paths: list[str] = []
    for index, item in enumerate(inventory):
        row = exact_object(item, f"native campaign inventory[{index}]",
                           {"path", "sha256", "size_bytes", "git_blob_oid"})
        path = validate_repo_path(row["path"],
                                  f"native campaign inventory[{index}].path",
                                  must_exist=False)
        del path
        item_blob = git_blob(code["commit"], row["path"],
                             f"native campaign inventory[{index}]")
        if type(row["size_bytes"]) is not int or row["size_bytes"] != len(item_blob):
            raise PolicyError("native campaign inventory byte size mismatch")
        if digest(item_blob) != validate_sha(
                row["sha256"], f"native campaign inventory[{index}].sha256"):
            raise PolicyError("native campaign inventory digest mismatch")
        expect(git_text(["rev-parse", f'{code["commit"]}:{row["path"]}'],
                        f"native campaign inventory[{index}] object"),
               row["git_blob_oid"], f"native campaign inventory[{index}] object")
        inventory_paths.append(row["path"])
    if len(set(inventory_paths)) != len(inventory_paths):
        raise PolicyError("native campaign inventory paths contain duplicates")

    result_record = exact_object(payload["result"], "native campaign result record",
                                 {"path", "sha256", "semantic_sha256",
                                  "size_bytes", "git_blob_oid"})
    result_blob = git_blob(payload["commit"], result_record["path"],
                           "native campaign result")
    if type(result_record["size_bytes"]) is not int or \
            len(result_blob) != result_record["size_bytes"] or \
            digest(result_blob) != validate_sha(result_record["sha256"],
                                                 "native campaign result.sha256"):
        raise PolicyError("native campaign result raw identity mismatch")
    expect(git_text(["rev-parse", f'{payload["commit"]}:{result_record["path"]}'],
                    "native campaign result object"), result_record["git_blob_oid"],
           "native campaign result object")
    result = parse_json_object(result_blob, "native campaign result")
    expect(result.get("schema"), "redred_single_edge_native_pipeline_result_v1",
           "native campaign result schema")
    expect(result.get("status"), "PASS_SCOPED_NATIVE_CAMPAIGN_PIPELINE",
           "native campaign result status")
    expect(result.get("campaign_recommendation"), "A2",
           "native campaign recommendation")
    expect(result.get("claims"), {
        "final_selection": "HOLD", "official": False, "physical": False,
        "power": False, "release": False,
    }, "native campaign result claims")
    unsigned_result = dict(result)
    seal = exact_object(unsigned_result.pop("seal", None), "native campaign seal",
                        {"algorithm", "semantic_sha256"})
    expect(seal["algorithm"], "SHA256_CANONICAL_JSON_EXCLUDING_SEAL",
           "native campaign seal algorithm")
    semantic = digest(compact_canonical(unsigned_result))
    expect(seal["semantic_sha256"], semantic, "native campaign semantic seal")
    expect(result_record["semantic_sha256"], semantic,
           "native campaign published semantic seal")

    registry = load_trace_registry(dependency["trace_registry"])
    harness = dependency["harness_bindings"]
    if not isinstance(harness, list) or len(harness) != 3:
        raise PolicyError("canonical harness must bind exactly three artifacts")
    roles = []
    for index, item in enumerate(harness):
        row = exact_object(item, f"harness_bindings[{index}]", {"role", "path", "sha256"})
        roles.append(row["role"])
        verify_local_artifact(row, f"harness_bindings[{index}]", {"role"})
    expect(roles, ["TESTBENCH", "INTERFACE", "ASSERTIONS"], "harness role order")

    suites = exact_object(dependency["suites"], "canonical_digital_dependency.suites", {"full50", "capacity22"})
    verify_suite("full50", suites["full50"], registry["full50"], registry["traces"])
    verify_suite("capacity22", suites["capacity22"], registry["capacity22"], registry["traces"])
    if not set(registry["capacity22"]).issubset(registry["full50"]):
        raise PolicyError("capacity22 registry is not a full50 subset")
    if set(registry["traces"]) != set(registry["full50"]):
        raise PolicyError("trace SHA registry does not exactly cover full50")

    expect(dependency["release_candidate_set"], ["A2", "A3"], "release_candidate_set")
    expect(
        dependency["qualified_evidence_fields"],
        [
            "CAMPAIGN_STATUS_AND_SCOPE",
            "PRODUCER_NATIVE_RESULT_PUBLICATION_ARCHIVE_HASHES",
            "ADAPTER_CODE_AND_SCHEMA_GIT_OBJECTS",
            "TEAM_CANONICAL_POLICY",
            "FULL50_METRICS_AND_EXECUTION_ACCOUNTING",
            "PUBLIC_RETIMING_FAMILY_ACCOUNTING",
            "EXACT_ONCE_AND_ORDERED_ORDINALS",
            "AGGREGATE_DECISION",
            "NONRELEASE_CLAIM_BOUNDARY",
            "RESULT_RAW_AND_SEMANTIC_DIGESTS",
        ],
        "qualified_evidence_fields",
    )


def verify_cycle_semantics(document: Mapping[str, Any]) -> None:
    cycle = exact_object(
        document["cycle_semantics"],
        "cycle_semantics",
        {
            "cycle_index_domain",
            "source_model",
            "reset_model",
            "counter_definitions",
            "equations",
            "hard_error_counters_required_zero",
            "capacity_outcomes_not_hard_errors",
            "per_event_receipt_fields",
            "identity_rule",
            "ordering_rule",
            "raw_reporting",
        },
    )
    expect(cycle["cycle_index_domain"], "NONNEGATIVE_INTEGER", "cycle_index_domain")
    expect(
        cycle["source_model"],
        {
            "source_count": 16,
            "pending_depth_per_source": 1,
            "occurrence_identity_unique": True,
            "source_withdrawal_allowed": False,
            "pending_clear_phase": "POST_ACCEPT_NONBLOCKING_UPDATE",
        },
        "cycle_semantics.source_model",
    )
    expect(
        cycle["reset_model"],
        {
            "reset_signal": "rst_i",
            "assertion": "ACTIVE_HIGH_SYNCHRONOUS",
            "deassertion": "SYNCHRONOUS",
            "pending_cleared": True,
            "endpoint_state_cleared": True,
            "retire_during_reset_allowed": False,
            "drain_before_reset_required": True,
            "reset_quiet_required": True,
            "post_reset_recovery_required": True,
        },
        "cycle_semantics.reset_model",
    )
    expect(
        cycle["counter_definitions"],
        {
            "generated": "COUNT_ALL_SOURCE_OCCURRENCES",
            "source_overrun": "COUNT_OCCURRENCE_WHILE_SAME_SOURCE_PENDING_OCCUPIED",
            "accepted": "COUNT_UNIQUE_EVENTS_AT_SYNCHRONOUS_PENDING_AND_ACCEPT_COMMIT",
            "retired": "COUNT_UNIQUE_EVENTS_AT_SYNCHRONOUS_RETIRE",
            "delivered": "ALIAS_OF_RETIRED",
        },
        "cycle_semantics.counter_definitions",
    )
    expect(cycle["equations"], EXPECTED_EQUATIONS, "cycle_semantics.equations")
    expect(
        cycle["hard_error_counters_required_zero"],
        [
            "PHANTOM",
            "DUPLICATE",
            "CORRUPT",
            "REORDER",
            "ACCEPTED_MISSING",
            "PARTIAL_PAIR_RETIREMENT",
            "ILLEGAL_OR_X_OUTPUT",
            "DRAIN_TIMEOUT",
            "RESET_ESCAPE",
            "PROTOCOL_ERROR",
        ],
        "hard error counters",
    )
    expect(cycle["capacity_outcomes_not_hard_errors"], ["SOURCE_OVERRUN"], "capacity outcomes")
    expect(
        cycle["per_event_receipt_fields"],
        [
            "EVENT_ID",
            "SOURCE_ID",
            "ADDRESS",
            "TRACE_NAME",
            "TRACE_SHA256",
            "OCCURRENCE_CYCLE",
            "ACCEPT_CYCLE_OR_NULL",
            "RETIRE_CYCLE_OR_NULL",
            "DISPOSITION",
        ],
        "per-event receipt fields",
    )
    expect(cycle["identity_rule"], "ACCEPTED_EVENT_ID_RETIRES_EXACTLY_ONCE", "identity_rule")
    expect(cycle["ordering_rule"], "GLOBAL_ACCEPTANCE_ORDER", "ordering_rule")
    expect(
        cycle["raw_reporting"],
        {
            "per_trace_counters_required": True,
            "suite_aggregate_counters_required": True,
            "loss_fields": ["GENERATED", "SOURCE_OVERRUN", "ACCEPTED", "RETIRED", "OVERRUN_FRACTION"],
            "throughput_fields": ["FIXED_WINDOW_CYCLES", "FIXED_WINDOW_RETIRED", "FIXED_WINDOW_EVENTS_PER_CYCLE"],
            "latency_fields": [
                "OCCURRENCE_TO_ACCEPT_RAW_SAMPLES",
                "ACCEPT_TO_RETIRE_RAW_SAMPLES",
                "MINIMUM",
                "MAXIMUM",
                "MEAN",
                "P50",
                "P95",
                "P99",
            ],
            "invented_score_threshold_allowed": False,
        },
        "cycle_semantics.raw_reporting",
    )


def verify_physical_evidence(document: Mapping[str, Any]) -> None:
    physical = exact_object(
        document["physical_power_evidence"],
        "physical_power_evidence",
        {"inherited_6p5_standard_cell_reference", "per_interface", "core_only_reference"},
    )
    inherited = exact_object(
        physical["inherited_6p5_standard_cell_reference"],
        "inherited_6p5_standard_cell_reference",
        {
            "dependency_id",
            "evidence_class",
            "status",
            "period_ns",
            "cohort",
            "io_bits_by_composition",
            "boundary",
            "source_commit",
            "verifier_commit",
            "document_git_object",
            "evidence_archive",
            "forwarded_clock_exception",
            "final_release_eligible",
            "hold_id",
        },
    )
    expected_fixed = {
        "dependency_id": "INHERITED_6P5_STANDARD_CELL_REFERENCE",
        "evidence_class": "INHERITED_ASSERTION",
        "status": "PASS_WITH_CLAIM_LIMIT",
        "period_ns": 6.5,
        "cohort": [
            "FOVEA_A7_R1_COMPLETE_ENDPOINT",
            "A2_P6_COMPLETE_ENDPOINT",
            "A3_P6_COMPLETE_ENDPOINT",
        ],
        "io_bits_by_composition": {
            "FOVEA_A7_R1_COMPLETE_ENDPOINT": 50,
            "A2_P6_COMPLETE_ENDPOINT": 53,
            "A3_P6_COMPLETE_ENDPOINT": 53,
        },
        "boundary": "STANDARD_CELL_COMPLETE_ENDPOINT_TOP_PORTS",
        "source_commit": "b5888526ae8edfab04b768ca5c7b00a920bcad19",
        "verifier_commit": "bc61c470d75dee6adb236ca6761f32e77a250cb0",
        "forwarded_clock_exception": {
            "signal": "link_clk_o",
            "intentional": True,
            "allowed_count": 1,
            "data_endpoint_exception_count": 0,
        },
        "final_release_eligible": False,
        "hold_id": "H_INHERITED_6P5_ARCHIVE_UNAVAILABLE",
    }
    for key, value in expected_fixed.items():
        expect(inherited[key], value, f"inherited_6p5.{key}")
    document_object = exact_object(
        inherited["document_git_object"],
        "inherited_6p5.document_git_object",
        {"commit", "path", "sha256", "present_in_current_checkout"},
    )
    expect(document_object["present_in_current_checkout"], False, "6p5 document checkout state")
    blob = git_blob(document_object["commit"], document_object["path"], "inherited 6p5 document")
    if digest(blob) != validate_sha(document_object["sha256"], "inherited 6p5 document sha"):
        raise PolicyError("inherited 6p5 document digest mismatch")
    archive = exact_object(
        inherited["evidence_archive"],
        "inherited_6p5.evidence_archive",
        {"sha256", "bytes_available_to_policy_verifier", "policy_verifier_validates_archive"},
    )
    validate_sha(archive["sha256"], "inherited 6p5 archive sha")
    expect(archive["bytes_available_to_policy_verifier"], False, "archive availability")
    expect(archive["policy_verifier_validates_archive"], False, "archive validation authority")

    expect(
        physical["per_interface"],
        {
            "P6": {
                "standard_cell_post_route": "PASS_WITH_CLAIM_LIMIT",
                "vectorless_power": "HOLD_NO_QUALIFIED_COMPLETE_ENDPOINT_RECEIPT",
                "real_pad_package_channel": "UNPROVEN",
                "competition_legality": "HOLD",
            },
            "PARALLEL_FALLBACK": {
                "integrated_digital": "PASS_BOUNDED_ACTUAL_RTL_SYNTHETIC_AND_PUBLIC_PROJECTED",
                "source_cdc_rdc": "PASS_SYNCHRONOUS_INPUT_SCOPE",
                "source_structure_pdk_legality": "PASS_SOURCE_ONLY",
                "mapped_pdk_legality": "HOLD",
                "standard_cell_post_route": "HOLD_NO_REAL_PNR_OR_POST_ROUTE_TIMING",
                "vectorless_power": "HOLD_NO_REAL_MAPPED_VECTORLESS_POWER",
                "real_pad_package_channel": "UNPROVEN",
                "competition_legality": "HOLD_MAPPED_AND_ORGANIZER_AUTHORITY",
            },
        },
        "physical_power_evidence.per_interface",
    )
    expect(
        physical["core_only_reference"],
        {
            "boundary": "CORE_ONLY",
            "candidates": ["FOVEA_CORE", "CLUSTER2_CORE"],
            "status": "SEPARATE_REFERENCE_ONLY",
            "final_endpoint_ranking_eligible": False,
            "may_be_combined_with_complete_endpoint_cohort": False,
        },
        "physical_power_evidence.core_only_reference",
    )


EXPECTED_EXTERNAL_POLICY = {
    "official_dataset": {
        "status": "HOLD",
        "hold_id": "H_OFFICIAL_DATASET",
        "blocks": ["OFFICIAL_DATASET_CLAIMS", "OFFICIAL_DATA_GENERALIZATION_CLAIMS"],
        "blocks_team_canonical_release": False,
        "arrival_policy": "VERSIONED_EXTENSION_ONLY",
        "may_modify_full50_or_capacity22": False,
    },
    "team_coordinate_numeric_contract": {
        "status": "HOLD",
        "hold_id": "H_TEAM_COORDINATE_NUMERIC_CONTRACT",
        "blocks": ["COORDINATE_TRANSFORM_RTL", "COORDINATE_NUMERIC_COMPLIANCE_CLAIMS"],
        "blocks_core_endpoint_release": False,
    },
    "coordinate_demo": {
        "scope": "STRETCH",
        "placement": "AFTER_RETIRE_EXTERNAL_MODEL",
        "input": "DELIVERED_EVENTS_ONLY",
        "motion_parameters": "SUPPLIED_TIME_INDEXED_POSE",
        "inside_endpoint_ppa": False,
        "may_change_canonical_traces": False,
        "out_of_fov_is_transport_loss": False,
    },
    "pdk_endpoint_io_rules": {
        "status": "HOLD",
        "hold_id": "H_PDK_ENDPOINT_IO_RULES",
        "required_pins": [
            "PDK_INPUT_DELAY_MIN_MAX",
            "PDK_OUTPUT_DELAY_MIN_MAX",
            "PDK_INPUT_TRANSITION_OR_DRIVE_CELL",
            "PDK_OUTPUT_LOAD",
            "CLOCK_UNCERTAINTY",
            "RESET_DELAY",
        ],
        "inherited_6p5_values_are_final_competition_rules": False,
    },
    "real_pad_phy": {
        "status": "UNPROVEN",
        "hold_id": "H_REAL_PAD_PHY",
        "required_scope": ["PAD_CELLS", "PACKAGE", "CHANNEL", "SIGNAL_INTEGRITY", "CLOCK_QUALITY"],
    },
}


EXPECTED_NODES = {
    "POLICY": {"kind": "POLICY", "state": "INTERNAL_VALIDITY_CHECKED"},
    "CANONICAL_DIGITAL": {"kind": "EVIDENCE", "state": "PASS"},
    "PARALLEL_SYNTHETIC_DIGITAL": {"kind": "BOUNDED_EVIDENCE", "state": "PASS"},
    "PARALLEL_PUBLIC_PROJECTED_DIGITAL": {"kind": "BOUNDED_EVIDENCE", "state": "PASS"},
    "PARALLEL_SOURCE_CDC_RDC": {"kind": "BOUNDED_EVIDENCE", "state": "PASS"},
    "PARALLEL_SOURCE_STRUCTURE_PDK": {"kind": "BOUNDED_EVIDENCE", "state": "PASS"},
    "PARALLEL_MAPPED_PDK": {"kind": "EVIDENCE", "state": "HOLD"},
    "PARALLEL_ORGANIZER_PDK": {"kind": "EXTERNAL_AUTHORITY", "state": "HOLD"},
    "PARALLEL_REAL_PNR_POST_ROUTE": {"kind": "EVIDENCE", "state": "HOLD"},
    "PARALLEL_REAL_VECTORLESS_POWER": {"kind": "EVIDENCE", "state": "HOLD"},
    "PARALLEL_COMPLETE_EVIDENCE": {"kind": "EVIDENCE", "state": "HOLD"},
    "PARALLEL_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "SELECTED_INTERFACE_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "FINAL_CDC_RDC": {"kind": "EVIDENCE", "state": "HOLD"},
    "FINAL_A2_A3_SELECTION": {"kind": "DECISION", "state": "HOLD"},
    "PDK_ENDPOINT_IO": {"kind": "EVIDENCE", "state": "HOLD"},
    "TEAM_CANONICAL_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "OFFICIAL_DATA": {"kind": "EXTERNAL_INPUT", "state": "HOLD"},
    "OFFICIAL_DATA_CLAIMS": {"kind": "DECISION", "state": "HOLD"},
    "COORDINATE_NUMERIC_CONTRACT": {"kind": "EXTERNAL_INPUT", "state": "HOLD"},
    "COORDINATE_RTL": {"kind": "DECISION", "state": "HOLD"},
    "KNOWN_MOTION_SYNTHETIC_DEMO": {"kind": "BOUNDED_EVIDENCE", "state": "PASS"},
}


EXPECTED_REQUIREMENTS = [
    {
        "target": "PARALLEL_COMPLETE_EVIDENCE",
        "operator": "ALL",
        "sources": [
            "PARALLEL_SYNTHETIC_DIGITAL",
            "PARALLEL_PUBLIC_PROJECTED_DIGITAL",
            "PARALLEL_SOURCE_CDC_RDC",
            "PARALLEL_SOURCE_STRUCTURE_PDK",
            "PARALLEL_MAPPED_PDK",
            "PARALLEL_ORGANIZER_PDK",
            "PARALLEL_REAL_PNR_POST_ROUTE",
            "PARALLEL_REAL_VECTORLESS_POWER",
        ],
    },
    {"target": "PARALLEL_RELEASE", "operator": "ALL", "sources": ["PARALLEL_COMPLETE_EVIDENCE"]},
    {"target": "SELECTED_INTERFACE_RELEASE", "operator": "ALL", "sources": ["PARALLEL_RELEASE"]},
    {"target": "TEAM_CANONICAL_RELEASE", "operator": "ALL", "sources": ["POLICY", "CANONICAL_DIGITAL", "SELECTED_INTERFACE_RELEASE", "FINAL_CDC_RDC", "FINAL_A2_A3_SELECTION", "PDK_ENDPOINT_IO"]},
    {"target": "OFFICIAL_DATA_CLAIMS", "operator": "ALL", "sources": ["OFFICIAL_DATA", "TEAM_CANONICAL_RELEASE"]},
    {"target": "COORDINATE_RTL", "operator": "ALL", "sources": ["COORDINATE_NUMERIC_CONTRACT"]},
]


EXPECTED_FORBIDDEN_REQUIREMENTS = [
    {"target": "TEAM_CANONICAL_RELEASE", "source": "OFFICIAL_DATA"},
    {"target": "PARALLEL_RELEASE", "source": "P6_STANDARD_CELL"},
    {"target": "PARALLEL_RELEASE", "source": "P6_VECTORLESS_POWER"},
    {"target": "TEAM_CANONICAL_RELEASE", "source": "COORDINATE_NUMERIC_CONTRACT"},
]


EXPECTED_HOLDS = {
    "H_PARALLEL_REAL_EVIDENCE": {"status": "HOLD", "blocks": ["PARALLEL_MAPPED_PDK", "PARALLEL_ORGANIZER_PDK", "PARALLEL_REAL_PNR_POST_ROUTE", "PARALLEL_REAL_VECTORLESS_POWER", "PARALLEL_COMPLETE_EVIDENCE", "PARALLEL_RELEASE"], "blocks_core_development": False},
    "H_FINAL_CDC_RDC": {"status": "HOLD", "blocks": ["FINAL_CDC_RDC", "TEAM_CANONICAL_RELEASE"], "blocks_core_development": False},
    "H_FINAL_A2_A3_SELECTION": {"status": "HOLD", "blocks": ["FINAL_A2_A3_SELECTION", "TEAM_CANONICAL_RELEASE"], "blocks_core_development": False},
    "H_PDK_ENDPOINT_IO_RULES": {"status": "HOLD", "blocks": ["PDK_ENDPOINT_IO", "TEAM_CANONICAL_RELEASE"], "blocks_core_development": False},
    "H_REAL_PAD_PHY": {"status": "HOLD", "blocks": ["REAL_PAD_PHY_CLAIMS"], "blocks_core_development": False},
    "H_OFFICIAL_DATASET": {"status": "HOLD", "blocks": ["OFFICIAL_DATA", "OFFICIAL_DATA_CLAIMS"], "blocks_core_development": False},
    "H_TEAM_COORDINATE_NUMERIC_CONTRACT": {"status": "HOLD", "blocks": ["COORDINATE_NUMERIC_CONTRACT", "COORDINATE_RTL"], "blocks_core_development": False},
}


def verify_external_graph_holds(document: Mapping[str, Any]) -> None:
    expect(document["external_data_and_coordinate_policy"], EXPECTED_EXTERNAL_POLICY, "external_data_and_coordinate_policy")
    graph = exact_object(
        document["release_dependency_graph"],
        "release_dependency_graph",
        {"nodes", "requirements", "forbidden_requirements"},
    )
    expect(graph["nodes"], EXPECTED_NODES, "release_dependency_graph.nodes")
    expect(graph["requirements"], EXPECTED_REQUIREMENTS, "release_dependency_graph.requirements")
    expect(graph["forbidden_requirements"], EXPECTED_FORBIDDEN_REQUIREMENTS, "release_dependency_graph.forbidden_requirements")
    expect(document["scoped_holds"], EXPECTED_HOLDS, "scoped_holds")

    satisfied_states = {"PASS", "RELEASED", "INTERNAL_VALIDITY_CHECKED"}
    actual_pairs = {
        (item["target"], source)
        for item in graph["requirements"]
        for source in item["sources"]
    }
    for forbidden in graph["forbidden_requirements"]:
        if (forbidden["target"], forbidden["source"]) in actual_pairs:
            raise PolicyError("forbidden release dependency is active")
    for item in graph["requirements"]:
        source_states = [graph["nodes"][source]["state"] for source in item["sources"]]
        ready = (
            all(state in satisfied_states for state in source_states)
            if item["operator"] == "ALL"
            else any(state in satisfied_states for state in source_states)
        )
        target_state = graph["nodes"][item["target"]]["state"]
        if ready and target_state == "HOLD":
            raise PolicyError(f"{item['target']} is HOLD despite satisfied dependencies")
        if not ready and target_state != "HOLD":
            raise PolicyError(f"{item['target']} released with unsatisfied dependencies")


def verify_security(document: Mapping[str, Any]) -> None:
    expect(
        document["security_and_portability"],
        {
            "passwords_allowed": False,
            "license_payloads_allowed": False,
            "pdk_payloads_allowed": False,
            "absolute_or_mutable_paths_allowed": False,
            "forbidden_relative_path_components": ["tmp", "latest"],
            "artifact_references": "REPOSITORY_RELATIVE_OR_GIT_OBJECT_WITH_SHA256",
        },
        "security_and_portability",
    )

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "path" or key.endswith("_path"):
                    validate_repo_path(child, child_path, must_exist=False)
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and value.startswith("/"):
            raise PolicyError(f"absolute path-like string forbidden at {path}")

    walk(document, "$")


def verify_document(document: Mapping[str, Any]) -> None:
    root = exact_object(
        document,
        "$",
        {
            "schema_version",
            "document_kind",
            "contract_id",
            "contract_status",
            "verifier_claim",
            "goal_policy",
            "endpoint_boundary",
            "candidate_semantics",
            "interfaces",
            "bounded_current_evidence",
            "canonical_digital_dependency",
            "cycle_semantics",
            "physical_power_evidence",
            "external_data_and_coordinate_policy",
            "release_dependency_graph",
            "scoped_holds",
            "security_and_portability",
        },
    )
    expect(root["schema_version"], 3, "schema_version")
    expect(root["document_kind"], "REDRED_POLICY_AND_RELEASE_DEPENDENCY_CONTRACT", "document_kind")
    expect(root["contract_id"], "redred-system-goal-v3-2026-08-19", "contract_id")
    expect(root["contract_status"], "ACTIVE_POLICY_WITH_SCOPED_RELEASE_HOLDS", "contract_status")
    expect(
        root["verifier_claim"],
        {
            "pass_status": "POLICY_INTERNALLY_VALID",
            "evidence_qualified": False,
            "release_qualified": False,
            "result_authority": "POLICY_ONLY",
        },
        "verifier_claim",
    )
    verify_goal_boundary_candidates(root)
    verify_bounded_current_evidence(root)
    verify_canonical_dependency(root)
    verify_cycle_semantics(root)
    verify_physical_evidence(root)
    verify_external_graph_holds(root)
    verify_security(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", nargs="?", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args(argv)
    try:
        document = load_contract(args.contract)
        verify_document(document)
    except PolicyError as exc:
        print(f"REDRED_SYSTEM_GOAL_POLICY_FAIL {exc}", file=sys.stderr)
        return 1
    print(
        "REDRED_SYSTEM_GOAL_POLICY_PASS "
        f"contract={document['contract_id']} "
        "claim=POLICY_INTERNALLY_VALID "
        "evidence_qualified=false release_qualified=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
