from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

import jsonschema
from referencing import Registry, Resource

from benchmarks.redred_mc_wtb_stage4_contract import (
    canonical_json_bytes,
    canonical_sha256,
)
from benchmarks.redred_mc_wtb_predictor_stage3 import development_refreeze as dr


SHA = "7" * 64


def _sealed(body, field):
    return dict(body, **{field: canonical_sha256(body)})


def _write_json(path: Path, value) -> bytes:
    payload = canonical_json_bytes(value)
    path.write_bytes(payload)
    return payload


def _policy():
    return {
        "cohort_role": "DEVELOPMENT_CONSUMED",
        "legacy_outcomes_unseen": "UNKNOWN",
        "labels_constructed_before_candidate_invocation": True,
        "descriptive_only": True,
        "development_model_measurement_allowed": True,
        "development_comparison_allowed": False,
        "unbiased_claim_allowed": False,
        "generalization_claim_allowed": False,
        "promotion_allowed": False,
        "rtl_ppa_allowed": False,
        "epoch2_authorized": False,
        "holdout_reconstituted": False,
        "single_attempt_per_candidate_config": True,
        "retry_allowed": False,
        "within_run_tuning_allowed": False,
        "future_variants_require_new_refreeze": True,
    }


def _bindings():
    fields = (
        "source_split_plan_sha256",
        "ordered_query_ids_sha256",
        "selector_labels_sidecar_sha256",
        "stage3_adapter_sha256",
        "neutral_input_builder_sha256",
        "candidate_authority_aggregate_sha256",
        "candidate_config_aggregate_sha256",
        "evaluator_sha256",
        "screen_result_schema_sha256",
    )
    return {
        field: ("%x" % (index + 1)) * 64
        for index, field in enumerate(fields)
    }


class ReceiptFixture:
    def __init__(self, anchor: Path):
        self.anchor = anchor
        self.roots = []
        self.artifacts = []
        manifests = []
        for index, candidate in enumerate(dr.CANDIDATE_ORDER):
            config = ("config-%s" % candidate).encode("ascii")
            executable = ("executable-%s" % candidate).encode("ascii")
            dependencies = [{
                "role": "model",
                "path": "benchmarks/frozen/%s.py" % candidate.lower(),
                "sha256": ("%x" % (index + 10)) * 64,
            }]
            manifest_body = {
                "schema": dr.V4_CANDIDATE_AUTHORITY_SCHEMA,
                "candidate": candidate,
                "native_candidate_id": dr.CANDIDATE_IDS[index],
                "config_encoding": "adapter-export-bytes-hex/v1",
                "config_bytes_hex": config.hex(),
                "config_sha256": hashlib.sha256(config).hexdigest(),
                "executable_encoding": "canonical-json-ascii-hex/v1",
                "executable_bytes_hex": executable.hex(),
                "executable_sha256": hashlib.sha256(executable).hexdigest(),
                "dependencies": dependencies,
                "dependency_aggregate_sha256": canonical_sha256(dependencies),
            }
            manifests.append(_sealed(manifest_body, "manifest_sha256"))
        campaign_body = {
            "schema": dr.V4_CAMPAIGN_AUTHORITY_SCHEMA,
            "candidate_order": list(dr.CANDIDATE_ORDER),
            "candidates": manifests,
        }
        self.campaign_authority = _sealed(campaign_body, "aggregate_sha256")
        authority_payload = canonical_json_bytes(self.campaign_authority)

        for index, candidate in enumerate(dr.CANDIDATE_ORDER):
            root_name = "legacy-%s" % candidate.lower()
            root = anchor / root_name
            root.mkdir()
            self.roots.append({"candidate": candidate, "path": root_name})
            manifest = manifests[index]
            attempt_body = {
                "schema": dr.V4_ATTEMPT_SCHEMA,
                "candidate_id": dr.CANDIDATE_IDS[index],
                "authority_name": candidate,
                "attempt_index": 1,
                "campaign_authority_sha256": self.campaign_authority[
                    "aggregate_sha256"
                ],
                "candidate_authority_sha256": manifest["manifest_sha256"],
                "authority_config_sha256": manifest["config_sha256"],
                "caller_config_sha256": manifest["config_sha256"],
                "caller_config_semantic_sha256": ("a" * 64),
                "cncp_sha256": ("b" * 64),
                "cncp_semantic_sha256": ("c" * 64),
                "campaign_runner_sha256": ("d" * 64),
                "adapter_execution_count": 2,
                "verification_replay_count": 1,
                "verification_replay_is_tuning": False,
                "retry_allowed": False,
                "tuning_allowed": False,
            }
            attempt = _sealed(attempt_body, "attempt_sha256")
            attempt_path = root / (candidate.lower() + ".attempt.json")
            attempt_payload = _write_json(attempt_path, attempt)
            authority_path = root / (candidate.lower() + ".campaign-authority.json")
            authority_path.write_bytes(authority_payload)
            self.artifacts.extend((
                self._artifact(
                    candidate,
                    "ATTEMPT_V4",
                    attempt_path,
                    attempt_payload,
                    attempt["attempt_sha256"],
                ),
                self._artifact(
                    candidate,
                    "CAMPAIGN_AUTHORITY_V4",
                    authority_path,
                    authority_payload,
                    self.campaign_authority["aggregate_sha256"],
                ),
            ))

        legacy_body = {
            "schema": dr.LEGACY_MIGRATION_SCHEMA,
            "status": "LEGACY_MIGRATION_HOLD",
            "source_campaign_schema": dr.V4_ATTEMPT_SCHEMA,
            "append_only": True,
            "legacy_attempts_preserved": True,
            "failure_classification": (
                "COMMON_PRE_SCORE_INFRASTRUCTURE_SUPPORTED_NOT_EXECUTION_PROVEN"
            ),
            "outcomes_unseen": "UNKNOWN",
            "labels_constructed_before_candidate_invocation": True,
            "epoch2_authorized": False,
            "cohort_role": "DEVELOPMENT_CONSUMED",
            "development_model_measurement_authorized": False,
            "development_refreeze_required": True,
            "claim_boundary": {
                "descriptive_only": True,
                "unbiased_claim_allowed": False,
                "generalization_claim_allowed": False,
                "promotion_allowed": False,
                "rtl_ppa_allowed": False,
            },
            "preserved_roots": self.roots,
            "artifacts": self.artifacts,
            "inventory_sha256": canonical_sha256(self.artifacts),
        }
        self.legacy = _sealed(legacy_body, "receipt_sha256")

        proposal_body = {
            "schema": dr.PROPOSAL_SCHEMA,
            "status": "DEVELOPMENT_REFREEZE_PROPOSED",
            "authority_domain": "CONSUMED_NEW108_DEVELOPMENT",
            "refreeze_id": "new108-development-refreeze-1",
            "legacy_migration_receipt_sha256": self.legacy["receipt_sha256"],
            "legacy_artifact_inventory_sha256": self.legacy["inventory_sha256"],
            "candidate_ids": list(dr.CANDIDATE_IDS),
            "bindings": _bindings(),
            "measurement_policy": _policy(),
        }
        self.proposal = _sealed(proposal_body, "proposal_sha256")

        direction_body = {
            "schema": dr.DIRECTION_SCHEMA,
            "status": "USER_CONTINUE_DIRECTION_RECORDED",
            "action": "CONTINUE_CONSUMED_NEW108_DEVELOPMENT",
            "authorization_provenance": "USER_SUPPLIED_UNAUTHENTICATED",
            "proposal_sha256": self.proposal["proposal_sha256"],
            "refreeze_id": self.proposal["refreeze_id"],
            "authorized_candidate_ids": list(dr.CANDIDATE_IDS),
            "acknowledged_policy": _policy(),
        }
        self.direction = _sealed(direction_body, "direction_sha256")

        authority_body = {
            "schema": dr.AUTHORITY_SCHEMA,
            "status": "DEVELOPMENT_REFREEZE_AUTHORIZED",
            "authority_domain": "CONSUMED_NEW108_DEVELOPMENT",
            "refreeze_id": self.proposal["refreeze_id"],
            "legacy_migration_receipt_sha256": self.legacy["receipt_sha256"],
            "proposal_sha256": self.proposal["proposal_sha256"],
            "direction_sha256": self.direction["direction_sha256"],
            "candidate_ids": list(dr.CANDIDATE_IDS),
            "bindings": _bindings(),
            "measurement_policy": _policy(),
        }
        self.authority = _sealed(authority_body, "authority_sha256")

    def _artifact(self, candidate, role, path, payload, semantic):
        return {
            "candidate": candidate,
            "candidate_id": dr.CANDIDATE_IDS[
                dr.CANDIDATE_ORDER.index(candidate)
            ],
            "artifact_role": role,
            "path": path.relative_to(self.anchor).as_posix(),
            "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "semantic_sha256": semantic,
        }

    def reseal_legacy(self):
        self.legacy["inventory_sha256"] = canonical_sha256(
            self.legacy["artifacts"]
        )
        unsigned = dict(self.legacy)
        unsigned.pop("receipt_sha256", None)
        self.legacy["receipt_sha256"] = canonical_sha256(unsigned)


class DevelopmentRefreezeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.anchor = Path(self.temporary.name)
        self.fixture = ReceiptFixture(self.anchor)

    def tearDown(self):
        self.temporary.cleanup()

    def assert_rejected(self, function, *arguments):
        with self.assertRaises(dr.DevelopmentRefreezeError):
            function(*arguments)

    def test_complete_chain_is_descriptive_only_and_sealed(self):
        fixture = self.fixture
        self.assertEqual(
            dr.verify_legacy_migration_hold_receipt(fixture.legacy, self.anchor),
            fixture.legacy["receipt_sha256"],
        )
        self.assertEqual(
            dr.verify_development_refreeze_authority(
                fixture.authority,
                fixture.proposal,
                fixture.direction,
                fixture.legacy,
                self.anchor,
            ),
            fixture.authority["authority_sha256"],
        )
        policy = fixture.authority["measurement_policy"]
        self.assertEqual(policy["legacy_outcomes_unseen"], "UNKNOWN")
        self.assertTrue(policy["labels_constructed_before_candidate_invocation"])
        self.assertTrue(policy["development_model_measurement_allowed"])
        for field in (
            "development_comparison_allowed",
            "unbiased_claim_allowed",
            "generalization_claim_allowed",
            "promotion_allowed",
            "rtl_ppa_allowed",
            "epoch2_authorized",
            "retry_allowed",
            "within_run_tuning_allowed",
        ):
            self.assertFalse(policy[field])

    def test_all_schema_files_are_exact_json_objects(self):
        directory = Path(dr.__file__).parent
        documents = {}
        instances = (
            self.fixture.legacy,
            self.fixture.proposal,
            self.fixture.direction,
            self.fixture.authority,
        )
        names = (
            "development_refreeze_legacy_migration.schema.json",
            "development_refreeze_proposal.schema.json",
            "development_refreeze_direction.schema.json",
            "development_refreeze_authority.schema.json",
        )
        for name in names:
            value = json.loads((directory / name).read_text(encoding="utf-8"))
            self.assertFalse(value["additionalProperties"])
            self.assertEqual(value["type"], "object")
            self.assertEqual(set(value["required"]), set(value["properties"]))
            documents[value["$id"]] = value
        registry = Registry().with_resources(
            (uri, Resource.from_contents(value))
            for uri, value in documents.items()
        )
        for schema, instance in zip(documents.values(), instances):
            jsonschema.Draft202012Validator(
                schema, registry=registry
            ).validate(instance)

    def test_byte_mutation_missing_and_extra_artifact_are_rejected(self):
        fixture = self.fixture
        path = self.anchor / fixture.artifacts[0]["path"]
        path.write_bytes(path.read_bytes() + b"\n")
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

        self.tearDown()
        self.setUp()
        fixture = self.fixture
        (self.anchor / fixture.artifacts[0]["path"]).unlink()
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

        self.tearDown()
        self.setUp()
        fixture = self.fixture
        root = self.anchor / fixture.roots[0]["path"]
        (root / "unrecorded.log").write_text("hidden", encoding="ascii")
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

    def test_resealed_path_aliases_are_rejected(self):
        for unsafe in (
            "../legacy-rg3/rg3.attempt.json",
            "/tmp/legacy-rg3/rg3.attempt.json",
            "legacy-rg3/../legacy-rg3/rg3.attempt.json",
            "legacy-rg3\\rg3.attempt.json",
        ):
            receipt = deepcopy(self.fixture.legacy)
            receipt["artifacts"][0]["path"] = unsafe
            receipt["inventory_sha256"] = canonical_sha256(receipt["artifacts"])
            unsigned = dict(receipt)
            unsigned.pop("receipt_sha256")
            receipt["receipt_sha256"] = canonical_sha256(unsigned)
            self.assert_rejected(
                dr.verify_legacy_migration_hold_receipt, receipt, self.anchor
            )

        aliased_anchor = self.anchor / ".." / self.anchor.name
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt,
            self.fixture.legacy,
            aliased_anchor,
        )

    def test_duplicate_root_and_artifact_rows_are_rejected(self):
        receipt = deepcopy(self.fixture.legacy)
        receipt["preserved_roots"][1]["path"] = receipt["preserved_roots"][0][
            "path"
        ]
        unsigned = dict(receipt)
        unsigned.pop("receipt_sha256")
        receipt["receipt_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, receipt, self.anchor
        )

        receipt = deepcopy(self.fixture.legacy)
        receipt["artifacts"][1] = deepcopy(receipt["artifacts"][0])
        receipt["inventory_sha256"] = canonical_sha256(receipt["artifacts"])
        unsigned = dict(receipt)
        unsigned.pop("receipt_sha256")
        receipt["receipt_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, receipt, self.anchor
        )

    def test_symlink_and_hardlink_aliases_are_rejected(self):
        fixture = self.fixture
        path = self.anchor / fixture.artifacts[0]["path"]
        target = self.anchor / "outside.json"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

        self.tearDown()
        self.setUp()
        fixture = self.fixture
        source = self.anchor / fixture.artifacts[1]["path"]
        destination = self.anchor / fixture.artifacts[3]["path"]
        destination.unlink()
        os.link(str(source), str(destination))
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

    def test_resealed_v4_retry_policy_is_rejected(self):
        fixture = self.fixture
        row = fixture.artifacts[0]
        path = self.anchor / row["path"]
        attempt = json.loads(path.read_text(encoding="utf-8"))
        attempt["retry_allowed"] = True
        unsigned = dict(attempt)
        unsigned.pop("attempt_sha256")
        attempt["attempt_sha256"] = canonical_sha256(unsigned)
        payload = _write_json(path, attempt)
        row["size_bytes"] = len(payload)
        row["sha256"] = hashlib.sha256(payload).hexdigest()
        row["semantic_sha256"] = attempt["attempt_sha256"]
        fixture.reseal_legacy()
        self.assert_rejected(
            dr.verify_legacy_migration_hold_receipt, fixture.legacy, self.anchor
        )

    def test_unknown_outcomes_labels_truth_and_epoch_hold_cannot_be_relaxed(self):
        mutations = (
            ("outcomes_unseen", "TRUE"),
            ("labels_constructed_before_candidate_invocation", False),
            ("epoch2_authorized", True),
            ("development_model_measurement_authorized", True),
        )
        for field, replacement in mutations:
            receipt = deepcopy(self.fixture.legacy)
            receipt[field] = replacement
            unsigned = dict(receipt)
            unsigned.pop("receipt_sha256")
            receipt["receipt_sha256"] = canonical_sha256(unsigned)
            self.assert_rejected(
                dr.verify_legacy_migration_hold_receipt, receipt, self.anchor
            )

    def test_all_claim_escalations_are_rejected_after_resealing(self):
        fields = (
            "development_comparison_allowed",
            "unbiased_claim_allowed",
            "generalization_claim_allowed",
            "promotion_allowed",
            "rtl_ppa_allowed",
            "epoch2_authorized",
            "holdout_reconstituted",
            "retry_allowed",
            "within_run_tuning_allowed",
        )
        for field in fields:
            proposal = deepcopy(self.fixture.proposal)
            proposal["measurement_policy"][field] = True
            unsigned = dict(proposal)
            unsigned.pop("proposal_sha256")
            proposal["proposal_sha256"] = canonical_sha256(unsigned)
            self.assert_rejected(
                dr.verify_development_refreeze_proposal,
                proposal,
                self.fixture.legacy,
                self.anchor,
            )

        proposal = deepcopy(self.fixture.proposal)
        proposal["measurement_policy"]["promotion_allowed"] = 0
        unsigned = dict(proposal)
        unsigned.pop("proposal_sha256")
        proposal["proposal_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_proposal,
            proposal,
            self.fixture.legacy,
            self.anchor,
        )

    def test_direction_must_be_explicit_and_bind_exact_proposal(self):
        direction = deepcopy(self.fixture.direction)
        direction["authorization_provenance"] = "SIGNED_USER"
        unsigned = dict(direction)
        unsigned.pop("direction_sha256")
        direction["direction_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_direction,
            direction,
            self.fixture.proposal,
            self.fixture.legacy,
            self.anchor,
        )

        direction = deepcopy(self.fixture.direction)
        direction["proposal_sha256"] = SHA
        unsigned = dict(direction)
        unsigned.pop("direction_sha256")
        direction["direction_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_direction,
            direction,
            self.fixture.proposal,
            self.fixture.legacy,
            self.anchor,
        )

    def test_authority_rejects_cross_binding_and_candidate_reordering(self):
        authority = deepcopy(self.fixture.authority)
        authority["bindings"]["evaluator_sha256"] = SHA
        unsigned = dict(authority)
        unsigned.pop("authority_sha256")
        authority["authority_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_authority,
            authority,
            self.fixture.proposal,
            self.fixture.direction,
            self.fixture.legacy,
            self.anchor,
        )

        authority = deepcopy(self.fixture.authority)
        authority["candidate_ids"].reverse()
        unsigned = dict(authority)
        unsigned.pop("authority_sha256")
        authority["authority_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_authority,
            authority,
            self.fixture.proposal,
            self.fixture.direction,
            self.fixture.legacy,
            self.anchor,
        )

    def test_unknown_fields_and_noncanonical_digest_are_rejected(self):
        proposal = deepcopy(self.fixture.proposal)
        proposal["promotion_note"] = "no"
        unsigned = dict(proposal)
        unsigned.pop("proposal_sha256")
        proposal["proposal_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_proposal,
            proposal,
            self.fixture.legacy,
            self.anchor,
        )

        proposal = deepcopy(self.fixture.proposal)
        proposal["bindings"]["evaluator_sha256"] = "A" * 64
        unsigned = dict(proposal)
        unsigned.pop("proposal_sha256")
        proposal["proposal_sha256"] = canonical_sha256(unsigned)
        self.assert_rejected(
            dr.verify_development_refreeze_proposal,
            proposal,
            self.fixture.legacy,
            self.anchor,
        )


if __name__ == "__main__":
    unittest.main()
