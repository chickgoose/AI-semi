import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import generate_trace
import pairwise_contention_metrics


class PairwiseContentionMetricTest(unittest.TestCase):
    def test_worst_address_pair_and_service_skew_are_measured(self):
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            input_manifest = directory / "input.json"
            input_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "runs": [
                            {
                                "name": "pairwise_s7",
                                "workload": "pairwise_contention",
                                "seed": 7,
                                "geometry": {"width": 2, "height": 2},
                                "load": 0.5,
                                "stim_cycles": 32,
                                "parameters": {"pair_spacing": 2, "pair_repeats": 2},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            generated = directory / "generated"
            metadata = generate_trace.generate_manifest(input_manifest, generated)[0]
            trace = [
                json.loads(line)
                for line in (generated / metadata["trace_file"])
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            event_path = directory / "events.csv"
            fields = (
                "candidate",
                "test",
                "seed",
                "load_pct",
                "tb_only_event_id",
                "logical_source",
                "source_count",
                "occurrence_cycle",
                "accept_cycle",
                "delivery_cycle",
                "deadline_cycle",
                "observation_end_cycle",
                "event_state",
            )
            with event_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for row in trace:
                    occurrence = row["occurrence_cycle"] + 4
                    latency = (
                        1
                        if row["relation_role"] == "a"
                        else row["relation_id"] + 2
                    )
                    writer.writerow(
                        {
                            "candidate": "fixture",
                            "test": metadata["report_group"],
                            "seed": "7",
                            "load_pct": "50",
                            "tb_only_event_id": row["tb_only_event_id"],
                            "logical_source": row["logical_source"],
                            "source_count": 4,
                            "occurrence_cycle": occurrence,
                            "accept_cycle": occurrence,
                            "delivery_cycle": occurrence + latency,
                            "deadline_cycle": occurrence + 32,
                            "observation_end_cycle": 128,
                            "event_state": "delivered",
                        }
                    )
            result = pairwise_contention_metrics.analyze(
                generated / metadata["trace_file"],
                generated / "pairwise_s7.manifest.json",
                event_path,
            )
            self.assertEqual(result["pair_count"], 12)
            self.assertEqual(result["evaluable_pairs"], 12)
            self.assertEqual(result["dropped_pairs"], 0)
            self.assertEqual(result["censored_pairs"], 0)
            self.assertEqual(result["max_pair_completion_latency_cycles"], 13)
            self.assertEqual(result["max_pair_service_skew_cycles"], 12)
            self.assertEqual(result["a_first_pairs"], 12)
            self.assertEqual(result["worst_completion_pair"]["relation_id"], 11)
            self.assertEqual(result["worst_skew_pair"]["relation_id"], 11)
            self.assertEqual(len(result["pair_aggregates"]), 6)
            self.assertEqual(len(result["trials"]), 12)
            self.assertEqual(
                {row["trial_count"] for row in result["pair_aggregates"]}, {2}
            )
            self.assertGreater(result["overlap_pairs"], 0)
            self.assertGreater(result["max_overlapping_prior_pairs"], 1)
            self.assertEqual(result["measurement_state"], "COMPLETE")
            self.assertEqual(result["nonevaluable_pairs"], 0)
            self.assertEqual(
                result["worst_completion_pair"]["canonical_source_a"], 2
            )

            run_manifest = generated / "pairwise_s7.manifest.json"
            manifest_payload = json.loads(run_manifest.read_text(encoding="utf-8"))
            manifest_payload["logical_source_permutation"] = [1, 0, 2, 3]
            run_manifest.write_text(
                json.dumps(manifest_payload) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                pairwise_contention_metrics.PairwiseMetricError,
                "frozen permutation",
            ):
                pairwise_contention_metrics.analyze(
                    generated / metadata["trace_file"], run_manifest, event_path
                )

            manifest_payload["logical_source_permutation"] = [0, 1, 2, 3]
            trace_path = generated / metadata["trace_file"]
            trace_rows = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            trace_rows[0]["relation_id"] = 99
            trace_rows[1]["relation_id"] = 99
            trace_text = "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in trace_rows
            )
            trace_path.write_text(trace_text, encoding="utf-8")
            manifest_payload["trace_sha256"] = hashlib.sha256(
                trace_text.encode("utf-8")
            ).hexdigest()
            run_manifest.write_text(
                json.dumps(manifest_payload) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                pairwise_contention_metrics.PairwiseMetricError,
                "contiguous",
            ):
                pairwise_contention_metrics.analyze(
                    trace_path, run_manifest, event_path
                )


if __name__ == "__main__":
    unittest.main()
