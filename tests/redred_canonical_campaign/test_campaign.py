from __future__ import annotations

import csv
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
RESULT_PATH = ROOT / "tests/a23_full_p6_replay/result.json"
SPEC = importlib.util.spec_from_file_location("redred_campaign", TOOL)
assert SPEC and SPEC.loader
campaign_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_module)


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def result() -> dict:
    return json.loads(RESULT_PATH.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_ref(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": digest(path)}


def zero_latency(count: int = 0) -> dict:
    return {"count": count, "mean": 0.0, "p50": 0, "p95": 0, "p99": 0, "max": 0}


class CanonicalCampaignTest(unittest.TestCase):
    def _mutated_result_manifest(self, mutation) -> tuple[tempfile.TemporaryDirectory, dict]:
        temporary = tempfile.TemporaryDirectory(prefix="redred-campaign-test.")
        document = result()
        mutation(document)
        path = Path(temporary.name) / "result.json"
        path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        value = manifest()
        value["providers"][0]["result"] = file_ref(path)
        return temporary, value

    def _reject_result_mutation(self, mutation, pattern: str) -> None:
        temporary, value = self._mutated_result_manifest(mutation)
        try:
            with self.assertRaisesRegex(campaign_module.CampaignError, pattern):
                campaign_module.validate_campaign(value, ROOT)
        finally:
            temporary.cleanup()

    def test_current_receipt_is_hold_and_only_receipt_consistent(self) -> None:
        receipt = campaign_module.validate_campaign(manifest(), ROOT, "validate")
        self.assertEqual(receipt["status"], "HOLD")
        self.assertEqual(receipt["trust_level"], {
            "receipt_envelope": "RECEIPT_CONSISTENT",
            "event_evidence": "NOT_REPLAYED",
            "release": "HOLD",
        })
        self.assertFalse(receipt["commands_executed"])
        self.assertFalse(receipt["event_artifact_verification"]["independent_event_replay"])
        self.assertIn("not independently replayed",
                      receipt["event_artifact_verification"]["statement"])
        for candidate in ("a2_p6", "a3_p6"):
            for suite in ("full50", "capacity22"):
                evidence = receipt["candidates"][candidate]["datasets"][suite]
                self.assertEqual(evidence["status"], "RECEIPT_CONSISTENT")
                self.assertEqual(evidence["event_evidence_status"], "NOT_REPLAYED")
                self.assertFalse(evidence["independent_event_replay"])
        self.assertNotIn("VALIDATED", json.dumps(receipt, sort_keys=True))

    def test_strict_envelope_and_local_pin_summary(self) -> None:
        receipt = campaign_module.validate_campaign(manifest(), ROOT)
        envelope = receipt["providers"]["actual_a23_p6"]["envelope"]
        self.assertEqual(envelope["status"], "RECEIPT_CONSISTENT")
        self.assertEqual(envelope["pins"]["verified_file_count"], 27)
        self.assertEqual(envelope["pins"]["verified_tool_count"], 2)
        self.assertEqual(envelope["pins"]["local_bytes"], "HASH_VERIFIED")
        self.assertEqual(envelope["mutation_status"], "15_KILLED_ACTUAL_RTL")
        self.assertEqual(envelope["qualification"], {
            "digital_RTL": "GO", "physical": "HOLD", "CDC_RDC": "HOLD",
        })

    def test_per_run_hashes_latencies_and_order_are_preserved(self) -> None:
        source = result()["owners"]["a2"]["full50"]["runs"]
        receipt = campaign_module.validate_campaign(manifest(), ROOT)
        evidence = receipt["candidates"]["a2_p6"]["datasets"]["full50"]
        official = campaign_module._load_official(ROOT, manifest()["official_registry"])[0]
        self.assertEqual(evidence["run_names"], list(official.FULL50))
        for name in evidence["run_names"]:
            preserved = evidence["per_run_evidence"][name]
            for key in ("events_sha256", "summary_sha256", "occurrence_to_accept",
                        "accept_to_retire", "prepared_trace_sha256", "trace_sha256"):
                self.assertEqual(preserved[key], source[name][key])

    def test_dry_run_has_pinned_plan_and_zero_execution(self) -> None:
        receipt = campaign_module.validate_campaign(manifest(), ROOT, "dry-run")
        self.assertEqual(receipt["status"], "HOLD")
        self.assertFalse(receipt["commands_executed"])
        self.assertEqual(len(receipt["execution_plan"]), 1)
        self.assertFalse(receipt["execution_plan"][0]["executed"])
        self.assertIn("tests/a23_full_p6_replay/run_replay.py",
                      receipt["execution_plan"][0]["command"])

    def test_cli_hold_exit_and_allow_hold_are_distinct(self) -> None:
        base = ["python3", str(TOOL), "validate", "--repo-root", str(ROOT)]
        held = subprocess.run(base, text=True, capture_output=True, check=False)
        allowed = subprocess.run(base + ["--allow-hold"], text=True,
                                 capture_output=True, check=False)
        self.assertEqual(held.returncode, 3)
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(json.loads(held.stdout)["status"], "HOLD")
        self.assertEqual(json.loads(allowed.stdout)["status"], "HOLD")

    def test_envelope_unknown_absent_and_contradictory_fields_fail(self) -> None:
        cases = [
            (lambda d: d.update({"unknown": 1}), "actual-P6 result keys differ"),
            (lambda d: d.__setitem__("schema", "unknown"), "schema/status differs"),
            (lambda d: d.__setitem__("status", "HOLD"), "schema/status differs"),
            (lambda d: d.__setitem__("ordered_link_adapter", True), "wrapper/link-adapter"),
            (lambda d: d.__setitem__("observation_wrapper_state_bits", False), "wrapper/link-adapter"),
            (lambda d: d.__setitem__("acceptance_observation", "ready_level"),
             "acceptance/retirement declaration"),
            (lambda d: d.__setitem__("retirement_scoreboard", "summary_only"),
             "acceptance/retirement declaration"),
            (lambda d: d["qualification"].__setitem__("digital_RTL", "HOLD"),
             "qualification boundary"),
            (lambda d: d["execution_accounting"].__setitem__("owners", True),
             "bool is forbidden"),
            (lambda d: d["execution_accounting"].__setitem__("full50_actual_executions", 149),
             "full50_actual_executions differs"),
            (lambda d: d["provenance"].update({"unknown": 1}), "provenance keys differ"),
            (lambda d: d["provenance"].__setitem__("package_commit", "0" * 40),
             "package commit verification"),
            (lambda d: d["provenance"].__setitem__("pins_path", "pins.json"),
             "pins_path differs"),
            (lambda d: d["provenance"].__setitem__("pins_sha256", "0" * 64),
             "pins hash differs"),
            (lambda d: d["provenance"]["verified_files"].pop(next(iter(
                d["provenance"]["verified_files"]))), "verified_files/verified_tools differ"),
            (lambda d: d["provenance"]["verified_tools"].__setitem__(next(iter(
                d["provenance"]["verified_tools"])), "0" * 64),
             "verified_files/verified_tools differ"),
            (lambda d: d["owners"].pop("a4"), "owners membership/order differs"),
            (lambda d: d["owners"]["a2"].pop("reset"), "owners.a2 keys differ"),
            (lambda d: d["owners"]["a2"]["reset"].__setitem__("generated", 7),
             "reset scenario counts differ|generated=source_overrun"),
            (lambda d: d["mutations"].pop(), "exactly 15"),
            (lambda d: d["mutations"][0].__setitem__("actual_rtl", False),
             "killed actual-RTL"),
            (lambda d: d["mutations"][0].__setitem__("first_required_diagnostic", "wrong"),
             "diagnostic/compile define"),
            (lambda d: d["mutations"][0].update({"unknown": 1}), r"mutations\[0\] keys differ"),
        ]
        for mutation, pattern in cases:
            with self.subTest(pattern=pattern):
                self._reject_result_mutation(mutation, pattern)

    def test_run_unknown_keys_bool_counters_and_bad_latency_fail(self) -> None:
        run = lambda d: d["owners"]["a2"]["full50"]["runs"]["core_sparse_identity"]
        cases = [
            (lambda d: run(d).update({"unknown": 0}), "runs.core_sparse_identity keys differ"),
            (lambda d: run(d).__setitem__("generated", True), "bool is forbidden"),
            (lambda d: run(d)["occurrence_to_accept"].__setitem__("count", True),
             "bool is forbidden"),
            (lambda d: run(d)["occurrence_to_accept"].update({"unknown": 0}),
             "occurrence_to_accept keys differ"),
            (lambda d: run(d)["occurrence_to_accept"].__setitem__("p50", 999),
             "percentiles are not monotonic"),
        ]
        for mutation, pattern in cases:
            with self.subTest(pattern=pattern):
                self._reject_result_mutation(mutation, pattern)

    def test_all_mutation_contract_bypasses_fail(self) -> None:
        cases = [
            (lambda d: d["mutations"][0].__setitem__("killed", False), "killed actual-RTL"),
            (lambda d: d["mutations"][0].__setitem__("exit_code", True), "exit_code"),
            (lambda d: d["mutations"][0]["source_mutation"].__setitem__(
                "base_sha256", "0" * 64), "source_mutation identity differs"),
            (lambda d: d["mutations"][2].__setitem__("source_mutation", {}),
             "source_mutation must be null"),
        ]
        for mutation, pattern in cases:
            with self.subTest(pattern=pattern):
                self._reject_result_mutation(mutation, pattern)

    def test_hard_conservation_equations_remain_fatal(self) -> None:
        self._reject_result_mutation(
            lambda d: d["owners"]["a2"]["full50"]["runs"][
                "core_sparse_identity"].__setitem__("accepted", 17),
            r"generated=source_overrun\+accepted",
        )
        self._reject_result_mutation(
            lambda d: d["owners"]["a2"]["full50"]["runs"][
                "core_sparse_identity"].__setitem__("retired", 15),
            "accepted=delivered",
        )

    def test_dataset_ids_and_classes_are_not_relabelable(self) -> None:
        for dataset_id, wrong_class in (
            ("full50", "public"), ("capacity22", "supplied"),
            ("organizer_supplied", "synthetic"), ("public_dataset", "supplied"),
        ):
            value = manifest()
            next(row for row in value["datasets"] if row["id"] == dataset_id)[
                "source_class"
            ] = wrong_class
            with self.subTest(dataset=dataset_id), self.assertRaisesRegex(
                    campaign_module.CampaignError, "class is hard-bound"):
                campaign_module.validate_campaign(value, ROOT)
        value = manifest()
        value["datasets"][3]["id"] = "anonymous_public"
        with self.assertRaisesRegex(campaign_module.CampaignError, "dataset IDs/order are hard-bound"):
            campaign_module.validate_campaign(value, ROOT)

    def test_present_supplied_bytes_are_all_hash_pinned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="redred-dataset.") as directory:
            root = Path(directory)
            content = root / "content.bin"
            adapter = root / "adapter.py"
            traces = root / "traces.json"
            content.write_bytes(b"supplied-content\n")
            adapter.write_bytes(b"adapter-v1\n")
            traces.write_bytes(b'{"traces":[]}\n')
            provenance = root / "provenance.json"
            provenance.write_text(json.dumps({
                "schema": "redred_dataset_provenance_v2",
                "dataset_id": "organizer_supplied", "source_class": "supplied",
                "provider": "organizer", "delivery_id": "delivery-1", "license": "competition-use",
                "content": file_ref(content), "adapter": file_ref(adapter),
                "trace_manifest": file_ref(traces),
            }, sort_keys=True) + "\n", encoding="utf-8")
            value = manifest()
            value["datasets"][2]["provenance_manifest"] = file_ref(provenance)
            receipt = campaign_module.validate_campaign(value, ROOT)
            supplied = receipt["datasets"]["organizer_supplied"]
            self.assertEqual(supplied["status"], "BYTES_PINNED_NO_CANDIDATE_RESULTS")
            self.assertEqual(supplied["content"]["sha256"], digest(content))
            content.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(campaign_module.CampaignError, "SHA-256 mismatch"):
                campaign_module.validate_campaign(value, ROOT)

    def test_capacity22_inherits_full50_without_full50_candidate_pointer(self) -> None:
        value = manifest()
        value["candidates"][2]["evidence"]["full50"] = None
        receipt = campaign_module.validate_campaign(value, ROOT)
        self.assertEqual(receipt["candidates"]["a2_p6"]["datasets"]["full50"]["status"],
                         "MISSING")
        capacity = receipt["candidates"]["a2_p6"]["datasets"]["capacity22"]
        self.assertEqual(capacity["status"], "RECEIPT_CONSISTENT")
        self.assertEqual(capacity["execution_count"], 0)
        self.assertTrue(capacity["subset_view"])

    def test_capacity22_window_and_zero_execution_bypasses_fail(self) -> None:
        def window(d: dict) -> None:
            d["owners"]["a2"]["full50"]["runs"]["core_simultaneous_identity"][
                "fixed_window_cycles"
            ] += 1

        self._reject_result_mutation(window, "fixed measurement window differs")
        self._reject_result_mutation(
            lambda d: d["owners"]["a2"]["capacity22"].__setitem__("execution_count", True),
            "bool is forbidden",
        )
        self._reject_result_mutation(
            lambda d: d["owners"]["a2"]["capacity22"].__setitem__(
                "independent_additional_sample_count", 1),
            "independent/additional samples",
        )

    def test_capacity22_cannot_be_pooled_with_full50(self) -> None:
        value = manifest()
        value["aggregation_groups"][0]["datasets"] = ["full50", "capacity22"]
        with self.assertRaisesRegex(campaign_module.CampaignError,
                                    "refuse to pool capacity22 with full50"):
            campaign_module.validate_campaign(value, ROOT)

    def test_missing_or_bad_verify_run_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="redred-empty-run.") as directory:
            with self.assertRaisesRegex(campaign_module.CampaignError, "containing artifacts"):
                campaign_module.validate_campaign(
                    manifest(), ROOT, verify_run_root=Path(directory)
                )

    def _write_case(self, work: Path, owner: str, trace: str, row: dict,
                    event_rows: list[dict[str, str]]) -> None:
        case = work / "artifacts" / owner / "none" / trace
        case.mkdir(parents=True)
        events_path = case / "events.csv"
        event_fields = [
            "owner", "trace", "tb_only_event_id", "logical_source",
            "occurrence_cycle", "accept_cycle", "retire_cycle", "deadline_cycle",
            "event_state",
        ]
        with events_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=event_fields)
            writer.writeheader()
            writer.writerows(event_rows)
        summary_path = case / "summary.csv"
        summary_fields = [
            "owner", "trace", "generated", "source_overrun", "accepted", "retired",
            "fixed_window_retired", "fixed_window_cycles", "observation_cycles",
            "reset_test",
        ]
        summary = {key: row[key] for key in summary_fields if key not in {"owner", "trace"}}
        summary.update({"owner": owner, "trace": trace})
        with summary_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerow(summary)
        row["events_sha256"] = digest(events_path)
        row["summary_sha256"] = digest(summary_path)

    def _artifact_fixture(self, root: Path) -> tuple[dict, Path]:
        document = result()
        full_manifest = json.loads((ROOT / "benchmarks/clean_slate_aer/manifest.neutrality-n16.json").read_text())
        cycles = {row["name"]: row["stim_cycles"] for row in full_manifest["runs"]}
        capacity_names = document["owners"]["a2"]["capacity22"]["run_names"]
        work = root / "work"
        for owner in ("a2", "a3", "a4"):
            runs = document["owners"][owner]["full50"]["runs"]
            for name, row in runs.items():
                row.update({
                    "generated": 0, "source_overrun": 0, "accepted": 0, "retired": 0,
                    "fixed_window_retired": 0, "fixed_window_cycles": cycles[name],
                    "fixed_window_events_per_cycle": 0.0, "reset_test": 0,
                    "occurrence_to_accept": zero_latency(), "accept_to_retire": zero_latency(),
                })
                if owner in {"a2", "a3"}:
                    self._write_case(work, owner, name, row, [])
            total_cycles = sum(cycles.values())
            document["owners"][owner]["full50"]["aggregate"] = {
                "run_count": 50,
                "totals": {"generated": 0, "source_overrun": 0, "accepted": 0,
                           "retired": 0, "fixed_window_retired": 0,
                           "fixed_window_cycles": total_cycles},
                "occurrence_to_accept": zero_latency(), "accept_to_retire": zero_latency(),
                "fixed_window_events_per_cycle": 0.0,
            }
            capacity_cycles = sum(cycles[name] for name in capacity_names)
            document["owners"][owner]["capacity22"]["aggregate"] = {
                "run_count": 22,
                "totals": {"generated": 0, "source_overrun": 0, "accepted": 0,
                           "retired": 0, "fixed_window_retired": 0,
                           "fixed_window_cycles": capacity_cycles},
                "occurrence_to_accept": zero_latency(), "accept_to_retire": zero_latency(),
                "fixed_window_events_per_cycle": 0.0,
            }
            if owner in {"a2", "a3"}:
                reset = document["owners"][owner]["reset"]
                reset.update({
                    "generated": 8, "source_overrun": 0, "accepted": 8, "retired": 8,
                    "fixed_window_retired": 0, "fixed_window_cycles": 0,
                    "fixed_window_events_per_cycle": 0.0, "reset_test": 1,
                    "occurrence_to_accept": zero_latency(8),
                    "accept_to_retire": zero_latency(8),
                })
                events = [{
                    "owner": owner, "trace": "basic_reset_drain",
                    "tb_only_event_id": str(index), "logical_source": str(index),
                    "occurrence_cycle": str(index), "accept_cycle": str(index),
                    "retire_cycle": str(index), "deadline_cycle": str(index),
                    "event_state": "retired",
                } for index in range(8)]
                self._write_case(work, owner, "basic_reset_drain", reset, events)
        result_path = root / "result.json"
        result_path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
        value = manifest()
        value["providers"][0]["result"] = file_ref(result_path)
        return value, work

    def test_accessible_artifacts_upgrade_only_event_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="redred-artifacts.") as directory:
            value, work = self._artifact_fixture(Path(directory))
            receipt = campaign_module.validate_campaign(
                value, ROOT, "validate", verify_run_root=work
            )
            self.assertEqual(receipt["status"], "HOLD")
            self.assertEqual(receipt["trust_level"], {
                "receipt_envelope": "RECEIPT_CONSISTENT",
                "event_evidence": "ARTIFACT_RECOMPUTED",
                "release": "HOLD",
            })
            artifacts = receipt["event_artifact_verification"]
            self.assertEqual(artifacts["status"], "ARTIFACT_RECOMPUTED")
            self.assertEqual(artifacts["case_count"], 102)
            self.assertTrue(artifacts["independent_event_replay"])
            evidence = receipt["candidates"]["a2_p6"]["datasets"]["full50"]
            self.assertEqual(evidence["status"], "RECEIPT_CONSISTENT")
            self.assertEqual(evidence["event_evidence_status"], "ARTIFACT_RECOMPUTED")


if __name__ == "__main__":
    unittest.main()
