#!/usr/bin/env python3
"""Fail-closed verifier for the REDRED active system-goal contract.

Only the Python standard library is used so the policy can be checked before
EDA tools, third-party schemas, or network services are available.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_CONTRACT = Path(__file__).with_name("active_goal.json")
MUTABLE_TEMP_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:var/)?tmp(?:/|\b)")


class ContractError(ValueError):
    """Raised when the contract is incomplete, malformed, or contradictory."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_contract(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read contract: {path}: {exc}") from exc
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ContractError) as exc:
        raise ContractError(f"invalid contract JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError("contract root must be an object")
    return document


def _object(value: Any, path: str, keys: Iterable[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{path} must be an object")
    expected = set(keys)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise ContractError(
            f"{path} field mismatch; missing={missing}, unknown={unknown}"
        )
    return value


def _list(value: Any, path: str, *, nonempty: bool = True) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    if nonempty and not value:
        raise ContractError(f"{path} must not be empty")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{path} must be a non-empty string")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{path} must be a boolean")
    return value


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{path} must be an integer")
    return value


def _expect(value: Any, expected: Any, path: str) -> None:
    if value != expected or type(value) is not type(expected):
        raise ContractError(f"{path} must equal {expected!r}, got {value!r}")


def _string_set(value: Any, path: str, required: set[str], *, exact: bool) -> set[str]:
    items = _list(value, path)
    if any(not isinstance(item, str) or not item for item in items):
        raise ContractError(f"{path} must contain only non-empty strings")
    if len(items) != len(set(items)):
        raise ContractError(f"{path} contains duplicates")
    actual = set(items)
    missing = required - actual
    unknown = actual - required if exact else set()
    if missing or unknown:
        raise ContractError(
            f"{path} set mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    return actual


def _scope_ids(items: Any, path: str) -> set[str]:
    ids: list[str] = []
    for index, item in enumerate(_list(items, path)):
        entry = _object(item, f"{path}[{index}]", {"id", "deliverable"})
        ids.append(_string(entry["id"], f"{path}[{index}].id"))
        _string(entry["deliverable"], f"{path}[{index}].deliverable")
    if len(ids) != len(set(ids)):
        raise ContractError(f"{path} contains duplicate ids")
    return set(ids)


def _walk_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _verify_goal_and_scope(document: Mapping[str, Any]) -> None:
    goal = _object(document["goal"], "goal", {"statement", "success_condition"})
    _string(goal["statement"], "goal.statement")
    _string(goal["success_condition"], "goal.success_condition")

    scope = _object(document["scope"], "scope", {"mandatory", "stretch", "excluded"})
    mandatory = _scope_ids(scope["mandatory"], "scope.mandatory")
    stretch = _scope_ids(scope["stretch"], "scope.stretch")
    _expect(
        mandatory,
        {
            "endpoint_correctness",
            "a2_primary",
            "a3_fallback",
            "interface_contingency",
            "canonical_evidence",
            "physical_release_evidence",
        },
        "scope.mandatory ids",
    )
    _expect(
        stretch,
        {"coordinate_demo", "coordinate_rtl", "motion_estimation"},
        "scope.stretch ids",
    )
    if mandatory & stretch:
        raise ContractError("mandatory and stretch scope must be disjoint")
    _string_set(
        scope["excluded"],
        "scope.excluded",
        {
            "SLAM",
            "depth recovery",
            "unknown-motion estimation in the mandatory release",
            "candidate-specific testbench storage or arbitration",
            "unsupported vendor primitives",
        },
        exact=True,
    )


def _verify_boundary(document: Mapping[str, Any]) -> None:
    boundary = _object(
        document["endpoint_boundary"],
        "endpoint_boundary",
        {
            "name",
            "starts_at",
            "ends_at",
            "includes",
            "excludes",
            "all_functional_state_is_charged",
            "same_boundary_required_for_candidate_comparison",
        },
    )
    _expect(boundary["name"], "complete_source_admission_to_retirement_endpoint", "endpoint_boundary.name")
    start = _object(
        boundary["starts_at"],
        "endpoint_boundary.starts_at",
        {"request_signal", "accept_signal", "acceptance_rule"},
    )
    _expect(start["request_signal"], "source_pending", "endpoint_boundary.starts_at.request_signal")
    _expect(start["accept_signal"], "source_accept", "endpoint_boundary.starts_at.accept_signal")
    if "synchronous" not in _string(start["acceptance_rule"], "endpoint_boundary.starts_at.acceptance_rule"):
        raise ContractError("acceptance rule must define a synchronous edge")
    end = _object(
        boundary["ends_at"],
        "endpoint_boundary.ends_at",
        {"retire_signal", "retirement_rule"},
    )
    _expect(end["retire_signal"], "retire_valid", "endpoint_boundary.ends_at.retire_signal")
    if "synchronous" not in _string(end["retirement_rule"], "endpoint_boundary.ends_at.retirement_rule"):
        raise ContractError("retirement rule must define the synchronous consumer")
    _string_set(
        boundary["includes"],
        "endpoint_boundary.includes",
        {
            "scheduler and all policy state",
            "admission control",
            "charged elastic buffering",
            "selected link launch and control",
            "transmitter",
            "physical link signals",
            "receiver",
            "retirement observation",
            "drain and error logic",
        },
        exact=True,
    )
    excluded = _string_set(
        boundary["excludes"],
        "endpoint_boundary.excludes",
        {
            "event generation before source_pending",
            "testbench-only queues",
            "coordinate transformation",
            "motion-parameter estimation",
            "visualization",
        },
        exact=True,
    )
    if "coordinate transformation" not in excluded:
        raise ContractError("coordinate transformation must remain outside the endpoint")
    _expect(boundary["all_functional_state_is_charged"], True, "endpoint_boundary.all_functional_state_is_charged")
    _expect(boundary["same_boundary_required_for_candidate_comparison"], True, "endpoint_boundary.same_boundary_required_for_candidate_comparison")


def _verify_candidates(document: Mapping[str, Any]) -> None:
    policy = _object(
        document["candidate_policy"],
        "candidate_policy",
        {"primary", "semantic_fallback", "link_policy"},
    )
    primary = _object(
        policy["primary"],
        "candidate_policy.primary",
        {"id", "architecture", "semantic_class", "weights", "preserves_exact_scalar_prefix", "claim_limit"},
    )
    _expect(primary["id"], "A2", "candidate_policy.primary.id")
    _expect(primary["architecture"], "batched_IWRR_K2", "candidate_policy.primary.architecture")
    _expect(primary["semantic_class"], "long_term_weighted_aggregate", "candidate_policy.primary.semantic_class")
    _expect(primary["weights"], [1, 5, 5, 1], "candidate_policy.primary.weights")
    _expect(primary["preserves_exact_scalar_prefix"], False, "candidate_policy.primary.preserves_exact_scalar_prefix")
    if "does not preserve" not in _string(primary["claim_limit"], "candidate_policy.primary.claim_limit"):
        raise ContractError("A2 claim limit must explicitly deny exact scalar-prefix preservation")

    fallback = _object(
        policy["semantic_fallback"],
        "candidate_policy.semantic_fallback",
        {"id", "architecture", "semantic_class", "weights", "preserves_exact_scalar_prefix", "activation_rule"},
    )
    _expect(fallback["id"], "A3", "candidate_policy.semantic_fallback.id")
    _expect(fallback["architecture"], "two_microstep_exact_scalar_prefix_K2", "candidate_policy.semantic_fallback.architecture")
    _expect(fallback["semantic_class"], "exact_scalar_prefix", "candidate_policy.semantic_fallback.semantic_class")
    _expect(fallback["weights"], [1, 5, 5, 1], "candidate_policy.semantic_fallback.weights")
    _expect(fallback["preserves_exact_scalar_prefix"], True, "candidate_policy.semantic_fallback.preserves_exact_scalar_prefix")
    activation = _string(fallback["activation_rule"], "candidate_policy.semantic_fallback.activation_rule")
    if "exact scalar-prefix" not in activation or "A2 fails" not in activation:
        raise ContractError("A3 activation must cover semantics and A2 gate failure")

    link = _object(
        policy["link_policy"],
        "candidate_policy.link_policy",
        {
            "preferred",
            "p6_description",
            "approval_recorded",
            "approval_status",
            "approval_requires",
            "selected_until_approved",
            "parallel_fallback",
        },
    )
    _expect(link["preferred"], "P6", "candidate_policy.link_policy.preferred")
    _string(link["p6_description"], "candidate_policy.link_policy.p6_description")
    _expect(link["approval_recorded"], False, "candidate_policy.link_policy.approval_recorded")
    _expect(link["approval_status"], "HOLD_PENDING_WRITTEN_ORGANIZER_AND_PDK_APPROVAL", "candidate_policy.link_policy.approval_status")
    _string_set(
        link["approval_requires"],
        "candidate_policy.link_policy.approval_requires",
        {
            "written organizer approval for the phase-related multi-edge transfer",
            "implementation using only constructs and cells available in the educational 45 nm flow",
        },
        exact=True,
    )
    _expect(link["selected_until_approved"], "single_edge_parallel_fallback", "candidate_policy.link_policy.selected_until_approved")
    parallel = _object(
        link["parallel_fallback"],
        "candidate_policy.link_policy.parallel_fallback",
        {"id", "must_be_maintained", "uses_unsupported_primitives", "same_endpoint_boundary", "selection_rule"},
    )
    _expect(parallel["id"], "single_edge_parallel_fallback", "candidate_policy.link_policy.parallel_fallback.id")
    _expect(parallel["must_be_maintained"], True, "candidate_policy.link_policy.parallel_fallback.must_be_maintained")
    _expect(parallel["uses_unsupported_primitives"], False, "candidate_policy.link_policy.parallel_fallback.uses_unsupported_primitives")
    _expect(parallel["same_endpoint_boundary"], True, "candidate_policy.link_policy.parallel_fallback.same_endpoint_boundary")
    selection = _string(parallel["selection_rule"], "candidate_policy.link_policy.parallel_fallback.selection_rule")
    if "either approval requirement" not in selection or "do not claim P6" not in selection:
        raise ContractError("parallel fallback rule is not fail-closed")


def _verify_correctness(document: Mapping[str, Any]) -> None:
    correctness = _object(
        document["correctness"],
        "correctness",
        {"equations", "counter_rules", "failure_taxonomy"},
    )
    expected_equations = [
        {
            "lhs": "generated",
            "operator": "=",
            "rhs_terms": ["source_overrun", "accepted"],
            "applies_to": "every completed run",
        },
        {
            "lhs": "accepted",
            "operator": "=",
            "rhs_terms": ["delivered"],
            "applies_to": "every hard-correct drained run",
        },
    ]
    _expect(correctness["equations"], expected_equations, "correctness.equations")
    counters = _object(
        correctness["counter_rules"],
        "correctness.counter_rules",
        {"all_nonnegative_integers", "accepted_definition", "delivered_definition", "source_overrun_definition"},
    )
    _expect(counters["all_nonnegative_integers"], True, "correctness.counter_rules.all_nonnegative_integers")
    for key in ("accepted_definition", "delivered_definition", "source_overrun_definition"):
        _string(counters[key], f"correctness.counter_rules.{key}")

    taxonomy = _object(
        correctness["failure_taxonomy"],
        "correctness.failure_taxonomy",
        {
            "hard_correctness_failures",
            "capacity_outcomes_not_hard_failures",
            "tool_exit_zero_is_sufficient",
            "unsupported_function_is_a_pass",
            "unsupported_function_status",
        },
    )
    hard = _string_set(
        taxonomy["hard_correctness_failures"],
        "correctness.failure_taxonomy.hard_correctness_failures",
        {
            "phantom",
            "duplicate",
            "corrupt",
            "reorder",
            "accepted_missing",
            "partial_pair_retirement",
            "illegal_or_x_output",
            "drain_timeout",
            "reset_escape",
            "protocol_error",
        },
        exact=True,
    )
    capacity = _string_set(
        taxonomy["capacity_outcomes_not_hard_failures"],
        "correctness.failure_taxonomy.capacity_outcomes_not_hard_failures",
        {"source_overrun"},
        exact=True,
    )
    if hard & capacity:
        raise ContractError("hard failures and capacity outcomes must be disjoint")
    _expect(taxonomy["tool_exit_zero_is_sufficient"], False, "correctness.failure_taxonomy.tool_exit_zero_is_sufficient")
    _expect(taxonomy["unsupported_function_is_a_pass"], False, "correctness.failure_taxonomy.unsupported_function_is_a_pass")
    _expect(taxonomy["unsupported_function_status"], "SKIP_UNSUPPORTED", "correctness.failure_taxonomy.unsupported_function_status")


def _verify_canonical_evidence(document: Mapping[str, Any]) -> None:
    evidence = _object(
        document["canonical_evidence"],
        "canonical_evidence",
        {
            "candidate_neutral",
            "single_canonical_head_to_head_receipt_required",
            "trace_suites",
            "subset_rule",
            "comparison_requirements",
            "required_provenance",
            "artifact_path_policy",
            "mutable_temporary_paths_allowed_in_contract_or_receipt",
        },
    )
    _expect(evidence["candidate_neutral"], True, "canonical_evidence.candidate_neutral")
    _expect(evidence["single_canonical_head_to_head_receipt_required"], True, "canonical_evidence.single_canonical_head_to_head_receipt_required")
    suites: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(_list(evidence["trace_suites"], "canonical_evidence.trace_suites")):
        suite = _object(item, f"canonical_evidence.trace_suites[{index}]", {"id", "trace_count", "role"})
        suite_id = _string(suite["id"], f"canonical_evidence.trace_suites[{index}].id")
        if suite_id in suites:
            raise ContractError("canonical_evidence.trace_suites contains duplicate ids")
        suites[suite_id] = suite
    _expect(set(suites), {"full50", "capacity22"}, "canonical_evidence trace suite ids")
    _expect(_integer(suites["full50"]["trace_count"], "full50.trace_count"), 50, "full50.trace_count")
    _expect(suites["full50"]["role"], "canonical_full_suite", "full50.role")
    _expect(_integer(suites["capacity22"]["trace_count"], "capacity22.trace_count"), 22, "capacity22.trace_count")
    _expect(suites["capacity22"]["role"], "exact_subset_of_full50", "capacity22.role")
    subset = _object(
        evidence["subset_rule"],
        "canonical_evidence.subset_rule",
        {"subset", "superset", "relationship", "counts_as_independent_additional_samples"},
    )
    _expect(subset["subset"], "capacity22", "canonical_evidence.subset_rule.subset")
    _expect(subset["superset"], "full50", "canonical_evidence.subset_rule.superset")
    _expect(subset["relationship"], "exact_trace_subset", "canonical_evidence.subset_rule.relationship")
    _expect(subset["counts_as_independent_additional_samples"], False, "canonical_evidence.subset_rule.counts_as_independent_additional_samples")
    _string_set(
        evidence["comparison_requirements"],
        "canonical_evidence.comparison_requirements",
        {
            "identical frozen demand traces",
            "identical source pending-latch semantics",
            "identical acceptance and retirement observation rules",
            "identical reset, measurement-window, and drain policy",
            "candidate-native RTL and actual selected link",
            "no testbench-added FIFO, retry, arbitration, serializer, or storage",
        },
        exact=True,
    )
    _string_set(
        evidence["required_provenance"],
        "canonical_evidence.required_provenance",
        {
            "contract_digest_sha256",
            "trace_manifest_digest_sha256",
            "per_trace_digest_sha256",
            "testbench_digest_sha256",
            "interface_digest_sha256",
            "assertion_digest_sha256",
            "candidate_rtl_digests_sha256",
            "integrated_top_digest_sha256",
            "link_rtl_digests_sha256",
            "repository_commit",
            "tool_names_and_versions",
            "exact_commands",
            "run_start_and_end_utc",
            "result_digest_sha256",
        },
        exact=True,
    )
    _expect(evidence["artifact_path_policy"], "repository_relative_or_content_addressed_immutable_location", "canonical_evidence.artifact_path_policy")
    _expect(evidence["mutable_temporary_paths_allowed_in_contract_or_receipt"], False, "canonical_evidence.mutable_temporary_paths_allowed_in_contract_or_receipt")


def _verify_coordinate_separation(document: Mapping[str, Any]) -> None:
    demo = _object(
        document["coordinate_demo"],
        "coordinate_demo",
        {
            "scope_class",
            "placement",
            "input_stream",
            "initial_motion_parameters",
            "initial_camera_model",
            "unknown_motion_estimation_in_scope",
            "may_change_core_rtl_or_canonical_aer_trace",
            "may_block_core_aer_release",
            "out_of_fov_is_transport_loss",
            "separate_metrics",
            "numeric_interface_contract_required_before_rtl",
        },
    )
    expected = {
        "scope_class": "STRETCH",
        "placement": "after_retire_external_model",
        "input_stream": "delivered_events_only",
        "initial_motion_parameters": "supplied_time_indexed_pose",
        "initial_camera_model": "calibrated_pinhole_pure_rotation",
        "unknown_motion_estimation_in_scope": False,
        "may_change_core_rtl_or_canonical_aer_trace": False,
        "may_block_core_aer_release": False,
        "out_of_fov_is_transport_loss": False,
        "numeric_interface_contract_required_before_rtl": True,
    }
    for key, value in expected.items():
        _expect(demo[key], value, f"coordinate_demo.{key}")
    _string_set(
        demo["separate_metrics"],
        "coordinate_demo.separate_metrics",
        {
            "geometric_reprojection_error",
            "valid_transformed_events",
            "out_of_fov_events",
            "event_accumulation_sharpness",
        },
        exact=True,
    )


def _verify_release_gates(document: Mapping[str, Any]) -> None:
    gates = _object(
        document["release_gates"],
        "release_gates",
        {"canonical_digital", "interface_approval", "post_route", "vectorless_power"},
    )
    digital = _object(
        gates["canonical_digital"],
        "release_gates.canonical_digital",
        {"mandatory", "gate_result", "current_evidence", "pass_requires"},
    )
    _expect(digital["mandatory"], True, "release_gates.canonical_digital.mandatory")
    _expect(digital["gate_result"], "HOLD", "release_gates.canonical_digital.gate_result")
    _expect(digital["current_evidence"], "HOLD_NO_SINGLE_CANONICAL_HEAD_TO_HEAD_RECEIPT", "release_gates.canonical_digital.current_evidence")
    _string(digital["pass_requires"], "release_gates.canonical_digital.pass_requires")

    interface = _object(
        gates["interface_approval"],
        "release_gates.interface_approval",
        {"mandatory", "gate_result", "current_evidence", "pass_requires"},
    )
    _expect(interface["mandatory"], True, "release_gates.interface_approval.mandatory")
    _expect(interface["gate_result"], "HOLD_P6_USE_FALLBACK", "release_gates.interface_approval.gate_result")
    _expect(interface["current_evidence"], "HOLD_NO_WRITTEN_P6_APPROVAL", "release_gates.interface_approval.current_evidence")
    _string(interface["pass_requires"], "release_gates.interface_approval.pass_requires")

    post = _object(
        gates["post_route"],
        "release_gates.post_route",
        {
            "mandatory",
            "gate_result",
            "qualified_operating_point_ns",
            "checks_required_zero_violation",
            "same_boundary_same_flow_same_pvt_and_load_required",
            "fresh_run_required_if_rtl_boundary_constraints_or_interface_change",
            "first_fail_bracket_available",
            "exact_fmax_claim_allowed",
        },
    )
    _expect(post["mandatory"], True, "release_gates.post_route.mandatory")
    _expect(post["gate_result"], "PASS_AT_PINNED_6P5NS_REFERENCE_BOUNDARY", "release_gates.post_route.gate_result")
    if post["qualified_operating_point_ns"] != 6.5 or isinstance(post["qualified_operating_point_ns"], bool):
        raise ContractError("release_gates.post_route.qualified_operating_point_ns must equal 6.5")
    _string_set(
        post["checks_required_zero_violation"],
        "release_gates.post_route.checks_required_zero_violation",
        {
            "setup",
            "hold",
            "recovery",
            "removal",
            "clock_gating",
            "pulse_width",
            "half_cycle",
            "DRC",
            "antenna",
            "regular_connectivity",
            "power_ground_connectivity",
            "placement",
        },
        exact=True,
    )
    for key in ("same_boundary_same_flow_same_pvt_and_load_required", "fresh_run_required_if_rtl_boundary_constraints_or_interface_change"):
        _expect(post[key], True, f"release_gates.post_route.{key}")
    _expect(post["first_fail_bracket_available"], False, "release_gates.post_route.first_fail_bracket_available")
    _expect(post["exact_fmax_claim_allowed"], False, "release_gates.post_route.exact_fmax_claim_allowed")

    power = _object(
        gates["vectorless_power"],
        "release_gates.vectorless_power",
        {
            "mandatory",
            "gate_result",
            "required_mode",
            "current_evidence",
            "same_netlist_boundary_flow_pvt_and_load_required",
            "activity_annotated_or_propagated_diagnostic_satisfies_gate",
            "ranking_requires_equal_power_method",
        },
    )
    _expect(power["mandatory"], True, "release_gates.vectorless_power.mandatory")
    _expect(power["gate_result"], "HOLD", "release_gates.vectorless_power.gate_result")
    _expect(power["required_mode"], "vectorless", "release_gates.vectorless_power.required_mode")
    _expect(power["current_evidence"], "HOLD_NO_QUALIFIED_SAME_BOUNDARY_ENDPOINT_VECTORLESS_RECEIPT", "release_gates.vectorless_power.current_evidence")
    _expect(power["same_netlist_boundary_flow_pvt_and_load_required"], True, "release_gates.vectorless_power.same_netlist_boundary_flow_pvt_and_load_required")
    _expect(power["activity_annotated_or_propagated_diagnostic_satisfies_gate"], False, "release_gates.vectorless_power.activity_annotated_or_propagated_diagnostic_satisfies_gate")
    _expect(power["ranking_requires_equal_power_method"], True, "release_gates.vectorless_power.ranking_requires_equal_power_method")


def _verify_holds_and_decisions(document: Mapping[str, Any]) -> None:
    holds = _object(
        document["holds"],
        "holds",
        {"official_dataset", "numeric_io_rules", "p6_approval", "canonical_head_to_head", "endpoint_vectorless_receipt"},
    )
    for hold_id, raw in holds.items():
        hold = _object(raw, f"holds.{hold_id}", {"status", "reason", "blocks", "blocks_core_aer_implementation", "clear_requires"})
        _expect(hold["status"], "HOLD", f"holds.{hold_id}.status")
        _string(hold["reason"], f"holds.{hold_id}.reason")
        _string_set(hold["blocks"], f"holds.{hold_id}.blocks", set(), exact=False)
        _expect(hold["blocks_core_aer_implementation"], False, f"holds.{hold_id}.blocks_core_aer_implementation")
        _string_set(hold["clear_requires"], f"holds.{hold_id}.clear_requires", set(), exact=False)

    _string_set(
        holds["official_dataset"]["clear_requires"],
        "holds.official_dataset.clear_requires",
        {
            "dataset bytes and usage terms",
            "official representation and scenario definitions",
            "immutable manifest and per-file digests",
            "documented mapping into the canonical source model",
        },
        exact=True,
    )
    _string_set(
        holds["numeric_io_rules"]["clear_requires"],
        "holds.numeric_io_rules.clear_requires",
        {
            "coordinate widths and signedness",
            "fixed-point scaling",
            "rounding and saturation rules",
            "coordinate range and out-of-frame rule",
            "timestamp and pose synchronization rule",
            "ready-valid or equivalent handshake",
            "I/O loading and timing constraints",
        },
        exact=True,
    )

    decisions = _object(
        document["release_decisions"],
        "release_decisions",
        {"core_aer_implementation", "p6_primary_link", "parallel_fallback", "coordinate_scenario_model", "coordinate_transform_rtl", "final_system_release"},
    )
    expected = {
        "core_aer_implementation": ("GO", []),
        "p6_primary_link": ("HOLD", ["p6_approval"]),
        "parallel_fallback": ("GO", []),
        "coordinate_scenario_model": ("GO_STRETCH_ONLY", []),
        "coordinate_transform_rtl": ("HOLD", ["numeric_io_rules"]),
        "final_system_release": ("HOLD", ["official_dataset", "canonical_head_to_head", "endpoint_vectorless_receipt"]),
    }
    for decision_id, (state, blockers) in expected.items():
        decision = _object(decisions[decision_id], f"release_decisions.{decision_id}", {"decision", "blockers"})
        _expect(decision["decision"], state, f"release_decisions.{decision_id}.decision")
        _expect(decision["blockers"], blockers, f"release_decisions.{decision_id}.blockers")
        for blocker in decision["blockers"]:
            if blocker not in holds or holds[blocker]["status"] != "HOLD":
                raise ContractError(f"release_decisions.{decision_id} has an inactive blocker: {blocker}")
        if state.startswith("GO") and decision["blockers"]:
            raise ContractError(f"GO decision {decision_id} cannot have blockers")
        if state == "HOLD" and not decision["blockers"]:
            raise ContractError(f"HOLD decision {decision_id} must have blockers")


def _verify_security(document: Mapping[str, Any]) -> None:
    policy = _object(
        document["security_and_portability"],
        "security_and_portability",
        {"passwords_allowed", "license_payloads_allowed", "pdk_payloads_allowed", "mutable_tmp_paths_allowed", "evidence_references_must_be_repository_relative_or_content_addressed"},
    )
    for key in ("passwords_allowed", "license_payloads_allowed", "pdk_payloads_allowed", "mutable_tmp_paths_allowed"):
        _expect(policy[key], False, f"security_and_portability.{key}")
    _expect(policy["evidence_references_must_be_repository_relative_or_content_addressed"], True, "security_and_portability.evidence_references_must_be_repository_relative_or_content_addressed")
    for path, value in _walk_strings(document):
        if MUTABLE_TEMP_PATH.search(value):
            raise ContractError(f"mutable temporary path forbidden at {path}")


def verify_document(document: Mapping[str, Any]) -> None:
    """Validate all required fields and cross-field invariants.

    This intentionally validates the current active contract, not merely a
    permissive family of possible documents. A changed policy therefore needs
    a deliberate verifier and test update in the same review.
    """

    root = _object(
        document,
        "$",
        {
            "schema_version",
            "contract_id",
            "contract_status",
            "goal",
            "scope",
            "endpoint_boundary",
            "candidate_policy",
            "correctness",
            "canonical_evidence",
            "coordinate_demo",
            "release_gates",
            "holds",
            "release_decisions",
            "security_and_portability",
        },
    )
    _expect(root["schema_version"], 1, "schema_version")
    _expect(root["contract_id"], "redred-system-goal-2026-08-18", "contract_id")
    _expect(root["contract_status"], "ACTIVE_WITH_SCOPED_HOLDS", "contract_status")
    _verify_goal_and_scope(root)
    _verify_boundary(root)
    _verify_candidates(root)
    _verify_correctness(root)
    _verify_canonical_evidence(root)
    _verify_coordinate_separation(root)
    _verify_release_gates(root)
    _verify_holds_and_decisions(root)
    _verify_security(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", nargs="?", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args(argv)
    try:
        document = load_contract(args.contract)
        verify_document(document)
    except ContractError as exc:
        print(f"REDRED_SYSTEM_GOAL_CONTRACT_FAIL {exc}", file=sys.stderr)
        return 1
    print(f"REDRED_SYSTEM_GOAL_CONTRACT_PASS {document['contract_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
