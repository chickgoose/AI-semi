#!/usr/bin/env python3
"""Fail-closed tests for the hardened REDRED single-edge campaign wrapper."""

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
PACKAGE = ROOT / "benchmarks" / "redred_single_edge_campaign"
CAMPAIGN_PATH = PACKAGE / "campaign.py"
MANIFEST_PATH = PACKAGE / "campaign.json"
SCHEMA_PATH = PACKAGE / "replay_receipt.schema.json"


def load_campaign():
    spec = importlib.util.spec_from_file_location("redred_single_edge_campaign", CAMPAIGN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load campaign module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


campaign = load_campaign()


class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        raw = subprocess.run(
            [
                "git", "-C", str(ROOT), "show",
                f"{campaign.PUBLICATION_COMMIT}:{campaign.RESULT_PATH}",
            ],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        cls.result_raw = raw
        cls.result = json.loads(raw)
        cls.context = campaign.validate_manifest(copy.deepcopy(cls.manifest), ROOT)

    def evaluate(self, manifest=None):
        if manifest is None:
            return campaign.evaluate(MANIFEST_PATH, ROOT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "campaign.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            return campaign.evaluate(path, ROOT)

    def assert_manifest_rejected(self, mutate, pattern=None):
        manifest = copy.deepcopy(self.manifest)
        mutate(manifest)
        with self.assertRaisesRegex(campaign.CampaignError, pattern or "."):
            self.evaluate(manifest)

    def assert_result_rejected(self, mutate, pattern=None):
        result = copy.deepcopy(self.result)
        mutate(result)
        with self.assertRaisesRegex(campaign.CampaignError, pattern or "."):
            campaign.validate_result_semantics(
                result, self.context["registry"], self.context["windows"], ROOT,
            )

    def test_default_exact_hold_pass_split(self):
        report = self.evaluate()
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["gates"], {
            "committed_hardened_receipt": "PASS",
            "canonical_synthetic_receipt_semantics": "PASS",
            "retained_replay_artifacts": "HOLD",
            "canonical_single_edge_campaign": "HOLD",
            "public_projected_extension": "HOLD",
            "system_release": "HOLD",
        })
        self.assertEqual(
            report["retained_artifact_validation"]["status"],
            "HOLD_MISSING_RETAINED_ARTIFACTS",
        )

    def test_committed_result_raw_and_semantic_hashes(self):
        self.assertEqual(hashlib.sha256(self.result_raw).hexdigest(), campaign.RESULT_SHA256)
        self.assertEqual(campaign.semantic_sha256(self.result), campaign.RESULT_SEMANTIC_SHA256)
        report = self.evaluate()["receipt_validation"]
        self.assertEqual(report["sha256"], campaign.RESULT_SHA256)
        self.assertEqual(report["semantic_sha256"], campaign.RESULT_SEMANTIC_SHA256)
        self.assertEqual(report["trust"], "COMMITTED_RECEIPT_CONSISTENT")

    def test_hardened_source_and_integration_are_exact(self):
        report = self.evaluate()["receipt_validation"]["provenance"]
        self.assertEqual(report["source_commit"], campaign.SOURCE_COMMIT)
        self.assertEqual(report["integration_commit"], campaign.INTEGRATION_COMMIT)
        self.assertEqual(campaign.git_tree(ROOT, campaign.SOURCE_COMMIT, "source"), campaign.SOURCE_TREE)
        self.assertEqual(
            campaign.git_tree(ROOT, campaign.INTEGRATION_COMMIT, "integration"),
            campaign.INTEGRATION_TREE,
        )
        self.assertNotIn("4ce4836", json.dumps(self.manifest))

    def test_receipt_reports_accounting_and_both_latencies(self):
        owners = self.evaluate()["datasets"]["canonical_synthetic_full50"]["candidates"]
        self.assertEqual(owners["a2"]["totals"], campaign.EXPECTED_TOTALS["a2"])
        self.assertEqual(owners["a3"]["totals"], campaign.EXPECTED_TOTALS["a3"])
        self.assertEqual(owners["a2"]["occurrence_to_accept"]["mean"], 0.234819)
        self.assertEqual(owners["a3"]["occurrence_to_accept"]["mean"], 1.225202)
        self.assertEqual(owners["a2"]["accept_to_retire"]["mean"], 3.0)
        self.assertEqual(owners["a3"]["accept_to_retire"]["mean"], 2.0)
        for owner in ("a2", "a3"):
            totals = owners[owner]["totals"]
            self.assertEqual(totals["generated"], totals["source_overrun"] + totals["accepted"])
            self.assertEqual(totals["accepted"], totals["retired"])

    def test_receipt_claim_is_not_promoted_to_artifact_replay(self):
        report = self.evaluate()
        self.assertEqual(report["claim_boundary"]["producer_claim"]["single_edge_digital_RTL"], "GO")
        self.assertFalse(report["claim_boundary"]["campaign_accepts_receipt_claim_as_artifact_replay"])
        self.assertFalse(report["claim_boundary"]["new_evidence_inferred"])
        self.assertEqual(report["gates"]["canonical_single_edge_campaign"], "HOLD")

    def test_full50_and_public_extension_remain_distinct(self):
        report = self.evaluate()
        full50 = report["datasets"]["canonical_synthetic_full50"]
        public = report["datasets"]["public_projected_extension"]
        self.assertEqual(full50["source_class"], "TEAM_DEFINED_SYNTHETIC")
        self.assertTrue(full50["canonical_redred_traffic"])
        self.assertFalse(full50["official_contest_traffic"])
        self.assertEqual(public["source_class"], "PUBLIC_PROJECTED_EXTENSION")
        self.assertFalse(public["canonical_redred_traffic"])
        self.assertFalse(public["official_contest_traffic"])
        self.assertEqual(report["aggregation_policy"]["full50_public_pooling"], "FORBIDDEN")

    def test_public_occurrences_and_scenario_relationship_are_retained(self):
        public = self.evaluate()["datasets"]["public_projected_extension"]
        self.assertEqual(public["unique_source_occurrences"], 1100)
        self.assertEqual(
            public["scenario_relation"],
            "TIMING_VARIANTS_OF_ONE_SOURCE_WINDOW_NOT_INDEPENDENT_SAMPLES",
        )
        self.assertEqual([row["id"] for row in public["scenarios"]], ["1x", "64x", "256x"])
        self.assertEqual(
            [row["same_source_cycle_collision_extras"] for row in public["scenarios"]],
            [81, 81, 133],
        )
        self.assertIn("identical projected traces", public["remaining_dependency"])

    def test_cli_hold_exit_codes(self):
        base = [sys.executable, str(CAMPAIGN_PATH), "evaluate"]
        held = subprocess.run(base, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        allowed = subprocess.run(base + ["--allow-hold"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(held.returncode, 3, held.stderr.decode())
        self.assertEqual(allowed.returncode, 0, allowed.stderr.decode())
        self.assertEqual(json.loads(held.stdout)["status"], "HOLD")

    def test_partial_explicit_artifact_tuple_fails_closed(self):
        with self.assertRaisesRegex(campaign.CampaignError, "supplied together"):
            campaign.evaluate(MANIFEST_PATH, ROOT, replay_schema=SCHEMA_PATH)

    def test_empty_retained_artifact_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = {
                "schema": "redred_single_edge_retained_artifact_index_v1",
                "evidence_class": campaign.EVIDENCE_CLASS,
                "replay_result_sha256": campaign.RESULT_SHA256,
                "replay_result_semantic_sha256": campaign.RESULT_SEMANTIC_SHA256,
                "artifacts": [],
            }
            index_path = root / "index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "must contain artifacts"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.file_sha256(SCHEMA_PATH),
                    index_path, campaign.file_sha256(index_path), root,
                )

    def test_wrong_result_binding_in_artifact_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index = {
                "schema": "redred_single_edge_retained_artifact_index_v1",
                "evidence_class": campaign.EVIDENCE_CLASS,
                "replay_result_sha256": "0" * 64,
                "replay_result_semantic_sha256": campaign.RESULT_SEMANTIC_SHA256,
                "artifacts": [{"path": "x", "sha256": "0" * 64, "size_bytes": 1}],
            }
            index_path = root / "index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "not bound"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.file_sha256(SCHEMA_PATH),
                    index_path, campaign.file_sha256(index_path), root,
                )

    def test_wrong_explicit_schema_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            index_path = root / "index.json"
            index_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "schema bytes/hash differ"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, "0" * 64,
                    index_path, campaign.file_sha256(index_path), root,
                )

    def test_old_rtl_source_commit_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["producer"].__setitem__(
                "source_commit", "4ce4836fab1309d3468db8e660d2da9af371f784"
            ),
            "producer provenance",
        )

    def test_wrong_integration_commit_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["producer"].__setitem__("integration_commit", "0" * 40),
            "producer provenance",
        )

    def test_nonproducer_evidence_class_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["producer"].__setitem__("evidence_class", "GENERIC_RESULT_V1"),
            "producer provenance",
        )

    def test_result_hash_or_semantic_hash_substitution_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["committed_replay_result"].__setitem__("sha256", "0" * 64),
            "result identity/hash",
        )
        self.assert_manifest_rejected(
            lambda row: row["committed_replay_result"].__setitem__("semantic_sha256", "0" * 64),
            "result identity/hash",
        )

    def test_full50_official_or_source_relabel_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["canonical_synthetic_full50"].__setitem__(
                "official_contest_traffic", True
            ),
            "official contest traffic",
        )
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["canonical_synthetic_full50"].__setitem__(
                "source_class", "OFFICIAL"
            ),
            "classification",
        )

    def test_public_extension_relabel_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"].__setitem__(
                "canonical_redred_traffic", True
            ),
            "relabeled",
        )
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"].__setitem__(
                "official_contest_traffic", True
            ),
            "relabeled",
        )

    def test_uncommitted_public_receipt_claim_is_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"].__setitem__(
                "actual_replay_receipt", {"status": "PASS"}
            ),
            "uncommitted projection/replay evidence",
        )

    def test_public_count_scenario_order_and_hash_mutations_are_rejected(self):
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"].__setitem__(
                "unique_source_occurrences", 3300
            ),
            "occurrence/scenario semantics",
        )
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"]["scenarios"].reverse(),
            "roster/order",
        )
        self.assert_manifest_rejected(
            lambda row: row["datasets"]["public_projected_extension"]["scenarios"][0].__setitem__(
                "trace_sha256", "0" * 64
            ),
            "scenario differs",
        )

    def test_unknown_result_field_is_rejected(self):
        self.assert_result_rejected(lambda row: row.__setitem__("future_claim", True), "keys differ")

    def test_boolean_counter_and_conservation_mutation_are_rejected(self):
        self.assert_result_rejected(
            lambda row: row["execution_accounting"].__setitem__("owners", True),
            "bool is forbidden",
        )
        self.assert_result_rejected(
            lambda row: row["owners"]["a2"]["full50"]["runs"][
                next(iter(row["owners"]["a2"]["full50"]["runs"]))
            ].__setitem__("accepted", 0),
            r"generated=source_overrun\+accepted",
        )

    def test_a2_a3_trace_or_window_drift_is_rejected(self):
        def mutate_trace(row):
            name = next(iter(row["owners"]["a3"]["full50"]["runs"]))
            row["owners"]["a3"]["full50"]["runs"][name]["trace_sha256"] = "0" * 64

        def mutate_window(row):
            name = next(iter(row["owners"]["a2"]["full50"]["runs"]))
            row["owners"]["a2"]["full50"]["runs"][name]["fixed_window_cycles"] += 1

        def mutate_prepared(row):
            name = next(iter(row["owners"]["a3"]["full50"]["runs"]))
            row["owners"]["a3"]["full50"]["runs"][name]["prepared_trace_sha256"] = "0" * 64

        self.assert_result_rejected(mutate_trace, "trace SHA")
        self.assert_result_rejected(mutate_window, "fixed window")
        self.assert_result_rejected(mutate_prepared, "prepared full50 input differs")

    def test_malformed_nested_provenance_fails_closed(self):
        self.assert_result_rejected(
            lambda row: row["provenance"].__setitem__("verified_files", []),
            "verified_files differ",
        )
        self.assert_result_rejected(
            lambda row: row["mutations"][0]["source_identity"].__setitem__(
                "mutant_sha256", "0" * 64
            ),
            "source rewrite identity differs",
        )

    def test_qualification_expansion_is_rejected(self):
        self.assert_result_rejected(
            lambda row: row["qualification"].__setitem__("physical", "GO"),
            "qualification boundary",
        )

    def test_failed_or_extra_mutant_is_rejected(self):
        self.assert_result_rejected(
            lambda row: row["mutations"][0].__setitem__("killed", False),
            "not the expected killed",
        )
        self.assert_result_rejected(
            lambda row: row["mutations"].append(copy.deepcopy(row["mutations"][0])),
            "mutation count",
        )

    def test_owner_expansion_is_rejected(self):
        self.assert_result_rejected(
            lambda row: row["owners"].__setitem__("p6", copy.deepcopy(row["owners"]["a2"])),
            "exactly ordered a2,a3",
        )

    def test_schema_is_exact_result_bound_artifact_index(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "redred_single_edge_retained_artifact_index_v1")
        self.assertEqual(
            schema["properties"]["evidence_class"]["const"], campaign.EVIDENCE_CLASS,
        )
        self.assertEqual(
            schema["properties"]["replay_result_sha256"]["const"], campaign.RESULT_SHA256,
        )
        self.assertEqual(
            schema["properties"]["replay_result_semantic_sha256"]["const"],
            campaign.RESULT_SEMANTIC_SHA256,
        )
        self.assertEqual(campaign.file_sha256(SCHEMA_PATH), "72b7842d3856a6e38d8f9e9983110d1cdb88129c7ed9e7cadacbfa0c6a06461d")


if __name__ == "__main__":
    unittest.main()
