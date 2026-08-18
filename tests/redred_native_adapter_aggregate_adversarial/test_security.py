#!/usr/bin/env python3
"""Independent adversarial tests for the unbound native adapter/aggregate seam."""

from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "benchmarks" / "redred_single_edge_campaign"
FIXTURES = Path(__file__).with_name("fixtures")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sealed = load_module("a7_security_sealed_v2", PACKAGE / "sealed_v2.py")
campaign = load_module("a7_security_campaign_v3", PACKAGE / "campaign_v3.py")


class ScaffoldError(ValueError):
    """The executable scaffold rejected unsafe or semantically false input."""


class AdapterAggregateScaffold:
    """Minimal oracle used until reviewed producer-native adapters are bound.

    It intentionally does not translate producer formats or claim evidence.  Its
    only output authority is to demonstrate the fail-closed seam contract.
    """

    PAYLOAD_KEYS = {
        "schema", "slot", "evidence_class", "source_class",
        "canonical_redred_traffic", "official_contest_traffic",
        "release_status", "selection_status", "events", "counts",
    }
    EVENT_KEYS = {"event_id", "accept_ordinal", "retire_ordinal"}
    COUNT_KEYS = {"generated", "source_overrun", "accepted", "retired"}

    @staticmethod
    def _exact(value: Any, expected: set[str], label: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != expected:
            raise ScaffoldError(f"{label} shape differs")
        return value

    def adapt(self, slot: str, raw: bytes) -> dict[str, Any]:
        try:
            payload = sealed.load_json_bytes(raw, f"{slot} native payload")
        except sealed.SealedTupleError as error:
            raise ScaffoldError(str(error)) from error
        payload = self._exact(payload, self.PAYLOAD_KEYS, "native payload")
        if slot not in sealed.SLOT_IDENTITIES or payload["slot"] != slot:
            raise ScaffoldError("native payload crossed its bound slot")
        expected = sealed.SLOT_IDENTITIES[slot]
        for key in ("evidence_class", "source_class", "canonical_redred_traffic"):
            if not sealed.strict_equal(payload[key], expected[key]):
                raise ScaffoldError(f"native payload {key} differs")
        if type(payload["official_contest_traffic"]) is not bool \
                or payload["official_contest_traffic"] is not False:
            raise ScaffoldError("native payload was relabeled official")
        if payload["release_status"] != "HOLD" or payload["selection_status"] != "HOLD":
            raise ScaffoldError("native payload promoted selection or release")
        events = payload["events"]
        if not isinstance(events, list):
            raise ScaffoldError("native events must be a list")
        for index, event in enumerate(events):
            event = self._exact(event, self.EVENT_KEYS, f"event {index}")
            if any(type(event[key]) is not int for key in self.EVENT_KEYS):
                raise ScaffoldError("native event integer type differs")
            if (event["event_id"], event["accept_ordinal"], event["retire_ordinal"]) != (
                index, index, index,
            ):
                raise ScaffoldError("native event identity/order differs")
        counts = self._exact(payload["counts"], self.COUNT_KEYS, "native counts")
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ScaffoldError("native count type differs")
        if counts["generated"] != counts["source_overrun"] + counts["accepted"] \
                or counts["accepted"] != counts["retired"] \
                or counts["retired"] != len(events):
            raise ScaffoldError("native conservation differs")
        return copy.deepcopy(payload)

    def aggregate(self, adapted: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(adapted, dict) or set(adapted) != set(sealed.SLOT_IDENTITIES):
            raise ScaffoldError("aggregate requires the two distinct bound slots")
        if adapted["synthetic_v2"] is adapted["public_v2"]:
            raise ScaffoldError("aggregate inputs are aliases")
        for slot, payload in adapted.items():
            if payload.get("slot") != slot:
                raise ScaffoldError("aggregate input crossed its bound slot")
        return {
            "status": "HOLD",
            "selection_status": "HOLD",
            "release_status": "HOLD",
            "synthetic_public_pooling": "FORBIDDEN",
            "slots": {
                slot: {"event_count": len(payload["events"]), "source_class": payload["source_class"]}
                for slot, payload in adapted.items()
            },
        }


def fixture_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def tar_bytes(entries: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for info, data in entries:
            archive.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return output.getvalue()


def regular_member(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def csv_bytes(fields: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def valid_run() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, int]]]:
    events = []
    trace = []
    for index in range(3):
        events.append({
            "owner": "a2", "run": "security", "event_id": index,
            "logical_source": index, "occurrence_cycle": index,
            "accept_cycle": index, "retire_cycle": index + 1,
            "deadline_cycle": 10, "accept_ordinal": index,
            "retire_ordinal": index, "event_state": "retired",
        })
        trace.append({
            "event_id": index, "logical_source": index,
            "occurrence_cycle": index, "deadline_cycle": 10,
        })
    summary = {
        "owner": "a2", "run": "security", "generated": 3,
        "source_overrun": 0, "accepted": 3, "retired": 3,
        "fixed_window_retired": 3, "measurement_start_cycle": 0,
        "measurement_end_cycle": 10, "observation_cycles": 10,
        "count2_commits": 0, "reset_test": 0,
        "pre_reset_clean_drain": 0, "protocol_error": 0,
    }
    return events, summary, trace


def fake_binding(kind: str = "synthetic_v2") -> dict[str, Any]:
    zero_sha = "0" * 64
    commit = "1" * 40
    tree = "2" * 40
    slot = sealed.SLOT_IDENTITIES[kind]
    return {
        "publication_sha256": zero_sha, "publication_size_bytes": 1,
        "publication_schema": slot["publication_schema"],
        "evidence_class": slot["evidence_class"], "status": slot["status"],
        "source_class": slot["source_class"],
        "canonical_redred_traffic": slot["canonical_redred_traffic"],
        "official_contest_traffic": False, "p6_evidence_used": False,
        "release_status": "HOLD", "selection_status": "HOLD",
        "producer": {
            "commit": commit, "tree": tree, "verifier_sha256": zero_sha,
            "schema_sha256": zero_sha, "runner_sha256": zero_sha,
            "testbench_sha256": zero_sha, "tool_pins_sha256": zero_sha,
            "inventory": [{"role": "x", "path": "x", "blob_sha256": "3" * 40}],
        },
        "rtl": {
            "source_commit": commit, "source_tree": tree,
            "integration_commit": commit, "integration_tree": tree,
            "source_inventory": [{"role": "x", "path": "x", "blob_sha256": "3" * 40}],
            "integration_inventory": [{"role": "x", "path": "x", "blob_sha256": "3" * 40}],
        },
        "bundle_sha256": zero_sha, "bundle_size_bytes": 1,
        "manifest_schema": "manifest", "manifest_member": "manifest.json",
        "manifest_sha256": zero_sha, "entry_count": 1,
        "result_schema": "result", "result_member": "result.json",
        "result_sha256": zero_sha, "result_semantic_sha256": zero_sha,
        "result_size_bytes": 1, "owners": ["a2", "a3"],
        "traffic_runs": ["run"], "reset_run": "reset",
        "activation_run": "activation", "mutations": ["drop"],
        "diagnostics": {"drop": "DROP_FAIL"},
    }


class ThreatModelCoverageTests(unittest.TestCase):
    def test_machine_readable_threat_model_covers_requested_attack_classes(self) -> None:
        model = fixture_json("threat_model.json")
        self.assertEqual(model["authority_limit"], "TEST_SCAFFOLD_ONLY_NOT_EVIDENCE_SELECTION_OR_RELEASE_AUTHORITY")
        categories = {case["category"] for case in model["cases"]}
        self.assertEqual(categories, {
            "duplicate_json_keys", "type_confusion", "traversal", "symlink",
            "hardlink", "race", "tar_bomb", "tar_alias", "git_substitution",
            "git_unreachability", "semantic_order_mutation",
            "semantic_conservation_mutation", "public_official_relabel",
            "cross_slot_swap", "false_selection_promotion",
            "false_release_promotion",
        })
        self.assertTrue(all(case["expected"] == "REJECT" for case in model["cases"]))


class JsonAndScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scaffold = AdapterAggregateScaffold()
        self.payloads = fixture_json("native_payloads.json")

    def test_duplicate_json_keys_fail_closed(self) -> None:
        raw = (FIXTURES / "duplicate_keys.json").read_bytes()
        with self.assertRaisesRegex(sealed.SealedTupleError, "duplicate JSON key"):
            sealed.load_json_bytes(raw, "duplicate fixture")
        with self.assertRaisesRegex(ScaffoldError, "duplicate JSON key"):
            self.scaffold.adapt("synthetic_v2", raw)

    def test_json_type_confusion_fails_closed(self) -> None:
        for field, value in (
            ("official_contest_traffic", 0),
            ("canonical_redred_traffic", 1),
            ("release_status", ["HOLD"]),
        ):
            with self.subTest(field=field):
                mutated = copy.deepcopy(self.payloads["synthetic_v2"])
                mutated[field] = value
                with self.assertRaises(ScaffoldError):
                    self.scaffold.adapt("synthetic_v2", json_bytes(mutated))
        with self.assertRaises(sealed.SealedTupleError):
            sealed.uint(True, "count")
        self.assertFalse(sealed.strict_equal(False, 0))

        manifest = json.loads(campaign.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        producer = manifest["sealed_producers"]["synthetic_v2"]
        confused = copy.deepcopy(producer)
        confused["canonical_redred_traffic"] = 1
        with self.assertRaises(campaign.CampaignV3Error):
            campaign.validate_producer("synthetic_v2", confused)

    def test_public_official_relabel_and_false_selection_fail_closed(self) -> None:
        for field, value in (
            ("official_contest_traffic", True),
            ("canonical_redred_traffic", True),
            ("selection_status", "A2"),
            ("release_status", "GO"),
        ):
            with self.subTest(field=field):
                mutated = copy.deepcopy(self.payloads["public_v2"])
                mutated[field] = value
                with self.assertRaises(ScaffoldError):
                    self.scaffold.adapt("public_v2", json_bytes(mutated))

        manifest = json.loads(campaign.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        producer = manifest["sealed_producers"]["public_v2"]
        for field, value in (
            ("official_contest_traffic", True),
            ("selection_status", "A2"),
            ("release_status", "GO"),
        ):
            with self.subTest(production_field=field):
                mutated = copy.deepcopy(producer)
                mutated[field] = value
                with self.assertRaises(campaign.CampaignV3Error):
                    campaign.validate_producer("public_v2", mutated)

    def test_cross_slot_swap_fails_closed(self) -> None:
        with self.assertRaisesRegex(ScaffoldError, "crossed its bound slot"):
            self.scaffold.adapt("public_v2", json_bytes(self.payloads["synthetic_v2"]))
        binding = fake_binding("synthetic_v2")
        with self.assertRaisesRegex(sealed.SealedTupleError, "classification/schema differs"):
            sealed.validate_tuple(Path("missing-publication"), Path("missing-bundle"), binding, "public_v2")

    def test_semantic_order_conservation_and_aggregate_hold(self) -> None:
        adapted = {
            slot: self.scaffold.adapt(slot, json_bytes(payload))
            for slot, payload in self.payloads.items()
        }
        report = self.scaffold.aggregate(adapted)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["selection_status"], "HOLD")
        self.assertEqual(report["release_status"], "HOLD")
        self.assertEqual(report["synthetic_public_pooling"], "FORBIDDEN")

        order = copy.deepcopy(self.payloads["synthetic_v2"])
        order["events"][0]["accept_ordinal"] = 1
        order["events"][0]["retire_ordinal"] = 1
        order["events"][1]["accept_ordinal"] = 0
        order["events"][1]["retire_ordinal"] = 0
        with self.assertRaisesRegex(ScaffoldError, "identity/order"):
            self.scaffold.adapt("synthetic_v2", json_bytes(order))

        conservation = copy.deepcopy(self.payloads["synthetic_v2"])
        conservation["counts"]["generated"] += 1
        with self.assertRaisesRegex(ScaffoldError, "conservation"):
            self.scaffold.adapt("synthetic_v2", json_bytes(conservation))

        swapped = {"synthetic_v2": adapted["public_v2"], "public_v2": adapted["synthetic_v2"]}
        with self.assertRaisesRegex(ScaffoldError, "crossed its bound slot"):
            self.scaffold.aggregate(swapped)


class FilesystemAndArchiveTests(unittest.TestCase):
    def test_traversal_paths_fail_closed(self) -> None:
        for path in ("../escape", "/absolute", "a/../b", "a\\b", "./a"):
            with self.subTest(path=path):
                with self.assertRaises(sealed.SealedTupleError):
                    sealed.safe_relative(path, "hostile path")

    def test_symlink_hardlink_and_post_read_replacement_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"trusted")
            symlink = root / "symlink"
            symlink.symlink_to(target)
            hardlink = root / "hardlink"
            hardlink.hardlink_to(target)
            with self.assertRaisesRegex(sealed.SealedTupleError, "symlink"):
                sealed.stable_file(symlink, "symlink input")
            with self.assertRaisesRegex(sealed.SealedTupleError, "unalias"):
                sealed.stable_file(hardlink, "hardlink input")

            target.unlink()
            target.write_bytes(b"trusted")
            resolved, data, identity = sealed.stable_file(target, "race input")
            self.assertEqual(data, b"trusted")
            replacement = root / "replacement"
            replacement.write_bytes(b"substituted")
            os.replace(replacement, target)
            with self.assertRaisesRegex(sealed.SealedTupleError, "changed after validation"):
                sealed.recheck_file(resolved, identity, "race input")

    def test_tar_traversal_duplicate_links_and_expansion_bomb_fail_closed(self) -> None:
        hostile_archives = []
        hostile_archives.append(tar_bytes([regular_member("../escape", b"x")]))
        hostile_archives.append(tar_bytes([
            regular_member("same", b"one"), regular_member("same", b"two"),
        ]))
        symlink = tarfile.TarInfo("alias")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "target"
        hostile_archives.append(tar_bytes([(symlink, b"")]))
        hardlink = tarfile.TarInfo("hard-alias")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "target"
        hostile_archives.append(tar_bytes([(hardlink, b"")]))
        for archive in hostile_archives:
            with self.subTest(sha=sealed.digest(archive)):
                with self.assertRaises(sealed.SealedTupleError):
                    sealed.archive_members(archive)

        bomb = tar_bytes([regular_member("large", b"xx")])
        with mock.patch.object(sealed, "MAX_MEMBER_BYTES", 1):
            with self.assertRaisesRegex(sealed.SealedTupleError, "size is unsafe"):
                sealed.archive_members(bomb)
        with mock.patch.object(sealed, "MAX_TOTAL_MEMBER_BYTES", 1):
            with self.assertRaisesRegex(sealed.SealedTupleError, "expansion limits"):
                sealed.archive_members(bomb)
        with mock.patch.object(sealed, "MAX_BUNDLE_BYTES", len(bomb) - 1):
            with self.assertRaisesRegex(sealed.SealedTupleError, "compressed size limit"):
                sealed.archive_members(bomb)


class ProvenanceAndSemanticTests(unittest.TestCase):
    def test_git_unreachability_and_object_substitution_fail_closed(self) -> None:
        binding = fake_binding()
        missing = subprocess.CalledProcessError(128, ["git", "cat-file"])
        with mock.patch.object(sealed.subprocess, "check_output", side_effect=missing):
            with self.assertRaisesRegex(sealed.SealedTupleError, "not resolvable"):
                sealed.validate_tuple(Path("publication"), Path("bundle"), binding, "synthetic_v2")

        with mock.patch.object(sealed.subprocess, "check_output", return_value="blob\n"):
            with self.assertRaisesRegex(sealed.SealedTupleError, "commit/tree relationship differs"):
                sealed.validate_tuple(Path("publication"), Path("bundle"), binding, "synthetic_v2")

    def test_order_identity_and_conservation_mutants_fail_existing_oracle(self) -> None:
        events, summary, trace = valid_run()
        event_data = csv_bytes(sealed.EVENT_FIELDS, events)
        summary_data = csv_bytes(sealed.SUMMARY_FIELDS, [summary])
        metrics, _, _ = sealed.validate_run("a2", "security", event_data, summary_data, trace)
        self.assertEqual(metrics["generated"], 3)
        self.assertEqual(metrics["accepted"], metrics["retired"])

        ordinal_swap = copy.deepcopy(events)
        for index, ordinal in ((0, 1), (1, 0)):
            ordinal_swap[index]["accept_ordinal"] = ordinal
            ordinal_swap[index]["retire_ordinal"] = ordinal
        with self.assertRaisesRegex(sealed.SealedTupleError, "temporal order differs"):
            sealed.validate_run(
                "a2", "security", csv_bytes(sealed.EVENT_FIELDS, ordinal_swap),
                summary_data, trace,
            )

        identity_swap = copy.deepcopy(events)
        identity_swap[0]["logical_source"], identity_swap[1]["logical_source"] = 1, 0
        with self.assertRaisesRegex(sealed.SealedTupleError, "differs from source trace"):
            sealed.validate_run(
                "a2", "security", csv_bytes(sealed.EVENT_FIELDS, identity_swap),
                summary_data, trace,
            )

        false_counts = copy.deepcopy(summary)
        false_counts["generated"] = 4
        with self.assertRaisesRegex(sealed.SealedTupleError, "conservation differs"):
            sealed.validate_run(
                "a2", "security", event_data,
                csv_bytes(sealed.SUMMARY_FIELDS, [false_counts]), trace,
            )


class AggregateIntegrationTests(unittest.TestCase):
    def test_current_unbound_interface_is_executable_hold_scaffold(self) -> None:
        manifest = campaign.validate_manifest(campaign.DEFAULT_MANIFEST, ROOT)
        self.assertEqual(manifest["producers"]["synthetic_v2"]["state"], "UNBOUND")
        self.assertEqual(manifest["producers"]["public_v2"]["state"], "UNBOUND")
        report = campaign.evaluate(campaign.DEFAULT_MANIFEST, ROOT)
        self.assertEqual(report["status"], "HOLD")
        self.assertEqual(report["gates"]["system_release"], "HOLD")
        self.assertEqual(report["gates"]["official_contest_evidence"], "HOLD_ABSENT")
        self.assertEqual(report["aggregation"]["synthetic_public_pooling"], "FORBIDDEN")

    def test_even_two_adapter_passes_cannot_promote_release(self) -> None:
        producers = {
            name: {"state": "BOUND", "evidence_class": identity["evidence_class"],
                   "required_binding_fields": []}
            for name, identity in sealed.SLOT_IDENTITIES.items()
        }
        context = {
            "legacy_path": Path("legacy"), "producers": producers,
            "policies": {"system_release_requires_independent_gates": True},
        }
        legacy_report = {
            "status": "HOLD", "gates": {
                "committed_hardened_receipt": "PASS",
                "canonical_single_edge_campaign": "HOLD",
            },
        }
        tuple_pass = {"status": "PASS", "binding_state": "BOUND", "validated": True}
        with mock.patch.object(campaign, "validate_manifest", return_value=context), \
                mock.patch.object(campaign.legacy, "evaluate", return_value=legacy_report), \
                mock.patch.object(campaign, "tuple_state", return_value=tuple_pass):
            report = campaign.evaluate(Path("manifest"), ROOT)
        self.assertEqual(report["gates"]["synthetic_v2_sealed_tuple"], "PASS")
        self.assertEqual(report["gates"]["public_v2_sealed_tuple"], "PASS")
        self.assertEqual(report["gates"]["system_release"], "HOLD")
        self.assertFalse(report["claim_boundary"]["release_claimed"])
        self.assertFalse(report["claim_boundary"]["official_contest_claimed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
