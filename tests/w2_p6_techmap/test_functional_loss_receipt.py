#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import tarfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RECEIPT_PATH = ROOT / "rtl/technology/p6/p6_functional_loss_receipt.json"
ARCHIVE_ENV = "W2_P6_FUNCTIONAL_EVAL"


def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FunctionalLossReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.receipt = json.loads(
            RECEIPT_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
        archive_receipt = cls.receipt["archive"]
        cls.archive = pathlib.Path(os.environ.get(
            ARCHIVE_ENV, archive_receipt["default_path"]
        ))
        archive_data = cls.archive.read_bytes()
        if sha256(archive_data) != archive_receipt["sha256"]:
            raise AssertionError(f"functional archive identity mismatch: {cls.archive}")
        with tarfile.open(cls.archive, "r:gz") as bundle:
            tar_members = bundle.getmembers()
            for item in tar_members:
                path = pathlib.PurePosixPath(item.name)
                if path.is_absolute() or ".." in path.parts:
                    raise AssertionError(f"unsafe functional archive member: {item.name}")
                if not (item.isfile() or item.isdir()):
                    raise AssertionError(f"unsupported functional archive member: {item.name}")
            cls.tar_member_count = len(tar_members)
            cls.directory_member_count = sum(item.isdir() for item in tar_members)
            regular_names = [item.name for item in tar_members if item.isfile()]
            if len(regular_names) != len(set(regular_names)):
                raise AssertionError("functional archive has duplicate regular members")
            cls.members = {
                name: bundle.extractfile(name).read() for name in regular_names
            }

    def test_schema_and_use_are_fail_closed(self) -> None:
        receipt = self.receipt
        self.assertEqual(set(receipt), {
            "schema", "receipt_class", "allowed_use", "eligibility",
            "archive", "provenance", "ledger", "workload_stems", "candidates",
            "excluded_evidence",
        })
        self.assertEqual(receipt["schema"], "w2-p6-functional-loss-receipt-v1")
        self.assertEqual(receipt["receipt_class"], "workspace-diff-non-official")
        self.assertEqual(receipt["allowed_use"], "functional-loss-only")
        self.assertEqual(set(receipt["eligibility"]), {
            "official_evidence", "ppa", "area", "power", "frequency", "timing_or_sta",
            "cell_or_library_evidence", "p6_technology_equivalence",
            "candidate_ranking",
        })
        self.assertFalse(any(receipt["eligibility"].values()))
        self.assertEqual(set(receipt["archive"]), {
            "default_path", "sha256", "size_bytes", "regular_files",
            "directory_members", "tar_members", "results_files",
            "root_regular_files", "verification",
        })
        self.assertEqual(set(receipt["provenance"]), {
            "member", "sha256", "server_attempt", "snapshot_head",
            "snapshot_archive_sha256", "binding_reset_quiet_arming_patch",
            "exact_source_closure_archived", "canonical_rtl_date_kst", "tool",
            "hostname", "start_utc", "finish_utc",
        })
        self.assertEqual(set(receipt["ledger"]), {
            "member", "sha256", "entries", "verified", "mismatched",
            "missing", "covers_all_results_files",
        })
        self.assertEqual(set(receipt["workload_stems"]), {
            "full50_member", "full50_sha256", "full50_count",
            "capacity22_member", "capacity22_sha256", "capacity22_count",
        })
        self.assertEqual(set(receipt["candidates"]), {"fovea", "cluster2"})
        candidate_keys = {
            "candidate", "run_log", "run_log_sha256", "full50_aggregate",
            "full50_aggregate_sha256", "workload_runs", "workload_passes",
            "workload_failures", "reset_pass", "pairwise_status", "full50",
        }
        result_keys = {"generated", "accepted", "delivered", "overrun", "errors"}
        for candidate in receipt["candidates"].values():
            self.assertEqual(set(candidate), candidate_keys)
            self.assertEqual(set(candidate["full50"]), result_keys)
        self.assertEqual(set(receipt["excluded_evidence"]),
                         {"outer_eval_driver_final_log"})
        self.assertEqual(set(receipt["excluded_evidence"]["outer_eval_driver_final_log"]),
                         {"bound", "reason"})
        technology_manifest = (ROOT / "rtl/technology/p6/p6_tech_manifest.json").read_text()
        self.assertNotIn("functional_loss", technology_manifest)
        self.assertNotIn("yZr1", technology_manifest)
        exclusion = receipt["excluded_evidence"]["outer_eval_driver_final_log"]
        self.assertFalse(exclusion["bound"])
        self.assertIn("stale", exclusion["reason"])

    def test_archive_provenance_and_stale_log_exclusion(self) -> None:
        archive = self.receipt["archive"]
        self.assertEqual(self.archive.stat().st_size, archive["size_bytes"])
        self.assertEqual(self.tar_member_count, archive["tar_members"])
        self.assertEqual(self.directory_member_count, archive["directory_members"])
        self.assertEqual(len(self.members), archive["regular_files"])
        self.assertEqual(sum(name.startswith("results/") for name in self.members),
                         archive["results_files"])
        root_files = sorted(name for name in self.members if "/" not in name)
        self.assertEqual(root_files, sorted(archive["root_regular_files"]))
        self.assertFalse(any(name.endswith("eval-driver-final.log")
                             for name in self.members))
        archive_text = b"\n".join(self.members.values())
        self.assertNotIn(b"0FfaT8kp", archive_text)

        expected = self.receipt["provenance"]
        data = self.members[expected["member"]]
        self.assertEqual(sha256(data), expected["sha256"])
        provenance_lines = data.decode().splitlines()
        self.assertEqual(provenance_lines, [
            f'snapshot_head={expected["snapshot_head"]}',
            f'binding_reset_quiet_arming_patch={expected["binding_reset_quiet_arming_patch"]}',
            f'snapshot_archive_sha256={expected["snapshot_archive_sha256"]}',
            f'canonical_rtl_date_kst={expected["canonical_rtl_date_kst"]}',
            f'attempt={expected["server_attempt"]}',
            f'hostname={expected["hostname"]}',
            f'start_utc={expected["start_utc"]}',
            f'TOOL:\t{expected["tool"]}',
            f'finish_utc={expected["finish_utc"]}',
        ])
        values = dict(line.split("=", 1) for line in provenance_lines
                      if "=" in line)
        self.assertEqual(values["attempt"], expected["server_attempt"])
        self.assertEqual(values["snapshot_head"], expected["snapshot_head"])
        self.assertEqual(values["snapshot_archive_sha256"],
                         expected["snapshot_archive_sha256"])
        self.assertEqual(values["binding_reset_quiet_arming_patch"],
                         expected["binding_reset_quiet_arming_patch"])
        self.assertEqual(values["hostname"], expected["hostname"])
        self.assertEqual(values["start_utc"], expected["start_utc"])
        self.assertEqual(values["finish_utc"], expected["finish_utc"])
        tool_line = next(line for line in provenance_lines
                         if line.startswith("TOOL:\t"))
        self.assertEqual(tool_line, f'TOOL:\t{expected["tool"]}')
        self.assertFalse(expected["exact_source_closure_archived"])

    def test_inner_ledger_verifies_every_result_file(self) -> None:
        expected = self.receipt["ledger"]
        data = self.members[expected["member"]]
        self.assertEqual(sha256(data), expected["sha256"])
        lines = data.decode().splitlines()
        self.assertEqual(len(lines), expected["entries"])
        prefix = self.receipt["provenance"]["server_attempt"] + "/"
        verified: set[str] = set()
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (/.+)", line)
            self.assertIsNotNone(match, line)
            expected_hash, server_path = match.groups()
            self.assertTrue(server_path.startswith(prefix), server_path)
            member = server_path[len(prefix):]
            self.assertTrue(member.startswith("results/"), member)
            self.assertNotIn(member, verified)
            self.assertIn(member, self.members)
            self.assertEqual(sha256(self.members[member]), expected_hash, member)
            verified.add(member)
        results_members = {name for name in self.members if name.startswith("results/")}
        self.assertEqual(verified, results_members)
        self.assertEqual(len(verified), expected["verified"])
        self.assertEqual(expected["mismatched"], 0)
        self.assertEqual(expected["missing"], 0)
        self.assertTrue(expected["covers_all_results_files"])

    def test_workload_stems_are_exact_and_capacity_is_a_subset(self) -> None:
        expected = self.receipt["workload_stems"]
        full_data = self.members[expected["full50_member"]]
        capacity_data = self.members[expected["capacity22_member"]]
        self.assertEqual(sha256(full_data), expected["full50_sha256"])
        self.assertEqual(sha256(capacity_data), expected["capacity22_sha256"])
        full = full_data.decode().splitlines()
        capacity = capacity_data.decode().splitlines()
        self.assertEqual(len(full), expected["full50_count"])
        self.assertEqual(len(capacity), expected["capacity22_count"])
        self.assertEqual(len(full), len(set(full)))
        self.assertEqual(len(capacity), len(set(capacity)))
        self.assertLessEqual(set(capacity), set(full))

    def test_candidate_logs_reproduce_functional_loss_only(self) -> None:
        self.assertEqual(set(self.receipt["candidates"]), {"fovea", "cluster2"})
        full_stems = self.members[
            self.receipt["workload_stems"]["full50_member"]
        ].decode().splitlines()
        for key, expected in self.receipt["candidates"].items():
            data = self.members[expected["run_log"]]
            self.assertEqual(sha256(data), expected["run_log_sha256"])
            aggregate_data = self.members[expected["full50_aggregate"]]
            self.assertEqual(sha256(aggregate_data),
                             expected["full50_aggregate_sha256"])
            aggregate = json.loads(aggregate_data, object_pairs_hook=reject_duplicates)
            self.assertTrue(all(row["candidate"] == expected["candidate"]
                                for row in aggregate["loads"]))
            self.assertTrue(all(row["candidate"] == expected["candidate"]
                                for row in aggregate["tests"]))
            log = data.decode(errors="replace")
            passed_stems = re.findall(
                rf"^RUN_PASS candidate={key} stem=(\S+)$", log, re.M
            )
            self.assertEqual(passed_stems, full_stems)
            self.assertEqual(len(passed_stems), expected["workload_passes"])
            self.assertEqual(len(re.findall(r"^RUN_FAIL ", log, re.M)),
                             expected["workload_failures"])
            self.assertEqual(expected["workload_runs"],
                             expected["workload_passes"] + expected["workload_failures"])
            self.assertEqual(len(re.findall(r"^AER_RESET_DRAIN_PASS ", log, re.M)),
                             int(expected["reset_pass"]))
            self.assertIn(f"CANDIDATE_COMPLETE key={key} pairwise_status=0", log)
            status = int(self.members[
                f"results/{key}/pairwise-cross-map.status"
            ].decode().strip())
            self.assertEqual(status, expected["pairwise_status"])

            metrics = []
            for line in log.splitlines():
                if line.startswith("AER_CLEAN_METRICS "):
                    row = dict(re.findall(r"(\w+)=([^ ]+)", line))
                    if row["test"] != "basic_reset_drain":
                        metrics.append(row)
            self.assertEqual(len(metrics), expected["workload_runs"])
            totals = {
                field: sum(int(row[field]) for row in metrics)
                for field in ("generated", "accepted", "delivered", "overrun", "errors")
            }
            self.assertEqual(totals, expected["full50"])
            self.assertEqual(totals["accepted"], totals["delivered"])
            self.assertEqual(totals["generated"],
                             totals["accepted"] + totals["overrun"])


if __name__ == "__main__":
    unittest.main()
