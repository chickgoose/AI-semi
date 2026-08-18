#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("run_v2", HERE / "run_v2.py")
assert spec and spec.loader
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


def semantic_fixture() -> dict:
    mutations = []
    for index in range(8):
        mutations.append({
            "owner": "a2" if index < 4 else "a3",
            "mutation": ("drop", "duplicate", "reorder", "reset_escape")[index % 4],
            "build_log_sha256": f"build-{index}",
            "simulation_log_sha256": f"simulation-{index}",
        })
    owners = {}
    for owner in ("a2", "a3"):
        owners[owner] = {
            "baseline_build_log_sha256": f"baseline-{owner}",
            "mutation_activation": {"simulation_log_sha256": f"activation-{owner}"},
            "reset": {"simulation_log_sha256": f"reset-{owner}"},
            "semantic": owner,
        }
    return {"owners": owners, "mutations": mutations, "package": "fixed"}


def v2_metadata_fixture() -> dict:
    rows = [{} for _ in range(100)]
    return {
        "schema": v2.V2_RESULT_SCHEMA,
        "status": v2.STATUS,
        "evidence_class": "TEAM_DEFINED_SYNTHETIC_FULL50_ACTUAL_SINGLE_EDGE_RTL_V2",
        "dataset": {
            "id": "full50", "source_class": "TEAM_DEFINED_SYNTHETIC",
            "organizer_official": False, "trace_count": 50,
            "shared_prepared_trace_count": 50,
            "per_campaign_actual_full50_executions": 100,
            "combined_actual_full50_executions": 200,
            "trace_identities": [{} for _ in range(50)],
        },
        "execution_accounting": v2.v2_execution_accounting(),
        "identities": {
            "package_commit": "1" * 40, "package_tree": "2" * 40,
            "package_input_identity_sha256": "3" * 64,
            "source_commit": v2.EXPECTED_SOURCE_COMMIT,
            "source_tree": v2.EXPECTED_SOURCE_TREE,
            "integration_commit": v2.EXPECTED_INTEGRATION_COMMIT,
            "integration_tree": v2.EXPECTED_INTEGRATION_TREE,
            "tool_identity_sha256": "4" * 64,
            "trace_identity_sha256": "5" * 64,
            "pins_sha256": v2.EXPECTED_PINS_SHA256,
        },
        "primary": {"legacy_result_sha256": "8" * 64,
                    "legacy_result_size_bytes": 1},
        "semantic_reproduction": {
            "definition": v2.semantic_definition(),
            "semantic_digest_sha256": "6" * 64,
            "ordinal_semantic_digest_sha256": "7" * 64,
            "primary_legacy_result_sha256": "8" * 64,
            "reproduction_legacy_result_sha256": "9" * 64,
            "reproduction_legacy_result_size_bytes": 1,
            "observed_difference_json_pointers": [],
            "reproduction_full50_runs": copy.deepcopy(rows),
        },
        "sequence_evidence": {
            "primary_full50_runs": rows,
            "event_row_order": "x", "execution_time_global_retire_order": "x",
            "primary_ordinal_observation_actual_RTL_executions": 100,
            "reproduction_ordinal_observation_actual_RTL_executions": 100,
            "within_same_cycle_global_order_reconstructable_from_ordinal_sidecars": True,
            "ordinal_definition": "x",
            "ordinal_semantic_projection_exclusion": [
                "/each_row/ordinal_simulation_log_sha256"
            ],
        },
        "qualification": {
            "hardened_synthetic_single_edge_RTL": "PASS",
            "canonical_campaign": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
            "physical": "HOLD", "power": "HOLD", "CDC_RDC": "HOLD",
        },
    }


def publication_fixture() -> dict:
    value = {key: "x" for key in v2.PUBLICATION_KEYS}
    value.update({
        "schema": v2.PUBLICATION_SCHEMA, "status": v2.STATUS,
        "pins_sha256": v2.EXPECTED_PINS_SHA256,
        "physical_status": "HOLD",
        "canonical_campaign_status": "HOLD_OUTSIDE_THIS_SYNTHETIC_V2_EXPORT",
    })
    return value


def current_base_result() -> dict:
    result = v2.load_json(v2.BASE_PACKAGE / "result.json", "committed base result")
    result["provenance"]["package_commit"] = v2.current_commit()
    return result


class SyntheticV2Test(unittest.TestCase):
    def test_semantic_digest_excludes_exactly_enumerated_log_fields(self) -> None:
        left = semantic_fixture()
        right = copy.deepcopy(left)
        for pointer in v2.EPHEMERAL_LOG_POINTERS:
            parts = v2.decode_pointer(pointer)
            current = right
            for part in parts[:-1]:
                current = current[int(part)] if isinstance(current, list) else current[part]
            final = parts[-1]
            if isinstance(current, list):
                current[int(final)] = "changed"
            else:
                current[final] = "changed"
        self.assertEqual(v2.semantic_digest(left), v2.semantic_digest(right))
        self.assertEqual(v2.difference_pointers(left, right), set(v2.EPHEMERAL_LOG_POINTERS))
        right["package"] = "changed"
        self.assertNotEqual(v2.semantic_digest(left), v2.semantic_digest(right))

    def test_semantic_digest_rejects_missing_exclusion_field(self) -> None:
        fixture = semantic_fixture()
        del fixture["owners"]["a2"]["baseline_build_log_sha256"]
        with self.assertRaisesRegex(v2.V2Error, "missing semantic exclusion"):
            v2.semantic_digest(fixture)

    def test_deterministic_archive_round_trip(self) -> None:
        payload = {"payload/a": b"alpha\n", "payload/b": b"beta\n"}
        roles = {name: "test" for name in payload}
        rows = v2.inventory(payload, roles)
        manifest = {
            "schema": v2.EXPORT_SCHEMA, "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows, "inventory_entry_count": 2,
            "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        }
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.tar.gz"
            second = Path(temporary) / "second.tar.gz"
            v2.write_archive(first, manifest, payload)
            v2.write_archive(second, manifest, payload)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            reopened, contents = v2.read_archive(first)
            self.assertEqual(reopened, manifest)
            self.assertEqual(contents, payload)

    def _raw_archive(self, path: Path, members: list[tarfile.TarInfo], data: bytes = b"x") -> None:
        with tarfile.open(path, "w:gz") as archive:
            for member in members:
                if member.isfile():
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
                else:
                    archive.addfile(member)

    def _safe_members(self, count: int) -> list[tarfile.TarInfo]:
        members = []
        for index in range(count):
            member = tarfile.TarInfo(f"{v2.ARCHIVE_PREFIX}/bounded-{index}")
            member.mode = 0o444
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ""
            members.append(member)
        return members

    def test_archive_rejects_compressed_member_count_member_and_total_limits(self) -> None:
        limits = (v2.MAX_ARCHIVE_COMPRESSED_BYTES, v2.MAX_ARCHIVE_MEMBER_COUNT,
                  v2.MAX_ARCHIVE_MEMBER_BYTES, v2.MAX_ARCHIVE_EXPANDED_BYTES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            try:
                compressed = root / "compressed.tgz"
                compressed.write_bytes(b"not a tar archive")
                v2.MAX_ARCHIVE_COMPRESSED_BYTES = compressed.stat().st_size - 1
                with self.assertRaisesRegex(v2.V2Error, "compressed-size"):
                    v2.read_archive(compressed)
                v2.MAX_ARCHIVE_COMPRESSED_BYTES = 1024 * 1024

                count = root / "count.tgz"
                self._raw_archive(count, self._safe_members(3), b"")
                v2.MAX_ARCHIVE_MEMBER_COUNT = 2
                with self.assertRaisesRegex(v2.V2Error, "member-count"):
                    v2.read_archive(count)
                v2.MAX_ARCHIVE_MEMBER_COUNT = 10

                member = root / "member.tgz"
                self._raw_archive(member, self._safe_members(1), b"12345")
                v2.MAX_ARCHIVE_MEMBER_BYTES = 4
                with self.assertRaisesRegex(v2.V2Error, "per-member"):
                    v2.read_archive(member)
                v2.MAX_ARCHIVE_MEMBER_BYTES = 4

                total = root / "total.tgz"
                self._raw_archive(total, self._safe_members(3), b"123")
                v2.MAX_ARCHIVE_EXPANDED_BYTES = 8
                with self.assertRaisesRegex(v2.V2Error, "expanded-size"):
                    v2.read_archive(total)
            finally:
                (v2.MAX_ARCHIVE_COMPRESSED_BYTES, v2.MAX_ARCHIVE_MEMBER_COUNT,
                 v2.MAX_ARCHIVE_MEMBER_BYTES, v2.MAX_ARCHIVE_EXPANDED_BYTES) = limits

    def test_archive_rejects_path_escape_duplicate_links_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            escape = tarfile.TarInfo("../escape")
            self._raw_archive(root / "escape.tgz", [escape])
            with self.assertRaisesRegex(Exception, "escapes"):
                v2.read_archive(root / "escape.tgz")

            name = f"{v2.ARCHIVE_PREFIX}/same"
            duplicate_members = [tarfile.TarInfo(name), tarfile.TarInfo(name)]
            for member in duplicate_members:
                member.mode = 0o444
                member.uid = member.gid = member.mtime = 0
                member.uname = member.gname = ""
            self._raw_archive(root / "duplicate.tgz", duplicate_members)
            with self.assertRaisesRegex(v2.V2Error, "duplicate"):
                v2.read_archive(root / "duplicate.tgz")

            for label, kind in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE)):
                member = tarfile.TarInfo(f"{v2.ARCHIVE_PREFIX}/{label}")
                member.type = kind
                member.linkname = "target"
                self._raw_archive(root / f"{label}.tgz", [member])
                with self.assertRaisesRegex(v2.V2Error, "regular file"):
                    v2.read_archive(root / f"{label}.tgz")

            unsafe = tarfile.TarInfo(f"{v2.ARCHIVE_PREFIX}/unsafe")
            unsafe.mode = 0o644
            self._raw_archive(root / "unsafe.tgz", [unsafe])
            with self.assertRaisesRegex(v2.V2Error, "metadata"):
                v2.read_archive(root / "unsafe.tgz")

    def test_archive_rejects_missing_extra_hash_and_size(self) -> None:
        payload = {"payload/a": b"alpha"}
        rows = v2.inventory(payload, {"payload/a": "test"})
        base = {
            "schema": v2.EXPORT_SCHEMA, "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows, "inventory_entry_count": len(rows),
            "inventory_size_bytes": sum(row["size_bytes"] for row in rows),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, manifest, actual_payload, pattern in (
                ("missing", base, {}, "not closed"),
                ("extra", base, {**payload, "payload/extra": b"x"}, "not closed"),
                ("hash", {**base, "inventory": [{**rows[0], "sha256": "0" * 64}]},
                 payload, "hash/size"),
                ("size", {**base, "inventory": [{**rows[0], "size_bytes": 99}],
                          "inventory_size_bytes": 99},
                 payload, "hash/size"),
            ):
                archive = root / f"{label}.tgz"
                v2.write_archive(archive, manifest, actual_payload)
                with self.assertRaisesRegex(v2.V2Error, pattern):
                    v2.read_archive(archive)

    def test_archive_rejects_derived_counter_drift(self) -> None:
        payload = {"payload/a": b"alpha"}
        rows = v2.inventory(payload, {"payload/a": "test"})
        manifest = {
            "schema": v2.EXPORT_SCHEMA,
            "status": v2.STATUS,
            "archive_prefix": v2.ARCHIVE_PREFIX,
            "safe_metadata": {
                "regular_files_only": True, "mode": "0444",
                "uid": 0, "gid": 0, "uname": "", "gname": "", "mtime": 0,
                "gzip_mtime": 0,
            },
            "closure": {
                "manifest_is_the_only_non_inventory_member": True,
                "symlinks": "FORBIDDEN", "hardlinks": "FORBIDDEN",
                "path_escapes": "FORBIDDEN", "duplicate_paths": "FORBIDDEN",
                "missing_or_extra_entries": "FORBIDDEN",
            },
            "inventory": rows,
            "inventory_entry_count": 999,
            "inventory_size_bytes": 999,
        }
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "counter-drift.tgz"
            v2.write_archive(archive, manifest, payload)
            with self.assertRaisesRegex(v2.V2Error, "counter differs"):
                v2.read_archive(archive)
        valid_manifest = {**manifest, "inventory_entry_count": 1,
                          "inventory_size_bytes": 5}
        with self.assertRaisesRegex(v2.V2Error, "publication inventory counter"):
            v2.validate_publication_inventory_counter(
                valid_manifest, {"export_inventory_entry_count": 999}
            )

    def test_result_identity_rejects_nonexistent_package_commit(self) -> None:
        result = v2.load_json(v2.BASE_PACKAGE / "result.json", "committed base result")
        result["provenance"]["package_commit"] = "0" * 40
        with self.assertRaisesRegex(v2.V2Error, "Git command failed"):
            v2.verify_result_identity(result, "0" * 40)

    def test_pinned_roster_rejects_omission_extra_and_relabel(self) -> None:
        valid = current_base_result()
        v2.verify_result_identity(valid, v2.current_commit())
        self.assertIn("tests/a23_full_single_edge_replay/run_replay.py",
                      valid["provenance"]["verified_files"])
        self.assertIn("rtl/technology/single_edge/w2_single_edge_pair_tx.sv",
                      valid["provenance"]["actual_rtl_git"]["verified_rtl_paths"])
        mutations = []
        omitted = copy.deepcopy(valid)
        del omitted["provenance"]["verified_files"][
            "tests/a23_full_single_edge_replay/run_replay.py"]
        mutations.append(omitted)
        extra = copy.deepcopy(valid)
        extra["provenance"]["verified_files"]["tests/rogue"] = "0" * 64
        mutations.append(extra)
        relabeled = copy.deepcopy(valid)
        relabeled["provenance"]["verified_files"][
            "tests/a23_full_single_edge_replay/run_replay.py"] = "0" * 64
        mutations.append(relabeled)
        rtl_omitted = copy.deepcopy(valid)
        rtl_omitted["provenance"]["actual_rtl_git"]["verified_rtl_paths"].remove(
            "rtl/technology/single_edge/w2_single_edge_pair_tx.sv")
        mutations.append(rtl_omitted)
        for changed in mutations:
            with self.assertRaisesRegex(v2.V2Error, "roster|hash map"):
                v2.verify_result_identity(changed, v2.current_commit())

    def test_base_result_recursive_schema_rejects_extra_and_missing(self) -> None:
        valid = current_base_result()
        cases = []
        top = copy.deepcopy(valid)
        top["unexpected_top_level_claim"] = {"counter": 999}
        cases.append(top)
        mutation = copy.deepcopy(valid)
        mutation["mutations"][0]["unexpected_mutation_claim"] = True
        cases.append(mutation)
        missing = copy.deepcopy(valid)
        del missing["owners"]["a2"]["reset"]["events_sha256"]
        cases.append(missing)
        source = copy.deepcopy(valid)
        source["mutations"][0]["source_identity"]["extra"] = "x"
        cases.append(source)
        for changed in cases:
            with self.assertRaisesRegex(v2.V2Error, "fields differ"):
                v2.verify_result_identity(changed, v2.current_commit())

    def test_base_semantics_reset_activation_and_generator_are_exact(self) -> None:
        valid = current_base_result()
        cases = []
        for key, value in (
            ("source_overrun_semantics", "P6_POOLING"),
            ("conservation", ["accepted = invented"]),
            ("boundary", "P6"),
            ("reset_qualification", "erase inflight"),
        ):
            changed = copy.deepcopy(valid)
            changed[key] = value
            cases.append(changed)
        generator = copy.deepcopy(valid)
        generator["generator"]["full50_manifest_sha256"] = "0" * 64
        cases.append(generator)
        reset_test = copy.deepcopy(valid)
        reset_test["owners"]["a2"]["reset"]["reset_test"] = 0
        cases.append(reset_test)
        clean_drain = copy.deepcopy(valid)
        clean_drain["owners"]["a3"]["reset"]["pre_reset_clean_drain"] = 0
        cases.append(clean_drain)
        activation = copy.deepcopy(valid)
        activation["owners"]["a2"]["mutation_activation"]["count2_commits"] = 0
        cases.append(activation)
        for changed in cases:
            with self.assertRaises(v2.V2Error):
                v2.verify_result_identity(changed, v2.current_commit())

    def test_pinned_tool_and_regenerated_trace_identity_reject_substitution(self) -> None:
        valid = current_base_result()
        tool = copy.deepcopy(valid)
        tool["provenance"]["verified_tools"]["python"] = copy.deepcopy(
            tool["provenance"]["verified_tools"]["make"]
        )
        with self.assertRaisesRegex(v2.V2Error, "tool identities"):
            v2.verify_tools(tool)
        names = v2.verify_result_identity(valid, v2.current_commit())
        substituted = copy.deepcopy(valid)
        first = names[0]
        for owner in ("a2", "a3"):
            substituted["owners"][owner]["full50"]["runs"][first][
                "trace_sha256"] = "0" * 64
        with self.assertRaisesRegex(v2.V2Error, "canonical trace"):
            v2.trace_identity(substituted, names)

    def test_metadata_rejects_definition_keys_pins_and_counters(self) -> None:
        valid = v2_metadata_fixture()
        v2.validate_v2_metadata(valid)
        for label, mutation in (
            ("definition", lambda value: value["semantic_reproduction"][
                "definition"].update({"serialization": "drift"})),
            ("keys", lambda value: value.update({"extra": 1})),
            ("counter", lambda value: value["dataset"].update({
                "combined_actual_full50_executions": 999
            })),
        ):
            changed = copy.deepcopy(valid)
            mutation(changed)
            with self.assertRaises(v2.V2Error, msg=label):
                v2.validate_v2_metadata(changed)
        publication = publication_fixture()
        v2.validate_publication_metadata(publication)
        publication["pins_sha256"] = "0" * 64
        with self.assertRaisesRegex(v2.V2Error, "pins"):
            v2.validate_publication_metadata(publication)
        publication = publication_fixture()
        publication["extra"] = "drift"
        with self.assertRaisesRegex(v2.V2Error, "fields"):
            v2.validate_publication_metadata(publication)
        for path in (("primary", "retention"),
                     ("semantic_reproduction", "retention")):
            changed = copy.deepcopy(valid)
            changed[path[0]][path[1]] = {
                "retained_payload_file_count": -999, "rogue": "accepted"
            }
            with self.assertRaisesRegex(v2.V2Error, "fields"):
                v2.validate_v2_metadata(changed)
        missing = copy.deepcopy(valid)
        del missing["primary"]["legacy_result_size_bytes"]
        with self.assertRaisesRegex(v2.V2Error, "fields"):
            v2.validate_v2_metadata(missing)
        coherent_counter = copy.deepcopy(valid)
        coherent_counter["primary"]["retained_payload_file_count"] = 999
        with self.assertRaisesRegex(v2.V2Error, "fields"):
            v2.validate_v2_metadata(coherent_counter)
        for bad_size in (-1, True):
            changed = copy.deepcopy(valid)
            changed["primary"]["legacy_result_size_bytes"] = bad_size
            with self.assertRaisesRegex(v2.V2Error, "size"):
                v2.validate_v2_metadata(changed)

    def test_ordinal_csv_schema_and_pass_log_fail_closed(self) -> None:
        event_header = ",".join(v2.EVENT_CSV_FIELDS)
        ordinal_header = ",".join(v2.ORDINAL_CSV_FIELDS)
        event_row = "a2,case,0,3,1,2,4,65,retired"
        ordinal_row = "a2,case,0,3,1,2,0,4,0,retired"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            event = root / "events.csv"
            ordinal = root / "ordinals.csv"
            simulation = root / "simulation.log"
            event.write_text(event_header + "\n" + event_row + "\n", encoding="utf-8")
            ordinal.write_text(ordinal_header + "\n" + ordinal_row + "\n",
                               encoding="utf-8")
            sentinel = (
                "A23_SYNTHETIC_V2_ORDINAL_PASS owner=a2 trace=case "
                "generated=1 accepted=1 retired=1\n"
            )
            simulation.write_text(sentinel, encoding="utf-8")
            record = v2.sequence_record(event, ordinal, simulation, "a2", "case")
            self.assertEqual(record["accepted_ordinal_count"], 1)
            ordinal.write_text(ordinal_header + ",extra\n" + ordinal_row + ",x\n",
                               encoding="utf-8")
            with self.assertRaisesRegex(v2.V2Error, "schema"):
                v2.sequence_record(event, ordinal, simulation, "a2", "case")
            ordinal.write_text(ordinal_header + "\n" + ordinal_row + "\n",
                               encoding="utf-8")
            simulation.write_text("arbitrary log\n", encoding="utf-8")
            with self.assertRaisesRegex(v2.V2Error, "PASS log"):
                v2.sequence_record(event, ordinal, simulation, "a2", "case")
            simulation.write_text("FORGED_PREFIX " + sentinel.strip() +
                                  " FORGED_SUFFIX\n", encoding="utf-8")
            with self.assertRaisesRegex(v2.V2Error, "PASS log"):
                v2.sequence_record(event, ordinal, simulation, "a2", "case")

    def test_reproduction_cannot_exclude_empty_ordinal_evidence(self) -> None:
        result = v2.load_json(v2.BASE_PACKAGE / "result.json", "committed base result")
        names = sorted(result["owners"]["a2"]["full50"]["runs"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copyfile(v2.BASE_PACKAGE / "result.json", root / "result.json")
            (root / "campaign.log").write_text("arbitrary\n", encoding="utf-8")
            (root / "ordinal_campaign.log").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(v2.V2Error, "missing"):
                v2.retained_payload(root, result, names, "reproduction")

    def test_ordinal_contract_has_explicit_global_fields(self) -> None:
        tb = (HERE / "a23_synthetic_v2_ordinal_tb.sv").read_text(encoding="utf-8")
        self.assertIn("accept_ordinal", tb)
        self.assertIn("retire_ordinal", tb)
        self.assertIn("accept_ordinal_next", tb)
        self.assertIn("retire_ordinal_next", tb)

    def test_committed_publication_reopens_when_present(self) -> None:
        result = HERE / "synthetic_v2_result.json"
        archive = HERE / "synthetic_v2_export.tar.gz"
        publication = HERE / "synthetic_v2_publication.json"
        if not all(path.is_file() for path in (result, archive, publication)):
            self.skipTest("generated v2 publication is not committed yet")
        report = v2.validate_reopened(archive, result, publication)
        self.assertEqual(report["status"], v2.STATUS)


if __name__ == "__main__":
    unittest.main()
