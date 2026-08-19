#!/usr/bin/env python3
"""Verify the current fail-closed REDRED A2/A3 final-selection readiness.

The authoritative CLI has no caller-controlled evidence or contract path.  It
verifies immutable Git objects and can only report the current HOLD.  The pure
policy evaluator is exercised for future decision-table coverage, but its
output is never selection or release authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTRACT_PATH = HERE / "contract.json"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
STATES = ("PASS", "HOLD", "FAIL")
SHARED_GATES = (
    "SINGLE_EDGE_INTERFACE_POLICY",
    "CANONICAL_DIGITAL",
    "ORGANIZER_CELL_CLOCK_IO_RULES",
    "OFFICIAL_CONSTRAINTS_CORNERS",
    "CONTROLLED_PRODUCER_FRESHNESS",
    "MATCHED_A2_A3_COHORT",
)
CANDIDATE_GATES = (
    "MAPPED_PDK_LEGALITY",
    "POST_ROUTE_TIMING_AREA",
    "VECTORLESS_POWER",
    "FINAL_CDC_RDC",
)
CANDIDATES = ("A2", "A3")
AGGREGATE_POLICY = "AGGREGATE_WEIGHTED_PRIMARY"
EXACT_POLICY = "EXACT_PREFIX_REQUIRED"
ARTIFACTS = {
    "policy_binding": (
        "119133d39ec7bbf4e341e515b784cfcb00e27c29",
        "contracts/redred_system_goal/active_goal.json",
        "eb13a9a7eae385968199a0f6501e63c0c2f2adfb6cd0f8ced1ba6a4857683e67",
    ),
    "canonical_campaign": (
        "ccc6064a2f28f0d0476ff4cb08b25a028cb47392",
        "benchmarks/redred_single_edge_campaign/native_pipeline_publication.json",
        "89a8439fc3c5796293b56f2dcc96f2bb2141d1d2d1041bff8ca84a09e581c93c",
    ),
    "endpoint_physical_contract": (
        "15593a72d68867641196992dd31bd00ef5dacaac",
        "physical/k2_single_edge_endpoint/contract.json",
        "6e0e8bb0381419bbb556561314f7bea774c4e131fddf904517baf13ae4232544",
    ),
    "vectorless_contract": (
        "dd1d30cfcb84aa1b760b8026af2807a11d84940b",
        "physical/k2_single_edge_vectorless/contract.json",
        "48759f420246ea102fb5ff0bbfc441d90689fc51f03375c81e3ff9f196e6c7a0",
    ),
    "pdk_legality_matrix": (
        "dd1d30cfcb84aa1b760b8026af2807a11d84940b",
        "tests/redred_single_edge_pdk_legality/legality_matrix.json",
        "edd0ddfb33b3cde02ec90a482cd0ed61436ea4434be4d2bc1a3ac22b737dd543",
    ),
    "source_cdc_rdc_contract": (
        "9d1dced49d3fceabf812d2ba2275c8d4c02eef13",
        "contracts/redred_single_edge_cdc_rdc/contract.json",
        "c4cbe85d704274a2f5d41a80652222880761465abeeca23df5b8291a7b4db44d",
    ),
}


class SelectionContractError(ValueError):
    """The selection policy or one of its immutable inputs is invalid."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SelectionContractError(message)


def exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == keys,
            f"{label} keys differ missing={sorted(keys-set(value))} "
            f"unknown={sorted(set(value)-keys)}")
    return value


def duplicate_safe(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload, object_pairs_hook=duplicate_safe,
                           parse_constant=lambda item: (_ for _ in ()).throw(
                               SelectionContractError(
                                   f"non-finite JSON value in {label}: {item}")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SelectionContractError(f"invalid JSON in {label}: {error}") from error
    require(isinstance(value, dict), f"{label} root must be an object")
    return value


def read_contract() -> dict[str, Any]:
    require(not CONTRACT_PATH.is_symlink() and CONTRACT_PATH.is_file(),
            "fixed contract path is not a regular file")
    before = CONTRACT_PATH.stat()
    payload = CONTRACT_PATH.read_bytes()
    after = CONTRACT_PATH.stat()
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size,
                            row.st_mtime_ns, row.st_ctime_ns)
    require(identity(before) == identity(after), "fixed contract changed while read")
    return parse_json(payload, "selection contract")


def validate_repo_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label} must be a path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value,
            f"{label} must be repository-relative and normalized")
    require(all(part not in {"", ".", "..", "tmp", "latest"}
                for part in path.parts), f"{label} contains a forbidden component")
    return value


def safe_git(*arguments: str) -> bytes:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    completed = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(ROOT), *arguments],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    require(completed.returncode == 0,
            f"sanitized Git command failed: {' '.join(arguments)}")
    return completed.stdout


def artifact_payload(row: Any, label: str,
                     expected: tuple[str, str, str]) -> bytes:
    item = exact(row, {"commit", "path", "sha256"}, label)
    commit, path, digest = expected
    require((item["commit"], item["path"], item["sha256"]) == expected,
            f"{label} immutable identity changed")
    require(COMMIT_RE.fullmatch(commit) is not None, f"{label} commit malformed")
    validate_repo_path(path, f"{label}.path")
    require(SHA_RE.fullmatch(digest) is not None, f"{label} digest malformed")
    tree = safe_git("ls-tree", commit, "--", path).decode("utf-8").rstrip("\n")
    require("\t" in tree, f"{label} Git path is absent")
    metadata, observed = tree.split("\t", 1)
    fields = metadata.split()
    require(observed == path and len(fields) == 3 and fields[1] == "blob" and
            fields[0] in {"100644", "100755"},
            f"{label} is not a regular Git blob")
    payload = safe_git("show", f"{commit}:{path}")
    require(hashlib.sha256(payload).hexdigest() == digest,
            f"{label} Git payload digest mismatch")
    return payload


def validate_policy(document: dict[str, Any]) -> None:
    require(document.get("schema_version") == 3 and
            document.get("contract_id") == "redred-system-goal-v3-2026-08-19",
            "goal policy version mismatch")
    goal = document.get("goal_policy", {})
    require(goal.get("primary_candidate") == "A2" and
            goal.get("semantic_fallback") == "A3" and
            goal.get("selected_release_interface") == "PARALLEL_FALLBACK" and
            goal.get("selected_release_interface_status") ==
            "IMPLEMENTED_RELEASE_HELD" and
            goal.get("score_threshold_defined") is False,
            "goal candidate/interface policy mismatch")
    triggers = document.get("candidate_semantics", {}).get("A3", {}).get(
        "activation_triggers")
    require(triggers == ["EXACT_PREFIX_REQUIRED",
                         "A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3"],
            "A3 activation policy mismatch")
    require(document.get("interfaces", {}).get("P6", {}).get("role") ==
            "HISTORICAL_RESEARCH_REFERENCE_ONLY",
            "P6 regained current authority")
    fallback = document.get("interfaces", {}).get("PARALLEL_FALLBACK", {})
    require(fallback.get("mapped_pdk_legality") == "HOLD" and
            fallback.get("post_route_evidence") ==
            "HOLD_NO_REAL_PNR_OR_POST_ROUTE_TIMING" and
            fallback.get("vectorless_power_evidence") ==
            "HOLD_NO_REAL_MAPPED_VECTORLESS_POWER" and
            fallback.get("may_borrow_p6_physical_evidence") is False,
            "goal physical evidence boundary mismatch")
    nodes = document.get("release_dependency_graph", {}).get("nodes", {})
    required = {
        "CANONICAL_DIGITAL": "PASS",
        "PARALLEL_MAPPED_PDK": "HOLD",
        "PARALLEL_ORGANIZER_PDK": "HOLD",
        "PARALLEL_REAL_PNR_POST_ROUTE": "HOLD",
        "PARALLEL_REAL_VECTORLESS_POWER": "HOLD",
        "FINAL_CDC_RDC": "HOLD",
        "FINAL_A2_A3_SELECTION": "HOLD",
        "PDK_ENDPOINT_IO": "HOLD",
    }
    require(all(nodes.get(key, {}).get("state") == state
                for key, state in required.items()),
            "goal release graph boundary mismatch")


def validate_campaign(document: dict[str, Any]) -> None:
    require(document.get("schema") ==
            "redred_single_edge_native_pipeline_publication_v1" and
            document.get("status") == "PASS_SCOPED_NATIVE_CAMPAIGN_PIPELINE" and
            document.get("noncircular_provenance") is True,
            "canonical campaign publication mismatch")
    require(document.get("campaign_decision") == {
        "aggregate_status": "A2_PRIMARY",
        "campaign_recommendation": "A2",
        "final_selected_candidate": None,
        "final_selection_status": "HOLD",
        "release_status": "HOLD",
    }, "canonical campaign decision boundary mismatch")
    require(document.get("claim_boundary") == {
        "final_selection": "HOLD", "official": False, "physical": False,
        "power": False, "release": False,
    }, "canonical campaign claim boundary mismatch")


def derive_current_observation(contract: Mapping[str, Any]) -> dict[str, Any]:
    bindings = exact(contract["evidence_bindings"], set(ARTIFACTS) - {"policy_binding"},
                     "evidence_bindings")
    policy = parse_json(artifact_payload(contract["policy_binding"],
                                         "policy_binding",
                                         ARTIFACTS["policy_binding"]), "goal policy")
    campaign = parse_json(artifact_payload(bindings["canonical_campaign"],
                                           "canonical_campaign",
                                           ARTIFACTS["canonical_campaign"]),
                          "canonical campaign")
    physical = parse_json(artifact_payload(bindings["endpoint_physical_contract"],
                                           "endpoint_physical_contract",
                                           ARTIFACTS["endpoint_physical_contract"]),
                          "endpoint physical contract")
    vectorless = parse_json(artifact_payload(bindings["vectorless_contract"],
                                             "vectorless_contract",
                                             ARTIFACTS["vectorless_contract"]),
                            "vectorless contract")
    pdk = parse_json(artifact_payload(bindings["pdk_legality_matrix"],
                                     "pdk_legality_matrix",
                                     ARTIFACTS["pdk_legality_matrix"]),
                    "PDK legality matrix")
    cdc = parse_json(artifact_payload(bindings["source_cdc_rdc_contract"],
                                     "source_cdc_rdc_contract",
                                     ARTIFACTS["source_cdc_rdc_contract"]),
                    "source CDC/RDC contract")

    validate_policy(policy)
    validate_campaign(campaign)
    require(physical.get("schema") == "k2_single_edge_endpoint_physical_contract_v1" and
            physical.get("status") == "STATIC_READY_CANDIDATE_PHYSICAL_HOLD" and
            physical.get("candidate_order") == ["a2", "a3"],
            "endpoint physical contract identity mismatch")
    require(physical.get("constraints", {}).get("authority_status") ==
            "UNCONFIRMED_TEAM_PLACEHOLDER" and
            physical.get("constraints", {}).get("candidate_go_eligible") is False,
            "endpoint constraint authority was promoted")
    qualification = physical.get("qualification", {})
    require(qualification.get("producer_authentication_available") is False and
            qualification.get("cohort_freshness_authority_available") is False and
            qualification.get("candidate_physical_go_possible") is False,
            "endpoint producer or GO boundary was promoted")

    require(vectorless.get("schema") == "k2_single_edge_vectorless_contract_v2" and
            vectorless.get("status") ==
            "DIAGNOSTIC_ONLY_PLACEHOLDER_IO_NO_CONTROLLED_PRODUCER" and
            vectorless.get("decision_policy", {}).get("candidate_order") ==
            ["a2_single_edge", "a3_single_edge"],
            "vectorless contract identity mismatch")
    require(vectorless.get("decision_policy", {}).get("candidate_go_possible") is False and
            vectorless.get("decision_policy", {}).get("comparison_ready_possible") is False and
            vectorless.get("constraint_authority", {}).get(
                "external_authority_available") is False,
            "vectorless eligibility boundary was promoted")
    provenance = vectorless.get("diagnostic_provenance", {})
    require(provenance.get("controlled_runner_available") is False and
            provenance.get("freshness_or_replay_protection_available") is False and
            provenance.get("keyring_or_hmac_accepted") is False,
            "vectorless provenance boundary was promoted")

    audited = pdk.get("audited_rtl", {})
    require(pdk.get("schema") == "redred_single_edge_gpdk045_legality_matrix_v2" and
            pdk.get("decision") == "HOLD" and
            audited.get("source_structure_status") == "PASS" and
            audited.get("mapped_structure_status") == "HOLD" and
            audited.get("organizer_approval_status") == "HOLD",
            "mapped PDK legality boundary mismatch")
    require(cdc.get("schema") == "redred-single-edge-cdc-rdc-contract-v1" and
            cdc.get("decision") == "BOUND_VERIFY_REQUIRED" and
            cdc.get("policy", {}).get("clock_domains") == 1 and
            cdc.get("policy", {}).get("clock_edge") == "posedge" and
            cdc.get("policy", {}).get("unknown_modules_or_clocks") == "FAIL",
            "source CDC/RDC contract mismatch")

    holds = {gate: "HOLD" for gate in CANDIDATE_GATES}
    return {
        "shared_gates": {
            "SINGLE_EDGE_INTERFACE_POLICY": "PASS",
            "CANONICAL_DIGITAL": "PASS",
            "ORGANIZER_CELL_CLOCK_IO_RULES": "HOLD",
            "OFFICIAL_CONSTRAINTS_CORNERS": "HOLD",
            "CONTROLLED_PRODUCER_FRESHNESS": "HOLD",
            "MATCHED_A2_A3_COHORT": "HOLD",
        },
        "candidate_gates": {"A2": dict(holds), "A3": dict(holds)},
        "semantic_requirement": AGGREGATE_POLICY,
        "a2_specific_failures": [],
    }


def evaluate_policy(observation: Mapping[str, Any]) -> dict[str, Any]:
    exact(observation, {"shared_gates", "candidate_gates",
                        "semantic_requirement", "a2_specific_failures"},
          "observation")
    shared = exact(observation["shared_gates"], set(SHARED_GATES), "shared_gates")
    candidates = exact(observation["candidate_gates"], set(CANDIDATES),
                       "candidate_gates")
    for candidate in CANDIDATES:
        exact(candidates[candidate], set(CANDIDATE_GATES),
              f"candidate_gates.{candidate}")
    require(all(value in STATES for value in shared.values()),
            "shared gate state is invalid")
    require(all(value in STATES for candidate in CANDIDATES
                for value in candidates[candidate].values()),
            "candidate gate state is invalid")
    semantic = observation["semantic_requirement"]
    require(semantic in {AGGREGATE_POLICY, EXACT_POLICY},
            "semantic requirement is invalid")
    declared = observation["a2_specific_failures"]
    require(isinstance(declared, list) and len(declared) == len(set(declared)) and
            all(item in CANDIDATE_GATES for item in declared),
            "A2-specific failure list is invalid")

    missing = [gate for gate in SHARED_GATES if shared[gate] == "HOLD"]
    missing += [f"{candidate}:{gate}" for candidate in CANDIDATES
                for gate in CANDIDATE_GATES
                if candidates[candidate][gate] == "HOLD"]
    failed = [gate for gate in SHARED_GATES if shared[gate] == "FAIL"]
    failed += [f"{candidate}:{gate}" for candidate in CANDIDATES
               for gate in CANDIDATE_GATES
               if candidates[candidate][gate] == "FAIL"]
    base = {
        "policy_candidate": None,
        "fallback_trigger": None,
        "missing_gate_ids": missing,
        "failed_gate_ids": failed,
        "final_selection_authority": False,
        "release_authority": False,
        "official_score_winner": False,
    }
    if missing:
        return {"policy_status": "HOLD_MISSING_EVIDENCE", **base}
    if any(shared[gate] == "FAIL" for gate in SHARED_GATES):
        return {"policy_status": "FAIL_SHARED_GATE", **base}
    if any(candidates["A3"][gate] == "FAIL" for gate in CANDIDATE_GATES):
        return {"policy_status": "FAIL_NO_ELIGIBLE_FALLBACK", **base}

    a2_failures = [gate for gate in CANDIDATE_GATES
                   if candidates["A2"][gate] == "FAIL"]
    require(set(declared) == set(a2_failures),
            "declared A2-specific failures do not equal evidenced FAIL gates")
    if semantic == EXACT_POLICY:
        return {
            "policy_status": "ELIGIBLE_A3_EXACT_PREFIX_NOT_PUBLISHED",
            **base, "policy_candidate": "A3",
            "fallback_trigger": "EXACT_PREFIX_REQUIRED",
        }
    if a2_failures:
        return {
            "policy_status": "ELIGIBLE_A3_A2_SPECIFIC_FAILURE_NOT_PUBLISHED",
            **base, "policy_candidate": "A3",
            "fallback_trigger":
                "A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3",
        }
    return {
        "policy_status": "ELIGIBLE_A2_PRIMARY_NOT_PUBLISHED",
        **base, "policy_candidate": "A2",
    }


def current_decision(observation: Mapping[str, Any]) -> dict[str, Any]:
    policy = evaluate_policy(observation)
    require(policy["policy_status"] == "HOLD_MISSING_EVIDENCE",
            "current immutable evidence unexpectedly escaped HOLD")
    return {
        "selection_status": "HOLD",
        "selected_candidate": None,
        "campaign_recommendation": "A2",
        "fallback_trigger": None,
        "missing_gate_ids": policy["missing_gate_ids"],
        "failed_gate_ids": policy["failed_gate_ids"],
        "final_selection_authority": False,
        "release_authority": False,
        "official_score_winner": False,
    }


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    exact(contract, {
        "schema", "contract_id", "status", "claim_scope", "authority",
        "policy_binding", "candidate_policy", "decision_model",
        "evidence_bindings", "current_observation", "current_decision", "security",
    }, "contract")
    require(contract["schema"] == "redred_final_a2_a3_selection_contract_v1" and
            contract["contract_id"] ==
            "redred-final-a2-a3-selection-v1-2026-08-19" and
            contract["status"] ==
            "HOLD_MISSING_AUTHENTICATED_MATCHED_PHYSICAL_POWER_PDK_CDC_EVIDENCE" and
            contract["claim_scope"] ==
            "CURRENT_SINGLE_EDGE_A2_A3_FINAL_SELECTION_READINESS_ONLY",
            "contract identity or HOLD boundary changed")
    authority = exact(contract["authority"], {
        "policy_engine_only", "current_selection_authority",
        "release_authority", "official_score_authority",
        "caller_supplied_evidence_allowed",
    }, "authority")
    require(authority == {
        "policy_engine_only": True,
        "current_selection_authority": False,
        "release_authority": False,
        "official_score_authority": False,
        "caller_supplied_evidence_allowed": False,
    }, "authority boundary changed")
    policy = contract["candidate_policy"]
    exact(policy, {
        "release_interface", "candidate_order", "primary_candidate",
        "semantic_fallback", "a3_activation_triggers",
        "forbidden_fallback_triggers", "p6_or_multi_edge_evidence_allowed",
        "score_formula_defined", "invented_scalar_score_allowed",
        "default_semantic_requirement",
    }, "candidate_policy")
    require(policy == {
        "release_interface": "PARALLEL_FALLBACK",
        "candidate_order": ["A2", "A3"],
        "primary_candidate": "A2",
        "semantic_fallback": "A3",
        "a3_activation_triggers": [
            "EXACT_PREFIX_REQUIRED",
            "A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3"],
        "forbidden_fallback_triggers": [
            "SHARED_INTERFACE_FAILURE", "SHARED_EVIDENCE_FAILURE",
            "SHARED_CDC_RDC_FAILURE", "SHARED_PDK_IO_FAILURE"],
        "p6_or_multi_edge_evidence_allowed": False,
        "score_formula_defined": False,
        "invented_scalar_score_allowed": False,
        "default_semantic_requirement": AGGREGATE_POLICY,
    }, "candidate policy changed")
    model = exact(contract["decision_model"], {
        "gate_states", "shared_gate_ids", "candidate_gate_ids",
        "ppa_metric_vector", "rules",
    }, "decision_model")
    require(model["gate_states"] == list(STATES) and
            model["shared_gate_ids"] == list(SHARED_GATES) and
            model["candidate_gate_ids"] == list(CANDIDATE_GATES) and
            isinstance(model["ppa_metric_vector"], list) and
            len(model["ppa_metric_vector"]) == 11 and
            len(model["ppa_metric_vector"]) == len(set(model["ppa_metric_vector"])) and
            isinstance(model["rules"], list) and len(model["rules"]) == 8 and
            len(model["rules"]) == len(set(model["rules"])),
            "decision model inventory changed")
    security = contract["security"]
    exact(security, {
        "authoritative_cli_uses_fixed_contract_path", "git_replace_objects_disabled",
        "git_configuration_sanitized", "duplicate_json_keys_rejected",
        "artifact_paths_repository_relative",
        "future_go_requires_separate_authenticated_payload_and_noncircular_publication",
        "unsigned_git_objects_are_producer_authentication",
        "self_hash_is_producer_authentication",
    }, "security")
    require(all(security[key] is True for key in (
        "authoritative_cli_uses_fixed_contract_path", "git_replace_objects_disabled",
        "git_configuration_sanitized", "duplicate_json_keys_rejected",
        "artifact_paths_repository_relative",
        "future_go_requires_separate_authenticated_payload_and_noncircular_publication")) and
            security["unsigned_git_objects_are_producer_authentication"] is False and
            security["self_hash_is_producer_authentication"] is False,
            "security boundary changed")

    observed = derive_current_observation(contract)
    require(contract["current_observation"] == observed,
            "declared current observation differs from immutable evidence")
    decision = current_decision(observed)
    require(contract["current_decision"] == decision,
            "declared current decision differs from recomputation")
    return decision


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the fixed current REDRED final-selection HOLD contract")
    parser.add_argument("--json", action="store_true",
                        help="print the current non-authoritative HOLD decision as JSON")
    args = parser.parse_args()
    decision = validate_contract(read_contract())
    if args.json:
        print(json.dumps(decision, sort_keys=True, separators=(",", ":")))
    else:
        print(
            "REDRED_FINAL_SELECTION_HOLD candidate=NONE "
            f"missing={len(decision['missing_gate_ids'])} "
            "selection_authority=false release_authority=false"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SelectionContractError, OSError, subprocess.SubprocessError) as error:
        print(f"REDRED_FINAL_SELECTION_CONTRACT_FAIL {error}", file=sys.stderr)
        raise SystemExit(2) from error
