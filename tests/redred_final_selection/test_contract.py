#!/usr/bin/env python3
"""Mutation tests for the current fail-closed REDRED final-selection gate."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "contracts/redred_final_selection/verify_contract.py"
SPEC = importlib.util.spec_from_file_location("redred_final_selection", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def contract() -> dict:
    return json.loads((ROOT / "contracts/redred_final_selection/contract.json").read_text())


def complete_observation() -> dict:
    return {
        "shared_gates": {name: "PASS" for name in gate.SHARED_GATES},
        "candidate_gates": {
            candidate: {name: "PASS" for name in gate.CANDIDATE_GATES}
            for candidate in gate.CANDIDATES
        },
        "semantic_requirement": gate.AGGREGATE_POLICY,
        "a2_specific_failures": [],
    }


class CurrentContractTest(unittest.TestCase):
    def reject(self, mutation, pattern: str) -> None:
        value = contract()
        mutation(value)
        with self.assertRaisesRegex(gate.SelectionContractError, pattern):
            gate.validate_contract(value)

    def test_current_immutable_evidence_recomputes_exact_hold(self) -> None:
        decision = gate.validate_contract(contract())
        self.assertEqual(decision["selection_status"], "HOLD")
        self.assertIsNone(decision["selected_candidate"])
        self.assertEqual(decision["campaign_recommendation"], "A2")
        self.assertEqual(len(decision["missing_gate_ids"]), 12)
        self.assertFalse(decision["final_selection_authority"])
        self.assertFalse(decision["release_authority"])
        self.assertFalse(decision["official_score_winner"])

    def test_cli_has_fixed_contract_and_cannot_accept_caller_evidence(self) -> None:
        passed = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        result = json.loads(passed.stdout)
        self.assertEqual(result["selection_status"], "HOLD")
        self.assertIsNone(result["selected_candidate"])
        rejected = subprocess.run(
            [sys.executable, str(SCRIPT), "--contract", "/tmp/forged.json"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unrecognized arguments", rejected.stderr)

    def test_duplicate_json_and_nonfinite_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(gate.SelectionContractError, "duplicate JSON key"):
            gate.parse_json(b'{"a":1,"a":2}', "duplicate")
        with self.assertRaisesRegex(gate.SelectionContractError, "non-finite"):
            gate.parse_json(b'{"a":NaN}', "nonfinite")

    def test_unknown_top_authority_and_security_keys_are_rejected(self) -> None:
        for mutation in (
            lambda value: value.update({"unknown": True}),
            lambda value: value["authority"].update({"unknown": True}),
            lambda value: value["candidate_policy"].update({"unknown": True}),
            lambda value: value["decision_model"].update({"unknown": True}),
            lambda value: value["security"].update({"unknown": True}),
        ):
            with self.subTest(mutation=mutation):
                self.reject(mutation, "keys differ")

    def test_every_artifact_identity_is_immutable(self) -> None:
        cases = [
            lambda value: value["policy_binding"].update({"commit": "0" * 40}),
            lambda value: value["policy_binding"].update({"sha256": "0" * 64}),
        ]
        for name in gate.ARTIFACTS:
            if name == "policy_binding":
                continue
            cases.append(lambda value, name=name: value["evidence_bindings"][name].update(
                {"sha256": "0" * 64}))
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "immutable identity")

    def test_traversal_mutation_is_rejected_without_reading_it(self) -> None:
        self.reject(
            lambda value: value["evidence_bindings"]["canonical_campaign"].update(
                {"path": "../forged.json"}),
            "immutable identity",
        )

    def test_current_hold_cannot_be_promoted_by_declared_json(self) -> None:
        self.reject(
            lambda value: value["current_observation"]["shared_gates"].update(
                {"ORGANIZER_CELL_CLOCK_IO_RULES": "PASS"}),
            "differs from immutable evidence",
        )
        self.reject(
            lambda value: value["current_decision"].update({
                "selection_status": "PASS", "selected_candidate": "A2",
                "final_selection_authority": True}),
            "differs from recomputation",
        )

    def test_p6_scalar_score_and_a3_trigger_policy_are_immutable(self) -> None:
        cases = (
            lambda value: value["candidate_policy"].update(
                {"p6_or_multi_edge_evidence_allowed": True}),
            lambda value: value["candidate_policy"].update(
                {"score_formula_defined": True}),
            lambda value: value["candidate_policy"].update(
                {"invented_scalar_score_allowed": True}),
            lambda value: value["candidate_policy"]["a3_activation_triggers"].append(
                "SHARED_EVIDENCE_FAILURE"),
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                self.reject(mutation, "candidate policy changed")

    def test_decision_gate_and_metric_inventories_are_bounded(self) -> None:
        self.reject(
            lambda value: value["decision_model"]["shared_gate_ids"].pop(),
            "decision model inventory changed")
        self.reject(
            lambda value: value["decision_model"]["candidate_gate_ids"].append(
                "PAD_PACKAGE"), "decision model inventory changed")
        self.reject(
            lambda value: value["decision_model"]["ppa_metric_vector"].append(
                "SCALAR_SCORE"), "decision model inventory changed")

    def test_safe_git_disables_replace_objects_and_sanitizes_config(self) -> None:
        completed = subprocess.CompletedProcess(["git"], 0, stdout=b"payload", stderr=b"")
        with mock.patch.object(gate.subprocess, "run", return_value=completed) as run:
            self.assertEqual(gate.safe_git("show", "HEAD:file"), b"payload")
        argv = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertIn("--no-replace-objects", argv)
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")


class PolicyDecisionTableTest(unittest.TestCase):
    def test_all_pass_uses_predeclared_a2_primary_without_score(self) -> None:
        result = gate.evaluate_policy(complete_observation())
        self.assertEqual(result["policy_status"],
                         "ELIGIBLE_A2_PRIMARY_NOT_PUBLISHED")
        self.assertEqual(result["policy_candidate"], "A2")
        self.assertFalse(result["final_selection_authority"])
        self.assertFalse(result["official_score_winner"])

    def test_exact_prefix_uses_a3_only_after_complete_evaluation(self) -> None:
        value = complete_observation()
        value["semantic_requirement"] = gate.EXACT_POLICY
        result = gate.evaluate_policy(value)
        self.assertEqual(result["policy_candidate"], "A3")
        self.assertEqual(result["fallback_trigger"], "EXACT_PREFIX_REQUIRED")
        self.assertFalse(result["final_selection_authority"])

    def test_named_a2_specific_failures_can_activate_passing_a3(self) -> None:
        value = complete_observation()
        value["candidate_gates"]["A2"]["POST_ROUTE_TIMING_AREA"] = "FAIL"
        value["a2_specific_failures"] = ["POST_ROUTE_TIMING_AREA"]
        result = gate.evaluate_policy(value)
        self.assertEqual(result["policy_candidate"], "A3")
        self.assertEqual(
            result["fallback_trigger"],
            "A2_SPECIFIC_GATE_FAILURE_INDEPENDENTLY_PASSED_BY_A3")

    def test_shared_failure_never_activates_a3(self) -> None:
        value = complete_observation()
        value["shared_gates"]["MATCHED_A2_A3_COHORT"] = "FAIL"
        result = gate.evaluate_policy(value)
        self.assertEqual(result["policy_status"], "FAIL_SHARED_GATE")
        self.assertIsNone(result["policy_candidate"])
        self.assertIsNone(result["fallback_trigger"])

    def test_a3_failure_never_selects_a2_or_a3(self) -> None:
        value = complete_observation()
        value["candidate_gates"]["A3"]["FINAL_CDC_RDC"] = "FAIL"
        result = gate.evaluate_policy(value)
        self.assertEqual(result["policy_status"], "FAIL_NO_ELIGIBLE_FALLBACK")
        self.assertIsNone(result["policy_candidate"])

    def test_any_hold_returns_hold_even_when_exact_prefix_is_requested(self) -> None:
        value = complete_observation()
        value["semantic_requirement"] = gate.EXACT_POLICY
        value["candidate_gates"]["A2"]["VECTORLESS_POWER"] = "HOLD"
        result = gate.evaluate_policy(value)
        self.assertEqual(result["policy_status"], "HOLD_MISSING_EVIDENCE")
        self.assertIsNone(result["policy_candidate"])
        self.assertEqual(result["missing_gate_ids"], ["A2:VECTORLESS_POWER"])

    def test_declared_a2_failure_must_equal_evidenced_failures(self) -> None:
        value = complete_observation()
        value["a2_specific_failures"] = ["POST_ROUTE_TIMING_AREA"]
        with self.assertRaisesRegex(gate.SelectionContractError,
                                    "do not equal evidenced"):
            gate.evaluate_policy(value)

    def test_unknown_missing_and_invalid_gate_states_fail_closed(self) -> None:
        cases = (
            lambda value: value["shared_gates"].update({"UNKNOWN": "PASS"}),
            lambda value: value["shared_gates"].pop("CANONICAL_DIGITAL"),
            lambda value: value["candidate_gates"]["A2"].update(
                {"VECTORLESS_POWER": "GO"}),
            lambda value: value.update({"semantic_requirement": "LOWEST_POWER"}),
        )
        for mutation in cases:
            value = complete_observation()
            mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(
                    gate.SelectionContractError):
                gate.evaluate_policy(value)

    def test_policy_simulation_never_claims_selection_or_release_authority(self) -> None:
        variants = []
        variants.append(complete_observation())
        exact_value = complete_observation()
        exact_value["semantic_requirement"] = gate.EXACT_POLICY
        variants.append(exact_value)
        fallback = complete_observation()
        fallback["candidate_gates"]["A2"]["MAPPED_PDK_LEGALITY"] = "FAIL"
        fallback["a2_specific_failures"] = ["MAPPED_PDK_LEGALITY"]
        variants.append(fallback)
        for value in variants:
            result = gate.evaluate_policy(value)
            self.assertFalse(result["final_selection_authority"])
            self.assertFalse(result["release_authority"])
            self.assertFalse(result["official_score_winner"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
