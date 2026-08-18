#!/usr/bin/env python3
"""Fail-closed tests for the native-adapter canonical campaign pipeline."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_single_edge_campaign"
PROGRAM = PACKAGE / "native_pipeline.py"
POLICY = PACKAGE / "team_canonical_policy.json"
SCHEMA = PACKAGE / "native_pipeline_result.schema.json"
VIEW_SCHEMA = PACKAGE / "campaign_normalized_view.schema.json"


def load_program():
    specification = importlib.util.spec_from_file_location("redred_native_pipeline_tested", PROGRAM)
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load native pipeline")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


pipeline = load_program()


def policy() -> dict:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def synthetic_common_view() -> dict:
    return {
        "schema": "redred_single_edge_campaign_normalized_view_v1",
        "slot": "synthetic_v2",
        "verification": {
            "status": "PASS", "separately_verified": True,
            "adapter_id": "synthetic_v2_native_adapter_v1",
            "adapter_sha256": "a" * 64,
            "source_result_sha256": "b" * 64,
            "source_publication_sha256": "c" * 64,
        },
        "classification": {
            "evidence_status": "PASS", "source_class": "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": True, "official_contest_traffic": False,
            "p6_evidence_used": False,
        },
        "campaign_units": {
            "family_id": "team-full50-family", "unit_kind": "SYNTHETIC_TRACE_CAMPAIGN",
            "independent_sample_count": 50, "retiming_labels": [],
            "retimings_are_independent_samples": False,
            "pooling_with_other_slots_permitted": False,
        },
        "shared_gates": {
            "native_tuple_integrity": "PASS", "canonical_campaign_policy": "HOLD",
        },
        "candidates": {
            "A2": pipeline.expected_candidate(
                "PRIMARY", "AGGREGATE_WEIGHTED_PERFORMANCE", "HOLD",
            ),
            "A3": pipeline.expected_candidate(
                "SEMANTIC_FALLBACK", "EXACT_SCALAR_PREFIX", "HOLD",
            ),
        },
        "claims": {"official": False, "physical": False, "power": False, "release": False},
    }


class PolicyAttestationTests(unittest.TestCase):
    def test_exact_committed_policy_is_hash_pinned_and_valid(self) -> None:
        document, record, resolved, identity = pipeline.load_policy(ROOT)
        self.assertEqual(document, policy())
        self.assertEqual(record["sha256"], pipeline.POLICY_SHA256)
        self.assertEqual(record["size_bytes"], pipeline.POLICY_SIZE_BYTES)
        pipeline.aggregate.recheck_file(resolved, identity, "policy")

    def test_policy_rejects_canonical_public_relabel_and_release_expansion(self) -> None:
        mutations = []
        public = policy()
        public["public_extension"]["canonical_redred_traffic"] = True
        mutations.append(public)
        official = policy()
        official["canonical_synthetic"]["organizer_official"] = True
        mutations.append(official)
        release = policy()
        release["claim_boundary"]["release"] = True
        mutations.append(release)
        final = policy()
        final["claim_boundary"]["final_selection"] = "PASS"
        mutations.append(final)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(pipeline.NativePipelineError, "policy .* differs"):
                    pipeline.validate_policy(mutation)

    def test_policy_rejects_extra_or_reordered_promotions(self) -> None:
        extra = policy()
        extra["authorized_synthetic_promotions"].append({
            "json_pointer": "/claims/release", "from": False, "to": True,
        })
        with self.assertRaisesRegex(pipeline.NativePipelineError, "promotions differs"):
            pipeline.validate_policy(extra)
        reordered = policy()
        reordered["authorized_synthetic_promotions"].reverse()
        with self.assertRaisesRegex(pipeline.NativePipelineError, "promotions differs"):
            pipeline.validate_policy(reordered)

    def test_only_three_exact_synthetic_holds_are_promoted(self) -> None:
        before = synthetic_common_view()
        after, receipt = pipeline.attest_synthetic_view(before, pipeline.validate_policy(policy()))
        self.assertEqual(receipt["changed_json_pointers"], list(pipeline.PROMOTION_POINTERS))
        self.assertFalse(receipt["other_fields_changed"])
        self.assertEqual(after["shared_gates"]["canonical_campaign_policy"], "PASS")
        for candidate in ("A2", "A3"):
            self.assertEqual(after["candidates"][candidate]["gate_status"], "PASS")
            self.assertEqual(after["candidates"][candidate]["failure_scope"], "NONE")
            self.assertEqual(after["candidates"][candidate]["reason_codes"], [])
        restored = copy.deepcopy(after)
        for row in policy()["authorized_synthetic_promotions"]:
            pipeline.pointer_set(restored, row["json_pointer"], row["from"])
        self.assertEqual(restored, before)
        self.assertEqual(after["claims"], before["claims"])
        self.assertEqual(after["classification"], before["classification"])

    def test_attestation_rejects_nonfull50_and_prepromoted_inputs(self) -> None:
        wrong_family = synthetic_common_view()
        wrong_family["campaign_units"]["family_id"] = "not-full50"
        with self.assertRaisesRegex(pipeline.NativePipelineError, "does not match full50"):
            pipeline.attest_synthetic_view(wrong_family, policy())
        prepromoted = synthetic_common_view()
        prepromoted["shared_gates"]["canonical_campaign_policy"] = "PASS"
        with self.assertRaisesRegex(pipeline.NativePipelineError, "pre-attestation"):
            pipeline.attest_synthetic_view(prepromoted, policy())

    def test_attestation_rejects_candidate_hold_drift(self) -> None:
        document = synthetic_common_view()
        document["candidates"]["A2"]["reason_codes"] = ["DIFFERENT_REASON"]
        with self.assertRaisesRegex(pipeline.NativePipelineError, "promotion source differs"):
            pipeline.attest_synthetic_view(document, policy())


class EndToEndNativePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with mock.patch.object(
            pipeline.synthetic_adapter, "evaluate",
            wraps=pipeline.synthetic_adapter.evaluate,
        ) as synthetic_call, mock.patch.object(
            pipeline.public_adapter, "validate_tuple",
            wraps=pipeline.public_adapter.validate_tuple,
        ) as public_call:
            cls.result = pipeline.evaluate(ROOT)
            cls.synthetic_call_count = synthetic_call.call_count
            cls.public_call_count = public_call.call_count

    def test_pipeline_calls_both_native_adapters_and_feeds_common_views(self) -> None:
        self.assertEqual(self.synthetic_call_count, 1)
        self.assertEqual(self.public_call_count, 1)
        execution = self.result["adapter_execution"]
        self.assertTrue(execution["synthetic_v2"]["adapter_called"])
        self.assertTrue(execution["public_v2"]["adapter_called"])
        self.assertEqual(
            execution["synthetic_v2"]["code"]["sha256"],
            pipeline.PINNED_MODULES["synthetic_v2_native_adapter"]["sha256"],
        )
        self.assertEqual(
            execution["public_v2"]["code"]["sha256"],
            pipeline.PINNED_MODULES["public_v2_native_adapter"]["sha256"],
        )
        aggregate_inputs = self.result["aggregate_result"]["input_views"]
        common = self.result["common_views"]
        self.assertEqual(
            aggregate_inputs["synthetic_v2"]["source_result_sha256"],
            common["synthetic_v2_attested"]["verification"]["source_result_sha256"],
        )
        self.assertEqual(
            aggregate_inputs["public_v2"]["source_result_sha256"],
            common["public_v2"]["verification"]["source_result_sha256"],
        )
        self.assertEqual(
            self.result["aggregate_result"]["authentication"]["status"],
            "PASS_HASH_PINNED_IN_PROCESS_NATIVE_ADAPTERS",
        )

    def test_full50_policy_only_promotes_synthetic_holds(self) -> None:
        views = self.result["common_views"]
        before = views["synthetic_v2_before_policy"]
        after = views["synthetic_v2_attested"]
        self.assertEqual(before["shared_gates"]["canonical_campaign_policy"], "HOLD")
        self.assertEqual(after["shared_gates"]["canonical_campaign_policy"], "PASS")
        self.assertEqual(before["candidates"]["A2"]["gate_status"], "HOLD")
        self.assertEqual(before["candidates"]["A3"]["gate_status"], "HOLD")
        self.assertEqual(after["candidates"]["A2"]["gate_status"], "PASS")
        self.assertEqual(after["candidates"]["A3"]["gate_status"], "PASS")
        self.assertEqual(
            self.result["synthetic_policy_promotion"]["changed_json_pointers"],
            list(pipeline.PROMOTION_POINTERS),
        )

    def test_raw_hashes_and_full_metrics_are_retained(self) -> None:
        raw = self.result["upstream_raw_hashes"]
        self.assertEqual(
            raw["synthetic_v2"]["result"]["sha256"],
            "7a4a8a3f0d8238b9c5f3c72c6ae1d2bf026030e7247eddfd62d9c4c2bbf70554",
        )
        self.assertEqual(
            raw["public_v2"]["result"]["sha256"],
            "815c752f4852790d4db5c3c935cc2edc5821fee9a36f2e83c35d3ec8b73c5c12",
        )
        synthetic = {
            row["owner"]: row["primary_aggregate"]
            for row in self.result["full_metrics"]["synthetic_v2"]["owners"]
        }
        self.assertEqual(synthetic["a2"]["totals"]["accepted"], 104046)
        self.assertEqual(synthetic["a3"]["totals"]["accepted"], 93645)
        public = self.result["full_metrics"]["public_v2"]
        self.assertEqual(public["a2"]["256x"]["accepted"], 906)
        self.assertEqual(public["a3"]["256x"]["accepted"], 817)

    def test_public_retimings_remain_one_unpooled_family(self) -> None:
        accounting = self.result["aggregation_accounting"]
        self.assertEqual(accounting["public_independent_sample_count"], 1)
        self.assertEqual(accounting["public_retiming_labels"], ["1x", "64x", "256x"])
        self.assertFalse(accounting["public_retimings_counted_as_independent_samples"])
        self.assertFalse(accounting["pooled_totals_emitted"])
        self.assertEqual(accounting["synthetic_public_pooling"], "FORBIDDEN")

    def test_a2_is_campaign_only_and_all_final_claims_hold(self) -> None:
        self.assertEqual(self.result["campaign_recommendation"], "A2")
        aggregate_result = self.result["aggregate_result"]
        self.assertEqual(aggregate_result["decision"]["status"], "A2_PRIMARY")
        self.assertIsNone(aggregate_result["decision"]["final_selected_candidate"])
        self.assertEqual(aggregate_result["decision"]["final_selection_status"], "HOLD")
        self.assertEqual(aggregate_result["decision"]["final_release_status"], "HOLD")
        self.assertFalse(aggregate_result["decision"]["release_authority"])
        self.assertEqual(self.result["claims"], pipeline.CLAIMS)

    def test_report_seal_covers_every_nonseal_field(self) -> None:
        unsigned = copy.deepcopy(self.result)
        seal = unsigned.pop("seal")
        self.assertEqual(seal["algorithm"], "SHA256_CANONICAL_JSON_EXCLUDING_SEAL")
        self.assertEqual(seal["semantic_sha256"], pipeline.digest(pipeline.canonical(unsigned)))

    def test_result_validates_against_committed_schema(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(self.result, schema)
        view_schema = json.loads(VIEW_SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(view_schema)
        for name, view in self.result["common_views"].items():
            with self.subTest(common_view=name):
                jsonschema.validate(view, view_schema)

    def test_cli_write_and_no_overwrite_with_pipeline_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="redred-pipeline-cli-") as directory:
            output = Path(directory) / "result.json"
            stdout = io.BytesIO()
            with mock.patch.object(pipeline, "evaluate", return_value=self.result), \
                    mock.patch.object(sys, "argv", [str(PROGRAM), "evaluate", "--output", str(output)]), \
                    mock.patch.object(sys, "stdout") as mocked_stdout:
                mocked_stdout.buffer = stdout
                self.assertEqual(pipeline.main(), 0)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_bytes()))
            stderr = io.StringIO()
            with mock.patch.object(pipeline, "evaluate", return_value=self.result), \
                    mock.patch.object(sys, "argv", [str(PROGRAM), "--output", str(output)]), \
                    mock.patch.object(sys, "stderr", stderr), \
                    mock.patch.object(sys, "stdout") as mocked_stdout:
                mocked_stdout.buffer = io.BytesIO()
                self.assertEqual(pipeline.main(), 2)
            self.assertIn("output already exists", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
