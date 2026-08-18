#!/usr/bin/env python3
"""Adversarial tests for the campaign-native aggregate gate."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROGRAM = ROOT / "benchmarks" / "redred_single_edge_campaign" / "aggregate_gate.py"
SCHEMA = ROOT / "benchmarks" / "redred_single_edge_campaign" / "aggregate_result.schema.json"


def load_program():
    specification = importlib.util.spec_from_file_location("redred_campaign_aggregate", PROGRAM)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load aggregate gate")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


gate = load_program()
TOKEN = "a" * 64


def view(slot: str) -> dict:
    public = slot == "public_v2"
    return {
        "schema": "redred_single_edge_campaign_normalized_view_v1",
        "slot": slot,
        "verification": {
            "status": "PASS",
            "separately_verified": True,
            "adapter_id": f"{slot}-adapter-v1",
            "adapter_sha256": TOKEN,
            "source_result_sha256": "b" * 64,
            "source_publication_sha256": "c" * 64,
        },
        "classification": {
            "evidence_status": "PUBLIC_PROJECTED_EXTENSION" if public else "PASS",
            "source_class": "PUBLIC_PROJECTED_EXTENSION" if public else "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": not public,
            "official_contest_traffic": False,
            "p6_evidence_used": False,
        },
        "campaign_units": {
            "family_id": "uzh-shapes-retiming-family" if public else "team-full50-family",
            "unit_kind": (
                "PUBLIC_DATASET_RETIMING_FAMILY" if public else "SYNTHETIC_TRACE_CAMPAIGN"
            ),
            "independent_sample_count": 1 if public else 50,
            "retiming_labels": ["1x", "64x", "256x"] if public else [],
            "retimings_are_independent_samples": False,
            "pooling_with_other_slots_permitted": False,
        },
        "shared_gates": {
            "view_integrity": "PASS",
            "evidence_classification": "PASS",
            "campaign_identity": "PASS",
        },
        "candidates": {
            "A2": {
                "role": "PRIMARY",
                "semantic_class": "AGGREGATE_WEIGHTED_PERFORMANCE",
                "gate_status": "PASS",
                "failure_scope": "NONE",
                "reason_codes": [],
            },
            "A3": {
                "role": "SEMANTIC_FALLBACK",
                "semantic_class": "EXACT_SCALAR_PREFIX",
                "gate_status": "PASS",
                "failure_scope": "NONE",
                "reason_codes": [],
            },
        },
        "claims": {"official": False, "physical": False, "power": False, "release": False},
    }


class AggregateFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="redred-aggregate-")
        self.directory = Path(self.temporary.name)
        self.synthetic = view("synthetic_v2")
        self.public = view("public_v2")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, value: dict) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def evaluate(self, semantic_requirement: str = "AGGREGATE_WEIGHTED") -> dict:
        return gate.evaluate_authenticated_views(
            self.synthetic, self.public, semantic_requirement,
            authentication=gate._PIPELINE_CONTEXT,
        )

    def external_evaluate(self) -> dict:
        synthetic = self.write("synthetic-external.json", self.synthetic)
        public = self.write("public-external.json", self.public)
        return gate.evaluate(synthetic, public)

    @staticmethod
    def candidate_state(document: dict, slot: str, candidate: str,
                        status: str, reason: str = "candidate_gate") -> None:
        row = document["candidates"][candidate]
        row["gate_status"] = status
        row["failure_scope"] = {
            "PASS": "NONE", "FAIL": "CANDIDATE_SPECIFIC", "HOLD": "UNRESOLVED",
        }[status]
        row["reason_codes"] = [] if status == "PASS" else [f"{slot}_{reason}"]

    def test_a2_primary_is_scoped_not_final_release(self) -> None:
        result = self.evaluate()
        self.assertEqual(result["status"], "PASS_SCOPED_CAMPAIGN_RECOMMENDATION")
        self.assertEqual(result["decision"]["status"], "A2_PRIMARY")
        self.assertEqual(result["decision"]["campaign_recommendation"], "A2")
        self.assertIsNone(result["decision"]["final_selected_candidate"])
        self.assertEqual(result["decision"]["final_selection_status"], "HOLD")
        self.assertEqual(result["decision"]["final_release_status"], "HOLD")
        self.assertFalse(result["decision"]["release_authority"])
        self.assertEqual(result["claims"], {
            "official": False, "physical": False, "power": False,
            "release": False, "final_candidate_selection": False,
        })

    def test_exact_prefix_requirement_selects_only_scoped_a3_fallback(self) -> None:
        result = self.evaluate("EXACT_SCALAR_PREFIX")
        self.assertEqual(result["decision"]["status"], "A3_FALLBACK")
        self.assertEqual(result["decision"]["campaign_recommendation"], "A3")
        self.assertEqual(result["decision"]["fallback_trigger"], "EXACT_PREFIX_REQUIRED")
        self.assertTrue(result["decision"]["fallback_activated"])
        self.assertIsNone(result["decision"]["final_selected_candidate"])

    def test_a2_specific_fail_uses_independently_passing_a3(self) -> None:
        self.candidate_state(self.public, "public_v2", "A2", "FAIL", "throughput_gate")
        result = self.evaluate()
        self.assertEqual(result["candidate_rollup"]["A2"]["status"], "FAIL")
        self.assertEqual(result["candidate_rollup"]["A3"]["status"], "PASS")
        self.assertEqual(result["decision"]["status"], "A3_FALLBACK")
        self.assertEqual(result["decision"]["fallback_trigger"], "A2_SPECIFIC_GATE_FAILURE")

    def test_a2_hold_is_not_misrepresented_as_failure_fallback(self) -> None:
        self.candidate_state(self.synthetic, "synthetic_v2", "A2", "HOLD", "missing_gate")
        result = self.evaluate()
        self.assertEqual(result["decision"]["status"], "HOLD_NO_QUALIFIED_CAMPAIGN_RECOMMENDATION")
        self.assertIsNone(result["decision"]["campaign_recommendation"])
        self.assertFalse(result["decision"]["fallback_activated"])

    def test_a3_must_independently_pass_both_views(self) -> None:
        self.candidate_state(self.synthetic, "synthetic_v2", "A2", "FAIL")
        self.candidate_state(self.public, "public_v2", "A3", "FAIL")
        result = self.evaluate()
        self.assertEqual(result["decision"]["status"], "HOLD_NO_QUALIFIED_CAMPAIGN_RECOMMENDATION")
        self.assertIsNone(result["decision"]["campaign_recommendation"])

    def test_shared_failure_cannot_activate_a3(self) -> None:
        self.public["shared_gates"]["campaign_identity"] = "FAIL"
        self.candidate_state(self.public, "public_v2", "A2", "FAIL")
        result = self.evaluate()
        self.assertEqual(result["decision"]["status"], "HOLD_SHARED_GATE")
        self.assertIsNone(result["decision"]["campaign_recommendation"])
        self.assertEqual(result["gates"]["shared_campaign_gates"], "HOLD")

    def test_public_retimings_are_one_family_and_never_pooled(self) -> None:
        result = self.evaluate()
        self.assertEqual(result["aggregation"], {
            "synthetic_public_pooling": "FORBIDDEN",
            "pooled_totals_emitted": False,
            "public_unit_kind": "PUBLIC_DATASET_RETIMING_FAMILY",
            "public_independent_sample_count": 1,
            "public_retiming_labels": ["1x", "64x", "256x"],
            "public_retimings_counted_as_independent_samples": False,
        })
        self.assertNotIn("metrics", result)
        self.assertNotIn("totals", result)

    def test_rejects_public_retimings_counted_as_three_samples(self) -> None:
        self.public["campaign_units"]["independent_sample_count"] = 3
        with self.assertRaisesRegex(gate.AggregateGateError, "one independent public dataset family"):
            self.evaluate()

    def test_rejects_public_retiming_independence_or_cross_slot_pooling(self) -> None:
        for key in ("retimings_are_independent_samples", "pooling_with_other_slots_permitted"):
            with self.subTest(key=key):
                self.public = view("public_v2")
                self.public["campaign_units"][key] = True
                with self.assertRaisesRegex(gate.AggregateGateError, "forbidden pooling"):
                    self.evaluate()

    def test_rejects_any_expanded_claim(self) -> None:
        for claim in ("official", "physical", "power", "release"):
            with self.subTest(claim=claim):
                self.synthetic = view("synthetic_v2")
                self.synthetic["claims"][claim] = True
                with self.assertRaisesRegex(gate.AggregateGateError, "expands"):
                    self.evaluate()

    def test_rejects_unverified_or_status_scope_laundering(self) -> None:
        self.public["verification"]["separately_verified"] = False
        with self.assertRaisesRegex(gate.AggregateGateError, "not separately verified"):
            self.evaluate()
        self.public = view("public_v2")
        self.public["classification"]["evidence_status"] = "PASS"
        with self.assertRaisesRegex(gate.AggregateGateError, "evidence_status differs"):
            self.evaluate()

    def test_rejects_nonpass_without_fail_closed_scope(self) -> None:
        row = self.synthetic["candidates"]["A2"]
        row["gate_status"] = "FAIL"
        row["failure_scope"] = "NONE"
        row["reason_codes"] = ["bad"]
        with self.assertRaisesRegex(gate.AggregateGateError, "failure scope"):
            self.evaluate()

    def test_cli_exit_codes_output_and_no_overwrite(self) -> None:
        synthetic = self.write("synthetic-cli.json", self.synthetic)
        public = self.write("public-cli.json", self.public)
        output = self.directory / "result.json"
        command = [
            sys.executable, str(PROGRAM), "evaluate",
            "--synthetic-v2-view", str(synthetic),
            "--public-v2-view", str(public), "--output", str(output),
        ]
        run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertEqual(json.loads(run.stdout), json.loads(output.read_text(encoding="utf-8")))
        self.assertEqual(json.loads(run.stdout)["decision"]["status"],
                         "HOLD_UNAUTHENTICATED_EXTERNAL_VIEWS")
        second = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(second.returncode, 2)
        self.assertIn("output already exists", second.stderr)

    def test_cli_hold_requires_allow_hold(self) -> None:
        self.candidate_state(self.synthetic, "synthetic_v2", "A2", "HOLD")
        synthetic = self.write("synthetic-hold.json", self.synthetic)
        public = self.write("public-hold.json", self.public)
        command = [
            sys.executable, str(PROGRAM),
            "--synthetic-v2-view", str(synthetic), "--public-v2-view", str(public),
        ]
        run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "HOLD_CAMPAIGN_RECOMMENDATION")
        allowed = subprocess.run(
            [*command, "--allow-hold"], cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_input_view_hashes_are_exact_file_hashes(self) -> None:
        synthetic = self.write("synthetic-hash.json", self.synthetic)
        public = self.write("public-hash.json", self.public)
        result = gate.evaluate(synthetic, public)
        self.assertEqual(
            result["input_views"]["synthetic_v2"]["sha256"],
            hashlib.sha256(synthetic.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result["input_views"]["public_v2"]["sha256"],
            hashlib.sha256(public.read_bytes()).hexdigest(),
        )

    def test_FORGED_ADAPTER_HASH_ACCEPTED_is_blocked(self) -> None:
        self.synthetic["verification"]["adapter_sha256"] = "f" * 64
        result = self.external_evaluate()
        self.assertEqual(result["status"], "HOLD_CAMPAIGN_RECOMMENDATION")
        self.assertEqual(result["authentication"]["status"],
                         "HOLD_UNAUTHENTICATED_EXTERNAL_VIEWS")
        self.assertEqual(result["gates"]["normalized_views_separately_verified"],
                         "HOLD_UNAUTHENTICATED")
        self.assertIsNone(result["decision"]["campaign_recommendation"])

    def test_FORGED_SYNTHETIC_PASS_VIEW_ACCEPTED_is_blocked(self) -> None:
        self.synthetic["verification"]["adapter_sha256"] = \
            "153c2038f6773eef28ff8bb50675164da7f265d3e8beb8d034c570311ad70895"
        self.public["verification"]["adapter_sha256"] = \
            "dae1de32a83457ac21ff3d84178cb6248fa1c24bdde41b656c11bbf983212e82"
        self.assertEqual(self.synthetic["candidates"]["A2"]["gate_status"], "PASS")
        self.assertEqual(self.synthetic["candidates"]["A3"]["gate_status"], "PASS")
        result = self.external_evaluate()
        self.assertEqual(result["decision"]["status"],
                         "HOLD_UNAUTHENTICATED_EXTERNAL_VIEWS")
        self.assertIsNone(result["decision"]["campaign_recommendation"])
        self.assertFalse(result["authentication"]["in_process_native_adapters"])

    def test_in_memory_recommendation_requires_pipeline_context(self) -> None:
        with self.assertRaisesRegex(gate.AggregateGateError, "pipeline context"):
            gate.evaluate_authenticated_views(
                self.synthetic, self.public, authentication=object(),
            )

    def test_result_schema_hard_codes_nonrelease_boundary(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        claims = schema["properties"]["claims"]["properties"]
        for name in ("official", "physical", "power", "release", "final_candidate_selection"):
            self.assertIs(claims[name]["const"], False)
        decision = schema["properties"]["decision"]["properties"]
        self.assertIsNone(decision["final_selected_candidate"]["const"])
        self.assertEqual(decision["final_selection_status"]["const"], "HOLD")
        self.assertEqual(decision["final_release_status"]["const"], "HOLD")
        self.assertIs(decision["release_authority"]["const"], False)

    def test_stable_normalized_input_contract_is_byte_pinned(self) -> None:
        gate.validate_view_contract()
        payload = gate.VIEW_CONTRACT.read_bytes()
        self.assertEqual(len(payload), gate.VIEW_CONTRACT_SIZE_BYTES)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), gate.VIEW_CONTRACT_SHA256)
        schema = json.loads(payload)
        self.assertEqual(schema["$id"], gate.VIEW_SCHEMA)
        claims = schema["properties"]["claims"]["properties"]
        for name in ("official", "physical", "power", "release"):
            self.assertIs(claims[name]["const"], False)


if __name__ == "__main__":
    unittest.main()
