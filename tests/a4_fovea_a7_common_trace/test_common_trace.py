from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.a4_fovea_a7_common_trace.run_common_trace import (
    RunError, expected_load_pct, validate_load_pct,
)

HERE = Path(__file__).resolve().parent


class CommonTraceSmokeTest(unittest.TestCase):
    def test_frozen_load_pct_rounding_positive_and_truncation_mutation(self) -> None:
        self.assertEqual(13, expected_load_pct("0.125"))
        self.assertEqual(77, expected_load_pct("0.769"))
        validate_load_pct(13, "0.125")
        validate_load_pct(77, "0.769")
        with self.assertRaisesRegex(RunError, "nearest-integer rounding"):
            validate_load_pct(12, "0.125")
        with self.assertRaisesRegex(RunError, "nearest-integer rounding"):
            validate_load_pct(76, "0.769")

    def test_generator_v4_core_simultaneous_actual_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            completed = subprocess.run(
                [sys.executable, str(HERE / "run_common_trace.py"),
                 "--suite", "smoke", "--output", str(output)],
                text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual("a4_fovea_a7_common_trace_v4", receipt["schema"])
            self.assertEqual("LOCAL_RTL_TRACE_REPLAY_PASS", receipt["status"])
            self.assertEqual(
                "e9f27e6aed302491011a5deb803a7b42a0c712b3",
                receipt["provenance"]["owner_hardening_commit"])
            capacity = receipt["capacity_accounting"]
            self.assertEqual(0, capacity["candidate_event_queue_entries"])
            self.assertEqual(16, capacity["benchmark_ingress_pending_slots"])
            self.assertEqual(1, capacity["candidate_sustained_output_cap_events_per_cycle"])
            self.assertFalse(capacity["free_queue_used"])
            self.assertEqual(16, capacity["totals"]["accepted"])
            self.assertEqual(0, capacity["totals"]["accepted_not_delivered"])
            self.assertEqual(0, capacity["totals"]["pending_at_end"])
            scope = receipt["functional_scope"]
            self.assertEqual(2, scope["consumer_latency_cycles"])
            self.assertEqual("initial_release_only_sample_fall_to_ref_rise_4ns",
                             scope["reset"])
            self.assertEqual("delivery_cycle < stim_cycles",
                             scope["measurement_delivered_definition"])
            self.assertEqual("delivered_in_measurement/stim_cycles",
                             scope["throughput_definition"])
            self.assertEqual("(load_milli+5)/10_integer", scope["load_pct_definition"])
            self.assertEqual(8, scope["post_drain_quiet_guard_cycles"])
            self.assertEqual("negedge_activation_with_sim_cycle_zero_assertion",
                             scope["traffic_epoch"])
            self.assertIn("frozen_aer_clean_tb_not_executed", receipt["hold_scope"])
            self.assertIn("physical_and_PPA_qualification_not_claimed",
                          receipt["hold_scope"])
            self.assertEqual(1, len(receipt["runs"]))
            execution = receipt["execution_scope"]
            self.assertEqual("full50", execution["official_suite"])
            self.assertEqual(50, execution["official_stems_generated"])
            self.assertEqual(1, execution["official_stems_executed"])
            self.assertTrue(execution["smoke_subset"])
            self.assertEqual("22_official_runs_not_queue_depth",
                             execution["capacity22_means"])
            events = output / "runs/core_simultaneous_identity/trace.events.csv"
            with events.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(16, len(rows))
            delivered = [row for row in rows if row["event_state"] == "delivered"]
            self.assertEqual(16, len(delivered))
            self.assertTrue(all(int(row["delivery_cycle"]) - int(row["accept_cycle"]) == 2
                                for row in delivered))
            self.assertEqual(list(range(16)), sorted(int(row["logical_source"]) for row in delivered))
            self.assertIn("PASS status=LOCAL_RTL_TRACE_REPLAY_PASS", completed.stdout)
            self.assertIn("HOLD frozen_common_tb", completed.stdout)

    def test_explicit_missing_verilator_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            completed = subprocess.run(
                [sys.executable, str(HERE / "run_common_trace.py"),
                 "--suite", "smoke", "--output", str(output),
                 "--verilator", str(Path(directory) / "missing-verilator")],
                text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("Verilator unavailable", completed.stderr)
            self.assertFalse((output / "receipt.json").exists())

    def test_high_load_has_deliveries_after_frozen_measurement_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "highload"
            completed = subprocess.run(
                [sys.executable, str(HERE / "run_common_trace.py"),
                 "--suite", "highload-smoke", "--output", str(output)],
                text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual("uniform_l2p00_s2001", receipt["runs"][0]["name"])
            totals = receipt["capacity_accounting"]["totals"]
            self.assertLess(totals["delivered_in_measurement"], totals["delivered"])
            self.assertEqual(
                totals["delivered"],
                totals["delivered_in_measurement"] + totals["delivered_after_measurement"])
            with (output / "runs/uniform_l2p00_s2001/trace.csv").open(newline="") as stream:
                summary = next(csv.DictReader(stream))
            self.assertEqual(totals["delivered_in_measurement"],
                             int(summary["measurement_delivered"]))
            self.assertEqual(int(summary["measurement_delivered"]),
                             receipt["runs"][0]["delivered_in_measurement"])
            self.assertEqual(int(summary["measurement_cycles"]),
                             receipt["runs"][0]["measurement_cycles"])
            self.assertEqual(summary["throughput"], receipt["runs"][0]["throughput"])
            self.assertAlmostEqual(
                int(summary["measurement_delivered"]) / int(summary["measurement_cycles"]),
                float(summary["throughput"]), places=6)

    def test_refuses_output_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing"
            output.mkdir()
            completed = subprocess.run(
                [sys.executable, str(HERE / "run_common_trace.py"),
                 "--suite", "smoke", "--output", str(output)],
                text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("refusing to overwrite", completed.stderr)


if __name__ == "__main__":
    unittest.main()
