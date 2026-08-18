#!/usr/bin/env python3
"""Regression and fail-closed mutation tests for the active-goal contract."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, Callable


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / "contracts" / "redred_system_goal" / "active_goal.json"
VERIFIER = REPO_ROOT / "contracts" / "redred_system_goal" / "verify_contract.py"


def _load_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("redred_goal_verifier", VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load verifier module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verifier()


class ActiveGoalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.good = VERIFY.load_contract(CONTRACT)

    def assert_invalid(
        self,
        mutate: Callable[[dict[str, Any]], None],
        message_fragment: str | None = None,
    ) -> None:
        document = copy.deepcopy(self.good)
        mutate(document)
        with self.assertRaises(VERIFY.ContractError) as caught:
            VERIFY.verify_document(document)
        if message_fragment is not None:
            self.assertIn(message_fragment, str(caught.exception))

    def test_committed_contract_passes(self) -> None:
        VERIFY.verify_document(copy.deepcopy(self.good))

    def test_cli_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFIER), str(CONTRACT)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REDRED_SYSTEM_GOAL_CONTRACT_PASS", result.stdout)

    def test_missing_root_field_fails_closed(self) -> None:
        self.assert_invalid(lambda doc: doc.pop("holds"), "missing=['holds']")

    def test_unknown_root_field_fails_closed(self) -> None:
        self.assert_invalid(lambda doc: doc.update({"implicit_default": True}), "unknown=['implicit_default']")

    def test_duplicate_json_key_is_rejected(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        duplicate = text.replace(
            '  "schema_version": 1,',
            '  "schema_version": 1,\n  "schema_version": 1,',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaises(VERIFY.ContractError) as caught:
                VERIFY.load_contract(path)
        self.assertIn("duplicate JSON key", str(caught.exception))

    def test_a2_cannot_claim_exact_prefix(self) -> None:
        self.assert_invalid(
            lambda doc: doc["candidate_policy"]["primary"].update(
                {"preserves_exact_scalar_prefix": True}
            ),
            "primary.preserves_exact_scalar_prefix",
        )

    def test_a3_must_remain_exact_prefix_fallback(self) -> None:
        self.assert_invalid(
            lambda doc: doc["candidate_policy"]["semantic_fallback"].update(
                {"semantic_class": "long_term_weighted_aggregate"}
            ),
            "semantic_fallback.semantic_class",
        )

    def test_p6_cannot_be_selected_without_approval(self) -> None:
        self.assert_invalid(
            lambda doc: doc["candidate_policy"]["link_policy"].update(
                {"selected_until_approved": "P6"}
            ),
            "selected_until_approved",
        )

    def test_parallel_fallback_is_mandatory(self) -> None:
        self.assert_invalid(
            lambda doc: doc["candidate_policy"]["link_policy"]["parallel_fallback"].update(
                {"must_be_maintained": False}
            ),
            "must_be_maintained",
        )

    def test_endpoint_must_end_at_retire(self) -> None:
        self.assert_invalid(
            lambda doc: doc["endpoint_boundary"]["ends_at"].update(
                {"retire_signal": "tx_valid"}
            ),
            "ends_at.retire_signal",
        )

    def test_conservation_equation_cannot_be_weakened(self) -> None:
        self.assert_invalid(
            lambda doc: doc["correctness"]["equations"][1].update(
                {"rhs_terms": ["delivered", "dropped"]}
            ),
            "correctness.equations",
        )

    def test_hard_failure_cannot_be_removed(self) -> None:
        self.assert_invalid(
            lambda doc: doc["correctness"]["failure_taxonomy"]["hard_correctness_failures"].remove(
                "reorder"
            ),
            "missing=['reorder']",
        )

    def test_source_overrun_cannot_be_reclassified_as_hard_failure(self) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["correctness"]["failure_taxonomy"]["hard_correctness_failures"].append(
                "source_overrun"
            )

        self.assert_invalid(mutate, "unknown=['source_overrun']")

    def test_capacity22_cannot_be_counted_as_independent(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_evidence"]["subset_rule"].update(
                {"counts_as_independent_additional_samples": True}
            ),
            "counts_as_independent_additional_samples",
        )

    def test_required_provenance_cannot_be_omitted(self) -> None:
        self.assert_invalid(
            lambda doc: doc["canonical_evidence"]["required_provenance"].remove(
                "trace_manifest_digest_sha256"
            ),
            "missing=['trace_manifest_digest_sha256']",
        )

    def test_coordinate_demo_cannot_block_core_release(self) -> None:
        self.assert_invalid(
            lambda doc: doc["coordinate_demo"].update(
                {"may_block_core_aer_release": True}
            ),
            "may_block_core_aer_release",
        )

    def test_coordinate_demo_cannot_enter_endpoint_boundary(self) -> None:
        def mutate(doc: dict[str, Any]) -> None:
            doc["endpoint_boundary"]["excludes"].remove("coordinate transformation")
            doc["endpoint_boundary"]["includes"].append("coordinate transformation")

        self.assert_invalid(mutate, "endpoint_boundary.includes")

    def test_post_route_reference_cannot_be_called_exact_fmax(self) -> None:
        self.assert_invalid(
            lambda doc: doc["release_gates"]["post_route"].update(
                {"exact_fmax_claim_allowed": True}
            ),
            "exact_fmax_claim_allowed",
        )

    def test_vectorless_gate_rejects_activity_diagnostic_substitution(self) -> None:
        self.assert_invalid(
            lambda doc: doc["release_gates"]["vectorless_power"].update(
                {"activity_annotated_or_propagated_diagnostic_satisfies_gate": True}
            ),
            "activity_annotated_or_propagated_diagnostic_satisfies_gate",
        )

    def test_official_dataset_hold_is_required(self) -> None:
        self.assert_invalid(
            lambda doc: doc["holds"].pop("official_dataset"),
            "missing=['official_dataset']",
        )

    def test_numeric_io_hold_is_required(self) -> None:
        self.assert_invalid(
            lambda doc: doc["holds"]["numeric_io_rules"].update(
                {"status": "PASS"}
            ),
            "numeric_io_rules.status",
        )

    def test_hold_decision_must_name_active_blocker(self) -> None:
        self.assert_invalid(
            lambda doc: doc["release_decisions"]["coordinate_transform_rtl"].update(
                {"blockers": []}
            ),
            "coordinate_transform_rtl.blockers",
        )

    def test_mutable_temporary_evidence_path_is_rejected(self) -> None:
        mutable_path = "/" + "tmp" + "/latest"
        self.assert_invalid(
            lambda doc: doc["goal"].update(
                {"statement": f"Use {mutable_path} as the evidence source."}
            ),
            "mutable temporary path forbidden",
        )

    def test_security_policy_cannot_allow_sensitive_payloads(self) -> None:
        self.assert_invalid(
            lambda doc: doc["security_and_portability"].update(
                {"pdk_payloads_allowed": True}
            ),
            "pdk_payloads_allowed",
        )


if __name__ == "__main__":
    unittest.main()
