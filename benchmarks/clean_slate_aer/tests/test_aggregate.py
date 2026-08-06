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
        self.event_summary = ROOT / "fixtures" / "event_metrics_summary.csv"
        self.events = ROOT / "fixtures" / "event_metrics.csv"

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

    def test_nearest_rank_percentile_definition(self):
        values = [9, 1, 7, 3, 5]
        self.assertEqual(aggregate._nearest_rank(values, 50), 5)
        self.assertEqual(aggregate._nearest_rank(values, 95), 9)
        self.assertEqual(aggregate._nearest_rank(values, 99), 9)
        self.assertIsNone(aggregate._nearest_rank([], 95))

    def test_per_event_tail_deadline_censor_and_service_metrics(self):
        runs = aggregate.read_runs([self.event_summary])
        events = aggregate.read_events([self.events])
        loads, _ = aggregate.aggregate_runs(
            runs, events=events, service_window_cycles=4
        )
        row = loads[0]

        self.assertEqual(row["event_metrics_state"], "COMPLETE")
        self.assertEqual(row["event_rows"], 8)
        self.assertEqual(row["delivered_event_rows"], 5)
        self.assertEqual(row["undelivered_event_rows"], 3)
        self.assertEqual(row["censored_event_rows"], 2)
        self.assertEqual(row["p50_e2e_latency_cycles"], 2)
        self.assertEqual(row["p95_e2e_latency_cycles"], 4)
        self.assertEqual(row["p99_e2e_latency_cycles"], 4)
        self.assertEqual(row["p50_internal_latency_cycles"], 1)
        self.assertEqual(row["p95_internal_latency_cycles"], 3)
        self.assertEqual(row["p99_internal_latency_cycles"], 3)

        # Delivery exactly at deadline meets it; delivery one cycle late,
        # terminal overrun, and undelivered at observation==deadline miss.
        # An undelivered event observed only before its deadline is censored.
        self.assertEqual(row["deadline_events"], 7)
        self.assertEqual(row["deadline_misses"], 4)
        self.assertEqual(row["deadline_censored"], 1)
        self.assertAlmostEqual(row["deadline_miss_ratio"], 4 / 6)

        self.assertEqual(row["service_sources_expected"], 3)
        self.assertEqual(row["service_sources_delivered"], 2)
        self.assertEqual(row["service_sources_unobserved"], 1)
        self.assertEqual(row["service_gap_samples"], 3)
        self.assertEqual(row["p95_service_gap_cycles"], 7)
        self.assertEqual(row["p99_service_gap_cycles"], 7)
        self.assertEqual(row["max_service_gap_cycles"], 7)
        self.assertEqual(row["service_window_cycles"], 4)
        self.assertEqual(row["service_source_windows"], 21)
        self.assertEqual(row["min_service_per_source_window"], 0)
        self.assertEqual(row["zero_service_source_windows"], 10)
        self.assertAlmostEqual(row["zero_service_source_window_ratio"], 10 / 21)

    def test_per_event_cli_and_cycle_only_json_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.json"
            event_output = Path(directory) / "event-runs.csv"
            self.assertEqual(
                aggregate.main(
                    [
                        str(self.event_summary),
                        "--events",
                        str(self.events),
                        "--service-window-cycles",
                        "4",
                        "--format",
                        "json",
                        "-o",
                        str(output),
                        "--event-output",
                        str(event_output),
                    ]
                ),
                0,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["policy"]["service_window_cycles"], 4)
            self.assertEqual(payload["loads"][0]["p99_e2e_latency_cycles"], 4)
            self.assertEqual(payload["event_runs"][0]["seed"], "1")
            self.assertEqual(payload["event_runs"][0]["p95_e2e_latency_cycles"], 4)
            self.assertNotIn("frequency", payload["policy"])
            self.assertNotIn("latency_ns", payload["loads"][0])
            event_rows = list(
                csv.DictReader(io.StringIO(event_output.read_text(encoding="utf-8")))
            )
            self.assertEqual(event_rows[0]["test"], "metrics")
            self.assertEqual(event_rows[0]["seed"], "1")
            self.assertEqual(event_rows[0]["p99_internal_latency_cycles"], "3")

    def test_event_identity_and_run_contract_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-events.csv"
            header = ",".join(aggregate.EVENT_REQUIRED_COLUMNS)
            row = "metrics,1,10,0,3,3,0,,,,9,pending"
            path.write_text(f"{header}\n{row}\n", encoding="utf-8")
            with self.assertRaisesRegex(aggregate.InputError, "logical_source"):
                aggregate.read_events([path])


if __name__ == "__main__":
    unittest.main()
