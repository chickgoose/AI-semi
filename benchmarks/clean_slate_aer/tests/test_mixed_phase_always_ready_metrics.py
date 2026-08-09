import csv
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mixed_phase_always_ready_metrics as mixed_metrics


class MixedPhaseFixture:
    fields = (
        "candidate", "test", "seed", "load_pct", "tb_only_event_id",
        "logical_source", "source_count", "occurrence_cycle", "accept_cycle",
        "delivery_cycle", "deadline_cycle", "observation_end_cycle", "event_state",
    )

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.trace_path = directory / "mixed.events.jsonl"
        self.manifest_path = directory / "mixed.manifest.json"
        self.event_path = directory / "events.csv"
        self.phases = [
            ("u_bernoulli", 0, 2),
            ("u_smooth", 2, 4),
            ("s_persistent", 4, 6),
            ("s_rotating", 6, 8),
            ("h_a", 8, 10),
            ("h_b", 10, 12),
            ("h_a_return", 12, 14),
        ]
        schedule = {
            "u_bernoulli": [(0, 0, None), (0, 1, None), (1, 2, None)],
            "u_smooth": [(0, 0, None), (0, 2, None), (1, 1, None)],
            "s_persistent": [(0, 0, None), (0, 1, None), (1, 0, None), (1, 1, None)],
            "s_rotating": [(0, 0, None), (0, 1, None), (1, 0, None), (1, 1, None)],
            "h_a": [(0, 0, 0), (0, 1, 1), (1, 0, 0)],
            "h_b": [(0, 2, 0), (0, 3, 1), (1, 2, 0)],
            "h_a_return": [(0, 0, 0), (0, 1, 1), (1, 0, 0)],
        }
        self.trace: list[dict[str, object]] = []
        for name, start, _ in self.phases:
            for local_cycle, source, rank in schedule[name]:
                row: dict[str, object] = {
                    "occurrence_cycle": start + local_cycle,
                    "tb_only_event_id": len(self.trace),
                    "logical_source": source,
                    "x": source % 2,
                    "y": source // 2,
                    "polarity": 1,
                    "event_type": "spike",
                    "relation_id": None,
                    "relation_role": None,
                    "deadline": start + local_cycle + 20,
                }
                if rank is not None:
                    row["canonical_rank"] = rank
                self.trace.append(row)
        self.trace.sort(key=lambda row: (int(row["occurrence_cycle"]), int(row["tb_only_event_id"])))
        for event_id, row in enumerate(self.trace):
            row["tb_only_event_id"] = event_id
        self.metadata: dict[str, object] = {}
        self.write_trace_and_manifest()
        self.write_events(overrun_id=7)

    def write_trace_and_manifest(self) -> None:
        text = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in self.trace)
        self.trace_path.write_text(text, encoding="utf-8")
        digest = hashlib.sha256(self.trace_path.read_bytes()).hexdigest()
        self.metadata = {
            "schema_version": 1,
            "generator_version": "mixed-test-1",
            "run": {
                "name": "mixed_phase_always_ready_identity",
                "workload": "mixed_phase_always_ready",
                "seed": 4001,
                "geometry": {"width": 2, "height": 2},
                "load": "1.0",
                "stim_cycles": 14,
                "parameters": {"fixed_polarity": 1, "fixed_event_type": "spike"},
                "sink": {"mode": "always"},
            },
            "report_group": "mixed_phase_always_ready_identity",
            "trace_file": self.trace_path.name,
            "trace_sha256": digest,
            "event_count": len(self.trace),
            "declared_mean_load": "1.0",
            "actual_mean_load": str(Decimal(len(self.trace)) / Decimal(14)),
            "event_identity_mode": "address_only",
            "dut_payload_fields": ["x", "y", "polarity", "event_type"],
            "dut_sideband_fields": ["logical_source"],
            "tb_only_fields": ["occurrence_cycle", "tb_only_event_id", "canonical_rank"],
            "generation_contract": "trace_is_fully_generated_before_any_DUT_ready_is_observed",
            "phase_provenance": {
                "schema_version": 1,
                "generator_version": "mixed-test-1",
                "trace_sha256": digest,
                "boundary_basis": "trace_occurrence_cycle",
                "phases": [
                    {"name": name, "start_cycle": start, "end_cycle_exclusive": end}
                    for name, start, end in self.phases
                ],
            },
        }
        self.manifest_path.write_text(json.dumps(self.metadata), encoding="utf-8")

    def write_events(self, *, overrun_id: int | None = None, censored_id: int | None = None) -> None:
        offset = 5
        with self.event_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fields)
            writer.writeheader()
            for row in self.trace:
                event_id = int(row["tb_only_event_id"])
                occurrence = int(row["occurrence_cycle"]) + offset
                state = "delivered"
                accept: int | str = occurrence
                delivery: int | str = occurrence + 1 + (event_id % 2)
                if event_id == overrun_id:
                    state, accept, delivery = "source_overrun", "", ""
                if event_id == censored_id:
                    state, accept, delivery = "accepted", occurrence, ""
                writer.writerow({
                    "candidate": "fixture",
                    "test": "mixed_phase_always_ready_identity",
                    "seed": "4001",
                    "load_pct": "100",
                    "tb_only_event_id": event_id,
                    "logical_source": row["logical_source"],
                    "source_count": 4,
                    "occurrence_cycle": occurrence,
                    "accept_cycle": accept,
                    "delivery_cycle": delivery,
                    "deadline_cycle": int(row["deadline"]) + offset,
                    "observation_end_cycle": 40,
                    "event_state": state,
                })


class MixedPhaseMetricTest(unittest.TestCase):
    def test_reports_complete_phase_metrics_deltas_and_capacity_loss(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            result = mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)
            self.assertEqual(result["classification"]["analysis_status"], "capacity_loss")
            self.assertEqual(result["classification"]["correctness_status"], "pass")
            self.assertEqual(result["classification"]["capacity_loss_events"], 1)
            self.assertEqual(result["provenance_validation"]["status"], "pass")
            self.assertEqual(result["matched_trace_validation"]["status"], "pass")
            self.assertEqual(len(result["phases"]), 7)
            sustained = next(row for row in result["phases"] if row["phase"] == "s_persistent")
            self.assertEqual(sustained["generated"], 4)
            self.assertEqual(sustained["source_overrun"], 1)
            self.assertEqual(sustained["accepted"], 3)
            self.assertEqual(sustained["delivered"], 3)
            self.assertIsNotNone(sustained["latency_cycles"]["p95"])
            self.assertIn("max_cycles", sustained["service_gap_cycles"])
            self.assertIn("backlog_peak", sustained)
            deltas = {row["pair"]: row for row in result["matched_pair_deltas"]}
            self.assertEqual(deltas["sustained_temporal"]["capacity_loss_events_delta"], 1)

    def test_rejects_censored_event_data(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            fixture.write_events(censored_id=0)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "right-censored"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

    def test_rejects_unbound_or_malformed_phase_provenance(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            metadata = copy.deepcopy(fixture.metadata)
            metadata["phase_provenance"]["trace_sha256"] = "0" * 64
            fixture.manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "not bound"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

    def test_rejects_non_address_only_or_mismatched_event_identity(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            metadata = copy.deepcopy(fixture.metadata)
            metadata["event_identity_mode"] = "payload16"
            fixture.manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "address_only"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

    def test_rejects_event_csv_source_identity_drift(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            with fixture.event_path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["logical_source"] = "3"
            with fixture.event_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fixture.fields)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "source identity"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

    def test_rejects_source_local_delivery_reordering(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            fixture.write_events(overrun_id=None)
            with fixture.event_path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            source_zero = [row for row in rows if row["logical_source"] == "0"]
            source_zero[0]["delivery_cycle"] = "35"
            with fixture.event_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fixture.fields)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "source-local order"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)

    def test_rejects_matched_pair_histogram_drift(self):
        with tempfile.TemporaryDirectory() as directory_name:
            fixture = MixedPhaseFixture(Path(directory_name))
            smooth = next(row for row in fixture.trace if row["occurrence_cycle"] == 2 and row["logical_source"] == 2)
            smooth["logical_source"], smooth["x"], smooth["y"] = 3, 1, 1
            fixture.write_trace_and_manifest()
            fixture.write_events(overrun_id=7)
            with self.assertRaisesRegex(mixed_metrics.MixedPhaseMetricError, "uniform pair"):
                mixed_metrics.analyze(fixture.manifest_path, fixture.event_path)


if __name__ == "__main__":
    unittest.main()
