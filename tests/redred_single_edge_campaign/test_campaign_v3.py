#!/usr/bin/env python3
"""Adversarial tests for the version-three sealed campaign consumer."""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_single_edge_campaign"
PROGRAM = PACKAGE / "campaign_v3.py"
MANIFEST = PACKAGE / "campaign_v3.json"
CONTRACT = PACKAGE / "sealed_tuple.schema.json"


def load_program():
    spec = importlib.util.spec_from_file_location("redred_single_edge_campaign_v3", PROGRAM)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load campaign v3")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


campaign = load_program()
sealed = campaign.sealed


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


class TupleFixture:
    owners = ("a2", "a3")
    traffic = ("trace0",)
    reset_run = "reset"
    activation_run = "activation"
    mutations = ("drop",)
    diagnostics = {"drop": "A23_SE_DROP_FAIL"}

    def __init__(self, directory: Path):
        self.directory = directory
        self.members = self._members()
        self.publication_path = directory / "publication.json"
        self.bundle_path = directory / "bundle.tar.gz"
        self.binding = self.seal(self.members)

    @staticmethod
    def events(owner, run, traffic):
        if traffic:
            rows = [
                {
                    "owner": owner, "run": run, "event_id": 0,
                    "logical_source": 1, "occurrence_cycle": 0,
                    "accept_cycle": 0, "retire_cycle": 1, "deadline_cycle": 10,
                    "accept_ordinal": 0, "retire_ordinal": 0,
                    "event_state": "retired",
                },
                {
                    "owner": owner, "run": run, "event_id": 1,
                    "logical_source": 1, "occurrence_cycle": 0,
                    "accept_cycle": -1, "retire_cycle": -1, "deadline_cycle": 10,
                    "accept_ordinal": -1, "retire_ordinal": -1,
                    "event_state": "source_overrun",
                },
                {
                    "owner": owner, "run": run, "event_id": 2,
                    "logical_source": 2, "occurrence_cycle": 1,
                    "accept_cycle": 1, "retire_cycle": 2, "deadline_cycle": 10,
                    "accept_ordinal": 1, "retire_ordinal": 1,
                    "event_state": "retired",
                },
            ]
        else:
            rows = [{
                "owner": owner, "run": run, "event_id": 0,
                "logical_source": 2, "occurrence_cycle": 0,
                "accept_cycle": 0, "retire_cycle": 1, "deadline_cycle": 10,
                "accept_ordinal": 0, "retire_ordinal": 0,
                "event_state": "retired",
            }]
        return csv_bytes(sealed.EVENT_FIELDS, rows)

    @staticmethod
    def summary(owner, run, traffic, reset=False, activation=False):
        row = {
            "owner": owner, "run": run,
            "generated": 3 if traffic else 1,
            "source_overrun": 1 if traffic else 0,
            "accepted": 2 if traffic else 1,
            "retired": 2 if traffic else 1,
            "fixed_window_retired": 2 if traffic else 1,
            "measurement_start_cycle": 0, "measurement_end_cycle": 2,
            "observation_cycles": 2,
            "count2_commits": 1 if activation else 0,
            "reset_test": 1 if reset else 0,
            "pre_reset_clean_drain": 1 if reset else 0,
            "protocol_error": 0,
        }
        return csv_bytes(sealed.SUMMARY_FIELDS, [row])

    def _members(self):
        trace = [
            {"event_id": 0, "logical_source": 1, "occurrence_cycle": 0, "deadline_cycle": 10},
            {"event_id": 1, "logical_source": 1, "occurrence_cycle": 0, "deadline_cycle": 10},
            {"event_id": 2, "logical_source": 2, "occurrence_cycle": 1, "deadline_cycle": 10},
        ]
        trace_bytes = b"".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for row in trace
        )
        prepared = b"4 3 0 0 0 0 0 0 0\n0 0 1 1 10\n0 1 1 1 10\n1 2 2 2 10\n"
        members = {"inputs/trace0.jsonl": trace_bytes, "prepared/trace0.trace": prepared}
        result_owners = {}
        for owner in self.owners:
            run_claims = {}
            for run in (*self.traffic, self.reset_run, self.activation_run):
                traffic_run = run in self.traffic
                base = f"runs/{owner}/{run}"
                events = self.events(owner, run, traffic_run)
                summary = self.summary(
                    owner, run, traffic_run, run == self.reset_run, run == self.activation_run,
                )
                members[f"{base}/events.csv"] = events
                members[f"{base}/summary.csv"] = summary
                members[f"{base}/simulation.log"] = b"A23_SE_ACTUAL_RTL_PASS\n"
                source = sealed.jsonl_records(trace_bytes, "fixture") if traffic_run else None
                metrics, _, _ = sealed.validate_run(owner, run, events, summary, source)
                run_claims[run] = metrics
            traffic_claim = run_claims["trace0"]
            result_owners[owner] = {
                "runs": run_claims,
                "aggregate": {
                    "run_count": 1,
                    "totals": {
                        key: traffic_claim[key] for key in (
                            "generated", "source_overrun", "accepted", "retired",
                            "fixed_window_retired",
                        )
                    },
                    "occurrence_to_accept": traffic_claim["occurrence_to_accept"],
                    "accept_to_retire": traffic_claim["accept_to_retire"],
                    "fixed_window_events_per_cycle": traffic_claim["fixed_window_events_per_cycle"],
                },
            }
            members[f"mutations/{owner}/drop.log"] = b"A23_SE_DROP_FAIL\n"
        result = {
            "schema": "fixture_result_v2",
            "evidence_class": "REDRED_SINGLE_EDGE_SYNTHETIC_ACTUAL_RTL_SEALED_V2",
            "status": "PASS", "source_class": "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": True, "official_contest_traffic": False,
            "p6_evidence_used": False, "release_status": "HOLD",
            "selection_status": "HOLD", "owners": result_owners,
            "mutations": [
                {
                    "owner": owner, "mutation": "drop", "killed": True,
                    "first_diagnostic": "A23_SE_DROP_FAIL",
                    "log_sha256": sealed.digest(members[f"mutations/{owner}/drop.log"]),
                }
                for owner in self.owners
            ],
        }
        members["result/result.json"] = json_bytes(result)
        return members

    def seal(self, source_members):
        members = copy.deepcopy(source_members)
        members.pop("MANIFEST.json", None)
        manifest = {
            "schema": "fixture_manifest_v2",
            "evidence_class": "REDRED_SINGLE_EDGE_SYNTHETIC_ACTUAL_RTL_SEALED_V2",
            "entries": {
                path: {"sha256": sealed.digest(data), "size_bytes": len(data)}
                for path, data in sorted(members.items())
            },
        }
        members["MANIFEST.json"] = json_bytes(manifest)
        bundle_stream = io.BytesIO()
        with tarfile.open(fileobj=bundle_stream, mode="w:gz") as archive:
            for path, data in sorted(members.items()):
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mode = 0o444
                archive.addfile(info, io.BytesIO(data))
        bundle = bundle_stream.getvalue()
        result_data = members["result/result.json"]
        result = sealed.load_json_bytes(result_data, "fixture result")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=ROOT, text=True,
        ).strip()

        def inventory(paths):
            return [{
                "path": path,
                "blob_sha256": subprocess.check_output(
                    ["git", "rev-parse", f"{commit}:{path}"], cwd=ROOT, text=True,
                ).strip(),
            } for path in paths]

        producer = {
            "commit": commit, "tree": tree,
            "verifier_sha256": "3" * 64, "schema_sha256": "4" * 64,
            "runner_sha256": "5" * 64, "testbench_sha256": "6" * 64,
            "tool_pins_sha256": "7" * 64,
            "inventory": inventory(["benchmarks/redred_single_edge_campaign/campaign.py"]),
        }
        rtl = {
            "source_commit": commit, "source_tree": tree,
            "integration_commit": commit, "integration_tree": tree,
            "inventory": inventory(["benchmarks/redred_single_edge_campaign/campaign.py"]),
        }
        publication = {
            "schema": "redred_single_edge_synthetic_publication_v2",
            "evidence_class": manifest["evidence_class"], "status": "PASS",
            "source_class": "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": True, "official_contest_traffic": False,
            "p6_evidence_used": False, "release_status": "HOLD",
            "selection_status": "HOLD", "producer": producer, "rtl": rtl,
            "bundle": {
                "sha256": sealed.digest(bundle), "size_bytes": len(bundle),
                "manifest_member": "MANIFEST.json",
                "manifest_sha256": sealed.digest(members["MANIFEST.json"]),
                "entry_count": len(members),
            },
            "result": {
                "member": "result/result.json", "sha256": sealed.digest(result_data),
                "semantic_sha256": sealed.digest(sealed.canonical_semantic(result)),
                "size_bytes": len(result_data),
            },
        }
        publication_data = json_bytes(publication)
        self.bundle_path.write_bytes(bundle)
        self.publication_path.write_bytes(publication_data)
        return {
            "publication_sha256": sealed.digest(publication_data),
            "publication_size_bytes": len(publication_data),
            "publication_schema": publication["schema"],
            "evidence_class": publication["evidence_class"], "status": "PASS",
            "source_class": "TEAM_DEFINED_SYNTHETIC",
            "canonical_redred_traffic": True, "official_contest_traffic": False,
            "p6_evidence_used": False, "release_status": "HOLD",
            "selection_status": "HOLD", "producer": producer, "rtl": rtl,
            "bundle_sha256": sealed.digest(bundle), "bundle_size_bytes": len(bundle),
            "manifest_schema": manifest["schema"], "manifest_member": "MANIFEST.json",
            "manifest_sha256": sealed.digest(members["MANIFEST.json"]),
            "entry_count": len(members), "result_schema": result["schema"],
            "result_member": "result/result.json", "result_sha256": sealed.digest(result_data),
            "result_semantic_sha256": sealed.digest(sealed.canonical_semantic(result)),
            "result_size_bytes": len(result_data), "owners": list(self.owners),
            "traffic_runs": list(self.traffic), "reset_run": self.reset_run,
            "activation_run": self.activation_run, "mutations": list(self.mutations),
            "diagnostics": self.diagnostics,
        }


class CampaignV3Tests(unittest.TestCase):
    def test_default_is_explicit_dual_missing_hold(self):
        report = campaign.evaluate(MANIFEST, ROOT)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(
            report["gates"]["synthetic_v2_sealed_tuple"],
            "HOLD_MISSING_SYNTHETIC_V2_PRODUCER_TUPLE",
        )
        self.assertEqual(
            report["gates"]["public_v2_sealed_tuple"],
            "HOLD_MISSING_PUBLIC_V2_PRODUCER_TUPLE",
        )
        self.assertEqual(report["gates"]["system_release"], "HOLD")
        self.assertTrue(all(value is False for value in report["claim_boundary"].values()))

    def test_cli_hold_exit_and_allow_hold(self):
        command = [sys.executable, str(PROGRAM), "evaluate"]
        held = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        allowed = subprocess.run(
            [*command, "--allow-hold"], cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(held.returncode, 3, held.stderr)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertNotIn('"status": "PASS"', held.stdout)

    def test_partial_or_unbound_supplied_tuple_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            dummy = Path(directory) / "dummy"
            dummy.write_bytes(b"x")
            with self.assertRaisesRegex(campaign.CampaignV3Error, "must be supplied together"):
                campaign.evaluate(MANIFEST, ROOT, synthetic_publication=dummy)
            with self.assertRaisesRegex(campaign.CampaignV3Error, "binding is unavailable"):
                campaign.evaluate(
                    MANIFEST, ROOT, synthetic_publication=dummy, synthetic_bundle=dummy,
                )

    def test_public_slot_rejects_synthetic_binding_and_p6_relabel(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = TupleFixture(Path(directory))
            manifest = sealed.load_json_bytes(MANIFEST.read_bytes(), "manifest")
            row = copy.deepcopy(manifest["sealed_producers"]["public_v2"])
            row["state"] = "BOUND"
            row["binding"] = fixture.binding
            with self.assertRaisesRegex(campaign.CampaignV3Error, "binding identity differs"):
                campaign.validate_producer("public_v2", row)
            row = copy.deepcopy(manifest["sealed_producers"]["synthetic_v2"])
            row["state"] = "BOUND"
            row["binding"] = copy.deepcopy(fixture.binding)
            row["binding"]["p6_evidence_used"] = True
            with self.assertRaisesRegex(campaign.CampaignV3Error, "binding identity differs"):
                campaign.validate_producer("synthetic_v2", row)
            with self.assertRaisesRegex(sealed.SealedTupleError, "classification/schema differs"):
                sealed.validate_tuple(
                    fixture.publication_path, fixture.bundle_path, fixture.binding, "public_v2",
                )

    def test_manifest_duplicate_key_and_policy_relabel_fail(self):
        raw = MANIFEST.read_text(encoding="utf-8")
        duplicate = raw.replace(
            '"schema": "redred_single_edge_campaign_manifest_v3",',
            '"schema": "redred_single_edge_campaign_manifest_v3", "schema": "attack",',
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaisesRegex(sealed.SealedTupleError, "duplicate JSON key"):
                sealed.load_json_bytes(path.read_bytes(), "campaign v3 manifest")
            with self.assertRaisesRegex(campaign.CampaignV3Error, "manifest binding differs"):
                campaign.validate_manifest(path, ROOT)
            value = sealed.load_json_bytes(MANIFEST.read_bytes(), "manifest")
            value["policies"]["synthetic_public_pooling"] = "ALLOWED"
            path.write_bytes(json_bytes(value))
            with self.assertRaisesRegex(campaign.CampaignV3Error, "manifest binding differs"):
                campaign.validate_manifest(path, ROOT)

    def test_contract_and_legacy_are_immutably_pinned(self):
        manifest = sealed.load_json_bytes(MANIFEST.read_bytes(), "manifest")
        self.assertEqual(manifest["sealed_tuple_contract"]["sha256"], sealed.digest(CONTRACT.read_bytes()))
        self.assertEqual(manifest["sealed_tuple_contract"]["sha256"], campaign.CONTRACT_SHA256)
        self.assertEqual(manifest["legacy_v2"]["sha256"], campaign.LEGACY_SHA256)
        self.assertEqual(sealed.digest(MANIFEST.read_bytes()), campaign.DEFAULT_MANIFEST_SHA256)

    def test_cli_manifest_and_repository_symlinks_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_link = Path(directory) / "campaign.json"
            root_link = Path(directory) / "repo"
            manifest_link.symlink_to(MANIFEST)
            root_link.symlink_to(ROOT, target_is_directory=True)
            for arguments in (
                ["--manifest", str(manifest_link)],
                ["--repo-root", str(root_link)],
            ):
                completed = subprocess.run(
                    [sys.executable, str(PROGRAM), "evaluate", *arguments],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertIn("REDRED_SINGLE_EDGE_CAMPAIGN_V3_FAIL", completed.stderr)


class SealedTupleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = TupleFixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, members=None):
        binding = self.fixture.binding if members is None else self.fixture.seal(members)
        return sealed.validate_tuple(
            self.fixture.publication_path, self.fixture.bundle_path, binding, "synthetic_v2",
        )

    def test_complete_tuple_recomputes_conservation_order_and_latency(self):
        report = self.validate()
        self.assertEqual(report["status"], "PASS")
        for owner in self.fixture.owners:
            aggregate = report["owners"][owner]
            self.assertEqual(aggregate["totals"], {
                "generated": 3, "source_overrun": 1, "accepted": 2,
                "retired": 2, "fixed_window_retired": 2,
            })
            self.assertEqual(aggregate["occurrence_to_accept"]["mean"], 0.0)
            self.assertEqual(aggregate["accept_to_retire"]["mean"], 1.0)

    def test_resealed_event_corruption_and_temporal_reorder_fail(self):
        members = copy.deepcopy(self.fixture.members)
        path = "runs/a2/trace0/events.csv"
        rows = sealed.csv_records(members[path], sealed.EVENT_FIELDS, "fixture")
        rows[2]["logical_source"] = "3"
        members[path] = csv_bytes(sealed.EVENT_FIELDS, rows)
        with self.assertRaisesRegex(sealed.SealedTupleError, "differs from source trace"):
            self.validate(members)
        members = copy.deepcopy(self.fixture.members)
        rows = sealed.csv_records(members[path], sealed.EVENT_FIELDS, "fixture")
        rows[0]["accept_ordinal"], rows[2]["accept_ordinal"] = "1", "0"
        rows[0]["retire_ordinal"], rows[2]["retire_ordinal"] = "1", "0"
        members[path] = csv_bytes(sealed.EVENT_FIELDS, rows)
        with self.assertRaisesRegex(sealed.SealedTupleError, "temporal order"):
            self.validate(members)

    def test_coherently_resealed_duplicate_source_acceptance_fails_latch_replay(self):
        members = copy.deepcopy(self.fixture.members)
        event_path = "runs/a2/trace0/events.csv"
        events = sealed.csv_records(members[event_path], sealed.EVENT_FIELDS, "fixture")
        events[1].update({
            "accept_cycle": "0", "retire_cycle": "1",
            "accept_ordinal": "1", "retire_ordinal": "1", "event_state": "retired",
        })
        events[2]["accept_ordinal"] = "2"
        events[2]["retire_ordinal"] = "2"
        members[event_path] = csv_bytes(sealed.EVENT_FIELDS, events)

        summary_path = "runs/a2/trace0/summary.csv"
        summary = sealed.csv_records(members[summary_path], sealed.SUMMARY_FIELDS, "fixture")
        summary[0].update({
            "source_overrun": "0", "accepted": "3", "retired": "3",
            "fixed_window_retired": "3",
        })
        members[summary_path] = csv_bytes(sealed.SUMMARY_FIELDS, summary)

        result = sealed.load_json_bytes(members["result/result.json"], "result")
        run = result["owners"]["a2"]["runs"]["trace0"]
        run.update({
            "source_overrun": 0, "accepted": 3, "retired": 3,
            "fixed_window_retired": 3,
            "occurrence_to_accept": sealed.latency([0, 0, 0]),
            "accept_to_retire": sealed.latency([1, 1, 1]),
            "fixed_window_events_per_cycle": 1.0,
        })
        aggregate = result["owners"]["a2"]["aggregate"]
        aggregate.update({
            "totals": {
                "generated": 3, "source_overrun": 0, "accepted": 3,
                "retired": 3, "fixed_window_retired": 3,
            },
            "occurrence_to_accept": sealed.latency([0, 0, 0]),
            "accept_to_retire": sealed.latency([1, 1, 1]),
            "fixed_window_events_per_cycle": 1.0,
        })
        members["result/result.json"] = json_bytes(result)
        with self.assertRaisesRegex(sealed.SealedTupleError, "source-latch replay differs"):
            self.validate(members)

    def test_resealed_summary_and_result_claim_mutations_fail(self):
        members = copy.deepcopy(self.fixture.members)
        path = "runs/a2/trace0/summary.csv"
        rows = sealed.csv_records(members[path], sealed.SUMMARY_FIELDS, "fixture")
        rows[0]["accepted"] = "3"
        members[path] = csv_bytes(sealed.SUMMARY_FIELDS, rows)
        with self.assertRaisesRegex(sealed.SealedTupleError, "conservation differs"):
            self.validate(members)
        members = copy.deepcopy(self.fixture.members)
        result = sealed.load_json_bytes(members["result/result.json"], "result")
        result["owners"]["a2"]["runs"]["trace0"]["accept_to_retire"]["mean"] = 99.0
        members["result/result.json"] = json_bytes(result)
        with self.assertRaisesRegex(sealed.SealedTupleError, "result run claim differs"):
            self.validate(members)

    def test_reset_activation_and_mutation_proofs_fail_closed(self):
        members = copy.deepcopy(self.fixture.members)
        path = "runs/a2/reset/summary.csv"
        rows = sealed.csv_records(members[path], sealed.SUMMARY_FIELDS, "fixture")
        rows[0]["pre_reset_clean_drain"] = "0"
        members[path] = csv_bytes(sealed.SUMMARY_FIELDS, rows)
        with self.assertRaisesRegex(sealed.SealedTupleError, "reset clean-drain proof"):
            self.validate(members)
        members = copy.deepcopy(self.fixture.members)
        path = "runs/a2/activation/summary.csv"
        rows = sealed.csv_records(members[path], sealed.SUMMARY_FIELDS, "fixture")
        rows[0]["count2_commits"] = "0"
        members[path] = csv_bytes(sealed.SUMMARY_FIELDS, rows)
        with self.assertRaisesRegex(sealed.SealedTupleError, "activation is vacuous"):
            self.validate(members)
        members = copy.deepcopy(self.fixture.members)
        members["mutations/a2/drop.log"] += b"A23_SE_ACTUAL_RTL_PASS\n"
        result = sealed.load_json_bytes(members["result/result.json"], "result")
        result["mutations"][0]["log_sha256"] = sealed.digest(members["mutations/a2/drop.log"])
        members["result/result.json"] = json_bytes(result)
        with self.assertRaisesRegex(sealed.SealedTupleError, "mutation log semantics"):
            self.validate(members)

    def test_extra_stale_member_and_missing_prepared_bytes_fail(self):
        members = copy.deepcopy(self.fixture.members)
        members["stale/old-result.json"] = b"{}\n"
        with self.assertRaisesRegex(sealed.SealedTupleError, "member roster differs"):
            self.validate(members)
        members = copy.deepcopy(self.fixture.members)
        del members["prepared/trace0.trace"]
        with self.assertRaisesRegex(sealed.SealedTupleError, "member roster differs"):
            self.validate(members)

    def test_duplicate_publication_key_and_cli_symlink_fail(self):
        data = self.fixture.publication_path.read_text(encoding="utf-8")
        data = data.replace('"schema":', '"schema": "attack", "schema":', 1)
        self.fixture.publication_path.write_text(data, encoding="utf-8")
        binding = copy.deepcopy(self.fixture.binding)
        raw = self.fixture.publication_path.read_bytes()
        binding["publication_sha256"] = hashlib.sha256(raw).hexdigest()
        binding["publication_size_bytes"] = len(raw)
        with self.assertRaisesRegex(sealed.SealedTupleError, "duplicate JSON key"):
            sealed.validate_tuple(
                self.fixture.publication_path, self.fixture.bundle_path, binding, "synthetic_v2",
            )
        self.fixture.binding = self.fixture.seal(self.fixture.members)
        link = Path(self.temp.name) / "publication-link.json"
        link.symlink_to(self.fixture.publication_path)
        with self.assertRaisesRegex(sealed.SealedTupleError, "symlink"):
            sealed.validate_tuple(link, self.fixture.bundle_path, self.fixture.binding, "synthetic_v2")

    def test_tuple_wide_source_replacement_is_rejected_after_semantics(self):
        original = sealed.stable_file
        original_bytes = self.fixture.publication_path.read_bytes()

        def replacing(path, label):
            snapshot = original(path, label)
            if label == "synthetic_v2 publication":
                path.write_bytes(original_bytes + b"replacement")
            return snapshot

        sealed.stable_file = replacing
        try:
            with self.assertRaisesRegex(sealed.SealedTupleError, "changed after validation"):
                sealed.validate_tuple(
                    self.fixture.publication_path, self.fixture.bundle_path,
                    self.fixture.binding, "synthetic_v2",
                )
        finally:
            sealed.stable_file = original

    def test_fake_producer_and_rtl_objects_are_rejected(self):
        binding = copy.deepcopy(self.fixture.binding)
        binding["producer"]["commit"] = "1" * 40
        binding["producer"]["tree"] = "2" * 40
        with self.assertRaisesRegex(sealed.SealedTupleError, "provenance is not resolvable|commit/tree"):
            sealed.validate_tuple(
                self.fixture.publication_path, self.fixture.bundle_path, binding, "synthetic_v2",
            )


if __name__ == "__main__":
    unittest.main()
