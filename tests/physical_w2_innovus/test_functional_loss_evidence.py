from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = (
    ROOT / "tests/physical_w2_innovus/ganghee_functional_loss_pin.json"
)
ARCHIVE = Path(os.environ.get(
    "W2_GANGHEE_FUNCTIONAL_ARCHIVE",
    "/tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz",
))
METRIC = re.compile(r"(\w+)=([^ ]+)")


class FunctionalLossEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
        if not ARCHIVE.is_file():
            raise RuntimeError(f"functional evidence archive missing: {ARCHIVE}")
        actual = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
        expected = cls.pin["archive"]["sha256"]
        if actual != expected:
            raise RuntimeError(
                f"functional evidence archive SHA mismatch: {actual} != {expected}"
            )
        cls.archive = tarfile.open(ARCHIVE, mode="r:gz")
        cls.names = set(cls.archive.getnames())

    @classmethod
    def tearDownClass(cls):
        cls.archive.close()

    def member_bytes(self, name: str) -> bytes:
        handle = self.archive.extractfile(name)
        self.assertIsNotNone(handle, name)
        return handle.read()

    def member_text(self, name: str) -> str:
        return self.member_bytes(name).decode("utf-8")

    def test_archive_and_selected_members_are_sha_bound(self):
        self.assertEqual(
            self.pin["archive"]["sha256"],
            "22e2e649deaf1c6698af5a21bacfd37933fd93f000166fd39b7955ef00782f39",
        )
        for name, expected in self.pin["members"].items():
            actual = hashlib.sha256(self.member_bytes(name)).hexdigest()
            self.assertEqual(actual, expected, name)

    def test_inner_ledger_verifies_all_338_artifacts(self):
        provenance = {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in self.member_text("provenance.txt").splitlines()
            if "=" in line
        }
        prefix = provenance["attempt"].rstrip("/") + "/"
        ledger = self.member_text("result-artifacts.sha256").splitlines()
        self.assertEqual(len(ledger), self.pin["ledger"]["entries"])
        for row in ledger:
            digest, absolute = row.split("  ", 1)
            self.assertTrue(absolute.startswith(prefix), absolute)
            member = absolute.removeprefix(prefix)
            path = PurePosixPath(member)
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            actual = hashlib.sha256(self.member_bytes(member)).hexdigest()
            self.assertEqual(actual, digest, member)

    @staticmethod
    def parse_run_log(text: str, candidate: str) -> dict[str, dict[str, int]]:
        metrics = None
        result = {}
        for line in text.splitlines():
            if line.startswith("AER_CLEAN_METRICS "):
                metrics = dict(METRIC.findall(line))
            elif line.startswith(f"RUN_PASS candidate={candidate} "):
                if metrics is None:
                    raise AssertionError("RUN_PASS has no preceding metrics")
                stem = dict(METRIC.findall(line))["stem"]
                if stem in result:
                    raise AssertionError(f"duplicate RUN_PASS stem: {stem}")
                result[stem] = {
                    key: int(metrics[key])
                    for key in ("generated", "accepted", "delivered", "overrun", "errors")
                }
                metrics = None
        return result

    def test_candidate_logs_recompute_full50_and_capacity22_loss(self):
        full = set(self.member_text("full-stems.txt").splitlines())
        capacity = set(self.member_text("capacity-stems.txt").splitlines())
        self.assertEqual(len(full), 50)
        self.assertEqual(len(capacity), 22)
        self.assertLessEqual(capacity, full)
        self.assertEqual(
            self.pin["capacity22_accounting"], "subset_of_full50_not_additive"
        )

        for candidate, expected in self.pin["candidates"].items():
            with self.subTest(candidate=candidate):
                log = self.member_text(f"{candidate}-run.log")
                rows = self.parse_run_log(log, candidate)
                self.assertEqual(set(rows), full)
                for label, stems in (("full50", full), ("capacity22", capacity)):
                    totals = {
                        key: sum(rows[stem][key] for stem in stems)
                        for key in ("generated", "accepted", "delivered", "overrun")
                    }
                    totals["runs"] = len(stems)
                    self.assertEqual(totals, expected[label])
                    self.assertEqual(
                        totals["generated"], totals["accepted"] + totals["overrun"]
                    )
                    self.assertEqual(totals["accepted"], totals["delivered"])
                    self.assertEqual(sum(rows[stem]["errors"] for stem in stems), 0)

    def test_reset_and_pairwise_completion_are_content_validated(self):
        for candidate in self.pin["candidates"]:
            with self.subTest(candidate=candidate):
                log = self.member_text(f"{candidate}-run.log")
                self.assertIn(
                    "AER_RESET_DRAIN_PASS generated=16 accepted=16 delivered=16",
                    log,
                )
                self.assertIn("AER_CLEAN_TEST_PASS basic_reset_drain", log)
                self.assertIn(
                    f"CANDIDATE_COMPLETE key={candidate} pairwise_status=0", log
                )
                status = self.member_text(
                    f"results/{candidate}/pairwise-cross-map.status"
                )
                self.assertEqual(status.strip(), "0")

    def test_provenance_is_workspace_diff_non_official(self):
        provenance = self.member_text("provenance.txt")
        expected = self.pin["provenance"]
        self.assertIn(f"snapshot_head={expected['snapshot_head']}", provenance)
        self.assertIn(
            "binding_reset_quiet_arming_patch=workspace-diff", provenance
        )
        self.assertIn("TOOL:\txrun(64)\t23.09-s013", provenance)
        self.assertEqual(expected["receipt_class"], "non-official")

    def test_scope_excludes_stale_outer_log_and_all_ppa_uses(self):
        self.assertEqual(self.pin["allowed_use"], ["functional_loss"])
        self.assertIn("eval-driver-final.log", self.pin["excluded_evidence"])
        self.assertFalse(any(
            name.endswith("eval-driver-final.log") for name in self.names
        ))
        self.assertEqual(
            set(self.pin["forbidden_use"]),
            {"area", "power", "timing", "fmax", "ppa", "physical_ranking"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
