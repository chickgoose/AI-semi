from __future__ import annotations

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("a5_w7_actual_import", HERE / "import_actual_archive.py")
assert SPEC and SPEC.loader
I = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(I)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LedgerImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="a5-w7-import-test-")
        self.root = Path(self.temporary.name)
        (self.root / "results" / "c" / "runs").mkdir(parents=True)
        lines = []
        for number in range(I.EXPECTED_LEDGER_COUNT):
            path = self.root / "results" / "c" / "runs" / f"artifact-{number:03d}.txt"
            path.write_text(f"artifact {number}\n", encoding="utf-8")
            lines.append(f"{sha(path)}  /stale/attempt/results/c/runs/{path.name}\n")
        (self.root / "result-artifacts.sha256").write_text("".join(lines), encoding="utf-8")
        (self.root / "provenance.txt").write_text(
            "snapshot_head=" + "1" * 40 + "\n"
            "binding_reset_quiet_arming_patch=workspace-diff\n"
            "snapshot_archive_sha256=" + "2" * 64 + "\n"
            "canonical_rtl_date_kst=2026-08-09\n"
            "attempt=/stale/attempt\n"
            "hostname=test-host\n"
            "start_utc=2026-08-12T00:00:00Z\n"
            "finish_utc=2026-08-12T00:01:00Z\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stale_prefix_is_safely_rebased(self) -> None:
        receipt = I.verify_result_ledger(self.root.resolve(), "/stale/attempt")
        self.assertEqual(338, receipt["count"])
        self.assertEqual("PASS_EXACT_PROVENANCE_PREFIX_AND_REBASED_RESULT_TREE", receipt["status"])

    def test_sha_swap_rejected(self) -> None:
        target = self.root / "results/c/runs/artifact-007.txt"
        target.write_text("mutated\n", encoding="utf-8")
        with self.assertRaisesRegex(I.ImportError, "SHA mismatch"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_missing_result_rejected(self) -> None:
        os.unlink(self.root / "results/c/runs/artifact-007.txt")
        with self.assertRaisesRegex(I.ImportError, "cannot safely open regular file"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_extra_result_rejected(self) -> None:
        (self.root / "results/c/runs/extra.txt").write_text("extra\n", encoding="utf-8")
        with self.assertRaisesRegex(I.ImportError, "ledger/result tree mismatch"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_duplicate_path_rejected(self) -> None:
        ledger = self.root / "result-artifacts.sha256"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        lines[-1] = lines[0]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(I.ImportError, "duplicate ledger"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_hardlink_inode_reuse_rejected(self) -> None:
        first = self.root / "results/c/runs/artifact-000.txt"
        second = self.root / "results/c/runs/artifact-001.txt"
        os.unlink(second)
        os.link(first, second)
        ledger = self.root / "result-artifacts.sha256"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        lines[1] = f"{sha(second)}  /stale/attempt/results/c/runs/{second.name}"
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(I.ImportError, "single-linked regular file"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_ambiguous_results_boundary_rejected(self) -> None:
        ledger = self.root / "result-artifacts.sha256"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("/stale/attempt/results/", "/results/stale/results/")
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(I.ImportError, "exact provenance"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_wrong_attempt_prefix_rejected(self) -> None:
        with self.assertRaisesRegex(I.ImportError, "exact provenance"):
            I.verify_result_ledger(self.root.resolve(), "/different/attempt")

    def test_parent_symlink_rejected(self) -> None:
        backing = self.root / "backing-results"
        os.rename(self.root / "results", backing)
        os.symlink(backing, self.root / "results", target_is_directory=True)
        with self.assertRaisesRegex(I.ImportError, "parent path contains a symlink"):
            I.verify_result_ledger(self.root.resolve(), "/stale/attempt")

    def test_provenance_regular_leaf_stable_read(self) -> None:
        observed = I.parse_provenance(self.root.resolve())
        self.assertEqual("/stale/attempt", observed["attempt"])
        self.assertEqual("workspace-diff", observed["binding_reset_quiet_arming_patch"])

    def test_provenance_leaf_symlink_rejected(self) -> None:
        provenance = self.root / "provenance.txt"
        target = self.root / "provenance-target.txt"
        os.rename(provenance, target)
        os.symlink(target, provenance)
        with self.assertRaisesRegex(I.ImportError, "leaf path contains a symlink"):
            I.parse_provenance(self.root.resolve())


class PerformanceParetoTest(unittest.TestCase):
    @staticmethod
    def metric(epc: float) -> dict:
        aggregate = {
            "fixed_window_event_per_cycle": epc, "overrun_ratio": 0.0,
            "worst_run_p99_e2e_latency": 3, "max_request_wait": 2,
            "worst_demand_normalized_fairness": 1.0, "min_source_delivery_ratio": 1.0,
        }
        return {
            "full50": dict(aggregate), "capacity22": dict(aggregate),
            "capacity": {"knee_load": 1.0, "curve": [{"load": 2.0}]},
            "families": {"spatial": dict(aggregate), "moving": dict(aggregate)},
            "pairwise_mapping": {
                "identity": {"completion_ratio": 1.0, "p99_pair_max_latency": 3},
                "affine": {"completion_ratio": 1.0, "p99_pair_max_latency": 3},
                "relation_completion_churn": 0,
            },
        }

    def test_fake_policy_dimension_cannot_change_performance_pareto(self) -> None:
        metrics = {"a": self.metric(0.5), "b": self.metric(0.75)}
        baseline = I.performance_pareto(metrics)
        metrics["a"]["fabricated_policy_score"] = 1e9
        metrics["b"]["fabricated_policy_score"] = -1e9
        self.assertEqual(baseline, I.performance_pareto(metrics))
        self.assertEqual(["b"], baseline["frontier"])

class ActualArchiveIntegrationTest(unittest.TestCase):
    def test_actual_archive_e2e_is_path_independent(self) -> None:
        archive_text = os.environ.get("A5_W7_ACTUAL_ARCHIVE")
        if not archive_text:
            self.skipTest("set A5_W7_ACTUAL_ARCHIVE for recovered-archive integration")
        archive = Path(archive_text).resolve()
        generator = Path("/home/chickgoose/projects/a1/benchmarks/clean_slate_aer/generate_trace.py")
        manifest_root = HERE.parent / "common_suite_receipt/fixtures"
        first = I.import_and_evaluate(archive, generator, manifest_root)
        second = I.import_and_evaluate(archive, generator, manifest_root)
        self.assertEqual(first, second)
        self.assertEqual(338, first["archive_receipt"]["result_artifacts"]["count"])
        self.assertEqual(100, first["archive_receipt"]["trace_logs"]["checked"])
        self.assertEqual(
            ["ganghee-cluster2-row-bitmap"], first["pareto"]["frontier"]
        )
        encoded = str(first)
        self.assertNotIn(str(archive.parent), encoded)
        self.assertEqual(
            "ganghee-native-coordinate-source-projection",
            first["recommendation"]["scalar_a7_base"],
        )


if __name__ == "__main__":
    unittest.main()
