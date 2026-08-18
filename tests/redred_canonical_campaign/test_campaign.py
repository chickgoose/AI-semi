from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks/redred_canonical_campaign"
MANIFEST_PATH = PACKAGE / "campaign.json"
TOOL = PACKAGE / "campaign.py"
SPEC = importlib.util.spec_from_file_location("redred_campaign", TOOL)
assert SPEC and SPEC.loader
campaign_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_module)


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CanonicalCampaignTest(unittest.TestCase):
    def test_current_campaign_is_honest_hold_with_actual_a2_a3_validated(self) -> None:
        receipt = campaign_module.validate_campaign(manifest(), ROOT, "validate")
        self.assertEqual(receipt["status"], "HOLD")
        self.assertFalse(receipt["commands_executed"])
        for candidate in ("a2_p6", "a3_p6"):
            self.assertEqual(
                receipt["candidates"][candidate]["datasets"]["full50"]["status"],
                "VALIDATED",
            )
            self.assertEqual(
                receipt["candidates"][candidate]["datasets"]["capacity22"]["status"],
                "VALIDATED",
            )
        for candidate in ("fovea", "cluster2"):
            self.assertEqual(
                receipt["candidates"][candidate]["datasets"]["full50"]["status"],
                "MISSING",
            )
        self.assertEqual(receipt["datasets"]["organizer_supplied"]["source_class"], "supplied")
        self.assertEqual(receipt["datasets"]["public_dataset"]["source_class"], "public")
        reasons = "\n".join(receipt["hold_reasons"])
        self.assertIn("candidate fovea/full50", reasons)
        self.assertIn("candidate cluster2/full50", reasons)
        self.assertIn("dataset organizer_supplied", reasons)
        self.assertIn("dataset public_dataset", reasons)

    def test_dry_run_emits_pinned_plan_without_execution(self) -> None:
        receipt = campaign_module.validate_campaign(manifest(), ROOT, "dry-run")
        self.assertEqual(receipt["status"], "HOLD")
        self.assertFalse(receipt["commands_executed"])
        self.assertEqual(len(receipt["execution_plan"]), 1)
        plan = receipt["execution_plan"][0]
        self.assertFalse(plan["executed"])
        self.assertIn("tests/a23_full_p6_replay/run_replay.py", plan["command"])
        self.assertEqual(plan["provider"], "actual_a23_p6")

    def test_cli_hold_exit_and_allow_hold_are_distinct(self) -> None:
        base = ["python3", str(TOOL), "validate", "--repo-root", str(ROOT)]
        held = subprocess.run(base, text=True, capture_output=True, check=False)
        allowed = subprocess.run(base + ["--allow-hold"], text=True,
                                 capture_output=True, check=False)
        self.assertEqual(held.returncode, 3)
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(json.loads(held.stdout)["status"], "HOLD")
        self.assertEqual(json.loads(allowed.stdout)["status"], "HOLD")

    def test_capacity22_cannot_be_pooled_with_full50(self) -> None:
        value = manifest()
        value["aggregation_groups"][0]["datasets"] = ["full50", "capacity22"]
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    "refuse to pool capacity22 with full50"):
            campaign_module.validate_campaign(value, ROOT)

    def test_measurement_definition_must_be_identical(self) -> None:
        value = manifest()
        value["candidates"][3]["measurement_definition"] = "candidate_private_window"
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    "measurement definition differs"):
            campaign_module.validate_campaign(value, ROOT)

    def _mutated_result_manifest(self, mutation) -> tuple[tempfile.TemporaryDirectory, dict]:
        temporary = tempfile.TemporaryDirectory(prefix="redred-campaign-test.")
        result = json.loads((ROOT / "tests/a23_full_p6_replay/result.json").read_text())
        mutation(result)
        path = Path(temporary.name) / "result.json"
        path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        value = manifest()
        value["providers"][0]["result"] = {
            "path": str(path), "sha256": digest(path),
        }
        return temporary, value

    def test_cross_candidate_trace_hash_difference_fails(self) -> None:
        def mutate(result: dict) -> None:
            result["owners"]["a3"]["full50"]["runs"]["core_sparse_identity"][
                "prepared_trace_sha256"
            ] = "0" * 64

        temporary, value = self._mutated_result_manifest(mutate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    "prepared_trace_sha256 differs across candidates"):
            campaign_module.validate_campaign(value, ROOT)

    def test_source_trace_hash_must_match_frozen_registry(self) -> None:
        def mutate(result: dict) -> None:
            result["owners"]["a3"]["full50"]["runs"]["core_sparse_identity"][
                "trace_sha256"
            ] = "0" * 64

        temporary, value = self._mutated_result_manifest(mutate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(campaign_module.CampaignError, "trace SHA differs"):
            campaign_module.validate_campaign(value, ROOT)

    def test_per_run_conservation_failure_is_fatal(self) -> None:
        def mutate(result: dict) -> None:
            result["owners"]["a2"]["full50"]["runs"]["core_sparse_identity"][
                "accepted"
            ] += 1

        temporary, value = self._mutated_result_manifest(mutate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    r"generated=source_overrun\+accepted"):
            campaign_module.validate_campaign(value, ROOT)

    def test_accepted_must_equal_delivered(self) -> None:
        def mutate(result: dict) -> None:
            result["owners"]["a2"]["full50"]["runs"]["core_sparse_identity"][
                "retired"
            ] -= 1

        temporary, value = self._mutated_result_manifest(mutate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(campaign_module.CampaignError, "accepted=delivered"):
            campaign_module.validate_campaign(value, ROOT)

    def test_capacity22_cannot_claim_additional_executions(self) -> None:
        def mutate(result: dict) -> None:
            result["owners"]["a2"]["capacity22"]["execution_count"] = 22

        temporary, value = self._mutated_result_manifest(mutate)
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    "claims independent or additional execution"):
            campaign_module.validate_campaign(value, ROOT)

    def test_missing_claimed_evidence_is_fail_closed_not_hold(self) -> None:
        value = manifest()
        value["providers"][0]["result"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(campaign_module.CampaignError, "SHA-256 mismatch"):
            campaign_module.validate_campaign(value, ROOT)

    def test_supplied_cannot_masquerade_as_synthetic(self) -> None:
        value = manifest()
        supplied = next(row for row in value["datasets"] if row["id"] == "organizer_supplied")
        supplied["suite_key"] = "full50"
        supplied["manifest"] = copy.deepcopy(value["datasets"][0]["manifest"])
        with self.assertRaisesRegex(campaign_module.CampaignError, "cannot masquerade"):
            campaign_module.validate_campaign(value, ROOT)


if __name__ == "__main__":
    unittest.main()
