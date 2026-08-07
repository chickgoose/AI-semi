import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import generate_trace
import phase_metrics


class PhaseMetricTest(unittest.TestCase):
    def test_phase_counts_backlog_and_recovery_use_exact_trace_cycles(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            input_manifest = directory / "input.json"
            input_manifest.write_text(json.dumps({
                "schema_version": 1,
                "runs": [{
                    "name": "phase_transition_s9",
                    "workload": "phase_transition",
                    "seed": 9,
                    "geometry": {"width": 2, "height": 2},
                    "load": 1.0,
                    "stim_cycles": 16,
                    "parameters": {
                        "sparse_load": 1.0,
                        "near_load": 1.0,
                        "overload_load": 2.0,
                        "post_load": 1.0,
                        "recovery_load": 0.0,
                        "fixed_polarity": 1,
                        "fixed_event_type": "spike"
                    }
                }]
            }), encoding="utf-8")
            output = directory / "generated"
            metadata = generate_trace.generate_manifest(input_manifest, output)[0]
            trace = [
                json.loads(line)
                for line in (output / metadata["trace_file"]).read_text().splitlines()
            ]
            event_path = directory / "events.csv"
            fieldnames = (
                "candidate", "test", "seed", "load_pct", "tb_only_event_id",
                "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
                "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
            )
            with event_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                for event in trace:
                    occurrence = event["occurrence_cycle"] + 5
                    writer.writerow({
                        "candidate": "fixture",
                        "test": metadata["report_group"],
                        "seed": "9",
                        "load_pct": "100",
                        "tb_only_event_id": event["tb_only_event_id"],
                        "logical_source": event["logical_source"],
                        "source_count": 4,
                        "occurrence_cycle": occurrence,
                        "accept_cycle": occurrence,
                        "delivery_cycle": occurrence + 2,
                        "deadline_cycle": occurrence + 32,
                        "observation_end_cycle": 24,
                        "event_state": "delivered",
                    })

            result = phase_metrics.analyze(
                output / metadata["trace_file"],
                output / "phase_transition_s9.manifest.json",
                event_path,
            )
            self.assertEqual(result["tb_cycle_offset"], 5)
            self.assertEqual([row["generated"] for row in result["phases"]], [4, 4, 8, 2, 0])
            self.assertEqual(result["recovery_to_zero_cycles"], 1)
            self.assertFalse(result["recovery_censored"])
            self.assertTrue(result["recovery_lossless"])
            self.assertGreater(result["phases"][2]["backlog_peak"], 0)


if __name__ == "__main__":
    unittest.main()
