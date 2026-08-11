#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("analyze_moving_block.py")
SPEC = importlib.util.spec_from_file_location("a5_w4_analysis", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


class MovingBlockAuditTest(unittest.TestCase):
    def test_committed_exact_suite_result_is_complete(self):
        result_path = MODULE_PATH.parents[2] / (
            "docs/research/results/a5_w4_moving_block_audit.json"
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(2, result["schema_version"])
        self.assertEqual(audit.A4_COMMIT, result["provenance"]["a4_commit"])
        materialization = result["provenance"]["source_materialization"]
        self.assertEqual("git_cat_file_pinned_commit_blobs", materialization["mode"])
        self.assertIs(False, materialization["current_head_consulted"])
        self.assertEqual(50, len(result["suites"]["full50"]["runs"]))
        self.assertEqual(22, len(result["suites"]["capacity22"]["runs"]))
        full = result["suites"]["full50"]["aggregate"]
        capacity = result["suites"]["capacity22"]["aggregate"]
        self.assertEqual((41, 5491 + 5532),
                         (full["accepted_delta"], full["fixed_only"] + full["moving_only"]))
        self.assertEqual((35, 5403 + 5438),
                         (capacity["accepted_delta"],
                          capacity["fixed_only"] + capacity["moving_only"]))
        self.assertEqual((46, 46), (full["fixed_matched_latency"]["p99"],
                                    full["moving_matched_latency"]["p99"]))
        self.assertEqual((46, 47), (capacity["fixed_matched_latency"]["p99"],
                                    capacity["moving_matched_latency"]["p99"]))
        pairwise = result["pairwise_mapping"]["per_mapping_moving_minus_fixed"]
        self.assertEqual(240, pairwise["identity"]["matched_complete_pairs"])
        self.assertEqual(240, pairwise["affine"]["matched_complete_pairs"])

    def test_git_object_snapshot_is_independent_of_current_head(self):
        def git(repository: Path, *arguments: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments], text=True,
                capture_output=True, check=True,
            )
            return result.stdout.strip()

        with tempfile.TemporaryDirectory(prefix="a5-w4-head-test.") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            git(repository, "init", "-q")
            git(repository, "config", "user.name", "A5 W4 Test")
            git(repository, "config", "user.email", "a5-w4@example.invalid")
            source = repository / "evidence.txt"
            source.write_text("pinned evidence\n", encoding="utf-8")
            git(repository, "add", "evidence.txt")
            git(repository, "commit", "-q", "-m", "pinned")
            pinned = git(repository, "rev-parse", "HEAD")
            source.write_text("new owner head\n", encoding="utf-8")
            git(repository, "add", "evidence.txt")
            git(repository, "commit", "-q", "-m", "owner moved")
            self.assertNotEqual(pinned, git(repository, "rev-parse", "HEAD"))

            snapshot = root / "snapshot"
            audit.materialize_git_snapshot(
                repository, pinned, ("evidence.txt",), snapshot
            )
            self.assertEqual("pinned evidence\n",
                             (snapshot / "evidence.txt").read_text(encoding="utf-8"))
            self.assertEqual(0o700, audit.stat.S_IMODE(snapshot.stat().st_mode))
            self.assertEqual(
                0o600,
                audit.stat.S_IMODE((snapshot / "evidence.txt").stat().st_mode),
            )
            with self.assertRaises(audit.AuditError):
                audit.git_blob(repository, pinned, "../current-worktree-file")

    def test_percentile_is_frozen_nearest_rank(self):
        self.assertEqual(1, audit.percentile([1, 2, 3, 4], 0.01))
        self.assertEqual(2, audit.percentile([1, 2, 3, 4], 0.50))
        self.assertEqual(4, audit.percentile([1, 2, 3, 4], 0.99))
        self.assertIsNone(audit.percentile([], 0.99))

    def test_run_sign_test_ignores_zero_deltas(self):
        self.assertEqual(1.0, audit.sign_test_pvalue([0, 0]))
        self.assertEqual(0.25, audit.sign_test_pvalue([1, 1, 1, 0]))
        self.assertEqual(1.0, audit.sign_test_pvalue([1, -1, 0]))

    def test_fairness_is_demand_normalized(self):
        events = {
            0: audit.ObservedEvent(0, 0, 0, None, None, "delivered", 0, 1),
            1: audit.ObservedEvent(1, 0, 1, None, None, "source_overrun"),
            2: audit.ObservedEvent(2, 1, 2, None, None, "delivered", 2, 3),
        }
        run = audit.Replay("fair", "synthetic", 4, 5, 0, events)
        observed = audit.fairness_document(run)
        # Acceptance ratios are [1/2, 1], rather than raw service counts [1, 1].
        self.assertAlmostEqual(0.9, observed["demand_normalized_jain"])
        self.assertEqual(0.5, observed["min_source_acceptance_ratio"])

    def test_occurrence_id_intersection_exposes_survivor_swap(self):
        def event(event_id, source, latency):
            return audit.ObservedEvent(
                event_id, source, 0, None, None, "delivered", 0, latency - 1
            )
        fixed = audit.Replay("swap", "synthetic", 4, 5, 0,
                             {0: event(0, 0, 2), 1: event(1, 1, 9),
                              2: audit.ObservedEvent(2, 2, 0, None, None,
                                                     "source_overrun")})
        moving = audit.Replay("swap", "synthetic", 4, 5, 0,
                              {0: event(0, 0, 1),
                               1: audit.ObservedEvent(1, 1, 0, None, None,
                                                      "source_overrun"),
                               2: event(2, 2, 8)})
        metadata = {"run": {"workload": "synthetic", "seed": 1},
                    "trace_sha256": "0" * 64}
        row = audit.run_comparison("swap", metadata, fixed, moving)
        self.assertEqual(1, row["matched_accepted"])
        self.assertEqual(1, row["fixed_only"])
        self.assertEqual(1, row["moving_only"])
        self.assertEqual(-1, row["paired_latency_delta_moving_minus_fixed"]["mean"])


if __name__ == "__main__":
    unittest.main()
