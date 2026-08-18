#!/usr/bin/env python3
"""Verify REDRED policy structure and dependency honesty, never evidence PASS."""

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


EXPECTED_GOAL = {
    "primary_candidate": "A2",
    "semantic_fallback": "A3",
    "selected_release_interface": None,
    "selected_release_interface_status": "HOLD",
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
    "boundary_id": "SOURCE_PENDING_ACCEPT_THROUGH_RETIRE",
    "request": {
        "signal": "source_pending",
        "state_model": "ONE_ENTRY_PENDING_LATCH_PER_SOURCE",
    },
    "acceptance": {
        "signal": "source_accept",
        "sample_edge": "ACTIVE_SYNCHRONOUS_EDGE",
        "condition": "PENDING_PRE_EDGE_AND_ACCEPT_PRE_EDGE",
    },
    "retirement": {
        "valid_signal": "retire_valid",
        "sample_edge": "SYNCHRONOUS_CONSUMER_EDGE",
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
        "kind": "STANDARD_CELL_LOGIC_PORTS",
        "input_roles": ["REF_CLOCK", "SAMPLE_CLOCK", "RESET_N", "SOURCE_PENDING_16"],
        "output_roles": [
            "SOURCE_ACCEPT_16",
            "FORWARDED_LINK_CLOCK",
            "LINK_DATA",
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
        "decision": "HOLD",
        "selected": None,
        "selection_rule": "ONE_INTERFACE_WITH_OWN_COMPLETE_DIGITAL_PNR_POWER_AND_LEGALITY",
        "cross_interface_evidence_borrowing": False,
    },
    "P6": {
        "role": "PREFERRED_IF_LEGAL_AND_QUALIFIED",
        "competition_release_status": "HOLD",
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
        "role": "LEGALITY_FALLBACK",
        "competition_release_status": "HOLD_NO_INTEGRATED_DIGITAL_PNR_POWER",
        "transfer_mode": "SINGLE_EDGE_PARALLEL",
        "integrated_digital_evidence": "MISSING",
        "post_route_evidence": "MISSING",
        "vectorless_power_evidence": "MISSING",
        "hold_id": "H_PARALLEL_INTEGRATION",
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
            "hold_id",
            "policy_verifier_validates_results",
            "policy_verifier_executes_campaign",
            "external_campaign_contract_reference",
            "trace_registry",
            "harness_bindings",
            "suites",
            "release_candidate_set",
            "required_external_receipt_fields",
            "results_embedded_in_policy",
        },
    )
    expected_scalars = {
        "dependency_id": "TEAM_REDRED_CANONICAL_DIGITAL_CAMPAIGN_V2",
        "status": "HOLD_EXTERNAL_CAMPAIGN_NOT_QUALIFIED_BY_POLICY",
        "hold_id": "H_CANONICAL_DIGITAL",
        "policy_verifier_validates_results": False,
        "policy_verifier_executes_campaign": False,
        "results_embedded_in_policy": False,
    }
    for key, value in expected_scalars.items():
        expect(dependency[key], value, f"canonical_digital_dependency.{key}")

    campaign_reference = exact_object(
        dependency["external_campaign_contract_reference"],
        "canonical_digital_dependency.external_campaign_contract_reference",
        {"commit", "path", "sha256", "reference_is_execution_evidence"},
    )
    expect(
        campaign_reference["reference_is_execution_evidence"],
        False,
        "external campaign reference evidence status",
    )
    blob = git_blob(
        campaign_reference["commit"],
        campaign_reference["path"],
        "external campaign contract reference",
    )
    if digest(blob) != validate_sha(
        campaign_reference["sha256"], "external campaign reference.sha256"
    ):
        raise PolicyError("external campaign contract reference digest mismatch")

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

    expect(dependency["release_candidate_set"], ["FOVEA", "CLUSTER2", "A2", "A3"], "release_candidate_set")
    expect(
        dependency["required_external_receipt_fields"],
        [
            "CAMPAIGN_SCHEMA_AND_STATUS",
            "CANDIDATE_ID_AND_RTL_DIGESTS",
            "SELECTED_INTERFACE_AND_LINK_RTL_DIGESTS",
            "TRACE_NAME_AND_TRACE_SHA256",
            "MANIFEST_AND_MEMBERSHIP_DIGESTS",
            "TOOL_IDENTITY",
            "EXACT_COMMAND",
            "START_END_UTC",
            "PER_TRACE_RAW_COUNTERS",
            "PER_EVENT_LEDGER",
            "RESET_AND_DRAIN_RESULT",
            "EXECUTION_COUNTS",
            "RESULT_DIGEST",
        ],
        "required_external_receipt_fields",
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
            "reset_signal": "rst_n",
            "assertion": "ACTIVE_LOW",
            "pending_cleared": True,
            "endpoint_state_cleared": True,
            "retire_during_reset_allowed": False,
            "post_reset_drain_required": True,
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
                "integrated_digital": "MISSING",
                "standard_cell_post_route": "MISSING",
                "vectorless_power": "MISSING",
                "real_pad_package_channel": "UNPROVEN",
                "competition_legality": "NOT_YET_EVALUATED",
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
    "CANONICAL_DIGITAL": {"kind": "EVIDENCE", "state": "HOLD"},
    "P6_STANDARD_CELL": {"kind": "EVIDENCE", "state": "PASS_WITH_CLAIM_LIMIT"},
    "P6_LEGALITY": {"kind": "EVIDENCE", "state": "HOLD"},
    "P6_PAD_PHY": {"kind": "EVIDENCE", "state": "HOLD"},
    "P6_VECTORLESS_POWER": {"kind": "EVIDENCE", "state": "HOLD"},
    "P6_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "PARALLEL_COMPLETE_EVIDENCE": {"kind": "EVIDENCE", "state": "HOLD"},
    "PARALLEL_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "SELECTED_INTERFACE_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "FINAL_CDC_RDC": {"kind": "EVIDENCE", "state": "HOLD"},
    "PDK_ENDPOINT_IO": {"kind": "EVIDENCE", "state": "HOLD"},
    "TEAM_CANONICAL_RELEASE": {"kind": "DECISION", "state": "HOLD"},
    "OFFICIAL_DATA": {"kind": "EXTERNAL_INPUT", "state": "HOLD"},
    "OFFICIAL_DATA_CLAIMS": {"kind": "DECISION", "state": "HOLD"},
    "COORDINATE_NUMERIC_CONTRACT": {"kind": "EXTERNAL_INPUT", "state": "HOLD"},
    "COORDINATE_RTL": {"kind": "DECISION", "state": "HOLD"},
}


EXPECTED_REQUIREMENTS = [
    {"target": "P6_RELEASE", "operator": "ALL", "sources": ["P6_STANDARD_CELL", "P6_LEGALITY", "P6_PAD_PHY", "P6_VECTORLESS_POWER"]},
    {"target": "PARALLEL_RELEASE", "operator": "ALL", "sources": ["PARALLEL_COMPLETE_EVIDENCE"]},
    {"target": "SELECTED_INTERFACE_RELEASE", "operator": "ONE", "sources": ["P6_RELEASE", "PARALLEL_RELEASE"]},
    {"target": "TEAM_CANONICAL_RELEASE", "operator": "ALL", "sources": ["POLICY", "CANONICAL_DIGITAL", "SELECTED_INTERFACE_RELEASE", "FINAL_CDC_RDC", "PDK_ENDPOINT_IO"]},
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
    "H_CANONICAL_DIGITAL": {"status": "HOLD", "blocks": ["CANONICAL_DIGITAL", "TEAM_CANONICAL_RELEASE"], "blocks_core_development": False},
    "H_P6_MULTI_EDGE_LEGALITY": {"status": "HOLD", "blocks": ["P6_LEGALITY", "P6_RELEASE"], "blocks_core_development": False},
    "H_P6_REAL_PAD_PHY": {"status": "HOLD", "blocks": ["P6_PAD_PHY", "P6_RELEASE"], "blocks_core_development": False},
    "H_P6_VECTORLESS_POWER": {"status": "HOLD", "blocks": ["P6_VECTORLESS_POWER", "P6_RELEASE"], "blocks_core_development": False},
    "H_PARALLEL_INTEGRATION": {"status": "HOLD", "blocks": ["PARALLEL_COMPLETE_EVIDENCE", "PARALLEL_RELEASE"], "blocks_core_development": False},
    "H_INHERITED_6P5_ARCHIVE_UNAVAILABLE": {"status": "HOLD", "blocks": ["P6_STANDARD_CELL_FINAL_RELEASE_USE"], "blocks_core_development": False},
    "H_FINAL_CDC_RDC": {"status": "HOLD", "blocks": ["FINAL_CDC_RDC", "TEAM_CANONICAL_RELEASE"], "blocks_core_development": False},
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
            "canonical_digital_dependency",
            "cycle_semantics",
            "physical_power_evidence",
            "external_data_and_coordinate_policy",
            "release_dependency_graph",
            "scoped_holds",
            "security_and_portability",
        },
    )
    expect(root["schema_version"], 2, "schema_version")
    expect(root["document_kind"], "REDRED_POLICY_AND_RELEASE_DEPENDENCY_CONTRACT", "document_kind")
    expect(root["contract_id"], "redred-system-goal-v2-2026-08-19", "contract_id")
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
