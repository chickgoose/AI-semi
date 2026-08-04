import csv
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

import aggregate


class AggregateFixtureTest(unittest.TestCase):
    def setUp(self):
        self.saturation = ROOT / "fixtures" / "saturation_sweep.csv"
        self.failure = ROOT / "fixtures" / "correctness_failure.csv"

    def test_saturation_is_not_correctness_failure(self):
        runs = aggregate.read_runs([self.saturation])
        loads, tests = aggregate.aggregate_runs(runs)
        by_load = {row["load_pct"]: row for row in loads}

        self.assertEqual(tests[0]["correctness"], "PASS")
        self.assertEqual(tests[0]["knee_load_pct"], 15.0)
        self.assertTrue(tests[0]["tail_degraded"])
        self.assertEqual(by_load[15.0]["performance_state"], "SATURATED")
        self.assertEqual(by_load[15.0]["correctness_issues"], "")
        self.assertAlmostEqual(by_load[15.0]["delivery_ratio"], 1.0)
        self.assertAlmostEqual(by_load[15.0]["acceptance_ratio"], 1.0)
        self.assertAlmostEqual(by_load[15.0]["overrun_ratio"], 0.3)
        self.assertAlmostEqual(by_load[15.0]["avg_throughput"], 0.975)
        self.assertAlmostEqual(by_load[15.0]["worst_throughput"], 0.97)
        self.assertAlmostEqual(by_load[15.0]["avg_e2e_latency"], 105.0)
        self.assertEqual(by_load[15.0]["worst_e2e_latency"], 550.0)
        self.assertAlmostEqual(by_load[15.0]["avg_request_wait"], 210.0)
        self.assertEqual(by_load[15.0]["worst_request_wait"], 220.0)
        self.assertAlmostEqual(by_load[15.0]["avg_timing_error"], 5.5)
        self.assertEqual(by_load[15.0]["worst_timing_error"], 25.0)

    def test_correctness_failure_is_distinct(self):
        runs = aggregate.read_runs([self.failure])
        loads, tests = aggregate.aggregate_runs(runs)
        self.assertEqual(tests[0]["correctness"], "FAIL")
        self.assertEqual(loads[0]["performance_state"], "CORRECTNESS_FAIL")
        self.assertIn("scoreboard_errors", loads[0]["correctness_issues"])
        self.assertIn("delivered_exceeds_accepted", loads[0]["correctness_issues"])

    def test_cli_exit_status_distinguishes_saturation_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.csv"
            self.assertEqual(
                aggregate.main(
                    [str(self.saturation), "--fail-on-correctness", "-o", str(output)]
                ),
                0,
            )
            self.assertEqual(
                aggregate.main(
                    [str(self.failure), "--fail-on-correctness", "-o", str(output)]
                ),
                2,
            )

    def test_csv_and_json_writers(self):
        runs = aggregate.read_runs([self.saturation])
        loads, tests = aggregate.aggregate_runs(runs)
        csv_stream = io.StringIO()
        aggregate.write_csv(loads, csv_stream)
        csv_rows = list(csv.DictReader(io.StringIO(csv_stream.getvalue())))
        self.assertEqual(len(csv_rows), 3)
        self.assertEqual(csv_rows[-1]["performance_state"], "SATURATED")

        json_stream = io.StringIO()
        aggregate.write_json(
            loads,
            tests,
            json_stream,
            acceptance_floor=0.99,
            overrun_ceiling=0.01,
            tail_factor=1.5,
        )
        payload = json.loads(json_stream.getvalue())
        self.assertEqual(payload["tests"][0]["knee_load_pct"], 15.0)

    def test_missing_schema_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("test,seed\ncase,1\n", encoding="utf-8")
            with self.assertRaisesRegex(aggregate.InputError, "missing columns"):
                aggregate.read_runs([path])


if __name__ == "__main__":
    unittest.main()
