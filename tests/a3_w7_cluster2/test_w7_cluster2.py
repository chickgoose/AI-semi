from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.a3_w7_cluster2 import run


class W7Cluster2Test(unittest.TestCase):
    def test_exact_rtl_provenance_and_semantics(self) -> None:
        provenance = run.check_provenance()
        self.assertEqual(provenance["introduction_commit"],
                         "3fb6a70592addcd5b3094987223c474d70f3db22")
        self.assertEqual(len(provenance["closure"]), 3)

    def test_official_run_sets_are_exact(self) -> None:
        provenance = run.check_provenance()
        names = run.check_benchmark(provenance)
        self.assertEqual(provenance["benchmark"]["a1_commit"],
                         "2a3a3be94be8f12585f484b5b1da2b372f7282d9")
        self.assertEqual(provenance["benchmark"]["generator_sha256"],
                         "59b649a1ec339fb4f2e92dee0f5a7dc7ec7130b05b3a578fea3ba6d7c9f61b50")
        self.assertEqual(len(names["full50"]), 50)
        self.assertEqual(len(names["capacity22"]), 22)
        self.assertEqual(names["full50"][-2:], [
            "mixed_phase_always_ready_identity",
            "mixed_phase_always_ready_bit_reverse",
        ])

    def test_rtl_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-w7-static-negative-") as temporary:
            root = Path(temporary)
            shutil.copytree(run.RTL_DIR, root / "rtl")
            shutil.copy2(run.PROVENANCE_PATH, root / "provenance.json")
            target = root / "rtl/aer_tx16_trad_rowcol_fovea_cluster2.v"
            target.write_text(target.read_text(encoding="utf-8").replace(
                "valid1 <= |periph_gnt;", "valid1 <= 1'b0;"), encoding="utf-8")
            with self.assertRaisesRegex(run.GateError, "RTL SHA mismatch"):
                run.check_provenance(root)

    def test_manifest_mutation_fails_closed(self) -> None:
        provenance = run.check_provenance()
        with tempfile.TemporaryDirectory(prefix="a3-w7-manifest-negative-") as temporary:
            root = Path(temporary)
            bench = root / "benchmarks/clean_slate_aer"
            bench.mkdir(parents=True)
            for name in ("generate_trace.py", "manifest.neutrality-n16.json",
                         "manifest.multilane-n16.json"):
                shutil.copy2(run.A1_OVERLAY / "benchmarks/clean_slate_aer" / name,
                             bench / name)
            manifest = bench / "manifest.multilane-n16.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["runs"].pop()
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(run.GateError, "provenance SHA mismatch"):
                run.check_benchmark(provenance, root)

    def test_persistent_probe_separates_weight_and_parallelism(self) -> None:
        weighted = run.persistent_policy_probe(run.WeightedBitmap)
        equal = run.persistent_policy_probe(run.EqualSplitBitmap)
        cluster = run.persistent_policy_probe(run.Cluster2)
        scalar = run.persistent_policy_probe(lambda: run.WeightedBitmap(scalar=True))
        self.assertEqual(weighted["row_opportunities_0_1_2_3"], [10, 50, 50, 10])
        self.assertEqual(scalar["row_opportunities_0_1_2_3"], [10, 50, 50, 10])
        self.assertEqual(equal["row_opportunities_0_1_2_3"], [30, 30, 30, 30])
        self.assertEqual(cluster["row_opportunities_0_1_2_3"], [60, 60, 60, 60])
        self.assertEqual(cluster["dual_lane_cycles"], 120)
        self.assertEqual(cluster["bitmap_events"], 960)

    def test_exact_pending_conservation(self) -> None:
        events = [run.Event(0, source, source) for source in range(16)]
        metrics = run.simulate(events, 8, run.Cluster2)
        self.assertEqual(metrics["generated"], 16)
        self.assertEqual(metrics["accepted"], metrics["delivered"])
        self.assertEqual(metrics["overrun"], 0)
        self.assertEqual(metrics["max_events_cycle"], 8)

    def test_repeated_same_source_is_capacity_overrun(self) -> None:
        events = [run.Event(cycle, cycle, 0) for cycle in range(8)]
        metrics = run.simulate(events, 8, run.Cluster2)
        self.assertEqual(metrics["generated"], metrics["accepted"] + metrics["overrun"])
        self.assertGreater(metrics["overrun"], 0)
        self.assertEqual(metrics["accepted"], metrics["delivered"])

    def test_full_receipt_is_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="a3-w7-receipt-") as temporary:
            first = Path(temporary) / "first.json"
            second = Path(temporary) / "second.json"
            run.execute(first)
            run.execute(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            receipt = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["suites"]["full50"]["rtl_lockstep_runs"], 50)
            self.assertEqual(receipt["suites"]["capacity22"]["rtl_lockstep_runs"], 22)
            self.assertEqual(len(receipt["negative_gate"]["mutants"]), 2)


if __name__ == "__main__":
    unittest.main()
