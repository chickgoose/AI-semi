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
        cls.manifest = campaign.load_json(MANIFEST_PATH, "test campaign manifest")
        raw = subprocess.run(
            [
                "git", "-C", str(ROOT), "show",
                f"{campaign.PUBLICATION_COMMIT}:{campaign.RESULT_PATH}",
            ],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        cls.result_raw = raw
        cls.result = campaign.load_json_bytes(raw, "test committed result")
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

    def make_prepared_inputs(self, root):
        artifact_root = root / "artifacts"
        artifact_root.mkdir()
        result = copy.deepcopy(self.result)
        prepared = {"a2": {}, "a3": {}}
        for position, name in enumerate(self.context["registry"].FULL50):
            content = f"prepared-input-{position}-{name}\n".encode("ascii")
            digest = hashlib.sha256(content).hexdigest()
            for owner in ("a2", "a3"):
                relative = Path("prepared") / owner / f"{name}.trace"
                path = artifact_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                prepared[owner][name] = {
                    "path": relative.as_posix(), "sha256": digest,
                    "size_bytes": len(content),
                }
                result["owners"][owner]["full50"]["runs"][name][
                    "prepared_trace_sha256"
                ] = digest
        return artifact_root, prepared, result

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

    def test_duplicate_json_keys_fail_closed_for_every_document_class(self):
        documents = {
            "campaign manifest": b'{"schema":"first","schema":"second"}',
            "retained artifact schema": b'{"$id":"first","$id":"second"}',
            "committed replay result": b'{"status":"PASS","status":"HOLD"}',
            "retained artifact index": b'{"artifacts":[],"artifacts":[]}',
        }
        for label, payload in documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(campaign.CampaignError, "duplicate JSON key"):
                    campaign.load_json_bytes(payload, label)

    def test_cli_rejects_schema_index_and_root_symlinks_before_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            artifact_root = base / "artifacts"
            artifact_root.mkdir()
            index = base / "index.json"
            index.write_text("{}", encoding="utf-8")
            schema_link = base / "schema-link.json"
            schema_link.symlink_to(SCHEMA_PATH)
            index_link = base / "index-link.json"
            index_link.symlink_to(index)
            root_link = base / "root-link"
            root_link.symlink_to(artifact_root, target_is_directory=True)
            cases = (
                (schema_link, index, artifact_root, "retained schema path traverses a symlink"),
                (SCHEMA_PATH, index_link, artifact_root, "retained artifact index path traverses a symlink"),
                (SCHEMA_PATH, index, root_link, "artifact root path traverses a symlink"),
            )
            for schema, receipt, root, diagnostic in cases:
                with self.subTest(diagnostic=diagnostic):
                    process = subprocess.run([
                        sys.executable, str(CAMPAIGN_PATH), "evaluate",
                        "--replay-schema", str(schema),
                        "--replay-schema-sha256", campaign.RETAINED_SCHEMA_SHA256,
                        "--replay-receipt", str(receipt),
                        "--replay-receipt-sha256", campaign.file_sha256(index),
                        "--artifact-root", str(root),
                    ], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    self.assertEqual(process.returncode, 2)
                    self.assertIn(diagnostic, process.stderr)

    def test_explicit_metadata_path_aliases_and_nesting_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            artifact_root = base / "artifacts"
            artifact_root.mkdir()
            with self.assertRaisesRegex(campaign.CampaignError, "path aliases"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.RETAINED_SCHEMA_SHA256,
                    SCHEMA_PATH, campaign.RETAINED_SCHEMA_SHA256, artifact_root,
                )
            hardlink = base / "schema-hardlink.json"
            hardlink.hardlink_to(SCHEMA_PATH)
            with self.assertRaisesRegex(campaign.CampaignError, "path aliases"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.RETAINED_SCHEMA_SHA256,
                    hardlink, campaign.RETAINED_SCHEMA_SHA256, artifact_root,
                )
            nested_index = artifact_root / "index.json"
            nested_index.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "outside the artifact root"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.RETAINED_SCHEMA_SHA256,
                    nested_index, campaign.file_sha256(nested_index), artifact_root,
                )

    def test_retained_prepared_inputs_are_bound_and_compared_as_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root, prepared, result = self.make_prepared_inputs(Path(directory))
            summary = campaign.validate_prepared_inputs(
                prepared, artifact_root, result, self.context["registry"], set(), set(),
            )
            self.assertEqual(summary, {
                "run_count": 100, "unique_trace_count": 50,
                "cross_owner_bytes_equal": True,
            })

    def test_retained_prepared_missing_alias_and_byte_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact_root, prepared, result = self.make_prepared_inputs(Path(directory))
            name = self.context["registry"].FULL50[0]

            missing = copy.deepcopy(prepared)
            del missing["a3"][name]
            with self.assertRaisesRegex(campaign.CampaignError, "roster differs"):
                campaign.validate_prepared_inputs(
                    missing, artifact_root, result, self.context["registry"], set(), set(),
                )

            aliased = copy.deepcopy(prepared)
            aliased["a3"][name] = copy.deepcopy(aliased["a2"][name])
            with self.assertRaisesRegex(campaign.CampaignError, "duplicate path or file alias"):
                campaign.validate_prepared_inputs(
                    aliased, artifact_root, result, self.context["registry"], set(), set(),
                )

            drifted = copy.deepcopy(prepared)
            drifted_result = copy.deepcopy(result)
            drift_path = artifact_root / drifted["a3"][name]["path"]
            drift_bytes = b"different-retained-prepared-input\n"
            drift_path.write_bytes(drift_bytes)
            drift_sha = hashlib.sha256(drift_bytes).hexdigest()
            drifted["a3"][name].update({
                "sha256": drift_sha, "size_bytes": len(drift_bytes),
            })
            drifted_result["owners"]["a3"]["full50"]["runs"][name][
                "prepared_trace_sha256"
            ] = drift_sha
            with self.assertRaisesRegex(campaign.CampaignError, "retained A2/A3 prepared input bytes differ"):
                campaign.validate_prepared_inputs(
                    drifted, artifact_root, drifted_result,
                    self.context["registry"], set(), set(),
                )

    def test_empty_retained_artifact_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            index = {
                "schema": "redred_single_edge_retained_artifact_index_v2",
                "evidence_class": campaign.EVIDENCE_CLASS,
                "replay_result_sha256": campaign.RESULT_SHA256,
                "replay_result_semantic_sha256": campaign.RESULT_SEMANTIC_SHA256,
                "prepared_inputs": {"a2": {}, "a3": {}},
                "artifacts": [],
            }
            index_path = root / "index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "must contain artifacts"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.file_sha256(SCHEMA_PATH),
                    index_path, campaign.file_sha256(index_path), artifact_root,
                )

    def test_wrong_result_binding_in_artifact_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            index = {
                "schema": "redred_single_edge_retained_artifact_index_v2",
                "evidence_class": campaign.EVIDENCE_CLASS,
                "replay_result_sha256": "0" * 64,
                "replay_result_semantic_sha256": campaign.RESULT_SEMANTIC_SHA256,
                "prepared_inputs": {"a2": {}, "a3": {}},
                "artifacts": [{"path": "x", "sha256": "0" * 64, "size_bytes": 1}],
            }
            index_path = root / "index.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "not bound"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, campaign.file_sha256(SCHEMA_PATH),
                    index_path, campaign.file_sha256(index_path), artifact_root,
                )

    def test_wrong_explicit_schema_hash_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            index_path = root / "index.json"
            index_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "schema bytes/hash differ"):
                campaign.evaluate(
                    MANIFEST_PATH, ROOT, SCHEMA_PATH, "0" * 64,
                    index_path, campaign.file_sha256(index_path), artifact_root,
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
        schema = campaign.load_json(SCHEMA_PATH, "test retained schema")
        self.assertEqual(schema["$id"], "redred_single_edge_retained_artifact_index_v2")
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
        self.assertIn("prepared_inputs", schema["required"])
        self.assertEqual(campaign.file_sha256(SCHEMA_PATH), "cb8b0e91c7a4f25191bbaff33692de440169d63cc97c8ed8a06ac9512c4500f4")


if __name__ == "__main__":
    unittest.main()
