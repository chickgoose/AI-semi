#!/usr/bin/env python3
"""Verify the immutable workspace-diff archive as loss-only evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "physical/k2_w2_functional_loss/loss_evidence.json"
BOUNDARY_REGISTRY = ROOT / "physical/k2_w2_boundaries.json"
SHA_RE = re.compile(r"^([0-9a-f]{64})  (/.+)$")
METRIC_RE = re.compile(r"([A-Za-z_]+)=([^ ]+)")


class LossEvidenceArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.archive = Path(cls.manifest["immutable_local_archive"]["path"])
        cls.archive_bytes = cls.archive.read_bytes()

    def test_receipt_is_loss_only_and_not_a_physical_cohort(self) -> None:
        self.assertEqual(self.manifest["receipt_status"], "workspace-diff/non-official")
        self.assertEqual(self.manifest["evidence_usage"], "loss_only")
        self.assertIsNone(self.manifest["physical_cohort"])
        self.assertIs(self.manifest["ppa_eligible"], False)
        self.assertIs(self.manifest["ranking_eligible"], False)
        self.assertEqual(
            set(self.manifest["prohibited_uses"]),
            {"area", "power", "timing", "energy", "ppa", "physical_ranking"},
        )
        registry = json.loads(BOUNDARY_REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(len(registry["cohorts"]), 3)
        evidence = registry["functional_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["usage"], "loss_only")
        self.assertIsNone(evidence[0]["physical_cohort"])
        self.assertIs(evidence[0]["ppa_or_ranking_eligible"], False)

    def test_archive_and_bound_member_hashes(self) -> None:
        self.assertEqual(
            hashlib.sha256(self.archive_bytes).hexdigest(),
            self.manifest["immutable_local_archive"]["sha256"],
        )
        self.assertEqual(
            self.manifest["immutable_local_archive"]["sha256"],
            "22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39",
        )
        with tarfile.open(self.archive, "r:gz") as archive:
            regular = {member.name: member for member in archive.getmembers() if member.isfile()}
            for name in regular:
                path = PurePosixPath(name)
                self.assertFalse(path.is_absolute(), name)
                self.assertNotIn("..", path.parts, name)
            for spec in self.manifest["bound_members"]:
                self.assertIn(spec["path"], regular)
                payload = archive.extractfile(regular[spec["path"]]).read()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), spec["sha256"])

    def test_provenance_and_complete_inner_ledger(self) -> None:
        with tarfile.open(self.archive, "r:gz") as archive:
            regular = {member.name: member for member in archive.getmembers() if member.isfile()}
            provenance = archive.extractfile("provenance.txt").read().decode()
            parsed = dict(line.split("=", 1) for line in provenance.splitlines()
                          if "=" in line)
            for key in ("snapshot_head", "binding_reset_quiet_arming_patch", "attempt"):
                self.assertEqual(parsed[key], self.manifest["provenance"][key])
            self.assertNotIn("0FfaT8kp", provenance)

            ledger_text = archive.extractfile(self.manifest["ledger"]["member"]).read().decode()
            ledger = {}
            for line in ledger_text.splitlines():
                match = SHA_RE.fullmatch(line)
                self.assertIsNotNone(match, line)
                digest, absolute = match.groups()
                prefix = self.manifest["provenance"]["attempt"] + "/"
                self.assertTrue(absolute.startswith(prefix), absolute)
                relative = absolute[len(prefix):]
                self.assertNotIn(relative, ledger)
                ledger[relative] = digest
            result_members = {name for name in regular if name.startswith("results/")}
            self.assertEqual(len(ledger), self.manifest["ledger"]["entries"])
            self.assertEqual(set(ledger), result_members)
            for relative, expected in ledger.items():
                payload = archive.extractfile(regular[relative]).read()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected, relative)

    def test_candidate_logs_reproduce_loss_totals(self) -> None:
        with tarfile.open(self.archive, "r:gz") as archive:
            for candidate, expected in self.manifest["candidates"].items():
                log = archive.extractfile(expected["run_log"]).read().decode(errors="replace")
                rows = []
                passes = []
                for line in log.splitlines():
                    if line.startswith("AER_CLEAN_METRICS "):
                        row = dict(METRIC_RE.findall(line))
                        if row.get("test") != "basic_reset_drain":
                            rows.append(row)
                    if line.startswith("AER_CLEAN_TEST_PASS "):
                        passes.append(line)
                self.assertEqual(len(rows), expected["functional_runs_total"])
                self.assertEqual(len(passes) - 1, expected["functional_runs_passed"])
                self.assertTrue(all(int(row["errors"]) == 0 for row in rows))
                totals = {
                    key: sum(int(row[key]) for row in rows)
                    for key in ("generated", "accepted", "delivered", "overrun")
                }
                self.assertEqual(totals, expected["full50"])
                self.assertIn("AER_RESET_DRAIN_PASS ", log)
                self.assertIn(
                    f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0", log
                )
                status = archive.extractfile(
                    f"results/{candidate}/pairwise-cross-map.status"
                ).read()
                self.assertEqual(status, b"0\n")

    def test_stale_outer_log_is_forbidden_not_bound(self) -> None:
        excluded = self.manifest["excluded_artifacts"]
        self.assertEqual(len(excluded), 1)
        self.assertTrue(excluded[0]["path"].endswith("/eval-driver-final.log"))
        self.assertIn("0FfaT8kp", excluded[0]["reason"])
        bound = {row["path"] for row in self.manifest["bound_members"]}
        self.assertNotIn("eval-driver-final.log", bound)
        with tarfile.open(self.archive, "r:gz") as archive:
            self.assertNotIn("eval-driver-final.log", archive.getnames())


if __name__ == "__main__":
    unittest.main(verbosity=2)
