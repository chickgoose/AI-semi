from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "physical/k2_w2_qualifier/qualify_functional_yzr1.py"
ARCHIVE = Path(os.environ.get(
    "K2_FUNCTIONAL_YZR1_ARCHIVE", "/tmp/eval-fovea-cluster2.yZr1kmYL.tar.gz"))
SPEC = importlib.util.spec_from_file_location("k2_functional_yzr1", PARSER)
assert SPEC and SPEC.loader
QUALIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFIER)


class FunctionalYzr1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not ARCHIVE.is_file():
            raise RuntimeError(
                f"yZr1 archive is required (set K2_FUNCTIONAL_YZR1_ARCHIVE): {ARCHIVE}")
        cls.archive_data, _ = QUALIFIER.stable_read(ARCHIVE)
        if QUALIFIER.sha256(cls.archive_data) != QUALIFIER.ARCHIVE_SHA256:
            raise RuntimeError("yZr1 archive SHA-256 mismatch")
        cls.members = QUALIFIER.extract_members(
            cls.archive_data, QUALIFIER.EXPECTED_MEMBER_COUNT)

    @staticmethod
    def replace(members: dict[str, bytes], path: str, old: bytes, new: bytes) -> dict[str, bytes]:
        mutated = dict(members)
        value = mutated[path]
        if value.count(old) != 1:
            raise AssertionError(f"mutation anchor must occur exactly once: {path}: {old!r}")
        mutated[path] = value.replace(old, new, 1)
        return mutated

    @staticmethod
    def rehash_ledger(members: dict[str, bytes], artifact: str) -> dict[str, bytes]:
        mutated = dict(members)
        ledger = mutated["result-artifacts.sha256"]
        suffix = (QUALIFIER.EXPECTED_ATTEMPT + "/" + artifact).encode()
        lines = ledger.splitlines()
        matches = [index for index, line in enumerate(lines) if line.endswith(b"  " + suffix)]
        if len(matches) != 1:
            raise AssertionError("ledger artifact anchor mismatch")
        lines[matches[0]] = QUALIFIER.sha256(mutated[artifact]).encode() + b"  " + suffix
        mutated["result-artifacts.sha256"] = b"\n".join(lines) + b"\n"
        return mutated

    def test_actual_archive_loss_only_receipt_and_exact_totals(self) -> None:
        receipt = QUALIFIER.qualify_archive(ARCHIVE)
        self.assertEqual(receipt["archive"]["sha256"], QUALIFIER.ARCHIVE_SHA256)
        self.assertEqual(receipt["archive"]["member_count"], 344)
        self.assertEqual(receipt["ledger"]["rows"], 338)
        self.assertEqual(receipt["ledger"]["verified"], 338)
        self.assertEqual(receipt["full_stems"].__len__(), 50)
        self.assertEqual(receipt["capacity_stems"].__len__(), 22)
        for candidate, totals in QUALIFIER.EXPECTED_TOTALS.items():
            row = receipt["candidates"][candidate]
            self.assertEqual(row["full50_runs"], 50)
            self.assertEqual(row["capacity22_runs"], 22)
            self.assertEqual(row["full50_totals"], totals)
            self.assertEqual(row["run_log"]["run_pass"], 50)
            self.assertTrue(row["run_log"]["reset_pass"])
            self.assertEqual(row["run_log"]["pairwise_status"], 0)
            self.assertTrue(row["accepted_equals_delivered"])
        self.assertEqual(receipt["excluded_untrusted_evidence"]["outer_eval_driver_final_log"],
                         "STALE_0Ffa_NOT_IN_ARCHIVE_NOT_BOUND")
        self.assertEqual(receipt["claim_boundary"]["official_common_receipt"],
                         "HOLD_WORKSPACE_DIFF_NON_OFFICIAL")
        self.assertEqual(receipt["claim_boundary"]["ppa_area_timing_power_energy"],
                         "FORBIDDEN_NOT_EVIDENCED")
        self.assertEqual(QUALIFIER.canonical(receipt),
                         QUALIFIER.canonical(QUALIFIER.qualify_archive(ARCHIVE)))

    def test_archive_mutation_and_wrong_hash_fail_before_claims(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-yzr1-pin-") as directory:
            path = Path(directory) / "mutated.tar.gz"
            path.write_bytes(self.archive_data + b"x")
            with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "SHA-256 mismatch"):
                QUALIFIER.qualify_archive(path)

    def test_ledger_missing_changed_partial_and_fabricated_outputs_fail(self) -> None:
        missing_row = dict(self.members)
        lines = missing_row["result-artifacts.sha256"].splitlines()
        missing_row["result-artifacts.sha256"] = b"\n".join(lines[:-1]) + b"\n"
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "expected 338 rows"):
            QUALIFIER.analyze_members(missing_row)

        bad_hash = self.replace(self.members, "result-artifacts.sha256",
                                self.members["result-artifacts.sha256"][:64], b"0" * 64)
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "artifact SHA-256 mismatch"):
            QUALIFIER.analyze_members(bad_hash)

        fabricated = dict(self.members)
        fabricated["results/fovea/runs/fabricated/trace.csv"] = b"sentinel PASS\n"
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "member count mismatch"):
            QUALIFIER.analyze_members(fabricated)

    def test_rehashed_metrics_corruption_still_fails_semantic_conservation(self) -> None:
        path = "results/fovea/runs/core_sparse_identity/trace.csv"
        mutated = self.replace(self.members, path,
                               b",16,0,16,16,0,", b",16,0,16,15,0,")
        mutated = self.rehash_ledger(mutated, path)
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError,
                                    "accepted/delivered conservation"):
            QUALIFIER.analyze_members(mutated)

    def test_run_reset_pairwise_and_stem_mutations_fail(self) -> None:
        run_log = self.replace(self.members, "fovea-run.log",
                               b"RUN_PASS candidate=fovea stem=core_sparse_identity",
                               b"RUN_FAIL candidate=fovea stem=core_sparse_identity")
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "failure marker"):
            QUALIFIER.analyze_members(run_log)

        pair = "results/cluster2/pairwise-cross-map.status"
        mutated = dict(self.members)
        mutated[pair] = b"1\n"
        mutated = self.rehash_ledger(mutated, pair)
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "not zero"):
            QUALIFIER.analyze_members(mutated)

        reset = "results/fovea/reset/basic_reset_drain.csv"
        mutated = self.replace(self.members, reset, b",16,0,16,16,0,",
                               b",16,0,16,15,0,")
        mutated = self.rehash_ledger(mutated, reset)
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError,
                                    "accepted/delivered conservation"):
            QUALIFIER.analyze_members(mutated)

        stems = dict(self.members)
        stems["full-stems.txt"] = b"\n".join(
            stems["full-stems.txt"].splitlines()[:-1]) + b"\n"
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "expected 50 unique"):
            QUALIFIER.analyze_members(stems)

    def test_stale_outer_log_and_provenance_attempt_are_rejected(self) -> None:
        stale = dict(self.members)
        stale["eval-driver-final.log"] = b"attempt=eval-fovea-cluster2.0FfaT8kp\nPASS\n"
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError, "member count mismatch"):
            QUALIFIER.analyze_members(stale)

        provenance = self.replace(self.members, "provenance.txt",
                                  b"eval-fovea-cluster2.yZr1kmYL",
                                  b"eval-fovea-cluster2.0FfaT8kp")
        with self.assertRaisesRegex(QUALIFIER.GoldenQualificationError,
                                    "exact yZr1 field binding mismatch"):
            QUALIFIER.analyze_members(provenance)

    def test_cli_receipt_is_exclusive_and_location_independent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="k2-yzr1-cli-") as directory:
            copied = Path(directory) / "renamed.tgz"
            copied.write_bytes(self.archive_data)
            self.assertEqual(QUALIFIER.canonical(QUALIFIER.qualify_archive(ARCHIVE)),
                             QUALIFIER.canonical(QUALIFIER.qualify_archive(copied)))
            output = Path(directory) / "receipt.json"
            command = [str(PARSER), "--archive", str(copied), "--output", str(output)]
            first = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("LOSS_ONLY_GO", first.stdout)
            original = output.read_bytes()
            second = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            self.assertEqual(second.returncode, 1)
            self.assertIn("K2_FUNCTIONAL_YZR1_HOLD", second.stderr)
            self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
