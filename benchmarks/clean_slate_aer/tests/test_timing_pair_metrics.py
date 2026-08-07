import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import generate_trace
import timing_pair_metrics


class TimingPairMetricTest(unittest.TestCase):
    def test_cross_source_relation_error_is_measured(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            input_manifest = directory / "input.json"
            input_manifest.write_text(json.dumps({
                "schema_version": 1,
                "runs": [{
                    "name": "timing_pair_s7",
                    "workload": "timing_pair",
                    "seed": 7,
                    "geometry": {"width": 2, "height": 2},
                    "load": 0.25,
                    "stim_cycles": 32,
                    "parameters": {"pair_count": 2, "pair_gap": 2}
                }]
            }), encoding="utf-8")
            generated = directory / "generated"
            metadata = generate_trace.generate_manifest(input_manifest, generated)[0]
            trace = [
                json.loads(line)
                for line in (generated / metadata["trace_file"]).read_text().splitlines()
            ]
            event_path = directory / "events.csv"
            fields = (
                "candidate", "test", "seed", "load_pct", "tb_only_event_id",
                "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
                "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
            )
            pair_extra = {0: {"a": 0, "b": 1}, 1: {"a": 0, "b": 3}}
            with event_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for row in trace:
                    occurrence = row["occurrence_cycle"] + 4
                    extra = pair_extra[row["relation_id"]][row["relation_role"]]
                    writer.writerow({
                        "candidate": "fixture",
                        "test": metadata["report_group"],
                        "seed": "7",
                        "load_pct": "25",
                        "tb_only_event_id": row["tb_only_event_id"],
                        "logical_source": row["logical_source"],
                        "source_count": 4,
                        "occurrence_cycle": occurrence,
                        "accept_cycle": occurrence,
                        "delivery_cycle": occurrence + 1 + extra,
                        "deadline_cycle": occurrence + 32,
                        "observation_end_cycle": 64,
                        "event_state": "delivered",
                    })
            result = timing_pair_metrics.analyze(
                generated / metadata["trace_file"],
                generated / "timing_pair_s7.manifest.json",
                event_path,
            )
            self.assertEqual(result["pair_count"], 2)
            self.assertEqual(result["evaluable_pairs"], 2)
            self.assertEqual(result["mean_pair_timing_error_cycles"], 2.0)
            self.assertEqual(result["max_pair_timing_error_cycles"], 3)


if __name__ == "__main__":
    unittest.main()
