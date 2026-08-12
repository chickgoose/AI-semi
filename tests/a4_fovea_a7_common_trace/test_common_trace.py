from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


class CommonTraceSmokeTest(unittest.TestCase):
    def test_generator_v4_core_simultaneous_actual_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            completed = subprocess.run(
                [sys.executable, str(HERE / "run_common_trace.py"),
                 "--suite", "smoke", "--output", str(output)],
                text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual("LOCAL_RTL", receipt["status"])
            self.assertEqual(0, receipt["queue_entries"])
            self.assertEqual(2, receipt["consumer_latency_cycles"])
            self.assertEqual(1, len(receipt["runs"]))
            events = output / "runs/core_simultaneous_identity/trace.events.csv"
            with events.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(16, len(rows))
            delivered = [row for row in rows if row["event_state"] == "delivered"]
            self.assertEqual(16, len(delivered))
            self.assertTrue(all(int(row["delivery_cycle"]) - int(row["accept_cycle"]) == 2
                                for row in delivered))
            self.assertEqual(list(range(16)), sorted(int(row["logical_source"]) for row in delivered))

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
