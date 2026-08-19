#!/usr/bin/env python3
"""Adversarial tests for the REDRED policy/dependency contract."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from typing import Any, Callable


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/redred_system_goal/active_goal.json"
VERIFIER = ROOT / "contracts/redred_system_goal/verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_policy", VERIFIER)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load REDRED policy verifier")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def requirement(document: dict[str, Any], target: str) -> dict[str, Any]:
    return next(
        item
        for item in document["release_dependency_graph"]["requirements"]
        if item["target"] == target
    )


class PolicyContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.good = policy.load_contract(CONTRACT)

    def assert_invalid(
        self,
        mutation: Callable[[dict[str, Any]], None],
        fragment: str | None = None,
    ) -> None:
        document = copy.deepcopy(self.good)
        mutation(document)
        with self.assertRaises(policy.PolicyError) as caught:
            policy.verify_document(document)
        if fragment is not None:
            self.assertIn(fragment, str(caught.exception))

    def test_committed_policy_is_internally_valid(self) -> None:
        policy.verify_document(copy.deepcopy(self.good))

    def test_cli_pass_is_policy_only(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFIER), str(CONTRACT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("POLICY_INTERNALLY_VALID", result.stdout)
        self.assertIn("evidence_qualified=false", result.stdout)
        self.assertIn("release_qualified=false", result.stdout)
        self.assertNotIn("EVIDENCE_PASS", result.stdout)
        self.assertNotIn("RELEASE_PASS", result.stdout)

    def test_duplicate_json_key_is_rejected(self) -> None:
        payload = CONTRACT.read_text(encoding="utf-8").replace(
            '  "schema_version": 3,',
            '  "schema_version": 3,\n  "schema_version": 3,',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(payload, encoding="utf-8")
            with self.assertRaisesRegex(policy.PolicyError, "duplicate JSON key"):
                policy.load_contract(path)

    def test_exact_keys_reject_missing_and_unknown_fields(self) -> None:
        self.assert_invalid(lambda doc: doc.pop("scoped_holds"), "keys mismatch")
        self.assert_invalid(
            lambda doc: doc["interfaces"]["selection"].update({"implicit_go": True}),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"].update(
                {"unverified_results": {}}
            ),
            "keys mismatch",
        )
        self.assert_invalid(
            lambda doc: doc["bounded_current_evidence"].update(
                {"unbounded_release_claim": {"status": "PASS"}}
            ),
            "keys mismatch",
        )
        self.assert_invalid(
            lambda doc: doc["bounded_current_evidence"][
                "single_edge_actual_rtl_synthetic"
            ]["artifacts"]["result"].update({"mutable_alias": "latest"}),
            "keys mismatch",
        )

    def test_policy_pass_cannot_claim_evidence_or_release(self) -> None:
        for field in ("evidence_qualified", "release_qualified"):
            with self.subTest(field=field):
                self.assert_invalid(
                    lambda doc, field=field: doc["verifier_claim"].update(
                        {field: True}
                    ),
                    "verifier_claim",
                )
        self.assert_invalid(
            lambda doc: doc["verifier_claim"].update(
                {"result_authority": "EVIDENCE_AND_RELEASE"}
            ),
            "verifier_claim",
        )

    def test_only_single_edge_interface_is_selected_but_release_held(self) -> None:
        self.assert_invalid(
            lambda doc: doc["interfaces"]["selection"].update(
                {"selected": "P6", "decision": "GO"}
            ),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["goal_policy"].update(
                {
                    "selected_release_interface": "PARALLEL_FALLBACK",
                    "selected_release_interface_status": "GO",
                }
            ),
            "goal_policy",
        )

    def test_a2_semantics_reject_reviewed_mutations(self) -> None:
        mutations = [
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"persistent_rows": [1, 2]}
            ),
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"calendar_phase_persistent": False}
            ),
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"empty_slot_behavior": "ACCUMULATE_DEBT"}
            ),
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"service_debt_model": "WEIGHTED_DEBT", "debt_catch_up": True}
            ),
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"exact_scalar_prefix": True}
            ),
            lambda doc: doc["candidate_semantics"]["A2"].update(
                {"future_trace_equivalence": True}
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation, "candidate_semantics")

    def test_a3_snapshot_scope_and_activation_are_bounded(self) -> None:
        mutations = [
            lambda doc: doc["candidate_semantics"]["A3"].update(
                {"microsteps": 3}
            ),
            lambda doc: doc["candidate_semantics"]["A3"].update(
                {"snapshot_held_across_microsteps": False}
            ),
            lambda doc: doc["candidate_semantics"]["A3"].update(
                {"future_arrivals_visible_to_held_snapshot": True}
            ),
            lambda doc: doc["candidate_semantics"]["A3"].update(
                {"future_trace_equivalence": True}
            ),
            lambda doc: doc["candidate_semantics"]["A3"][
                "activation_triggers"
            ].append("SHARED_INTERFACE_FAILURE"),
            lambda doc: doc["candidate_semantics"]["A3"][
                "activation_triggers"
            ].remove("A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3"),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation, "candidate_semantics")

    def test_a4_is_research_only_and_not_a_candidate(self) -> None:
        self.assert_invalid(
            lambda doc: doc["candidate_semantics"]["A4"].update(
                {"release_candidate": True, "ranking_eligible": True}
            ),
            "candidate_semantics",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "release_candidate_set"
            ].append("A4"),
            "release_candidate_set",
        )
        self.assert_invalid(
            lambda doc: doc["goal_policy"].update({"primary_candidate": "A4"}),
            "goal_policy",
        )

    def test_p6_exact_transfer_rejects_link_leakage(self) -> None:
        mutations = [
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"].update(
                {"cell_bits": 8}
            ),
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"].update(
                {"data_wires": 6, "physical_wires_total": 7}
            ),
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"][
                "low_half"
            ].update({"launch_edge": "FALLING"}),
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"][
                "high_half"
            ].update({"launch_edge": "RISING"}),
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"].update(
                {"receiver_commit_edge": "RISING"}
            ),
            lambda doc: doc["interfaces"]["P6"]["cell_transfer"][
                "high_half"
            ].update({"payload": "CELL_BITS_8_TO_5"}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation, "interfaces")

    def test_forwarded_clock_exception_is_exact_and_data_safe(self) -> None:
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"][
                "forwarded_clock_exception"
            ].update({"intentional": False}),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"][
                "forwarded_clock_exception"
            ].update({"allowed_unconstrained_endpoint_count": 2}),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"][
                "forwarded_clock_exception"
            ].update({"data_endpoint_exceptions_allowed": 1}),
            "interfaces",
        )

    def test_p6_legality_pad_and_power_cannot_be_promoted(self) -> None:
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"][
                "competition_multi_edge_legality"
            ].update({"status": "PASS"}),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"][
                "real_pad_package_channel"
            ].update({"status": "PROVEN"}),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["interfaces"]["P6"]["vectorless_power"].update(
                {"status": "PASS"}
            ),
            "interfaces",
        )

    def test_parallel_fallback_never_borrows_p6_or_claims_go(self) -> None:
        self.assert_invalid(
            lambda doc: doc["interfaces"]["PARALLEL_FALLBACK"].update(
                {"competition_release_status": "GO"}
            ),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["interfaces"]["PARALLEL_FALLBACK"].update(
                {"may_borrow_p6_physical_evidence": True}
            ),
            "interfaces",
        )
        self.assert_invalid(
            lambda doc: doc["physical_power_evidence"]["per_interface"][
                "PARALLEL_FALLBACK"
            ].update({"standard_cell_post_route": "PASS_WITH_CLAIM_LIMIT"}),
            "per_interface",
        )

    def test_native_canonical_dependency_cannot_expand_scope_or_be_substituted(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"].update(
                {"status": "PASS_RELEASE", "release_status": "GO"}
            ),
            "canonical_digital_dependency",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"].update(
                {"results_embedded_in_policy": True}
            ),
            "canonical_digital_dependency",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "native_pipeline_publication"
            ].update({"reference_is_execution_evidence": False}),
            "native campaign publication evidence status",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "native_pipeline_publication"
            ].update({"sha256": "0" * 64}),
            "publication digest mismatch",
        )

    def test_trace_and_harness_digests_are_live_bound(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["trace_registry"].update(
                {"sha256": "0" * 64}
            ),
            "digest mismatch",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["harness_bindings"][0].update(
                {"sha256": "0" * 64}
            ),
            "digest mismatch",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["suites"]["full50"].update(
                {"manifest_sha256": "0" * 64}
            ),
            "manifest digest mismatch",
        )

    def test_capacity22_exact_subset_dependency_and_accounting(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["suites"][
                "capacity22"
            ]["members"].append("core_sparse_identity"),
            "structured membership mismatch",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["suites"][
                "capacity22"
            ]["membership_relation"].update(
                {"relation": "INDEPENDENT_SUITE", "independent_additional_samples": True}
            ),
            "membership_relation",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"]["suites"][
                "capacity22"
            ]["execution_accounting"].update(
                {"additional_independent_runs": 22, "independent_runs_contributed_to_union": 22}
            ),
            "execution_accounting",
        )

    def test_native_receipt_qualification_fields_are_exact(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "qualified_evidence_fields"
            ].remove("EXACT_ONCE_AND_ORDERED_ORDINALS"),
            "qualified_evidence_fields",
        )
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "qualified_evidence_fields"
            ].remove("FULL50_METRICS_AND_EXECUTION_ACCOUNTING"),
            "qualified_evidence_fields",
        )

    def test_cycle_equations_pending_reset_and_errors_are_exact(self) -> None:
        mutations = [
            lambda doc: doc["cycle_semantics"]["equations"][0].update(
                {"rhs": ["accepted"]}
            ),
            lambda doc: doc["cycle_semantics"]["source_model"].update(
                {"source_withdrawal_allowed": True}
            ),
            lambda doc: doc["cycle_semantics"]["source_model"].update(
                {"pending_clear_phase": "PRE_EDGE"}
            ),
            lambda doc: doc["cycle_semantics"]["reset_model"].update(
                {"retire_during_reset_allowed": True}
            ),
            lambda doc: doc["cycle_semantics"][
                "hard_error_counters_required_zero"
            ].remove("REORDER"),
            lambda doc: doc["cycle_semantics"]["per_event_receipt_fields"].remove(
                "RETIRE_CYCLE_OR_NULL"
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_active_endpoint_is_single_edge_posedge_active_high(self) -> None:
        mutations = [
            lambda doc: doc["endpoint_boundary"].update(
                {"boundary_id": "SOURCE_PENDING_ACCEPT_THROUGH_RETIRE"}
            ),
            lambda doc: doc["endpoint_boundary"]["clock_reset_contract"].update(
                {"primary_clock_port": "ref_clk_i"}
            ),
            lambda doc: doc["endpoint_boundary"]["clock_reset_contract"].update(
                {"active_edge": "NEGEDGE"}
            ),
            lambda doc: doc["endpoint_boundary"]["clock_reset_contract"].update(
                {"clock_domain_count": 2}
            ),
            lambda doc: doc["endpoint_boundary"]["clock_reset_contract"].update(
                {"forwarded_clocks_allowed": True}
            ),
            lambda doc: doc["endpoint_boundary"]["clock_reset_contract"].update(
                {"reset_polarity": "ACTIVE_LOW"}
            ),
            lambda doc: doc["endpoint_boundary"]["top_port_scope"][
                "input_roles"
            ].remove("LINK_ENABLE"),
            lambda doc: doc["cycle_semantics"]["reset_model"].update(
                {"reset_signal": "rst_n"}
            ),
            lambda doc: doc["cycle_semantics"]["reset_model"].update(
                {"drain_before_reset_required": False}
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_no_invented_score_threshold(self) -> None:
        self.assert_invalid(
            lambda doc: doc["goal_policy"].update({"score_threshold_defined": True}),
            "goal_policy",
        )
        self.assert_invalid(
            lambda doc: doc["cycle_semantics"]["raw_reporting"].update(
                {"invented_score_threshold_allowed": True}
            ),
            "raw_reporting",
        )

    def test_inherited_6p5_claim_limit_and_archive_hold_are_enforced(self) -> None:
        inherited = lambda doc: doc["physical_power_evidence"][
            "inherited_6p5_standard_cell_reference"
        ]
        self.assert_invalid(
            lambda doc: inherited(doc).update({"evidence_class": "DIRECTLY_VALIDATED"}),
            "evidence_class",
        )
        self.assert_invalid(
            lambda doc: inherited(doc).update({"final_release_eligible": True}),
            "final_release_eligible",
        )
        self.assert_invalid(
            lambda doc: inherited(doc)["evidence_archive"].update(
                {
                    "bytes_available_to_policy_verifier": True,
                    "policy_verifier_validates_archive": True,
                }
            ),
            "archive availability",
        )
        self.assert_invalid(
            lambda doc: inherited(doc)["cohort"].append("CLUSTER2_CORE"),
            "cohort",
        )
        self.assert_invalid(
            lambda doc: inherited(doc)["io_bits_by_composition"].update(
                {"A2_P6_COMPLETE_ENDPOINT": 50}
            ),
            "io_bits_by_composition",
        )

    def test_core_only_reference_stays_separate_nonranking(self) -> None:
        self.assert_invalid(
            lambda doc: doc["physical_power_evidence"]["core_only_reference"].update(
                {"final_endpoint_ranking_eligible": True}
            ),
            "core_only_reference",
        )
        self.assert_invalid(
            lambda doc: doc["physical_power_evidence"]["core_only_reference"].update(
                {"may_be_combined_with_complete_endpoint_cohort": True}
            ),
            "core_only_reference",
        )

    def test_final_cdc_rdc_is_an_explicit_release_hold(self) -> None:
        self.assert_invalid(
            lambda doc: doc["release_dependency_graph"]["nodes"][
                "FINAL_CDC_RDC"
            ].update({"state": "PASS"}),
            "nodes",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "TEAM_CANONICAL_RELEASE")["sources"].remove(
                "FINAL_CDC_RDC"
            ),
            "requirements",
        )
        self.assert_invalid(
            lambda doc: doc["scoped_holds"].pop("H_FINAL_CDC_RDC"),
            "scoped_holds",
        )

    def test_official_dataset_only_blocks_official_claims(self) -> None:
        self.assert_invalid(
            lambda doc: doc["external_data_and_coordinate_policy"][
                "official_dataset"
            ].update({"blocks_team_canonical_release": True}),
            "external_data_and_coordinate_policy",
        )
        self.assert_invalid(
            lambda doc: doc["external_data_and_coordinate_policy"][
                "official_dataset"
            ].update({"may_modify_full50_or_capacity22": True}),
            "external_data_and_coordinate_policy",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "TEAM_CANONICAL_RELEASE")["sources"].append(
                "OFFICIAL_DATA"
            ),
            "requirements",
        )

    def test_coordinate_numeric_contract_is_scoped_outside_endpoint_ppa(self) -> None:
        self.assert_invalid(
            lambda doc: doc["external_data_and_coordinate_policy"][
                "coordinate_demo"
            ].update({"inside_endpoint_ppa": True}),
            "external_data_and_coordinate_policy",
        )
        self.assert_invalid(
            lambda doc: doc["endpoint_boundary"].update(
                {"coordinate_inside_endpoint_ppa": True}
            ),
            "endpoint_boundary",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "TEAM_CANONICAL_RELEASE")["sources"].append(
                "COORDINATE_NUMERIC_CONTRACT"
            ),
            "requirements",
        )

    def test_pdk_io_rules_and_real_pad_phy_cannot_be_assumed(self) -> None:
        self.assert_invalid(
            lambda doc: doc["external_data_and_coordinate_policy"][
                "pdk_endpoint_io_rules"
            ].update({"status": "PASS", "inherited_6p5_values_are_final_competition_rules": True}),
            "external_data_and_coordinate_policy",
        )
        self.assert_invalid(
            lambda doc: doc["external_data_and_coordinate_policy"][
                "real_pad_phy"
            ].update({"status": "PROVEN"}),
            "external_data_and_coordinate_policy",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "TEAM_CANONICAL_RELEASE")["sources"].remove(
                "PDK_ENDPOINT_IO"
            ),
            "requirements",
        )
        self.assert_invalid(
            lambda doc: doc["release_dependency_graph"]["nodes"][
                "TEAM_CANONICAL_RELEASE"
            ].update({"state": "RELEASED"}),
            "nodes",
        )

    def test_selected_interface_cannot_borrow_p6_evidence(self) -> None:
        self.assert_invalid(
            lambda doc: requirement(doc, "PARALLEL_RELEASE")["sources"].append(
                "P6_STANDARD_CELL"
            ),
            "requirements",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "PARALLEL_RELEASE")["sources"].append(
                "P6_VECTORLESS_POWER"
            ),
            "requirements",
        )

    def test_bounded_single_edge_actual_rtl_claim_is_exact_and_live_bound(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "single_edge_actual_rtl_synthetic"
        ]
        mutations = [
            lambda doc: evidence(doc).update({"claim_scope": "CANONICAL_RELEASE_EVIDENCE"}),
            lambda doc: evidence(doc).update({"release_status": "GO"}),
            lambda doc: evidence(doc).update({"selection_status": "A2"}),
            lambda doc: evidence(doc).update({"p6_evidence_used": True}),
            lambda doc: evidence(doc).update({"source_commit": "4ce4836fab1309d3468db8e660d2da9af371f784"}),
            lambda doc: evidence(doc)["full50_aggregate"]["A2"].update({"source_overrun": 0}),
            lambda doc: evidence(doc)["artifacts"]["result"].update({"sha256": "0" * 64}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_public_projection_cannot_pool_retimings_or_claim_release(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "public_uzh_projected_actual_rtl"
        ]
        mutations = [
            lambda doc: evidence(doc).update({"canonical_redred_traffic": True}),
            lambda doc: evidence(doc).update({"official_redred_traffic": True}),
            lambda doc: evidence(doc).update({"p6_evidence_used": True}),
            lambda doc: evidence(doc).update({"release_status": "GO"}),
            lambda doc: evidence(doc).update({"selection_status": "A3"}),
            lambda doc: evidence(doc)["dataset_accounting"].update(
                {
                    "unique_projected_window_events": 3300,
                    "retimings_are_independent_unique_samples": True,
                }
            ),
            lambda doc: evidence(doc)["artifacts"]["publication"].update(
                {"sha256": "f" * 64}
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_source_cdc_rdc_pass_stays_synchronous_input_and_source_scoped(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "single_edge_source_cdc_rdc"
        ]
        mutations = [
            lambda doc: evidence(doc).update({"external_input_scope": "ASYNCHRONOUS_INPUTS_PROVEN"}),
            lambda doc: evidence(doc).update({"mapped_cdc_rdc_status": "PASS"}),
            lambda doc: evidence(doc).update({"final_selected_interface_status": "PASS"}),
            lambda doc: evidence(doc).update({"integrated_rtl_commit": "4ce4836fab1309d3468db8e660d2da9af371f784"}),
            lambda doc: evidence(doc)["artifacts"]["source_binding"].update(
                {"sha256": "0" * 64}
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_source_pdk_pass_cannot_promote_mapped_or_organizer_legality(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "single_edge_source_structure_pdk"
        ]
        mutations = [
            lambda doc: evidence(doc).update({"claim_scope": "MAPPED_AND_ORGANIZER_APPROVED"}),
            lambda doc: evidence(doc).update({"mapped_legality_status": "PASS"}),
            lambda doc: evidence(doc).update({"organizer_legality_status": "PASS"}),
            lambda doc: evidence(doc).update({"release_status": "GO"}),
            lambda doc: evidence(doc)["artifacts"]["matrix"].update({"sha256": "0" * 64}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_real_physical_and_vectorless_rows_remain_hold(self) -> None:
        physical = lambda doc: doc["bounded_current_evidence"]["single_edge_physical"]
        vectorless = lambda doc: doc["bounded_current_evidence"]["single_edge_vectorless"]
        mutations = [
            lambda doc: physical(doc).update({"status": "PASS", "real_pnr_status": "PASS"}),
            lambda doc: physical(doc).update({"post_route_timing_status": "PASS"}),
            lambda doc: physical(doc).update({"constraint_authority_status": "ORGANIZER_APPROVED"}),
            lambda doc: vectorless(doc).update({"status": "PASS", "real_mapped_vectorless_status": "PASS"}),
            lambda doc: vectorless(doc).update({"release_comparison_eligible": True}),
            lambda doc: vectorless(doc)["artifacts"]["contract"].update({"sha256": "0" * 64}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_final_selection_readiness_is_pinned_hold_only(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "final_a2_a3_selection_readiness"
        ]
        mutations = [
            lambda doc: evidence(doc).update(
                {"status": "PASS", "selected_candidate": "A2"}),
            lambda doc: evidence(doc).update({"selection_authority": True}),
            lambda doc: evidence(doc).update({"release_authority": True}),
            lambda doc: evidence(doc).update({"missing_gate_count": 0}),
            lambda doc: evidence(doc)["artifacts"]["contract"].update(
                {"sha256": "0" * 64}),
            lambda doc: evidence(doc)["artifacts"]["verifier"].update(
                {"commit": "0" * 40}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_known_motion_pass_is_synthetic_rotation_only_and_outside_ppa(self) -> None:
        evidence = lambda doc: doc["bounded_current_evidence"][
            "known_motion_supplied_rotation_synthetic_demo"
        ]
        mutations = [
            lambda doc: evidence(doc).update({"claim_scope": "MOTION_ESTIMATION"}),
            lambda doc: evidence(doc).update({"evidence_class": "CANONICAL_COMMON_SUITE"}),
            lambda doc: evidence(doc).update({"inside_endpoint_ppa": True}),
            lambda doc: evidence(doc).update({"canonical_coordinate_status": "PASS"}),
            lambda doc: evidence(doc).update({"coordinate_rtl_status": "PASS"}),
            lambda doc: evidence(doc).update({"release_status": "GO"}),
            lambda doc: evidence(doc)["artifacts"][1].update({"sha256": "0" * 64}),
            lambda doc: evidence(doc)["artifacts"].pop(2),
            lambda doc: evidence(doc)["artifacts"].__setitem__(
                3, copy.deepcopy(evidence(doc)["artifacts"][0])
            ),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                self.assert_invalid(mutation)

    def test_final_a2_a3_selection_is_an_explicit_team_release_hold(self) -> None:
        self.assert_invalid(
            lambda doc: doc["release_dependency_graph"]["nodes"][
                "FINAL_A2_A3_SELECTION"
            ].update({"state": "PASS"}),
            "nodes",
        )
        self.assert_invalid(
            lambda doc: requirement(doc, "TEAM_CANONICAL_RELEASE")["sources"].remove(
                "FINAL_A2_A3_SELECTION"
            ),
            "requirements",
        )
        self.assert_invalid(
            lambda doc: doc["scoped_holds"].pop("H_FINAL_A2_A3_SELECTION"),
            "scoped_holds",
        )

    def test_relative_tmp_latest_and_absolute_paths_are_rejected(self) -> None:
        mutable = "/".join(["docs", "tmp", "latest", "campaign.json"])
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "native_pipeline_publication"
            ].update({"path": mutable}),
            "publication path",
        )
        absolute = "/" + "/".join(["var", "cache", "campaign.json"])
        self.assert_invalid(
            lambda doc: doc["canonical_digital_dependency"][
                "native_pipeline_publication"
            ].update({"path": absolute}),
            "publication path",
        )


if __name__ == "__main__":
    unittest.main()
